"""RYVEN 3.0 — Grok API AI Provider.

Connects to the xAI Grok API using OpenAI-compatible /chat/completions format.
Strictly opt-in, non-default, and protected by credentials and security checks.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
import httpx

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.provider import AIProvider, AIResponse, ChatMessage
from app.ai.security import ModelSecurityPolicy, model_security_policy
from app.core.config import settings
from app.core.logging_config import logger


class GrokUnavailableError(RuntimeError):
    """Raised when Grok API is unconfigured, unreachable, or encounters runtime errors."""

    pass


class GrokProvider(AIProvider):
    """Grok API provider communicating via OpenAI-compatible REST endpoints."""

    provider_name: str = "grok"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        security_policy: Optional[ModelSecurityPolicy] = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.grok_api_key
        self.base_url = (base_url or settings.grok_base_url).rstrip("/")
        self.model = model or settings.grok_model
        self.timeout_seconds = timeout_seconds or settings.grok_timeout_seconds
        self.security_policy = security_policy or model_security_policy

    async def check_health(self) -> Dict[str, Any]:
        """Verify Grok API configuration and connectivity without leaking credentials."""
        health: Dict[str, Any] = {
            "provider": self.provider_name,
            "configured": bool(self.api_key and self.api_key.strip()),
            "authenticated": False,
            "available": False,
            "local": False,
            "model": self.model,
            "latency_ms": None,
            "error": None,
        }

        if not health["configured"]:
            health["error"] = "GROK_API_KEY is not configured"
            return health

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=min(self.timeout_seconds, 5.0)) as client:
                res = await client.get(f"{self.base_url}/models", headers=headers)
                latency = round((time.monotonic() - t0) * 1000, 2)
                health["latency_ms"] = latency
                if res.status_code == 200:
                    health["authenticated"] = True
                    health["available"] = True
                elif res.status_code in (401, 403):
                    health["error"] = "Authentication failed (Invalid GROK_API_KEY)"
                else:
                    health["error"] = f"Grok API returned HTTP {res.status_code}"
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
        """Dispatch completion request to Grok API."""
        if not self.api_key or not self.api_key.strip():
            raise GrokUnavailableError("Grok API key is not configured. Set GROK_API_KEY in environment.")

        # Remote security validation: Verify remote inference is permitted and no secrets are in prompt
        is_sensitive, reason = self.security_policy.scan_for_sensitive_data(prompt)
        if is_sensitive:
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.REMOTE_INFERENCE_BLOCKED,
                    status=ActionStatus.FAILED,
                    title="Blocked Remote Grok Transmission",
                    description=f"Prompt contains sensitive pattern. Blocked: {reason}",
                    error_code="SECURITY_POLICY_VIOLATION",
                    safe_metadata={"provider": self.provider_name, "reason": reason},
                )
            )
            raise GrokUnavailableError(f"Security policy blocked transmission to Grok: {reason}")

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
                title=f"Grok Request: {self.model}",
                description="Dispatching chat completion to Grok API",
                safe_metadata={"provider": self.provider_name, "model": self.model, "local": False},
            )
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                res = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
                if res.status_code == 401:
                    raise GrokUnavailableError("Authentication failed with Grok API (HTTP 401).")
                if res.status_code != 200:
                    raise GrokUnavailableError(f"Grok API returned error HTTP {res.status_code}: {res.text[:200]}")

                data = res.json()
                choices = data.get("choices", [])
                if not choices:
                    raise GrokUnavailableError("Grok returned an empty choices list.")

                content = choices[0].get("message", {}).get("content", "").strip()
                if not content:
                    raise GrokUnavailableError("Grok returned empty response content.")

                await action_bus.publish(
                    ActionEvent(
                        action_type=ActionType.MODEL_PROVIDER_COMPLETED,
                        status=ActionStatus.COMPLETED,
                        title="Grok Response Received",
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
                    title="Grok Request Failed",
                    description=f"Error executing Grok completion: {str(exc)[:100]}",
                    error_code="GROK_API_ERROR",
                    safe_metadata={"provider": self.provider_name, "error": str(exc)[:200]},
                )
            )
            if isinstance(exc, GrokUnavailableError):
                raise
            raise GrokUnavailableError(f"Failed to communicate with Grok API: {str(exc)}") from exc
