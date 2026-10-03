"""RYVEN 3.0 — Hugging Face Remote AI Provider.

Connects to Hugging Face Inference API / router.
Strictly opt-in, non-default, and protected by credentials and leak-prevention policy.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
import httpx

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.models import ModelProfile, ModelProvider, TaskType
from app.ai.provider import AIProvider, AIResponse, ChatMessage
from app.ai.security import ModelSecurityPolicy, ModelSecurityViolationError, model_security_policy
from app.core.config import settings
from app.core.logging_config import logger


class HuggingFaceRemoteUnavailableError(RuntimeError):
    """Raised when Hugging Face Remote API is unconfigured, disabled, unreachable, or errors."""

    pass


class HuggingFaceRemoteProvider(AIProvider):
    """Remote Hugging Face Inference provider communicating via HTTP REST."""

    provider_name: str = "huggingface_remote"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        security_policy: Optional[ModelSecurityPolicy] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.hf_api_key
        self.base_url = (base_url or settings.hf_base_url).rstrip("/")
        self.model = model or settings.hf_model
        self.timeout_seconds = timeout_seconds or settings.hf_timeout_seconds
        self.security_policy = security_policy or model_security_policy

    async def check_health(self) -> Dict[str, Any]:
        """Verify Hugging Face remote configuration without leaking tokens."""
        is_configured = bool(self.api_key and self.api_key.strip())
        is_allowed = self.security_policy.allow_remote_inference

        health: Dict[str, Any] = {
            "provider": self.provider_name,
            "configured": is_configured,
            "authenticated": False,
            "available": False,
            "local": False,
            "model": self.model,
            "remote_allowed": is_allowed,
            "latency_ms": None,
            "error": None,
        }

        if not is_configured:
            health["error"] = "HF_API_KEY is not configured"
            return health

        if not is_allowed:
            health["error"] = "Remote inference is disabled by default policy (ALLOW_REMOTE_AI_INFERENCE=false)"
            return health

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 5.0)) as client:
                res = await client.get(f"{self.base_url}/models/{self.model}", headers=headers)
                health["latency_ms"] = round((time.monotonic() - t0) * 1000, 2)
                if res.status_code in (200, 404):  # 404 router endpoint might just be chat-only
                    health["authenticated"] = True
                    health["available"] = True
                elif res.status_code in (401, 403):
                    health["error"] = "Authentication failed (Invalid HF_API_KEY)"
                else:
                    health["available"] = True
        except Exception as exc:
            health["error"] = f"Connection failed: {str(exc)}"

        return health

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        **kwargs: Any,
    ) -> AIResponse:
        """Dispatch completion request to remote Hugging Face endpoint."""
        # 1. Verification of credentials
        if not self.api_key or not self.api_key.strip():
            raise HuggingFaceRemoteUnavailableError(
                "Hugging Face API key is not configured. Set HF_API_KEY in environment."
            )

        # 2. Security validation: remote inference must be enabled and free of secrets
        profile = ModelProfile(
            id=f"hf-remote-{self.model}",
            provider=ModelProvider.HUGGINGFACE_REMOTE,
            task_types=[TaskType.GENERAL_REASONING, TaskType.CODE],
            local_or_remote="remote",
            model_name=self.model,
            memory_estimate_gb=0.0,
            sensitive_data_allowed=False,
        )

        try:
            await self.security_policy.validate_invocation(profile, prompt)
        except ModelSecurityViolationError as sec_err:
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.REMOTE_INFERENCE_BLOCKED,
                    status=ActionStatus.FAILED,
                    title="Blocked Remote Hugging Face Transmission",
                    description=str(sec_err),
                    error_code="SECURITY_POLICY_VIOLATION",
                    safe_metadata={"provider": self.provider_name, "model": self.model},
                )
            )
            raise HuggingFaceRemoteUnavailableError(str(sec_err)) from sec_err

        # 3. Format OpenAI-compatible messages payload
        payload_messages: List[Dict[str, str]] = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})
        if messages:
            for msg in messages:
                if msg.role == "system":
                    continue
                payload_messages.append({"role": msg.role, "content": msg.content})

        if not payload_messages or payload_messages[-1].get("role") != "user" or payload_messages[-1].get("content") != prompt:
            payload_messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "stream": False,
        }
        if kwargs:
            payload.update(kwargs)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.MODEL_PROVIDER_STARTED,
                status=ActionStatus.STARTED,
                title=f"Hugging Face Remote Request: {self.model}",
                description="Dispatching chat completion to Hugging Face Remote API",
                safe_metadata={"provider": self.provider_name, "model": self.model, "local": False},
            )
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                res = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                if res.status_code == 401:
                    raise HuggingFaceRemoteUnavailableError("Authentication failed with Hugging Face (HTTP 401).")
                if res.status_code != 200:
                    raise HuggingFaceRemoteUnavailableError(
                        f"Hugging Face returned error HTTP {res.status_code}: {res.text[:200]}"
                    )

                data = res.json()
                choices = data.get("choices", [])
                if not choices:
                    raise HuggingFaceRemoteUnavailableError("Hugging Face returned an empty choices list.")

                content = choices[0].get("message", {}).get("content", "").strip()
                if not content:
                    raise HuggingFaceRemoteUnavailableError("Hugging Face returned empty response content.")

                await action_bus.publish(
                    ActionEvent(
                        action_type=ActionType.MODEL_PROVIDER_COMPLETED,
                        status=ActionStatus.COMPLETED,
                        title="Hugging Face Remote Response Received",
                        description=f"Received completion from {self.model}",
                        safe_metadata={"provider": self.provider_name, "model": self.model},
                    )
                )

                return AIResponse(
                    content=content,
                    model=data.get("model", self.model),
                    provider=self.provider_name,
                    metadata={"usage": data.get("usage", {})},
                )
        except Exception as exc:
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.MODEL_PROVIDER_FAILED,
                    status=ActionStatus.FAILED,
                    title="Hugging Face Remote Request Failed",
                    description=f"Error executing HF completion: {str(exc)[:100]}",
                    error_code="HF_REMOTE_ERROR",
                    safe_metadata={"provider": self.provider_name, "error": str(exc)[:200]},
                )
            )
            if isinstance(exc, HuggingFaceRemoteUnavailableError):
                raise
            raise HuggingFaceRemoteUnavailableError(f"Failed to communicate with Hugging Face: {str(exc)}") from exc
