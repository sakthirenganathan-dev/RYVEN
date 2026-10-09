"""RYVEN 3.0 — AI Provider Contracts, Normalized Types & Error Hierarchy.

Defines provider-neutral request, response, usage, error schemas, and adapter protocol
for the multi-model intelligence architecture (M17.10 Phase 1).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union
from pydantic import BaseModel, ConfigDict, Field

from app.ai.models import ModelProvider, TaskType
from app.ai.provider import ChatMessage


# ============================================================================
# SECRET REDACTION & SANITIZATION UTILITIES
# ============================================================================

_SECRET_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", re.IGNORECASE),
    re.compile(r"((?:xai|hf|sk|key|token)[-_][A-Za-z0-9_\-]+)", re.IGNORECASE),
    re.compile(r"((?:api[_-]?key|password|secret|authorization|cookie)\s*[:=]\s*['\"]?)([^'\"\s,;]+)(['\"]?)", re.IGNORECASE),
    re.compile(r"((?:confirmation[_-]?token|confirm[_-]?token)\s*[:=]\s*['\"]?)([A-Fa-f0-9\-]{16,64})(['\"]?)", re.IGNORECASE),
]

_SENSITIVE_KEY_SUBSTRINGS = (
    "key",
    "token",
    "secret",
    "password",
    "auth",
    "cookie",
    "credential",
    "confirmation",
)


def redact_secrets(text: str) -> str:
    """Mask credentials, bearer tokens, confirmation tokens, and API keys from string."""
    if not isinstance(text, str):
        return str(text)

    cleaned = text
    # 1. Bearer tokens
    cleaned = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", r"\1[REDACTED]", cleaned, flags=re.IGNORECASE)
    # 2. Known token prefixes (xai-, hf-, sk-)
    cleaned = re.sub(r"\b(xai-[A-Za-z0-9_\-]{8,})\b", "[REDACTED_API_KEY]", cleaned)
    cleaned = re.sub(r"\b(hf_[A-Za-z0-9_\-]{8,})\b", "[REDACTED_API_KEY]", cleaned)
    cleaned = re.sub(r"\b(sk-[A-Za-z0-9_\-]{8,})\b", "[REDACTED_API_KEY]", cleaned)
    # 3. Key-value style patterns
    cleaned = re.sub(
        r"((?:api[_-]?key|password|secret|authorization|cookie)\s*[:=]\s*['\"]?)[^'\"\s,;]+(['\"]?)",
        r"\1[REDACTED]\2",
        cleaned,
        flags=re.IGNORECASE,
    )
    # 4. Confirmation tokens
    cleaned = re.sub(
        r"((?:confirmation[_-]?token|confirm[_-]?token)\s*[:=]\s*['\"]?)[A-Fa-f0-9\-]{16,64}(['\"]?)",
        r"\1[REDACTED]\2",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


def sanitize_dict(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Recursively redact dictionary keys that might contain credentials."""
    if not data or not isinstance(data, dict):
        return {}

    sanitized: Dict[str, Any] = {}
    for k, v in data.items():
        key_lower = str(k).lower()
        if any(sensitive in key_lower for sensitive in _SENSITIVE_KEY_SUBSTRINGS):
            sanitized[k] = "[REDACTED]"
        elif isinstance(v, dict):
            sanitized[k] = sanitize_dict(v)
        elif isinstance(v, list):
            sanitized[k] = [
                sanitize_dict(item) if isinstance(item, dict)
                else redact_secrets(str(item)) if isinstance(item, str)
                else item
                for item in v
            ]
        elif isinstance(v, str):
            sanitized[k] = redact_secrets(v)
        else:
            sanitized[k] = v
    return sanitized


class StreamChunkSanitizer:
    """Incremental bounded stream sanitizer preventing secret leakage and stripping
    <think> internal reasoning blocks across chunk boundaries.
    """

    MAX_TAIL_CHARS: int = 128
    _THINK_OPEN: str = "<think>"
    _THINK_CLOSE: str = "</think>"

    # Trigger prefixes that could begin a credential or confirmation token
    _TRIGGER_PREFIXES = (
        "bearer",
        "xai-",
        "hf_",
        "sk-",
        "confirmation_token",
        "confirm_token",
        "api_key",
        "api-key",
        "password",
        "secret",
        "authorization",
        "cookie",
        "token",
    )

    def __init__(self) -> None:
        self.in_think: bool = False
        self.buffer: str = ""

    def feed(self, chunk: str) -> str:
        """Process an incremental chunk and return safe text ready for emission."""
        if not chunk:
            return ""

        text = self.buffer + chunk
        self.buffer = ""
        emitted_parts: List[str] = []

        while text:
            if self.in_think:
                lower_text = text.lower()
                close_idx = lower_text.find(self._THINK_CLOSE)
                if close_idx != -1:
                    # Found </think>: discard content up to </think>
                    text = text[close_idx + len(self._THINK_CLOSE):]
                    self.in_think = False
                    continue
                else:
                    # Check if text ends with a partial prefix of </think>
                    partial_len = 0
                    for k in range(1, len(self._THINK_CLOSE)):
                        if lower_text.endswith(self._THINK_CLOSE[:k]):
                            partial_len = k
                            break
                    if partial_len > 0:
                        self.buffer = text[-partial_len:]
                    text = ""
                    break
            else:
                lower_text = text.lower()
                open_idx = lower_text.find(self._THINK_OPEN)
                if open_idx != -1:
                    # Found <think>: text before it is potential candidate for emission
                    safe_candidate = text[:open_idx]
                    text = text[open_idx + len(self._THINK_OPEN):]
                    self.in_think = True
                    if safe_candidate:
                        safe_emitted, held = self._split_sensitive_tail(safe_candidate)
                        if safe_emitted:
                            emitted_parts.append(redact_secrets(safe_emitted))
                        self.buffer = held + self.buffer
                    continue
                else:
                    # No <think> found in text. Check if text ends with a partial prefix of <think>
                    partial_len = 0
                    for k in range(1, len(self._THINK_OPEN)):
                        if lower_text.endswith(self._THINK_OPEN[:k]):
                            partial_len = k
                            break

                    if partial_len > 0:
                        candidate = text[:-partial_len]
                        pending_prefix = text[-partial_len:]
                        safe_emitted, held = self._split_sensitive_tail(candidate)
                        if safe_emitted:
                            emitted_parts.append(redact_secrets(safe_emitted))
                        self.buffer = held + pending_prefix
                    else:
                        safe_emitted, held = self._split_sensitive_tail(text)
                        if safe_emitted:
                            emitted_parts.append(redact_secrets(safe_emitted))
                        self.buffer = held

                    text = ""
                    break

        return "".join(emitted_parts)

    def _split_sensitive_tail(self, text: str) -> Tuple[str, str]:
        """Split text into (safe_prefix, sensitive_tail_to_hold)."""
        if not text:
            return "", ""

        lower = text.lower()
        tail_inspect_len = min(len(text), self.MAX_TAIL_CHARS)
        tail_slice = lower[-tail_inspect_len:]

        hold_idx = -1
        # Search for trigger words or prefixes in tail slice
        for trig in self._TRIGGER_PREFIXES:
            # Check if tail ends with a prefix of trig (at least 3 chars)
            for k in range(3, len(trig) + 1):
                if tail_slice.endswith(trig[:k]):
                    cand_idx = len(text) - k
                    if hold_idx == -1 or cand_idx < hold_idx:
                        hold_idx = cand_idx
                    break

            # Check if trig appears in tail slice followed by potential token chars
            pos = tail_slice.rfind(trig)
            if pos != -1:
                cand_idx = len(text) - len(tail_slice) + pos
                after_trig = text[cand_idx + len(trig):]
                # If after_trig is short (value in progress) and doesn't have closing delimiter
                if len(after_trig) <= 64 and not re.search(r"[\s,;'\"]\S+", after_trig):
                    if hold_idx == -1 or cand_idx < hold_idx:
                        hold_idx = cand_idx

        if hold_idx != -1 and hold_idx < len(text):
            return text[:hold_idx], text[hold_idx:]

        return text, ""

    def flush(self) -> str:
        """Flush any remaining tail buffer at end of stream."""
        if self.in_think:
            self.buffer = ""
            return ""
        flushed = redact_secrets(self.buffer)
        self.buffer = ""
        return flushed


# ============================================================================
# NORMALIZED DATA MODELS: USAGE, REQUEST, RESPONSE
# ============================================================================

class AIUsage(BaseModel):
    """Normalized token accounting for model requests and responses."""

    prompt_tokens: Optional[int] = Field(None, description="Input tokens consumed")
    completion_tokens: Optional[int] = Field(None, description="Output tokens generated")
    total_tokens: Optional[int] = Field(None, description="Total tokens consumed")

    @classmethod
    def from_counts(
        cls,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
    ) -> AIUsage:
        """Construct AIUsage safely computing total if only parts are known."""
        if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
            total_tokens = prompt_tokens + completion_tokens
        return cls(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )


class AIRequest(BaseModel):
    """Provider-neutral model invocation request."""

    messages: List[ChatMessage] = Field(default_factory=list, description="Conversation turns")
    system_prompt: Optional[str] = Field(None, description="System guidance instructions")
    model_id: Optional[str] = Field(None, description="Requested target model identifier")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: Optional[int] = Field(None, gt=0, description="Maximum tokens to generate")
    timeout_seconds: Optional[float] = Field(None, gt=0.0, description="Inference timeout in seconds")
    structured_output_schema: Optional[Dict[str, Any]] = Field(None, description="JSON schema for structured outputs")
    images: Optional[List[str]] = Field(None, description="Transient base64-encoded image payloads for multimodal tasks")
    task_type: Optional[TaskType] = Field(None, description="Cognitive task classification")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Bounded execution metadata")
    stream: bool = Field(default=False, description="Whether streaming delivery is requested")

    @classmethod
    def from_prompt(
        cls,
        prompt: str,
        system_prompt: Optional[str] = None,
        model_id: Optional[str] = None,
        task_type: Optional[TaskType] = None,
        images: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> AIRequest:
        """Convenience constructor from a single prompt string."""
        messages = [ChatMessage(role="user", content=prompt)]
        return cls(
            messages=messages,
            system_prompt=system_prompt,
            model_id=model_id,
            task_type=task_type,
            images=images,
            **kwargs,
        )

    def get_prompt_text(self) -> str:
        """Extract the primary user prompt text from conversation messages."""
        for msg in reversed(self.messages):
            if msg.role == "user":
                return msg.content
        return ""

    def __repr__(self) -> str:
        """Safe representation masking raw image payloads and sensitive metadata."""
        images_repr = f"[{len(self.images)} image(s)]" if self.images else "None"
        sanitized_meta = sanitize_dict(self.metadata)
        return (
            f"AIRequest(model_id={self.model_id!r}, "
            f"messages_count={len(self.messages)}, "
            f"system_prompt={'set' if self.system_prompt else 'None'}, "
            f"images={images_repr}, "
            f"task_type={self.task_type!r}, "
            f"stream={self.stream}, "
            f"metadata={sanitized_meta!r})"
        )


class AIResponse(BaseModel):
    """Normalized response returned by all AI provider adapters."""

    content: str = Field(..., description="Generated text content from the model")
    model_id: str = Field(..., description="Canonical model identifier used for inference")
    provider: Union[ModelProvider, str] = Field(..., description="Provider that executed the inference")
    finish_reason: Optional[str] = Field(None, description="Termination reason, e.g. 'stop', 'length'")
    usage: Optional[AIUsage] = Field(None, description="Normalized token accounting")
    latency_ms: Optional[float] = Field(None, description="Round-trip request latency in milliseconds")
    request_id: Optional[str] = Field(None, description="Correlation identifier for tracing")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Provider-specific sanitized metadata")

    @property
    def model(self) -> str:
        """Backward-compatibility alias matching legacy AIResponse.model."""
        return self.model_id

    def __repr__(self) -> str:
        """Safe string representation without secret leakage."""
        provider_val = self.provider.value if isinstance(self.provider, ModelProvider) else self.provider
        content_preview = (self.content[:60] + "...") if len(self.content) > 60 else self.content
        return (
            f"AIResponse(model_id={self.model_id!r}, "
            f"provider={provider_val!r}, "
            f"finish_reason={self.finish_reason!r}, "
            f"content_length={len(self.content)}, "
            f"latency_ms={self.latency_ms}, "
            f"usage={self.usage!r})"
        )


# ============================================================================
# NORMALIZED ERROR HIERARCHY
# ============================================================================

class AIProviderError(Exception):
    """Root class for all provider-neutral model exceptions."""

    def __init__(
        self,
        message: str,
        error_code: str = "AI_PROVIDER_ERROR",
        provider: Optional[Union[ModelProvider, str]] = None,
        model_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.raw_message = message
        self.message = redact_secrets(message)
        self.error_code = error_code
        self.provider = provider
        self.model_id = model_id
        self.details = sanitize_dict(details or {})
        super().__init__(self.message)

    def __repr__(self) -> str:
        provider_val = self.provider.value if isinstance(self.provider, ModelProvider) else self.provider
        return (
            f"{self.__class__.__name__}(error_code={self.error_code!r}, "
            f"message={self.message!r}, "
            f"provider={provider_val!r}, "
            f"model_id={self.model_id!r})"
        )


class ProviderUnavailableError(AIProviderError):
    """Raised when the requested provider service or model daemon is unreachable or offline."""

    def __init__(
        self,
        message: str,
        provider: Optional[Union[ModelProvider, str]] = None,
        model_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code="PROVIDER_UNAVAILABLE",
            provider=provider,
            model_id=model_id,
            details=details,
        )


class AuthenticationError(AIProviderError):
    """Raised when authentication with the provider fails (missing/invalid key, unauthorized)."""

    def __init__(
        self,
        message: str,
        provider: Optional[Union[ModelProvider, str]] = None,
        model_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code="AUTHENTICATION_FAILED",
            provider=provider,
            model_id=model_id,
            details=details,
        )


class ContextOverflowError(AIProviderError):
    """Raised when total prompt tokens exceed the configured model context window."""

    def __init__(
        self,
        message: str,
        provider: Optional[Union[ModelProvider, str]] = None,
        model_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code="CONTEXT_OVERFLOW",
            provider=provider,
            model_id=model_id,
            details=details,
        )


class RateLimitError(AIProviderError):
    """Raised when the provider endpoint enforces a rate limit (HTTP 429)."""

    def __init__(
        self,
        message: str,
        provider: Optional[Union[ModelProvider, str]] = None,
        model_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code="RATE_LIMIT_EXCEEDED",
            provider=provider,
            model_id=model_id,
            details=details,
        )


class SecurityViolationError(AIProviderError):
    """Raised when security boundaries or leak-prevention policies prevent dispatch."""

    def __init__(
        self,
        message: str,
        provider: Optional[Union[ModelProvider, str]] = None,
        model_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code="SECURITY_VIOLATION",
            provider=provider,
            model_id=model_id,
            details=details,
        )


class ProviderTimeoutError(AIProviderError):
    """Raised when a provider execution exceeds the configured timeout budget."""

    def __init__(
        self,
        message: str,
        provider: Optional[Union[ModelProvider, str]] = None,
        model_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code="PROVIDER_TIMEOUT",
            provider=provider,
            model_id=model_id,
            details=details,
        )


class ProviderInvalidRequestError(AIProviderError):
    """Raised when request arguments, parameters, or payloads are malformed or unsupported."""

    def __init__(
        self,
        message: str,
        provider: Optional[Union[ModelProvider, str]] = None,
        model_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code="INVALID_REQUEST",
            provider=provider,
            model_id=model_id,
            details=details,
        )


class ProviderResponseError(AIProviderError):
    """Raised when provider returns an unparseable or corrupted response structure."""

    def __init__(
        self,
        message: str,
        provider: Optional[Union[ModelProvider, str]] = None,
        model_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(
            message=message,
            error_code="PROVIDER_RESPONSE_ERROR",
            provider=provider,
            model_id=model_id,
            details=details,
        )


# ============================================================================
# NORMALIZED STREAM EVENT MODEL
# ============================================================================

class StreamEventType(str, Enum):
    """Event classifications for incremental streaming deltas and lifecycle."""

    START = "start"
    DELTA = "delta"
    DONE = "done"
    ERROR = "error"


class AIStreamChunk(BaseModel):
    """Normalized stream event chunk returned by streaming provider adapters."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    event_type: StreamEventType = Field(
        default=StreamEventType.DELTA,
        description="Type of stream event: start, delta, done, error",
    )
    delta: str = Field(
        default="",
        description="Incremental sanitized text delta emitted in this chunk",
    )
    model_id: str = Field(
        ...,
        description="Canonical model identifier used for inference",
    )
    provider: Union[ModelProvider, str] = Field(
        ...,
        description="Provider that executed the inference",
    )
    finish_reason: Optional[str] = Field(
        None,
        description="Termination reason on completion, e.g. 'stop', 'length'",
    )
    usage: Optional[AIUsage] = Field(
        None,
        description="Normalized token usage details (populated on done)",
    )
    latency_ms: Optional[float] = Field(
        None,
        description="Execution latency in milliseconds",
    )
    request_id: Optional[str] = Field(
        None,
        description="Correlation tracking ID",
    )
    error: Optional[AIProviderError] = Field(
        None,
        description="Normalized provider error if stream encountered a failure",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Sanitized metadata associated with this chunk",
    )

    @property
    def model(self) -> str:
        """Backward-compatibility alias matching legacy AIResponse.model."""
        return self.model_id

    @property
    def is_done(self) -> bool:
        return self.event_type == StreamEventType.DONE

    @property
    def is_error(self) -> bool:
        return self.event_type == StreamEventType.ERROR

    @property
    def is_start(self) -> bool:
        return self.event_type == StreamEventType.START

    @property
    def is_delta(self) -> bool:
        return self.event_type == StreamEventType.DELTA

    def __repr__(self) -> str:
        """Safe representation without secret or raw payload leakage."""
        provider_val = self.provider.value if isinstance(self.provider, ModelProvider) else self.provider
        delta_preview = (self.delta[:40] + "...") if len(self.delta) > 40 else self.delta
        delta_clean = redact_secrets(delta_preview)
        return (
            f"AIStreamChunk(event_type={self.event_type.value!r}, "
            f"model_id={self.model_id!r}, "
            f"provider={provider_val!r}, "
            f"delta_length={len(self.delta)}, "
            f"delta_preview={delta_clean!r}, "
            f"finish_reason={self.finish_reason!r})"
        )


# ============================================================================
# PROVIDER ADAPTER ABSTRACT CONTRACT
# ============================================================================

class ProviderAdapter(ABC):
    """Provider-neutral model invocation contract."""

    @property
    @abstractmethod
    def provider_id(self) -> ModelProvider:
        """Canonical provider enum identifier."""
        raise NotImplementedError

    @abstractmethod
    async def generate(self, request: AIRequest) -> AIResponse:
        """Generate response from normalized AIRequest."""
        raise NotImplementedError

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        """Stream response chunks incrementally from the provider."""
        raise NotImplementedError(f"Streaming is not supported by {self.__class__.__name__}")
        yield  # type: ignore[unreachable]

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        """Check provider operational status and return sanitized health info."""
        raise NotImplementedError

    @abstractmethod
    async def is_available(self) -> bool:
        """Check whether provider is currently reachable and ready for inference."""
        raise NotImplementedError


def __getattr__(name: str) -> Any:
    """Allow lazy resolution of concrete adapters from contracts module."""
    if name in ("OllamaAdapter", "GrokAdapter", "HuggingFaceAdapter"):
        from app.ai.adapters import GrokAdapter, HuggingFaceAdapter, OllamaAdapter

        adapters = {
            "OllamaAdapter": OllamaAdapter,
            "GrokAdapter": GrokAdapter,
            "HuggingFaceAdapter": HuggingFaceAdapter,
        }
        return adapters[name]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

