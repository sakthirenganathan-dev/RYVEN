"""RYVEN 3.0 — Hugging Face Remote AI Provider.

Connects to Hugging Face Inference API / router.
Strictly opt-in, non-default, and protected by credentials and leak-prevention policy.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional
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
    api_key: Optional[str] = None
    base_url: str = "https://api-inference.huggingface.co/v1"
    model: str = "Qwen/Qwen2.5-Coder-32B-Instruct"
    timeout_seconds: float = 60.0
    max_retries: int = 2
    supports_streaming: bool = False

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        max_retries: Optional[int] = None,
        supports_streaming: Optional[bool] = None,
        security_policy: Optional[ModelSecurityPolicy] = None,
    ) -> None:
        raw_key = api_key if api_key is not None else settings.hf_api_key
        self.api_key = raw_key.strip() if (raw_key and raw_key.strip()) else None
        self.base_url = (base_url or settings.hf_base_url).rstrip("/")
        self.model = model or settings.hf_model
        self.timeout_seconds = float(timeout_seconds if timeout_seconds is not None else settings.hf_timeout_seconds)
        self.max_retries = max_retries if max_retries is not None else 2
        self.supports_streaming = supports_streaming if supports_streaming is not None else True
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
        probe_timeout = min(self.timeout_seconds, 5.0)
        try:
            async with httpx.AsyncClient(timeout=probe_timeout) as client:
                res = await client.get(f"{self.base_url}/models/{self.model}", headers=headers)
                health["latency_ms"] = round((time.monotonic() - t0) * 1000, 2)
                if res.status_code in (200, 404):  # 404 router endpoint might just be chat-only
                    health["authenticated"] = True
                    health["available"] = True
                elif res.status_code in (401, 403):
                    health["error"] = "Authentication failed (Invalid HF_API_KEY)"
                elif res.status_code == 429:
                    health["authenticated"] = True
                    health["error"] = "Rate limit exceeded (HTTP 429)"
                else:
                    health["available"] = True
        except httpx.TimeoutException:
            health["error"] = f"Health check probe timed out after {probe_timeout}s"
        except Exception as exc:
            safe_err = str(exc)
            if self.api_key and self.api_key in safe_err:
                safe_err = safe_err.replace(self.api_key, "[REDACTED]")
            health["error"] = f"Connection failed: {safe_err}"

        return health

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        **kwargs: Any,
    ) -> AIResponse:
        """Dispatch completion request to remote Hugging Face endpoint with retry handling."""
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

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    res = await client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)

                    # Non-retryable auth / permission failure
                    if res.status_code in (401, 403):
                        raise HuggingFaceRemoteUnavailableError(
                            f"Authentication failed with Hugging Face (HTTP {res.status_code})."
                        )

                    # Rate limited: retry if attempts left
                    if res.status_code == 429:
                        if attempt < self.max_retries:
                            retry_after_hdr = res.headers.get("Retry-After")
                            backoff = (
                                min(float(retry_after_hdr), 5.0)
                                if (retry_after_hdr and retry_after_hdr.isdigit())
                                else 0.5 * (2 ** attempt)
                            )
                            logger.warning(
                                f"Hugging Face rate limited (HTTP 429). Retrying attempt {attempt + 1}/{self.max_retries} in {backoff:.2f}s"
                            )
                            await asyncio.sleep(backoff)
                            continue
                        raise HuggingFaceRemoteUnavailableError("Hugging Face rate limit exceeded (HTTP 429).")

                    # Transient server errors: retry if attempts left
                    if res.status_code in (502, 503, 504):
                        if attempt < self.max_retries:
                            backoff = 0.5 * (2 ** attempt)
                            logger.warning(
                                f"Hugging Face server error (HTTP {res.status_code}). Retrying attempt {attempt + 1}/{self.max_retries} in {backoff:.2f}s"
                            )
                            await asyncio.sleep(backoff)
                            continue
                        raise HuggingFaceRemoteUnavailableError(
                            f"Hugging Face server error (HTTP {res.status_code}): {res.text[:200]}"
                        )

                    if res.status_code != 200:
                        raise HuggingFaceRemoteUnavailableError(
                            f"Hugging Face returned error HTTP {res.status_code}: {res.text[:200]}"
                        )

                    data = res.json()
                    choices = data.get("choices", [])
                    if not choices:
                        raise HuggingFaceRemoteUnavailableError("Hugging Face returned an empty choices list.")

                    content = choices[0].get("message", {}).get("content", "")
                    if content is None:
                        content = ""
                    content = content.strip()
                    if not content:
                        raise HuggingFaceRemoteUnavailableError("Hugging Face returned empty response content.")

                    raw_usage = data.get("usage", {})
                    usage_dict: Dict[str, Any] = {}
                    if isinstance(raw_usage, dict):
                        usage_dict = {
                            "prompt_tokens": raw_usage.get("prompt_tokens"),
                            "completion_tokens": raw_usage.get("completion_tokens"),
                            "total_tokens": raw_usage.get("total_tokens"),
                        }

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
                        metadata={"usage": usage_dict},
                    )

            except asyncio.CancelledError:
                logger.info("Hugging Face request cancelled by caller")
                raise
            except (HuggingFaceRemoteUnavailableError, ModelSecurityViolationError):
                raise
            except httpx.TimeoutException as exc:
                last_error = exc
                raise HuggingFaceRemoteUnavailableError(
                    f"Hugging Face request timed out after {self.timeout_seconds}s: {exc}"
                ) from exc
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_error = exc
                if attempt < self.max_retries:
                    backoff = 0.5 * (2 ** attempt)
                    logger.warning(
                        f"Hugging Face connection error: {exc}. Retrying attempt {attempt + 1}/{self.max_retries} in {backoff:.2f}s"
                    )
                    await asyncio.sleep(backoff)
                    continue
                raise HuggingFaceRemoteUnavailableError(f"Hugging Face network connection failed: {exc}") from exc
            except Exception as exc:
                last_error = exc
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
                raise HuggingFaceRemoteUnavailableError(f"Failed to communicate with Hugging Face: {str(exc)}") from exc

        if last_error:
            raise HuggingFaceRemoteUnavailableError(f"Failed to communicate with Hugging Face after retries: {last_error}")
        raise HuggingFaceRemoteUnavailableError("Failed to communicate with Hugging Face: Unknown error.")

    async def stream(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        **kwargs: Any,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Stream chat completion chunks from Hugging Face via SSE."""
        # 1. Verification of credentials
        if not self.api_key or not self.api_key.strip():
            raise HuggingFaceRemoteUnavailableError(
                "Hugging Face API key is not configured. Set HF_API_KEY in environment."
            )

        # 2. Security validation
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
                    title="Blocked Remote Hugging Face Streaming",
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
            "stream": True,
        }
        if kwargs:
            payload.update(kwargs)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.status_code in (401, 403):
                        raise HuggingFaceRemoteUnavailableError(
                            f"Authentication failed with Hugging Face (HTTP {response.status_code})."
                        )
                    if response.status_code == 429:
                        raise HuggingFaceRemoteUnavailableError("Hugging Face rate limit exceeded (HTTP 429).")
                    if response.status_code in (502, 503, 504):
                        raise HuggingFaceRemoteUnavailableError(
                            f"Hugging Face server error (HTTP {response.status_code})."
                        )
                    if response.status_code != 200:
                        body_sample = await response.aread()
                        sample_str = body_sample.decode("utf-8", errors="replace")[:200]
                        raise HuggingFaceRemoteUnavailableError(
                            f"Hugging Face returned error HTTP {response.status_code}: {sample_str}"
                        )

                    collected_usage: Optional[Dict[str, Any]] = None
                    done_received = False

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        if line.startswith("data:"):
                            data_str = line[5:].strip()
                            if data_str == "[DONE]":
                                done_received = True
                                yield {
                                    "done": True,
                                    "done_reason": "stop",
                                    "usage": collected_usage,
                                    "model": self.model,
                                }
                                break

                            try:
                                chunk_data = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            if "usage" in chunk_data and chunk_data["usage"]:
                                collected_usage = chunk_data["usage"]

                            choices = chunk_data.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                delta_content = delta.get("content", "")
                                finish_reason = choices[0].get("finish_reason")
                                if delta_content:
                                    yield {
                                        "message": {"content": delta_content},
                                        "model": chunk_data.get("model", self.model),
                                        "done": False,
                                    }
                                if finish_reason and not done_received:
                                    done_received = True
                                    yield {
                                        "done": True,
                                        "done_reason": finish_reason,
                                        "usage": collected_usage,
                                        "model": chunk_data.get("model", self.model),
                                    }
                                    break

                    if not done_received:
                        yield {
                            "done": True,
                            "done_reason": "stop",
                            "usage": collected_usage,
                            "model": self.model,
                        }

        except asyncio.CancelledError:
            logger.info("Hugging Face streaming cancelled by consumer")
            raise
        except httpx.TimeoutException as exc:
            raise HuggingFaceRemoteUnavailableError(f"Hugging Face streaming timed out: {exc}") from exc
        except Exception as exc:
            if isinstance(exc, (HuggingFaceRemoteUnavailableError, asyncio.CancelledError)):
                raise
            raise HuggingFaceRemoteUnavailableError(f"Hugging Face streaming error: {exc}") from exc
