"""RYVEN 3.0 — Unified Multi-Model AI Provider & Fallback Engine.

Coordinates Ollama (default), Grok (opt-in), Hugging Face Remote (opt-in), and
Local Hugging Face providers with deterministic routing, safety checks, and fallback chains.
"""

from __future__ import annotations

import time
from typing import Any, AsyncIterator, Dict, List, Optional
from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.contracts import (
    AIRequest,
    AIStreamChunk,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
)
from app.ai.grok import GrokProvider, GrokUnavailableError
from app.ai.hf_local import HuggingFaceLocalProvider, HuggingFaceLocalUnavailableError
from app.ai.hf_remote import HuggingFaceRemoteProvider, HuggingFaceRemoteUnavailableError
from app.ai.models import RoutingDecision, TaskType
from app.ai.ollama import OllamaProvider, OllamaUnavailableError
from app.ai.provider import AIProvider, AIResponse, ChatMessage
from app.ai.router import ModelRouter, model_router
from app.core.config import settings
from app.core.logging_config import logger


class UnifiedAIProvider(AIProvider):
    """Unified multi-model AI provider delegating to Ollama, Grok, and Hugging Face."""

    provider_name: str = "unified"

    def __init__(
        self,
        router: Optional[ModelRouter] = None,
        ollama_provider: Optional[OllamaProvider] = None,
        grok_provider: Optional[GrokProvider] = None,
        hf_remote_provider: Optional[HuggingFaceRemoteProvider] = None,
        hf_local_provider: Optional[HuggingFaceLocalProvider] = None,
    ) -> None:
        self.router = router or model_router
        self.ollama = ollama_provider or OllamaProvider()
        self.grok = grok_provider or GrokProvider()
        self.hf_remote = hf_remote_provider or HuggingFaceRemoteProvider()
        self.hf_local = hf_local_provider or HuggingFaceLocalProvider()

        self._providers: Dict[str, AIProvider] = {
            "ollama": self.ollama,
            "grok": self.grok,
            "huggingface_remote": self.hf_remote,
            "huggingface_local": self.hf_local,
        }

    def get_provider(self, name: str) -> Optional[AIProvider]:
        """Fetch provider implementation by lowercase name."""
        return self._providers.get(name.lower())

    async def check_health(self) -> Dict[str, Any]:
        """Aggregate health telemetry from all providers without exposing secrets."""
        ollama_health = await self.ollama.check_health()
        grok_health = await self.grok.check_health()
        hf_remote_health = await self.hf_remote.check_health()
        hf_local_health = await self.hf_local.check_health()

        # Augment with router health summary
        router_health = self.router.health_tracker.get_health_summary()

        return {
            "provider": self.provider_name,
            "active_default": "ollama",
            "active_default_model": self.ollama.model,
            "providers": {
                "ollama": ollama_health,
                "grok": grok_health,
                "huggingface_remote": hf_remote_health,
                "huggingface_local": hf_local_health,
            },
            "circuit_breakers": router_health,
        }

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        task_type: TaskType = TaskType.GENERAL_REASONING,
        allow_remote: bool = False,
        preferred_model_id: Optional[str] = None,
        **kwargs: Any,
    ) -> AIResponse:
        """Route prompt deterministically and execute with automated fallback chain."""
        # 1. Determine model and provider routing
        decision: RoutingDecision = await self.router.route(
            task_type=task_type,
            prompt=prompt,
            allow_remote=allow_remote,
            preferred_model_id=preferred_model_id,
        )

        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.MODEL_ROUTE_SELECTED,
                status=ActionStatus.COMPLETED,
                title=f"Route Selected: {decision.selected_model}",
                description=f"Assigned {decision.provider} for {decision.task_type.value}: {decision.reason}",
                safe_metadata={
                    "selected_model": decision.selected_model,
                    "provider": decision.provider,
                    "task_type": decision.task_type.value,
                    "reason_code": decision.reason_code,
                    "local": decision.local_or_remote == "local",
                    "reason": decision.reason,
                },
            )
        )

        # 2. Select matching provider
        provider_key = decision.provider.lower()
        if "huggingface" in provider_key:
            provider_key = "huggingface_remote" if decision.local_or_remote == "remote" else "huggingface_local"

        target_provider = self._providers.get(provider_key, self.ollama)

        # 3. Attempt execution with chosen provider
        from app.runtime.concurrency import concurrency_controller
        from app.runtime.performance import runtime_performance_service

        t0 = time.monotonic()
        try:
            async with concurrency_controller.limit_llm():
                async with runtime_performance_service.profile(
                    name=f"llm_{decision.selected_model}",
                    category="llm",
                    provider=decision.provider,
                    model=decision.selected_model,
                ):
                    resp = await target_provider.generate(
                        prompt=prompt,
                        system_prompt=system_prompt,
                        messages=messages,
                        **kwargs,
                    )
                    latency_ms = (time.monotonic() - t0) * 1000
                    self.router.health_tracker.record_success(decision.provider, latency_ms)
                    return resp
        except (
            GrokUnavailableError,
            HuggingFaceRemoteUnavailableError,
            HuggingFaceLocalUnavailableError,
            OllamaUnavailableError,
            ProviderUnavailableError,
            ProviderTimeoutError,
            RateLimitError,
        ) as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            self.router.health_tracker.record_failure(decision.provider, exc, latency_ms)
            logger.warning(
                f"Provider '{decision.provider}' failed ({exc}). Initiating deterministic fallback chain."
            )

            # 4. Fallback Execution Chain
            # Check if fallback is permitted by router config
            if (
                self.router.config.fallback_enabled
                and self.router.config.max_fallback_depth >= 1
                and decision.fallback_eligible
                and target_provider != self.ollama
            ):
                logger.info(f"Falling back from '{decision.provider}' to default local Ollama Qwen.")
                await action_bus.publish(
                    ActionEvent(
                        action_type=ActionType.MODEL_ROUTE_SELECTED,
                        status=ActionStatus.PROGRESS,
                        title="Model Provider Fallback",
                        description=f"Provider {decision.provider} unavailable. Falling back to Ollama {self.ollama.model}",
                        safe_metadata={"fallback_provider": "ollama", "fallback_model": self.ollama.model},
                    )
                )
                t_fb = time.monotonic()
                try:
                    async with concurrency_controller.limit_llm():
                        async with runtime_performance_service.profile(
                            name=f"llm_fallback_{self.ollama.model}",
                            category="llm",
                            provider="ollama",
                            model=self.ollama.model,
                        ):
                            fb_resp = await self.ollama.generate(
                                prompt=prompt,
                                system_prompt=system_prompt,
                                messages=messages,
                                **kwargs,
                            )
                            fb_latency = (time.monotonic() - t_fb) * 1000
                            self.router.health_tracker.record_success("OLLAMA", fb_latency)
                            return fb_resp
                except Exception as fb_exc:
                    fb_latency = (time.monotonic() - t_fb) * 1000
                    self.router.health_tracker.record_failure("OLLAMA", fb_exc, fb_latency)
                    raise

            # If Ollama itself was the target or fallback disabled, propagate
            raise

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        task_type: TaskType = TaskType.GENERAL_REASONING,
        allow_remote: bool = False,
        preferred_model_id: Optional[str] = None,
        **kwargs: Any,
    ) -> AsyncIterator[AIStreamChunk]:
        """Stream response chunks incrementally via authoritative router."""
        req = AIRequest.from_prompt(
            prompt=prompt,
            system_prompt=system_prompt,
            task_type=task_type,
            model_id=preferred_model_id,
            stream=True,
            **kwargs,
        )
        if messages:
            req.messages = messages

        async for chunk in self.router.stream(
            request=req,
            preferred_model_id=preferred_model_id,
            allow_remote=allow_remote,
        ):
            yield chunk


# Global default unified provider singleton
unified_ai_provider = UnifiedAIProvider()
