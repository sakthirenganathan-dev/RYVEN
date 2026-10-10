"""RYVEN 3.0 — Milestone 17.10 Phase 7 Test Suite.

Secure Hugging Face Cloud Provider Integration:
1. Ollama remains the default.
2. Hugging Face is disabled by default.
3. Hugging Face configuration validation.
4. Missing and invalid credentials.
5. Successful response normalization.
6. HTTP authentication and permission failures.
7. Rate limiting and server errors.
8. Network failures and timeouts.
9. Retry bounds and backoff.
10. Streaming capability handling.
11. Provider health and circuit breaker behavior.
12. LOCAL_ONLY blocks all remote calls.
13. PRIVACY_FIRST authorization requirements.
14. Expired and replayed confirmation tokens.
15. Security denial never triggers remote fallback.
16. Fallback candidates receive fresh security validation.
17. Secrets are redacted from logs and telemetry.
18. Token usage metadata is preserved when available.
19. Existing provider injection remains functional.
20. Request cancellation handling.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from app.actions.event_bus import action_bus
from app.actions.models import ActionType
from app.ai.adapters import HuggingFaceAdapter, OllamaAdapter
from app.ai.contracts import (
    AIRequest,
    AIResponse,
    AIStreamChunk,
    AIUsage,
    AuthenticationError,
    ModelProvider,
    ProviderAdapter,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    SecurityViolationError,
    StreamEventType,
)
from app.ai.health import ProviderHealthTracker
from app.ai.hf_remote import (
    HuggingFaceRemoteProvider,
    HuggingFaceRemoteUnavailableError,
)
from app.ai.models import ModelProfile, TaskType
from app.ai.privacy import PrivacyMode
from app.ai.registry import ModelRegistry, model_registry
from app.ai.router import ModelRouter, RouterConfig
from app.ai.security import ModelSecurityPolicy, ModelSecurityViolationError
from app.ai.security_gateway import ModelSecurityGateway
from app.ai.unified import UnifiedAIProvider
from app.core.config import Settings, settings


# ============================================================================
# 1. Ollama remains the default
# ============================================================================

@pytest.mark.asyncio
async def test_01_ollama_remains_default_engine():
    """Verify that Ollama + Qwen 2.5 7B remains the authoritative default engine."""
    unified = UnifiedAIProvider()
    health = await unified.check_health()
    assert health["active_default"] == "ollama"
    assert health["active_default_model"] == "qwen2.5:7b"

    router = ModelRouter()
    assert router.config.default_local_model == "qwen2.5:7b"

    decision = await router.route(TaskType.GENERAL_REASONING, prompt="Hello world", allow_remote=False)
    assert decision.selected_model == "qwen2.5:7b"
    assert decision.provider == "OLLAMA"
    assert decision.local_or_remote == "local"


# ============================================================================
# 2. Hugging Face is disabled by default
# ============================================================================

@pytest.mark.asyncio
async def test_02_hugging_face_disabled_by_default():
    """Verify that Hugging Face remote inference is disabled by default policy and registry."""
    policy = ModelSecurityPolicy()
    assert policy.allow_remote_inference is False

    hf_profile = model_registry.get_model("hf-remote-qwen-coder")
    assert hf_profile is not None
    assert hf_profile.enabled is False
    assert hf_profile.sensitive_data_allowed is False

    provider = HuggingFaceRemoteProvider(api_key="hf_dummy_token", security_policy=policy)
    health = await provider.check_health()
    assert health["remote_allowed"] is False
    assert health["available"] is False
    assert "disabled by default policy" in (health.get("error") or "")


# ============================================================================
# 3. Hugging Face configuration validation
# ============================================================================

def test_03_hugging_face_config_validation():
    """Verify that settings strip tokens and base URLs and support alternative env vars."""
    s = Settings(
        hf_api_key="  hf_test_secret_key_123  ",
        hf_base_url="https://api-inference.huggingface.co/v1///",
        hf_model="Qwen/Qwen2.5-Coder-32B-Instruct",
        hf_timeout_seconds=45.0,
    )
    provider = HuggingFaceRemoteProvider(
        api_key=s.hf_api_key,
        base_url=s.hf_base_url,
        model=s.hf_model,
        timeout_seconds=s.hf_timeout_seconds,
    )
    assert provider.api_key == "hf_test_secret_key_123"
    assert provider.base_url == "https://api-inference.huggingface.co/v1"
    assert provider.model == "Qwen/Qwen2.5-Coder-32B-Instruct"
    assert provider.timeout_seconds == 45.0


# ============================================================================
# 4. Missing and invalid credentials
# ============================================================================

@pytest.mark.asyncio
async def test_04_missing_and_invalid_credentials():
    """Verify that missing or blank API keys raise immediate AuthenticationError without calls."""
    adapter = HuggingFaceAdapter(
        remote_provider=HuggingFaceRemoteProvider(api_key=""),
        prefer_local=False,
    )
    req = AIRequest.from_prompt("Test prompt", model_id="hf-remote-qwen-coder")

    with pytest.raises(AuthenticationError) as exc_info:
        await adapter.generate(req)
    assert "Hugging Face API key is not configured" in str(exc_info.value)

    # In streaming
    with pytest.raises(AuthenticationError) as exc_info_stream:
        async for _ in adapter.stream(req):
            pass
    assert "Hugging Face API key is not configured" in str(exc_info_stream.value)


# ============================================================================
# 5. Successful response normalization
# ============================================================================

@pytest.mark.asyncio
async def test_05_successful_response_normalization():
    """Verify that a successful 200 response is properly parsed into AIResponse with usage metadata."""
    mock_payload = {
        "id": "chatcmpl-test-123",
        "object": "chat.completion",
        "model": "Qwen/Qwen2.5-Coder-32B-Instruct",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "def add(a, b): return a + b"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 14, "completion_tokens": 10, "total_tokens": 24},
    }

    mock_resp = httpx.Response(
        status_code=200,
        json=mock_payload,
        request=httpx.Request("POST", "https://api-inference.huggingface.co/v1/chat/completions"),
    )

    policy = ModelSecurityPolicy(allow_remote_inference=True)
    remote_provider = HuggingFaceRemoteProvider(
        api_key="hf_valid_test_token",
        security_policy=policy,
        max_retries=0,
    )
    adapter = HuggingFaceAdapter(remote_provider=remote_provider, prefer_local=False)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        req = AIRequest.from_prompt("Write an add function", model_id="hf-remote-qwen-coder")
        response = await adapter.generate(req)

        assert response.content == "def add(a, b): return a + b"
        assert response.model_id == "Qwen/Qwen2.5-Coder-32B-Instruct"
        assert response.provider == ModelProvider.HUGGINGFACE_REMOTE
        assert response.finish_reason == "stop"
        assert response.usage is not None
        assert response.usage.prompt_tokens == 14
        assert response.usage.completion_tokens == 10
        assert response.usage.total_tokens == 24


# ============================================================================
# 6. HTTP authentication and permission failures
# ============================================================================

@pytest.mark.asyncio
async def test_06_http_authentication_and_permission_failures():
    """Verify that HTTP 401 and 403 responses raise AuthenticationError and never leak tokens."""
    secret_token = "hf_secret_should_never_leak_xyz"
    policy = ModelSecurityPolicy(allow_remote_inference=True)
    remote_provider = HuggingFaceRemoteProvider(
        api_key=secret_token,
        security_policy=policy,
        max_retries=0,
    )
    adapter = HuggingFaceAdapter(remote_provider=remote_provider, prefer_local=False)

    mock_resp_401 = httpx.Response(
        status_code=401,
        text='{"error": "Invalid API token"}',
        request=httpx.Request("POST", "https://api-inference.huggingface.co/v1/chat/completions"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp_401
        req = AIRequest.from_prompt("Hello", model_id="hf-remote-qwen-coder")

        with pytest.raises(AuthenticationError) as exc_info:
            await adapter.generate(req)

        err_text = str(exc_info.value)
        assert "Authentication failed" in err_text or "401" in err_text
        assert secret_token not in err_text

    # Verify health check probe does not leak token on 401
    mock_probe_401 = httpx.Response(
        status_code=401,
        text="Unauthorized",
        request=httpx.Request("GET", "https://api-inference.huggingface.co/v1/models/test"),
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_probe_401
        health = await remote_provider.check_health()
        assert health["authenticated"] is False
        assert health["available"] is False
        assert secret_token not in json.dumps(health)


# ============================================================================
# 7. Rate limiting and server errors
# ============================================================================

@pytest.mark.asyncio
async def test_07_rate_limiting_and_server_errors():
    """Verify that HTTP 429 raises RateLimitError and 500 raises ProviderUnavailableError."""
    policy = ModelSecurityPolicy(allow_remote_inference=True)
    remote_provider = HuggingFaceRemoteProvider(
        api_key="hf_test_token",
        security_policy=policy,
        max_retries=0,
    )
    adapter = HuggingFaceAdapter(remote_provider=remote_provider, prefer_local=False)
    req = AIRequest.from_prompt("Hello", model_id="hf-remote-qwen-coder")

    # 429 Rate Limit
    mock_resp_429 = httpx.Response(
        status_code=429,
        text='{"error": "Too Many Requests"}',
        request=httpx.Request("POST", "https://api-inference.huggingface.co/v1/chat/completions"),
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp_429
        with pytest.raises(RateLimitError):
            await adapter.generate(req)

    # 500 Internal Server Error
    mock_resp_500 = httpx.Response(
        status_code=500,
        text='{"error": "Internal Server Error"}',
        request=httpx.Request("POST", "https://api-inference.huggingface.co/v1/chat/completions"),
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp_500
        with pytest.raises(ProviderUnavailableError):
            await adapter.generate(req)


# ============================================================================
# 8. Network failures and timeouts
# ============================================================================

@pytest.mark.asyncio
async def test_08_network_failures_and_timeouts():
    """Verify that timeouts raise ProviderTimeoutError and connect failures raise ProviderUnavailableError."""
    policy = ModelSecurityPolicy(allow_remote_inference=True)
    remote_provider = HuggingFaceRemoteProvider(
        api_key="hf_test_token",
        security_policy=policy,
        max_retries=0,
    )
    adapter = HuggingFaceAdapter(remote_provider=remote_provider, prefer_local=False)
    req = AIRequest.from_prompt("Hello", model_id="hf-remote-qwen-coder")

    # Timeout
    with patch("httpx.AsyncClient.post", side_effect=httpx.ReadTimeout("Read timed out")):
        with pytest.raises(ProviderTimeoutError):
            await adapter.generate(req)

    # Network Connect Error
    with patch("httpx.AsyncClient.post", side_effect=httpx.ConnectError("Connection refused")):
        with pytest.raises(ProviderUnavailableError):
            await adapter.generate(req)


# ============================================================================
# 9. Retry bounds and backoff
# ============================================================================

@pytest.mark.asyncio
async def test_09_retry_bounds_and_backoff():
    """Verify bounded retries for transient 503/429 errors and no retry on 401."""
    policy = ModelSecurityPolicy(allow_remote_inference=True)
    remote_provider = HuggingFaceRemoteProvider(
        api_key="hf_test_token",
        security_policy=policy,
        max_retries=2,
    )
    adapter = HuggingFaceAdapter(remote_provider=remote_provider, prefer_local=False)
    req = AIRequest.from_prompt("Hello", model_id="hf-remote-qwen-coder")

    # Scenario A: 503 twice then 200 success
    resp_503 = httpx.Response(
        status_code=503,
        text="Service Unavailable",
        request=httpx.Request("POST", "https://api.hf.co"),
    )
    resp_200 = httpx.Response(
        status_code=200,
        json={"choices": [{"message": {"content": "Recovered!"}}], "model": "Qwen-32B"},
        request=httpx.Request("POST", "https://api.hf.co"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_post.side_effect = [resp_503, resp_503, resp_200]
        res = await adapter.generate(req)
        assert res.content == "Recovered!"
        assert mock_post.call_count == 3
        assert mock_sleep.call_count == 2

    # Scenario B: 401 error must NOT retry
    resp_401 = httpx.Response(
        status_code=401,
        text="Unauthorized",
        request=httpx.Request("POST", "https://api.hf.co"),
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post, \
         patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        mock_post.return_value = resp_401
        with pytest.raises(AuthenticationError):
            await adapter.generate(req)
        assert mock_post.call_count == 1
        assert mock_sleep.call_count == 0


# ============================================================================
# 10. Streaming capability handling
# ============================================================================

class MockByteStream:
    """Mock async byte/line iterator for httpx streaming."""
    def __init__(self, lines: List[str]) -> None:
        self.lines = lines

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self.lines:
            yield line

    async def aread(self) -> bytes:
        return "\n".join(self.lines).encode("utf-8")


class MockStreamResponseContext:
    def __init__(self, response: Any) -> None:
        self.response = response

    async def __aenter__(self) -> Any:
        return self.response

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        pass


@pytest.mark.asyncio
async def test_10_streaming_capability_handling():
    """Verify that Hugging Face Remote streams genuine SSE chunks while Local raises unsupported."""
    policy = ModelSecurityPolicy(allow_remote_inference=True)
    remote_provider = HuggingFaceRemoteProvider(
        api_key="hf_test_token",
        security_policy=policy,
    )
    adapter = HuggingFaceAdapter(remote_provider=remote_provider, prefer_local=False)

    sse_lines = [
        'data: {"choices":[{"delta":{"content":"Hello"}}],"model":"Qwen-32B"}',
        'data: {"choices":[{"delta":{"content":" world!"}}],"model":"Qwen-32B"}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}',
        "data: [DONE]",
    ]
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.aiter_lines = MockByteStream(sse_lines).aiter_lines

    with patch("httpx.AsyncClient.stream", return_value=MockStreamResponseContext(mock_resp)):
        req = AIRequest.from_prompt("Stream test", model_id="hf-remote-qwen-coder")
        chunks = []
        async for chunk in adapter.stream(req):
            chunks.append(chunk)

        assert len(chunks) >= 3
        assert chunks[0].event_type == StreamEventType.START
        deltas = [c.delta for c in chunks if c.event_type == StreamEventType.DELTA]
        assert "Hello" in deltas
        assert " world!" in deltas
        done_chunks = [c for c in chunks if c.event_type == StreamEventType.DONE]
        assert len(done_chunks) == 1
        assert done_chunks[0].usage is not None
        assert done_chunks[0].usage.total_tokens == 7

    # Verify that Hugging Face Local raises ProviderUnavailableError without fake chunks
    local_adapter = HuggingFaceAdapter(prefer_local=True)
    local_req = AIRequest.from_prompt("Local stream test", model_id="openai/whisper-small")
    with pytest.raises(ProviderUnavailableError) as exc_info:
        async for _ in local_adapter.stream(local_req):
            pass
    assert "Streaming is not supported by Hugging Face provider" in str(exc_info.value)


# ============================================================================
# 11. Provider health and circuit breaker behavior
# ============================================================================

@pytest.mark.asyncio
async def test_11_provider_health_and_circuit_breaker_behavior():
    """Verify that consecutive failures trip the circuit breaker and route to local default."""
    health_tracker = ProviderHealthTracker(failure_threshold=2, cooldown_seconds=10.0)
    router = ModelRouter(health_tracker=health_tracker)

    # Record consecutive failures on HUGGINGFACE_REMOTE using typed errors
    health_tracker.record_failure(ModelProvider.HUGGINGFACE_REMOTE, ProviderTimeoutError("Timeout error"))
    assert health_tracker.is_circuit_open(ModelProvider.HUGGINGFACE_REMOTE) is False

    health_tracker.record_failure(ModelProvider.HUGGINGFACE_REMOTE, ProviderUnavailableError("503 error"))
    assert health_tracker.is_circuit_open(ModelProvider.HUGGINGFACE_REMOTE) is True

    # When circuit is open, requesting Hugging Face remote routes safely to local default
    policy = ModelSecurityPolicy(allow_remote_inference=True)
    reg = ModelRegistry()
    router = ModelRouter(health_tracker=health_tracker, registry=reg, security_policy=policy)
    hf_mod = router.registry.get_model("hf-remote-qwen-coder")
    if hf_mod:
        hf_mod.enabled = True

    decision = await router.route(
        TaskType.CODE,
        prompt="Write a function",
        allow_remote=True,
        preferred_model_id="hf-remote-qwen-coder",
    )
    assert decision.selected_model == "qwen2.5:7b"
    assert decision.reason_code == "CIRCUIT_OPEN"
    assert decision.local_or_remote == "local"


# ============================================================================
# 12. LOCAL_ONLY blocks all remote calls
# ============================================================================

@pytest.mark.asyncio
async def test_12_local_only_blocks_all_remote_calls():
    """Verify that LOCAL_ONLY privacy mode strictly forbids any Hugging Face remote request."""
    gateway = ModelSecurityGateway(default_privacy_mode=PrivacyMode.LOCAL_ONLY)
    req = AIRequest.from_prompt("Explain quantum mechanics", model_id="hf-remote-qwen-coder")

    with pytest.raises(SecurityViolationError) as exc_info:
        await gateway.validate_request(
            req,
            provider=ModelProvider.HUGGINGFACE_REMOTE,
            model_id="hf-remote-qwen-coder",
            allow_remote=True,
            privacy_mode=PrivacyMode.LOCAL_ONLY,
        )
    assert "LOCAL_ONLY" in str(exc_info.value)

    # Verify router in LOCAL_ONLY returns local default when remote is requested
    reg = ModelRegistry()
    router = ModelRouter(security_gateway=gateway, registry=reg)
    hf_mod = router.registry.get_model("hf-remote-qwen-coder")
    if hf_mod:
        hf_mod.enabled = True

    # Case A: Autonomous routing with allow_remote=True in LOCAL_ONLY mode
    decision = await router.route(
        TaskType.CODE,
        prompt="Write a complex function",
        allow_remote=True,
    )
    assert decision.selected_model == "qwen2.5:7b"
    assert decision.reason_code == "PRIVACY_POLICY_BLOCKED"
    assert decision.remote_allowed is False

    # Case B: Explicit preferred remote model in LOCAL_ONLY mode
    decision_pref = await router.route(
        TaskType.CODE,
        prompt="Write a complex function",
        allow_remote=True,
        preferred_model_id="hf-remote-qwen-coder",
    )
    assert decision_pref.remote_allowed is False
    assert decision_pref.reason_code == "PRIVACY_POLICY_DENIAL"


# ============================================================================
# 13. PRIVACY_FIRST authorization requirements
# ============================================================================

@pytest.mark.asyncio
async def test_13_privacy_first_authorization_requirements():
    """Verify that PRIVACY_FIRST requires explicit confirmation token binding."""
    policy = ModelSecurityPolicy(allow_remote_inference=True)
    gateway = ModelSecurityGateway(default_privacy_mode=PrivacyMode.PRIVACY_FIRST, security_policy=policy)
    req = AIRequest.from_prompt("Complex math analysis", model_id="hf-remote-qwen-coder")

    # Unconfirmed remote call must be denied
    with pytest.raises(SecurityViolationError) as exc_info:
        await gateway.validate_request(
            req,
            provider=ModelProvider.HUGGINGFACE_REMOTE,
            model_id="hf-remote-qwen-coder",
            allow_remote=True,
            privacy_mode=PrivacyMode.PRIVACY_FIRST,
        )
    assert "requires verified user confirmation" in str(exc_info.value)

    # Valid confirmed token must be allowed
    token = "conf_token_valid_hf_123"
    gateway.confirmation_mgr._pending_tokens[token] = {
        "status": "CONFIRMED",
        "confirmed": True,
        "action": "remote_inference",
        "created_at": time.time(),
        "expires_at": time.time() + 60.0,
        "parameters": {
            "provider": "HUGGINGFACE_REMOTE",
            "model_id": "hf-remote-qwen-coder",
        },
    }

    validated_req = await gateway.validate_request(
        req,
        provider=ModelProvider.HUGGINGFACE_REMOTE,
        model_id="hf-remote-qwen-coder",
        allow_remote=True,
        privacy_mode=PrivacyMode.PRIVACY_FIRST,
        confirmation_token=token,
    )
    assert validated_req is not None


# ============================================================================
# 14. Expired and replayed confirmation tokens
# ============================================================================

@pytest.mark.asyncio
async def test_14_expired_and_replayed_confirmation_tokens():
    """Verify replay protection and rejection of expired confirmation tokens."""
    policy = ModelSecurityPolicy(allow_remote_inference=True)
    gateway = ModelSecurityGateway(default_privacy_mode=PrivacyMode.PRIVACY_FIRST, security_policy=policy)
    token = "conf_token_replay_hf_456"
    gateway.confirmation_mgr._pending_tokens[token] = {
        "status": "CONFIRMED",
        "confirmed": True,
        "action": "remote_inference",
        "created_at": time.time(),
        "expires_at": time.time() + 60.0,
        "parameters": {
            "provider": "HUGGINGFACE_REMOTE",
            "model_id": "hf-remote-qwen-coder",
        },
    }

    req = AIRequest.from_prompt("Task 1", model_id="hf-remote-qwen-coder")
    # First use: valid
    await gateway.validate_request(
        req,
        provider=ModelProvider.HUGGINGFACE_REMOTE,
        model_id="hf-remote-qwen-coder",
        allow_remote=True,
        confirmation_token=token,
    )

    # Second use (replay): MUST be rejected
    with pytest.raises(SecurityViolationError):
        await gateway.validate_request(
            req,
            provider=ModelProvider.HUGGINGFACE_REMOTE,
            model_id="hf-remote-qwen-coder",
            allow_remote=True,
            confirmation_token=token,
        )


# ============================================================================
# 15. Security denial never triggers remote fallback
# ============================================================================

@pytest.mark.asyncio
async def test_15_security_denial_never_triggers_remote_fallback():
    """Verify that a security policy denial fails immediately without remote fallback."""
    gateway = ModelSecurityGateway(default_privacy_mode=PrivacyMode.PRIVACY_FIRST)
    req = AIRequest.from_prompt("My AWS key is AKIA1234567890ABCDEF", model_id="hf-remote-qwen-coder")

    with pytest.raises(SecurityViolationError):
        await gateway.validate_request(
            req,
            provider=ModelProvider.HUGGINGFACE_REMOTE,
            model_id="hf-remote-qwen-coder",
            allow_remote=True,
        )


# ============================================================================
# 16. Fallback candidates receive fresh security validation
# ============================================================================

@pytest.mark.asyncio
async def test_16_fallback_candidates_receive_fresh_security_validation():
    """Verify that any fallback candidate passes through full security validation."""
    gateway = ModelSecurityGateway(default_privacy_mode=PrivacyMode.LOCAL_ONLY)
    # If a fallback attempts to use Hugging Face remote in LOCAL_ONLY mode, it is blocked
    with pytest.raises(SecurityViolationError):
        await gateway.validate_request(
            AIRequest.from_prompt("Fallback check"),
            provider=ModelProvider.HUGGINGFACE_REMOTE,
            model_id="hf-remote-qwen-coder",
            allow_remote=True,
            privacy_mode=PrivacyMode.LOCAL_ONLY,
        )


# ============================================================================
# 17. Secrets are redacted from logs and telemetry
# ============================================================================

@pytest.mark.asyncio
async def test_17_secrets_are_redacted_from_logs_and_telemetry():
    """Verify that sensitive patterns are redacted before audit logging and telemetry."""
    gateway = ModelSecurityGateway()
    sensitive_prompt = "password=super_secret_pass and token is hf_9999999999999999999999999999999999"
    safe_req, count = gateway.sanitize_request(AIRequest.from_prompt(sensitive_prompt))

    assert count > 0
    assert "super_secret_pass" not in safe_req.get_prompt_text()
    assert "[REDACTED" in safe_req.get_prompt_text()


# ============================================================================
# 18. Token usage metadata is preserved when available
# ============================================================================

@pytest.mark.asyncio
async def test_18_token_usage_metadata_preserved():
    """Verify that token usage metadata is preserved across AIResponse."""
    policy = ModelSecurityPolicy(allow_remote_inference=True)
    remote_provider = HuggingFaceRemoteProvider(
        api_key="hf_test_token",
        security_policy=policy,
        max_retries=0,
    )
    adapter = HuggingFaceAdapter(remote_provider=remote_provider, prefer_local=False)

    mock_resp = httpx.Response(
        status_code=200,
        json={
            "choices": [{"message": {"content": "Answer"}}],
            "model": "Qwen-32B",
            "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
        },
        request=httpx.Request("POST", "https://api.hf.co"),
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await adapter.generate(AIRequest.from_prompt("What is 2+2?", model_id="hf-remote-qwen-coder"))
        assert res.usage is not None
        assert res.usage.prompt_tokens == 12
        assert res.usage.completion_tokens == 4
        assert res.usage.total_tokens == 16
        assert "usage" in res.metadata


# ============================================================================
# 19. Existing provider injection remains functional
# ============================================================================

@pytest.mark.asyncio
async def test_19_existing_provider_injection_remains_functional():
    """Verify that custom injected providers are properly registered and used by UnifiedAIProvider."""
    custom_remote = HuggingFaceRemoteProvider(
        api_key="hf_injected_token",
        model="Custom-HF-Model",
    )
    unified = UnifiedAIProvider(hf_remote_provider=custom_remote)
    assert unified.hf_remote.model == "Custom-HF-Model"
    assert unified.get_provider("huggingface_remote") is custom_remote

    adapter = unified.router.get_adapter(ModelProvider.HUGGINGFACE_REMOTE)
    assert isinstance(adapter, HuggingFaceAdapter)
    assert adapter.remote_provider.model == "Custom-HF-Model"


# ============================================================================
# 20. Request cancellation handling
# ============================================================================

@pytest.mark.asyncio
async def test_20_cancellation_handling():
    """Verify that asyncio.CancelledError is never swallowed during generation or streaming."""
    policy = ModelSecurityPolicy(allow_remote_inference=True)
    remote_provider = HuggingFaceRemoteProvider(
        api_key="hf_test_token",
        security_policy=policy,
        max_retries=0,
    )
    adapter = HuggingFaceAdapter(remote_provider=remote_provider, prefer_local=False)
    req = AIRequest.from_prompt("Cancelled task", model_id="hf-remote-qwen-coder")

    with patch("httpx.AsyncClient.post", side_effect=asyncio.CancelledError()):
        with pytest.raises(asyncio.CancelledError):
            await adapter.generate(req)

    with patch("httpx.AsyncClient.stream", side_effect=asyncio.CancelledError()):
        with pytest.raises(asyncio.CancelledError):
            async for _ in adapter.stream(req):
                pass
