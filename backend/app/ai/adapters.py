"""RYVEN 3.0 — AI Provider Adapters.

Implements thin, provider-neutral adapters wrapping existing low-level AI engines
(Ollama, Grok, Hugging Face) as specified in M17.10 Phase 1.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional
import httpx

from app.ai.contracts import (
    AIProviderError,
    AIRequest,
    AIResponse,
    AIStreamChunk,
    AIUsage,
    AuthenticationError,
    ContextOverflowError,
    ProviderAdapter,
    ProviderInvalidRequestError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    SecurityViolationError,
    StreamChunkSanitizer,
    StreamEventType,
    sanitize_dict,
)
from app.ai.grok import GrokProvider, GrokUnavailableError
from app.ai.hf_local import HuggingFaceLocalProvider, HuggingFaceLocalUnavailableError
from app.ai.hf_remote import HuggingFaceRemoteProvider, HuggingFaceRemoteUnavailableError
from app.ai.models import ModelProvider, TaskType
from app.ai.ollama import OllamaProvider, OllamaUnavailableError
from app.ai.security import ModelSecurityViolationError
from app.core.logging_config import logger


# ============================================================================
# OLLAMA ADAPTER
# ============================================================================

class OllamaAdapter(ProviderAdapter):
    """Adapter wrapping OllamaProvider into normalized provider-neutral contract."""

    def __init__(self, provider: Optional[OllamaProvider] = None) -> None:
        self.provider = provider or OllamaProvider()

    @property
    def provider_id(self) -> ModelProvider:
        return ModelProvider.OLLAMA

    async def health_check(self) -> Dict[str, Any]:
        """Perform health probe on Ollama daemon."""
        return await self.provider.check_health()

    async def is_available(self) -> bool:
        """Verify Ollama service is reachable and default model is available."""
        try:
            health = await self.health_check()
            return bool(health.get("online"))
        except Exception:
            return False

    async def generate(self, request: AIRequest) -> AIResponse:
        """Execute chat generation via Ollama with normalized request/response."""
        prompt = request.get_prompt_text()
        if not prompt and request.messages:
            prompt = request.messages[-1].content

        orig_model = self.provider.model
        if request.model_id and request.model_id.strip():
            self.provider.model = request.model_id.strip()

        kwargs: Dict[str, Any] = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["num_predict"] = request.max_tokens

        t0 = time.monotonic()
        try:
            legacy_resp = await self.provider.generate(
                prompt=prompt,
                system_prompt=request.system_prompt,
                messages=request.messages,
                images=request.images,
                **kwargs,
            )
            latency_ms = round((time.monotonic() - t0) * 1000, 2)

            # Extract token counts safely without fabrication
            meta = legacy_resp.metadata or {}
            eval_count = meta.get("eval_count")
            prompt_eval_count = meta.get("prompt_eval_count")
            usage = None
            if eval_count is not None or prompt_eval_count is not None:
                usage = AIUsage.from_counts(
                    prompt_tokens=prompt_eval_count,
                    completion_tokens=eval_count,
                )

            return AIResponse(
                content=legacy_resp.content,
                model_id=legacy_resp.model,
                provider=ModelProvider.OLLAMA,
                finish_reason="stop",
                usage=usage,
                latency_ms=latency_ms,
                metadata=sanitize_dict(meta),
            )
        except OllamaUnavailableError as exc:
            err_msg = str(exc)
            if "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
                raise ProviderTimeoutError(
                    message=err_msg,
                    provider=ModelProvider.OLLAMA,
                    model_id=self.provider.model,
                ) from exc
            raise ProviderUnavailableError(
                message=err_msg,
                provider=ModelProvider.OLLAMA,
                model_id=self.provider.model,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                message=f"Ollama request timed out: {exc}",
                provider=ModelProvider.OLLAMA,
                model_id=self.provider.model,
            ) from exc
        except Exception as exc:
            raise ProviderUnavailableError(
                message=f"Ollama execution failed: {exc}",
                provider=ModelProvider.OLLAMA,
                model_id=self.provider.model,
            ) from exc
        finally:
            self.provider.model = orig_model

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        """Stream response chunks incrementally from Ollama with bounded chunk sanitization."""
        prompt = request.get_prompt_text()
        if not prompt and request.messages:
            prompt = request.messages[-1].content

        orig_model = self.provider.model
        if request.model_id and request.model_id.strip():
            self.provider.model = request.model_id.strip()

        kwargs: Dict[str, Any] = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["num_predict"] = request.max_tokens

        t0 = time.monotonic()
        sanitizer = StreamChunkSanitizer()

        try:
            # Emit stream-start event
            yield AIStreamChunk(
                event_type=StreamEventType.START,
                model_id=self.provider.model,
                provider=ModelProvider.OLLAMA,
                metadata={"started_at": t0},
            )

            done_emitted = False
            last_chunk: Dict[str, Any] = {}

            async for raw_chunk in self.provider.stream(
                prompt=prompt,
                system_prompt=request.system_prompt,
                messages=request.messages,
                images=request.images,
                **kwargs,
            ):
                last_chunk = raw_chunk
                msg = raw_chunk.get("message", {})
                content = msg.get("content", "") if isinstance(msg, dict) else ""

                if content:
                    clean_delta = sanitizer.feed(content)
                    if clean_delta:
                        yield AIStreamChunk(
                            event_type=StreamEventType.DELTA,
                            delta=clean_delta,
                            model_id=raw_chunk.get("model", self.provider.model),
                            provider=ModelProvider.OLLAMA,
                        )

                if raw_chunk.get("done") is True:
                    # Flush sanitizer remaining tail
                    flushed = sanitizer.flush()
                    if flushed:
                        yield AIStreamChunk(
                            event_type=StreamEventType.DELTA,
                            delta=flushed,
                            model_id=raw_chunk.get("model", self.provider.model),
                            provider=ModelProvider.OLLAMA,
                        )

                    eval_count = raw_chunk.get("eval_count")
                    prompt_eval_count = raw_chunk.get("prompt_eval_count")
                    usage = None
                    if eval_count is not None or prompt_eval_count is not None:
                        usage = AIUsage.from_counts(
                            prompt_tokens=prompt_eval_count,
                            completion_tokens=eval_count,
                        )

                    latency_ms = round((time.monotonic() - t0) * 1000, 2)
                    yield AIStreamChunk(
                        event_type=StreamEventType.DONE,
                        model_id=raw_chunk.get("model", self.provider.model),
                        provider=ModelProvider.OLLAMA,
                        finish_reason=raw_chunk.get("done_reason", "stop"),
                        usage=usage,
                        latency_ms=latency_ms,
                        metadata=sanitize_dict(raw_chunk),
                    )
                    done_emitted = True
                    break

            if not done_emitted:
                flushed = sanitizer.flush()
                if flushed:
                    yield AIStreamChunk(
                        event_type=StreamEventType.DELTA,
                        delta=flushed,
                        model_id=self.provider.model,
                        provider=ModelProvider.OLLAMA,
                    )
                latency_ms = round((time.monotonic() - t0) * 1000, 2)
                yield AIStreamChunk(
                    event_type=StreamEventType.DONE,
                    model_id=self.provider.model,
                    provider=ModelProvider.OLLAMA,
                    finish_reason="stop",
                    latency_ms=latency_ms,
                    metadata=sanitize_dict(last_chunk),
                )

        except asyncio.CancelledError:
            logger.info("Ollama streaming cancelled by consumer")
            raise
        except OllamaUnavailableError as exc:
            err_msg = str(exc)
            mapped_err = (
                ProviderTimeoutError(err_msg, provider=ModelProvider.OLLAMA, model_id=self.provider.model)
                if "timeout" in err_msg.lower() or "timed out" in err_msg.lower()
                else ProviderUnavailableError(err_msg, provider=ModelProvider.OLLAMA, model_id=self.provider.model)
            )
            yield AIStreamChunk(
                event_type=StreamEventType.ERROR,
                error=mapped_err,
                model_id=self.provider.model,
                provider=ModelProvider.OLLAMA,
                metadata={"error": str(mapped_err)},
            )
            raise mapped_err from exc
        except httpx.TimeoutException as exc:
            timeout_err = ProviderTimeoutError(
                f"Ollama request timed out: {exc}",
                provider=ModelProvider.OLLAMA,
                model_id=self.provider.model,
            )
            yield AIStreamChunk(
                event_type=StreamEventType.ERROR,
                error=timeout_err,
                model_id=self.provider.model,
                provider=ModelProvider.OLLAMA,
                metadata={"error": str(timeout_err)},
            )
            raise timeout_err from exc
        except Exception as exc:
            prov_err = ProviderUnavailableError(
                f"Ollama streaming failed: {exc}",
                provider=ModelProvider.OLLAMA,
                model_id=self.provider.model,
            )
            yield AIStreamChunk(
                event_type=StreamEventType.ERROR,
                error=prov_err,
                model_id=self.provider.model,
                provider=ModelProvider.OLLAMA,
                metadata={"error": str(prov_err)},
            )
            raise prov_err from exc
        finally:
            self.provider.model = orig_model


# ============================================================================
# GROK ADAPTER
# ============================================================================

class GrokAdapter(ProviderAdapter):
    """Adapter wrapping xAI GrokProvider into normalized provider-neutral contract."""

    def __init__(self, provider: Optional[GrokProvider] = None) -> None:
        self.provider = provider or GrokProvider()

    @property
    def provider_id(self) -> ModelProvider:
        return ModelProvider.GROK

    async def health_check(self) -> Dict[str, Any]:
        """Perform health probe on Grok API without credential exposure."""
        return await self.provider.check_health()

    async def is_available(self) -> bool:
        """Check whether Grok has an active API key and is reachable."""
        if not self.provider.api_key or not self.provider.api_key.strip():
            return False
        try:
            health = await self.health_check()
            return bool(health.get("available"))
        except Exception:
            return False

    async def generate(self, request: AIRequest) -> AIResponse:
        """Execute chat generation via Grok API with normalized request/response."""
        if not self.provider.api_key or not self.provider.api_key.strip():
            raise AuthenticationError(
                message="Grok API key is not configured. Set GROK_API_KEY in environment.",
                provider=ModelProvider.GROK,
                model_id=request.model_id or self.provider.model,
            )

        prompt = request.get_prompt_text()
        if not prompt and request.messages:
            prompt = request.messages[-1].content

        orig_model = self.provider.model
        if request.model_id and request.model_id.strip():
            self.provider.model = request.model_id.strip()

        kwargs: Dict[str, Any] = {}
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_tokens is not None:
            kwargs["max_tokens"] = request.max_tokens

        t0 = time.monotonic()
        try:
            legacy_resp = await self.provider.generate(
                prompt=prompt,
                system_prompt=request.system_prompt,
                messages=request.messages,
                **kwargs,
            )
            latency_ms = round((time.monotonic() - t0) * 1000, 2)

            meta = legacy_resp.metadata or {}
            usage_data = meta.get("usage", {})
            usage = None
            if usage_data:
                usage = AIUsage(
                    prompt_tokens=usage_data.get("prompt_tokens"),
                    completion_tokens=usage_data.get("completion_tokens"),
                    total_tokens=usage_data.get("total_tokens"),
                )

            return AIResponse(
                content=legacy_resp.content,
                model_id=legacy_resp.model,
                provider=ModelProvider.GROK,
                finish_reason="stop",
                usage=usage,
                latency_ms=latency_ms,
                metadata=sanitize_dict(meta),
            )
        except GrokUnavailableError as exc:
            err_msg = str(exc)
            err_lower = err_msg.lower()
            if "authentication" in err_lower or "401" in err_lower or "403" in err_lower:
                raise AuthenticationError(
                    message=err_msg,
                    provider=ModelProvider.GROK,
                    model_id=self.provider.model,
                ) from exc
            if "429" in err_lower or "rate limit" in err_lower:
                raise RateLimitError(
                    message=err_msg,
                    provider=ModelProvider.GROK,
                    model_id=self.provider.model,
                ) from exc
            if "timeout" in err_lower or "timed out" in err_lower:
                raise ProviderTimeoutError(
                    message=err_msg,
                    provider=ModelProvider.GROK,
                    model_id=self.provider.model,
                ) from exc
            if "security policy" in err_lower or "security_policy_violation" in err_lower:
                raise SecurityViolationError(
                    message=err_msg,
                    provider=ModelProvider.GROK,
                    model_id=self.provider.model,
                ) from exc
            if "context" in err_lower or "maximum context" in err_lower:
                raise ContextOverflowError(
                    message=err_msg,
                    provider=ModelProvider.GROK,
                    model_id=self.provider.model,
                ) from exc
            raise ProviderUnavailableError(
                message=err_msg,
                provider=ModelProvider.GROK,
                model_id=self.provider.model,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                message=f"Grok request timed out: {exc}",
                provider=ModelProvider.GROK,
                model_id=self.provider.model,
            ) from exc
        except Exception as exc:
            raise ProviderUnavailableError(
                message=f"Grok execution error: {exc}",
                provider=ModelProvider.GROK,
                model_id=self.provider.model,
            ) from exc
        finally:
            self.provider.model = orig_model

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        """Stream response chunks (Grok streaming is not supported by transport in Phase 2)."""
        err = ProviderUnavailableError(
            message="Streaming is not supported by Grok provider in this phase.",
            provider=ModelProvider.GROK,
            model_id=request.model_id or self.provider.model,
        )
        yield AIStreamChunk(
            event_type=StreamEventType.ERROR,
            error=err,
            model_id=request.model_id or self.provider.model,
            provider=ModelProvider.GROK,
            metadata={"error": str(err)},
        )
        raise err



# ============================================================================
# HUGGING FACE ADAPTER
# ============================================================================

class HuggingFaceAdapter(ProviderAdapter):
    """Adapter wrapping Hugging Face local and remote providers into normalized contract."""

    def __init__(
        self,
        local_provider: Optional[HuggingFaceLocalProvider] = None,
        remote_provider: Optional[HuggingFaceRemoteProvider] = None,
        prefer_local: bool = True,
    ) -> None:
        self.local_provider = local_provider or HuggingFaceLocalProvider()
        self.remote_provider = remote_provider or HuggingFaceRemoteProvider()
        self.prefer_local = prefer_local

    @property
    def provider_id(self) -> ModelProvider:
        return ModelProvider.HUGGINGFACE_LOCAL if self.prefer_local else ModelProvider.HUGGINGFACE_REMOTE

    async def health_check(self) -> Dict[str, Any]:
        """Aggregate health telemetry from local and remote HF providers."""
        local_health = await self.local_provider.check_health()
        remote_health = await self.remote_provider.check_health()
        return {
            "provider": "huggingface",
            "prefer_local": self.prefer_local,
            "local": local_health,
            "remote": remote_health,
            "available": local_health.get("available", False) or remote_health.get("available", False),
        }

    async def is_available(self) -> bool:
        """Check whether local or remote provider is currently usable."""
        try:
            if self.prefer_local:
                local_h = await self.local_provider.check_health()
                if local_h.get("available", False):
                    return True
            remote_h = await self.remote_provider.check_health()
            return bool(remote_h.get("available", False))
        except Exception:
            return False

    async def generate(self, request: AIRequest) -> AIResponse:
        """Execute inference using local or remote Hugging Face provider."""
        # Determine target provider based on model ID or preference
        model_id = request.model_id or ""
        use_remote = (
            "remote" in model_id.lower()
            or not self.prefer_local
        )

        prompt = request.get_prompt_text()
        if not prompt and request.messages:
            prompt = request.messages[-1].content

        t0 = time.monotonic()
        if use_remote:
            if not self.remote_provider.api_key or not self.remote_provider.api_key.strip():
                raise AuthenticationError(
                    message="Hugging Face API key is not configured for remote inference.",
                    provider=ModelProvider.HUGGINGFACE_REMOTE,
                    model_id=model_id or self.remote_provider.model,
                )

            orig_model = self.remote_provider.model
            if model_id and model_id.strip():
                self.remote_provider.model = model_id.strip()

            try:
                legacy_resp = await self.remote_provider.generate(
                    prompt=prompt,
                    system_prompt=request.system_prompt,
                    messages=request.messages,
                )
                latency_ms = round((time.monotonic() - t0) * 1000, 2)
                meta = legacy_resp.metadata or {}
                return AIResponse(
                    content=legacy_resp.content,
                    model_id=legacy_resp.model,
                    provider=ModelProvider.HUGGINGFACE_REMOTE,
                    finish_reason="stop",
                    latency_ms=latency_ms,
                    metadata=sanitize_dict(meta),
                )
            except HuggingFaceRemoteUnavailableError as exc:
                err_msg = str(exc)
                if "authentication" in err_msg.lower() or "401" in err_msg.lower():
                    raise AuthenticationError(
                        message=err_msg,
                        provider=ModelProvider.HUGGINGFACE_REMOTE,
                        model_id=self.remote_provider.model,
                    ) from exc
                if "security policy" in err_msg.lower() or "not allowed" in err_msg.lower():
                    raise SecurityViolationError(
                        message=err_msg,
                        provider=ModelProvider.HUGGINGFACE_REMOTE,
                        model_id=self.remote_provider.model,
                    ) from exc
                raise ProviderUnavailableError(
                    message=err_msg,
                    provider=ModelProvider.HUGGINGFACE_REMOTE,
                    model_id=self.remote_provider.model,
                ) from exc
            except ModelSecurityViolationError as exc:
                raise SecurityViolationError(
                    message=str(exc),
                    provider=ModelProvider.HUGGINGFACE_REMOTE,
                    model_id=self.remote_provider.model,
                ) from exc
            except Exception as exc:
                raise ProviderUnavailableError(
                    message=f"Hugging Face Remote execution failed: {exc}",
                    provider=ModelProvider.HUGGINGFACE_REMOTE,
                    model_id=self.remote_provider.model,
                ) from exc
            finally:
                self.remote_provider.model = orig_model
        else:
            # Local execution
            orig_model = self.local_provider.model_name
            if model_id and model_id.strip():
                self.local_provider.model_name = model_id.strip()

            try:
                legacy_resp = await self.local_provider.generate(
                    prompt=prompt,
                    system_prompt=request.system_prompt,
                    messages=request.messages,
                )
                latency_ms = round((time.monotonic() - t0) * 1000, 2)
                meta = legacy_resp.metadata or {}
                return AIResponse(
                    content=legacy_resp.content,
                    model_id=legacy_resp.model,
                    provider=ModelProvider.HUGGINGFACE_LOCAL,
                    finish_reason="stop",
                    latency_ms=latency_ms,
                    metadata=sanitize_dict(meta),
                )
            except HuggingFaceLocalUnavailableError as exc:
                raise ProviderUnavailableError(
                    message=str(exc),
                    provider=ModelProvider.HUGGINGFACE_LOCAL,
                    model_id=self.local_provider.model_name,
                ) from exc
            except Exception as exc:
                raise ProviderUnavailableError(
                    message=f"Hugging Face Local execution failed: {exc}",
                    provider=ModelProvider.HUGGINGFACE_LOCAL,
                    model_id=self.local_provider.model_name,
                ) from exc
            finally:
                self.local_provider.model_name = orig_model

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        """Stream response chunks (Hugging Face streaming is not supported by transport in Phase 2)."""
        err = ProviderUnavailableError(
            message="Streaming is not supported by Hugging Face provider in this phase.",
            provider=self.provider_id,
            model_id=request.model_id or "huggingface",
        )
        yield AIStreamChunk(
            event_type=StreamEventType.ERROR,
            error=err,
            model_id=request.model_id or "huggingface",
            provider=self.provider_id,
            metadata={"error": str(err)},
        )
        raise err

