"""RYVEN 3.0 — M17.10 Phase 1 Verification Test Suite.

Tests provider-neutral contracts, request/response models, usage tracking, error hierarchy,
provider adapters (Ollama, Grok, Hugging Face), model registry, router compatibility,
and security leak-prevention boundaries.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.ai.adapters import GrokAdapter, HuggingFaceAdapter, OllamaAdapter
from app.ai.contracts import (
    AIProviderError,
    AIRequest,
    AIResponse,
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
    redact_secrets,
    sanitize_dict,
)
from app.ai.grok import GrokProvider, GrokUnavailableError
from app.ai.hf_local import HuggingFaceLocalProvider, HuggingFaceLocalUnavailableError
from app.ai.hf_remote import HuggingFaceRemoteProvider, HuggingFaceRemoteUnavailableError
from app.ai.models import ModelProfile, ModelProvider, TaskType
from app.ai.ollama import OllamaProvider, OllamaUnavailableError
from app.ai.provider import AIResponse as LegacyAIResponse, ChatMessage
from app.ai.registry import ModelRegistry, model_registry
from app.ai.router import ModelRouter, model_router
from app.ai.security import ModelSecurityPolicy, ModelSecurityViolationError


# ============================================================================
# GROUP A: AIRequest
# ============================================================================

def test_ai_request_creation_defaults():
    req = AIRequest()
    assert req.messages == []
    assert req.system_prompt is None
    assert req.model_id is None
    assert req.temperature is None
    assert req.max_tokens is None
    assert req.timeout_seconds is None
    assert req.structured_output_schema is None
    assert req.images is None
    assert req.task_type is None
    assert req.metadata == {}
    assert req.stream is False


def test_ai_request_from_prompt_helper():
    req = AIRequest.from_prompt(
        prompt="Synthesize the report",
        system_prompt="You are an expert",
        model_id="qwen2.5:7b",
        task_type=TaskType.GENERAL_REASONING,
        temperature=0.7,
    )
    assert len(req.messages) == 1
    assert req.messages[0].role == "user"
    assert req.messages[0].content == "Synthesize the report"
    assert req.system_prompt == "You are an expert"
    assert req.model_id == "qwen2.5:7b"
    assert req.task_type == TaskType.GENERAL_REASONING
    assert req.temperature == 0.7
    assert req.get_prompt_text() == "Synthesize the report"


def test_ai_request_get_prompt_text_multiturn():
    req = AIRequest(
        messages=[
            ChatMessage(role="user", content="First question"),
            ChatMessage(role="assistant", content="First reply"),
            ChatMessage(role="user", content="Follow-up question"),
        ]
    )
    assert req.get_prompt_text() == "Follow-up question"


def test_ai_request_images_transient_in_repr():
    fake_b64 = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    req = AIRequest.from_prompt(
        prompt="Inspect UI element",
        images=[fake_b64, fake_b64],
    )
    repr_str = repr(req)
    assert fake_b64 not in repr_str
    assert "[2 image(s)]" in repr_str


def test_ai_request_metadata_sanitization_in_repr():
    req = AIRequest(
        messages=[ChatMessage(role="user", content="Run step")],
        metadata={
            "session_id": "sess-123",
            "api_key": "secret-key-12345",
            "token": "tok-abcdef",
        },
    )
    repr_str = repr(req)
    assert "secret-key-12345" not in repr_str
    assert "tok-abcdef" not in repr_str
    assert "[REDACTED]" in repr_str


def test_ai_request_serialization():
    req = AIRequest.from_prompt(prompt="Test serialization", model_id="qwen2.5:7b")
    dumped = req.model_dump()
    assert dumped["model_id"] == "qwen2.5:7b"
    assert dumped["messages"][0]["content"] == "Test serialization"


# ============================================================================
# GROUP B: AIResponse
# ============================================================================

def test_ai_response_creation():
    usage = AIUsage(prompt_tokens=10, completion_tokens=25, total_tokens=35)
    resp = AIResponse(
        content="Operation completed successfully.",
        model_id="qwen2.5:7b",
        provider=ModelProvider.OLLAMA,
        finish_reason="stop",
        usage=usage,
        latency_ms=125.4,
        request_id="req-999",
        metadata={"eval_count": 25},
    )
    assert resp.content == "Operation completed successfully."
    assert resp.model_id == "qwen2.5:7b"
    assert resp.provider == ModelProvider.OLLAMA
    assert resp.finish_reason == "stop"
    assert resp.usage.total_tokens == 35
    assert resp.latency_ms == 125.4
    assert resp.request_id == "req-999"


def test_ai_response_legacy_model_property():
    resp = AIResponse(
        content="Response text",
        model_id="grok-2-latest",
        provider=ModelProvider.GROK,
    )
    assert resp.model == "grok-2-latest"
    assert resp.model == resp.model_id


def test_ai_response_safe_repr():
    resp = AIResponse(
        content="Clean content without secrets",
        model_id="qwen2.5:7b",
        provider=ModelProvider.OLLAMA,
        metadata={"auth_token": "secret_token_value"},
    )
    repr_str = repr(resp)
    assert "secret_token_value" not in repr_str
    assert "qwen2.5:7b" in repr_str
    assert "OLLAMA" in repr_str


def test_ai_response_metadata_handling():
    resp = AIResponse(
        content="Test content",
        model_id="test-model",
        provider="TEST_PROVIDER",
        metadata={"duration_ns": 4500000},
    )
    assert resp.metadata["duration_ns"] == 4500000


# ============================================================================
# GROUP C: AIUsage
# ============================================================================

def test_ai_usage_complete():
    usage = AIUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 50
    assert usage.total_tokens == 150


def test_ai_usage_partial_computes_total():
    usage = AIUsage.from_counts(prompt_tokens=30, completion_tokens=70)
    assert usage.prompt_tokens == 30
    assert usage.completion_tokens == 70
    assert usage.total_tokens == 100


def test_ai_usage_unavailable_no_fabrication():
    usage = AIUsage()
    assert usage.prompt_tokens is None
    assert usage.completion_tokens is None
    assert usage.total_tokens is None


def test_ai_usage_serialization():
    usage = AIUsage.from_counts(prompt_tokens=12, completion_tokens=18)
    data = usage.model_dump()
    assert data == {"prompt_tokens": 12, "completion_tokens": 18, "total_tokens": 30}


# ============================================================================
# GROUP D: Provider Error Hierarchy
# ============================================================================

def test_error_hierarchy_inheritance():
    errors = [
        ProviderUnavailableError("Unavailable"),
        AuthenticationError("Auth failed"),
        ContextOverflowError("Context exceeded"),
        RateLimitError("Rate limit hit"),
        SecurityViolationError("Policy blocked"),
        ProviderTimeoutError("Timed out"),
        ProviderInvalidRequestError("Bad schema"),
        ProviderResponseError("Corrupt payload"),
    ]
    for err in errors:
        assert isinstance(err, AIProviderError)
        assert isinstance(err, Exception)


def test_error_codes_stable():
    assert ProviderUnavailableError("x").error_code == "PROVIDER_UNAVAILABLE"
    assert AuthenticationError("x").error_code == "AUTHENTICATION_FAILED"
    assert ContextOverflowError("x").error_code == "CONTEXT_OVERFLOW"
    assert RateLimitError("x").error_code == "RATE_LIMIT_EXCEEDED"
    assert SecurityViolationError("x").error_code == "SECURITY_VIOLATION"
    assert ProviderTimeoutError("x").error_code == "PROVIDER_TIMEOUT"
    assert ProviderInvalidRequestError("x").error_code == "INVALID_REQUEST"
    assert ProviderResponseError("x").error_code == "PROVIDER_RESPONSE_ERROR"


def test_error_secret_redaction_in_message():
    raw_msg = "Error connecting with Bearer secret-tok-998811 and key=xai-998877665544"
    err = AuthenticationError(raw_msg, provider=ModelProvider.GROK)
    assert "secret-tok-998811" not in str(err)
    assert "xai-998877665544" not in str(err)
    assert "[REDACTED]" in str(err) or "[REDACTED_API_KEY]" in str(err)


def test_error_details_sanitized():
    err = ProviderUnavailableError(
        "Failed",
        details={
            "status": 401,
            "api_key": "xai-ultra-secret-key",
            "auth_header": "Bearer token123",
            "public_metric": 42,
        },
    )
    assert err.details["api_key"] == "[REDACTED]"
    assert err.details["auth_header"] == "[REDACTED]"
    assert err.details["public_metric"] == 42


def test_error_repr_redaction():
    err = SecurityViolationError(
        "Confirmation token confirmation_token=a1b2c3d4e5f60718293a4b5c6d7e8f90 denied",
        provider=ModelProvider.OLLAMA,
    )
    repr_str = repr(err)
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f90" not in repr_str
    assert "[REDACTED]" in repr_str


# ============================================================================
# GROUP E: ProviderAdapter Contract
# ============================================================================

def test_provider_adapter_cannot_instantiate_abstract():
    with pytest.raises(TypeError):
        ProviderAdapter()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_provider_adapter_custom_implementation():
    class DummyAdapter(ProviderAdapter):
        @property
        def provider_id(self) -> ModelProvider:
            return ModelProvider.OLLAMA

        async def generate(self, request: AIRequest) -> AIResponse:
            return AIResponse(
                content="Mocked answer",
                model_id="mock-model",
                provider=self.provider_id,
            )

        async def health_check(self):
            return {"online": True}

        async def is_available(self):
            return True

    adapter = DummyAdapter()
    assert adapter.provider_id == ModelProvider.OLLAMA
    assert await adapter.is_available() is True
    res = await adapter.generate(AIRequest.from_prompt("Hi"))
    assert res.content == "Mocked answer"


# ============================================================================
# GROUP F: ModelProvider Enum
# ============================================================================

def test_model_provider_grok_exists():
    assert hasattr(ModelProvider, "GROK")
    assert ModelProvider.GROK == "GROK"
    assert ModelProvider.GROK.value == "GROK"


def test_model_provider_existing_preserved():
    assert ModelProvider.OLLAMA == "OLLAMA"
    assert ModelProvider.HUGGINGFACE_LOCAL == "HUGGINGFACE_LOCAL"
    assert ModelProvider.HUGGINGFACE_REMOTE == "HUGGINGFACE_REMOTE"


def test_model_provider_serialized_values():
    providers = [p.value for p in ModelProvider]
    assert "OLLAMA" in providers
    assert "HUGGINGFACE_LOCAL" in providers
    assert "HUGGINGFACE_REMOTE" in providers
    assert "GROK" in providers


# ============================================================================
# GROUP G: OllamaAdapter
# ============================================================================

def test_ollama_adapter_provider_id():
    adapter = OllamaAdapter()
    assert adapter.provider_id == ModelProvider.OLLAMA


@pytest.mark.asyncio
async def test_ollama_adapter_generate_success():
    mock_legacy_provider = MagicMock(spec=OllamaProvider)
    mock_legacy_provider.model = "qwen2.5:7b"
    mock_legacy_provider.generate = AsyncMock(
        return_value=LegacyAIResponse(
            content="Summary of logs",
            model="qwen2.5:7b",
            provider="ollama",
            metadata={"eval_count": 42, "prompt_eval_count": 15},
        )
    )

    adapter = OllamaAdapter(provider=mock_legacy_provider)
    req = AIRequest.from_prompt("Summarize", system_prompt="Sys context", temperature=0.2)

    resp = await adapter.generate(req)

    assert resp.content == "Summary of logs"
    assert resp.model_id == "qwen2.5:7b"
    assert resp.provider == ModelProvider.OLLAMA
    assert resp.usage is not None
    assert resp.usage.completion_tokens == 42
    assert resp.usage.prompt_tokens == 15
    assert resp.usage.total_tokens == 57
    assert resp.latency_ms is not None
    mock_legacy_provider.generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_ollama_adapter_generate_with_images():
    mock_legacy_provider = MagicMock(spec=OllamaProvider)
    mock_legacy_provider.model = "llava:7b"
    mock_legacy_provider.generate = AsyncMock(
        return_value=LegacyAIResponse(
            content="Image contains dialog box",
            model="llava:7b",
            provider="ollama",
            metadata={},
        )
    )

    adapter = OllamaAdapter(provider=mock_legacy_provider)
    req = AIRequest.from_prompt("What is this?", images=["base64_img_data"])

    resp = await adapter.generate(req)
    assert resp.content == "Image contains dialog box"
    mock_legacy_provider.generate.assert_awaited_once_with(
        prompt="What is this?",
        system_prompt=None,
        messages=req.messages,
        images=["base64_img_data"],
    )


@pytest.mark.asyncio
async def test_ollama_adapter_model_override():
    mock_legacy_provider = MagicMock(spec=OllamaProvider)
    mock_legacy_provider.model = "qwen2.5:7b"
    mock_legacy_provider.generate = AsyncMock(
        return_value=LegacyAIResponse(
            content="Specialist response",
            model="codellama",
            provider="ollama",
            metadata={},
        )
    )

    adapter = OllamaAdapter(provider=mock_legacy_provider)
    req = AIRequest.from_prompt("Code task", model_id="codellama")

    await adapter.generate(req)
    # Verifies model reverted to original after execution
    assert mock_legacy_provider.model == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_ollama_adapter_unavailable_error_mapping():
    mock_legacy_provider = MagicMock(spec=OllamaProvider)
    mock_legacy_provider.model = "qwen2.5:7b"
    mock_legacy_provider.generate = AsyncMock(
        side_effect=OllamaUnavailableError("Ollama daemon is not reachable at localhost:11434")
    )

    adapter = OllamaAdapter(provider=mock_legacy_provider)
    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.generate(AIRequest.from_prompt("Hi"))

    assert exc_info.value.error_code == "PROVIDER_UNAVAILABLE"
    assert exc_info.value.provider == ModelProvider.OLLAMA


@pytest.mark.asyncio
async def test_ollama_adapter_timeout_mapping():
    mock_legacy_provider = MagicMock(spec=OllamaProvider)
    mock_legacy_provider.model = "qwen2.5:7b"
    mock_legacy_provider.generate = AsyncMock(
        side_effect=OllamaUnavailableError("Request timed out after 30.0s")
    )

    adapter = OllamaAdapter(provider=mock_legacy_provider)
    with pytest.raises(ProviderTimeoutError) as exc_info:
        await adapter.generate(AIRequest.from_prompt("Hi"))

    assert exc_info.value.error_code == "PROVIDER_TIMEOUT"
    assert exc_info.value.provider == ModelProvider.OLLAMA


@pytest.mark.asyncio
async def test_ollama_adapter_health_check_and_availability():
    mock_legacy_provider = MagicMock(spec=OllamaProvider)
    mock_legacy_provider.check_health = AsyncMock(
        return_value={"online": True, "model_available": True}
    )

    adapter = OllamaAdapter(provider=mock_legacy_provider)
    health = await adapter.health_check()
    assert health["online"] is True
    assert await adapter.is_available() is True


# ============================================================================
# GROUP H: GrokAdapter
# ============================================================================

def test_grok_adapter_provider_id():
    adapter = GrokAdapter()
    assert adapter.provider_id == ModelProvider.GROK


@pytest.mark.asyncio
async def test_grok_adapter_missing_api_key_is_unavailable():
    mock_grok = MagicMock(spec=GrokProvider)
    mock_grok.api_key = ""
    adapter = GrokAdapter(provider=mock_grok)
    assert await adapter.is_available() is False


@pytest.mark.asyncio
async def test_grok_adapter_missing_api_key_generate_raises_auth():
    mock_grok = MagicMock(spec=GrokProvider)
    mock_grok.api_key = None
    mock_grok.model = "grok-2-latest"
    adapter = GrokAdapter(provider=mock_grok)

    with pytest.raises(AuthenticationError) as exc_info:
        await adapter.generate(AIRequest.from_prompt("Query Grok"))

    assert exc_info.value.error_code == "AUTHENTICATION_FAILED"
    assert exc_info.value.provider == ModelProvider.GROK


@pytest.mark.asyncio
async def test_grok_adapter_generate_success():
    mock_grok = MagicMock(spec=GrokProvider)
    mock_grok.api_key = "dummy-test-key"
    mock_grok.model = "grok-2-latest"
    mock_grok.generate = AsyncMock(
        return_value=LegacyAIResponse(
            content="Grok reasoning response",
            model="grok-2-latest",
            provider="grok",
            metadata={
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 120,
                    "total_tokens": 170,
                }
            },
        )
    )

    adapter = GrokAdapter(provider=mock_grok)
    resp = await adapter.generate(AIRequest.from_prompt("Analyze complexity"))

    assert resp.content == "Grok reasoning response"
    assert resp.model_id == "grok-2-latest"
    assert resp.provider == ModelProvider.GROK
    assert resp.usage is not None
    assert resp.usage.total_tokens == 170
    assert resp.latency_ms is not None


@pytest.mark.asyncio
async def test_grok_adapter_auth_error_mapping():
    mock_grok = MagicMock(spec=GrokProvider)
    mock_grok.api_key = "invalid-key"
    mock_grok.model = "grok-2-latest"
    mock_grok.generate = AsyncMock(
        side_effect=GrokUnavailableError("Authentication failed with Grok API (HTTP 401).")
    )

    adapter = GrokAdapter(provider=mock_grok)
    with pytest.raises(AuthenticationError) as exc_info:
        await adapter.generate(AIRequest.from_prompt("Test"))

    assert exc_info.value.error_code == "AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_grok_adapter_rate_limit_mapping():
    mock_grok = MagicMock(spec=GrokProvider)
    mock_grok.api_key = "valid-key"
    mock_grok.model = "grok-2-latest"
    mock_grok.generate = AsyncMock(
        side_effect=GrokUnavailableError("Grok API returned error HTTP 429: Rate limit exceeded")
    )

    adapter = GrokAdapter(provider=mock_grok)
    with pytest.raises(RateLimitError) as exc_info:
        await adapter.generate(AIRequest.from_prompt("Test"))

    assert exc_info.value.error_code == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_grok_adapter_timeout_mapping():
    mock_grok = MagicMock(spec=GrokProvider)
    mock_grok.api_key = "valid-key"
    mock_grok.model = "grok-2-latest"
    mock_grok.generate = AsyncMock(
        side_effect=GrokUnavailableError("Grok connection timed out after 10s")
    )

    adapter = GrokAdapter(provider=mock_grok)
    with pytest.raises(ProviderTimeoutError) as exc_info:
        await adapter.generate(AIRequest.from_prompt("Test"))

    assert exc_info.value.error_code == "PROVIDER_TIMEOUT"


@pytest.mark.asyncio
async def test_grok_adapter_security_violation_mapping():
    mock_grok = MagicMock(spec=GrokProvider)
    mock_grok.api_key = "valid-key"
    mock_grok.model = "grok-2-latest"
    mock_grok.generate = AsyncMock(
        side_effect=GrokUnavailableError("Security policy blocked transmission to Grok: Sensitive regex matched")
    )

    adapter = GrokAdapter(provider=mock_grok)
    with pytest.raises(SecurityViolationError) as exc_info:
        await adapter.generate(AIRequest.from_prompt("Test"))

    assert exc_info.value.error_code == "SECURITY_VIOLATION"


def test_grok_adapter_no_secrets_in_repr_or_errors():
    mock_grok = MagicMock(spec=GrokProvider)
    mock_grok.api_key = "xai-super-secret-api-key-999"
    mock_grok.model = "grok-2-latest"
    adapter = GrokAdapter(provider=mock_grok)

    repr_str = repr(adapter)
    assert "xai-super-secret-api-key-999" not in repr_str


# ============================================================================
# GROUP I: HuggingFaceAdapter
# ============================================================================

def test_hf_adapter_provider_id_local_and_remote():
    adapter_local = HuggingFaceAdapter(prefer_local=True)
    assert adapter_local.provider_id == ModelProvider.HUGGINGFACE_LOCAL

    adapter_remote = HuggingFaceAdapter(prefer_local=False)
    assert adapter_remote.provider_id == ModelProvider.HUGGINGFACE_REMOTE


@pytest.mark.asyncio
async def test_hf_adapter_local_uncached_raises_unavailable():
    mock_local = MagicMock(spec=HuggingFaceLocalProvider)
    mock_local.model_name = "local-uncached-model"
    mock_local.generate = AsyncMock(
        side_effect=HuggingFaceLocalUnavailableError("Model 'local-uncached-model' is not cached locally")
    )

    adapter = HuggingFaceAdapter(local_provider=mock_local, prefer_local=True)
    with pytest.raises(ProviderUnavailableError) as exc_info:
        await adapter.generate(AIRequest.from_prompt("Synthesize", model_id="local-uncached-model"))

    assert exc_info.value.error_code == "PROVIDER_UNAVAILABLE"
    assert exc_info.value.provider == ModelProvider.HUGGINGFACE_LOCAL


@pytest.mark.asyncio
async def test_hf_adapter_local_generate_success():
    mock_local = MagicMock(spec=HuggingFaceLocalProvider)
    mock_local.model_name = "openai/whisper-small"
    mock_local.generate = AsyncMock(
        return_value=LegacyAIResponse(
            content="Local audio transcribed",
            model="openai/whisper-small",
            provider="huggingface_local",
            metadata={"device": "cpu"},
        )
    )

    adapter = HuggingFaceAdapter(local_provider=mock_local, prefer_local=True)
    resp = await adapter.generate(AIRequest.from_prompt("Transcribe"))

    assert resp.content == "Local audio transcribed"
    assert resp.provider == ModelProvider.HUGGINGFACE_LOCAL


@pytest.mark.asyncio
async def test_hf_adapter_remote_missing_key_raises_auth():
    mock_remote = MagicMock(spec=HuggingFaceRemoteProvider)
    mock_remote.api_key = ""
    mock_remote.model = "hf-remote-qwen-coder"

    adapter = HuggingFaceAdapter(remote_provider=mock_remote, prefer_local=False)
    with pytest.raises(AuthenticationError) as exc_info:
        await adapter.generate(AIRequest.from_prompt("Refactor code", model_id="hf-remote-qwen-coder"))

    assert exc_info.value.error_code == "AUTHENTICATION_FAILED"
    assert exc_info.value.provider == ModelProvider.HUGGINGFACE_REMOTE


@pytest.mark.asyncio
async def test_hf_adapter_remote_security_violation():
    mock_remote = MagicMock(spec=HuggingFaceRemoteProvider)
    mock_remote.api_key = "hf-dummy-key"
    mock_remote.model = "hf-remote-qwen-coder"
    mock_remote.generate = AsyncMock(
        side_effect=ModelSecurityViolationError("Remote transmission blocked by security policy")
    )

    adapter = HuggingFaceAdapter(remote_provider=mock_remote, prefer_local=False)
    with pytest.raises(SecurityViolationError) as exc_info:
        await adapter.generate(AIRequest.from_prompt("Prompt with key"))

    assert exc_info.value.error_code == "SECURITY_VIOLATION"


@pytest.mark.asyncio
async def test_hf_adapter_remote_generate_success():
    mock_remote = MagicMock(spec=HuggingFaceRemoteProvider)
    mock_remote.api_key = "hf-valid-key"
    mock_remote.model = "Qwen/Qwen2.5-Coder-32B-Instruct"
    mock_remote.generate = AsyncMock(
        return_value=LegacyAIResponse(
            content="Remote coder completion",
            model="Qwen/Qwen2.5-Coder-32B-Instruct",
            provider="huggingface_remote",
            metadata={"latency": 0.45},
        )
    )

    adapter = HuggingFaceAdapter(remote_provider=mock_remote, prefer_local=False)
    resp = await adapter.generate(AIRequest.from_prompt("Optimize algorithm", model_id="hf-remote-qwen-coder"))

    assert resp.content == "Remote coder completion"
    assert resp.provider == ModelProvider.HUGGINGFACE_REMOTE


@pytest.mark.asyncio
async def test_hf_adapter_health_check():
    mock_local = MagicMock(spec=HuggingFaceLocalProvider)
    mock_local.check_health = AsyncMock(return_value={"available": False, "local": True})
    mock_remote = MagicMock(spec=HuggingFaceRemoteProvider)
    mock_remote.check_health = AsyncMock(return_value={"available": False, "local": False})

    adapter = HuggingFaceAdapter(local_provider=mock_local, remote_provider=mock_remote)
    health = await adapter.health_check()
    assert health["provider"] == "huggingface"
    assert health["available"] is False


# ============================================================================
# GROUP J: ModelRegistry Integration
# ============================================================================

def test_registry_contains_grok_and_all_providers():
    grok_profile = model_registry.get("grok-2-latest")
    assert grok_profile is not None
    assert grok_profile.provider == ModelProvider.GROK
    assert grok_profile.local_or_remote == "remote"

    qwen_profile = model_registry.get("qwen2.5:7b")
    assert qwen_profile is not None
    assert qwen_profile.provider == ModelProvider.OLLAMA

    hf_profile = model_registry.get("hf-remote-qwen-coder")
    assert hf_profile is not None
    assert hf_profile.provider == ModelProvider.HUGGINGFACE_REMOTE


def test_registry_grok_default_disabled_and_remote():
    grok_profile = model_registry.get("grok-2-latest")
    assert grok_profile is not None
    assert grok_profile.enabled is False
    assert grok_profile.sensitive_data_allowed is False


def test_registry_authoritative_single_instance():
    reg1 = ModelRegistry()
    assert reg1.get("grok-2-latest") is not None
    assert model_registry.get("grok-2-latest") is not None


# ============================================================================
# GROUP K: ModelRouter Compatibility
# ============================================================================

@pytest.mark.asyncio
async def test_router_qwen_default_unaffected():
    decision = await model_router.route(task_type=TaskType.GENERAL_REASONING)
    assert decision.selected_model == "qwen2.5:7b"
    assert decision.provider == "OLLAMA"
    assert decision.local_or_remote == "local"


@pytest.mark.asyncio
async def test_router_grok_recognized_when_enabled_and_authorized():
    custom_policy = ModelSecurityPolicy(allow_remote_inference=True)
    custom_registry = ModelRegistry()

    # Enable grok profile for explicit preference test
    grok_profile = custom_registry.get("grok-2-latest")
    assert grok_profile is not None
    grok_profile.enabled = True

    router = ModelRouter(registry=custom_registry, security_policy=custom_policy)

    decision = await router.route(
        task_type=TaskType.GENERAL_REASONING,
        prompt="Safe non-sensitive prompt",
        allow_remote=True,
        preferred_model_id="grok-2-latest",
    )

    assert decision.selected_model == "grok-2-latest"
    assert decision.provider == "GROK"
    assert decision.local_or_remote == "remote"


# ============================================================================
# GROUP L: Security & Leak Prevention Boundaries
# ============================================================================

def test_security_redact_secrets_utility():
    text = "Authorization: Bearer my-secret-token-123456; password=my_top_secret_pass"
    redacted = redact_secrets(text)
    assert "my-secret-token-123456" not in redacted
    assert "my_top_secret_pass" not in redacted
    assert "[REDACTED]" in redacted


def test_security_redact_confirmation_token():
    text = "Execution approved with confirmation_token=9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d"
    redacted = redact_secrets(text)
    assert "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d" not in redacted
    assert "[REDACTED]" in redacted


def test_security_sanitize_dict_recursive():
    nested = {
        "status": "ok",
        "nested": {
            "api_key": "xai-real-key-value",
            "safe_val": 123,
            "secret_list": ["Bearer tok-abc", "normal text"],
        },
    }
    sanitized = sanitize_dict(nested)
    assert sanitized["nested"]["api_key"] == "[REDACTED]"
    assert sanitized["nested"]["safe_val"] == 123
    assert "tok-abc" not in str(sanitized)


def test_security_no_authorization_header_in_errors():
    err = AIProviderError(
        message="Headers sent: {'Authorization': 'Bearer supersecrettoken'}",
        provider=ModelProvider.GROK,
    )
    assert "supersecrettoken" not in str(err)
    assert "supersecrettoken" not in repr(err)
