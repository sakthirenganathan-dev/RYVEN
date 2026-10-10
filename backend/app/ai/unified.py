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
    ModelProvider,
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

        # Synchronize custom transports with router adapters if injected
        if ollama_provider is not None:
            from app.ai.adapters import OllamaAdapter
            self.router.register_adapter(ModelProvider.OLLAMA, OllamaAdapter(self.ollama))
        if grok_provider is not None:
            from app.ai.adapters import GrokAdapter
            self.router.register_adapter(ModelProvider.GROK, GrokAdapter(self.grok))
        if hf_remote_provider is not None or hf_local_provider is not None:
            from app.ai.adapters import HuggingFaceAdapter
            self.router.register_adapter(
                ModelProvider.HUGGINGFACE_REMOTE,
                HuggingFaceAdapter(remote_provider=self.hf_remote, local_provider=self.hf_local),
            )
            self.router.register_adapter(
                ModelProvider.HUGGINGFACE_LOCAL,
                HuggingFaceAdapter(remote_provider=self.hf_remote, local_provider=self.hf_local),
            )

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

        ollama_online = bool(ollama_health.get("online", False))
        ollama_model_avail = bool(ollama_health.get("model_available", False))

        return {
            "provider": self.provider_name,
            "active_default": "ollama",
            "active_default_model": getattr(self.ollama, "model", "qwen2.5:7b"),
            "online": ollama_online,
            "model_available": ollama_model_avail,
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
        """Route prompt deterministically and execute via authoritative ModelRouter."""
        req = AIRequest.from_prompt(
            prompt=prompt,
            system_prompt=system_prompt,
            task_type=task_type,
            model_id=preferred_model_id,
            images=kwargs.pop("images", None),
            **kwargs,
        )
        if messages:
            req.messages = messages

        return await self.router.execute(
            request=req,
            preferred_model_id=preferred_model_id,
            allow_remote=allow_remote,
        )

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
