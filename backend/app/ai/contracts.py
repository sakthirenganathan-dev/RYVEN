"""RYVEN 3.0 — AI Provider Contracts, Normalized Types & Error Hierarchy.

Defines provider-neutral request, response, usage, error schemas, and adapter protocol
for the multi-model intelligence architecture (M17.10 Phase 1).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field

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

