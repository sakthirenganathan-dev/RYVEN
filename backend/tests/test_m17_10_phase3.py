"""RYVEN 3.0 — M17.10 Phase 3 Intelligent Multi-Model Dynamic Router & Controlled Fallbacks Tests.

Comprehensive test suite verifying:
- Local-first deterministic routing (Qwen default)
- Task capability matching (Vision, OCR, Embedding, Speech)
- Deterministic explainable complexity classification
- Remote routing safety gates & privacy enforcement
- Thread-safe bounded provider health tracking & zero secret leakage
- Configurable circuit breakers (thresholds, cooldowns, half-open probing)
- Controlled execution fallbacks (depth <= 1, no cycles, no non-retryable fallback)
- Streaming fallback safety (no provider switching after visible output)
- Configuration validation and backward compatibility
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.ai.adapters import GrokAdapter, HuggingFaceAdapter, OllamaAdapter
from app.ai.config import RouterConfig
from app.ai.contracts import (
    AIProviderError,
    AIRequest,
    AIResponse,
    AIStreamChunk,
    AuthenticationError,
    ProviderAdapter,
    ProviderInvalidRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    SecurityViolationError,
    StreamEventType,
)
from app.ai.health import (
    CircuitBreaker,
    CircuitState,
    ProviderHealthRecord,
    ProviderHealthTracker,
)
from app.ai.models import (
    ComplexityTier,
    ModelProfile,
    ModelProvider,
    RoutingDecision,
    TaskType,
)
from app.ai.registry import ModelRegistry
from app.ai.router import (
    ModelRouter,
    estimate_task_complexity,
    model_router,
)
from app.ai.security import ModelSecurityPolicy, ModelSecurityViolationError
from app.ai.unified import UnifiedAIProvider, unified_ai_provider


@pytest.fixture(autouse=True)
def reset_health_tracker_fixture():
    """Ensure clean health tracker state across tests."""
    from app.ai.health import provider_health_tracker
    provider_health_tracker.reset()
    yield
    provider_health_tracker.reset()



# ============================================================================
# MOCK ADAPTER HELPER
# ============================================================================

class MockTestAdapter(ProviderAdapter):
    """Test adapter with configurable response, errors, and streaming generator."""

    def __init__(
        self,
        provider_id: ModelProvider = ModelProvider.OLLAMA,
        model_name: str = "qwen2.5:7b",
    ) -> None:
        self._provider_id = provider_id
        self.model_name = model_name
        self.generate_calls: List[AIRequest] = []
        self.stream_calls: List[AIRequest] = []
        self.fail_with: Optional[Exception] = None
        self.stream_fail_after_chunks: Optional[int] = None
        self.response_text: str = "Test response content"

    @property
    def provider_id(self) -> ModelProvider:
        return self._provider_id

    async def is_available(self) -> bool:
        return self.fail_with is None

    async def health_check(self) -> Dict[str, Any]:
        return {"available": self.fail_with is None, "provider": self._provider_id.value}

    async def generate(self, request: AIRequest) -> AIResponse:
        self.generate_calls.append(request)
        if self.fail_with:
            raise self.fail_with
        return AIResponse(
            content=self.response_text,
            model_id=request.model_id or self.model_name,
            provider=self._provider_id,
            finish_reason="stop",
            latency_ms=42.0,
        )

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        self.stream_calls.append(request)
        if self.fail_with and self.stream_fail_after_chunks is None:
            raise self.fail_with

        # Emit start
        yield AIStreamChunk(
            event_type=StreamEventType.START,
            model_id=request.model_id or self.model_name,
            provider=self._provider_id,
        )

        tokens = ["Hello", " world", " from", " test"]
        for idx, tok in enumerate(tokens):
            if self.stream_fail_after_chunks is not None and idx >= self.stream_fail_after_chunks:
                if self.fail_with:
                    raise self.fail_with
            yield AIStreamChunk(
                event_type=StreamEventType.DELTA,
                delta=tok,
                model_id=request.model_id or self.model_name,
                provider=self._provider_id,
            )

        if self.fail_with:
            raise self.fail_with

        yield AIStreamChunk(
            event_type=StreamEventType.DONE,
            model_id=request.model_id or self.model_name,
            provider=self._provider_id,
            finish_reason="stop",
        )


# ============================================================================
# 1. QWEN REMAINS THE DEFAULT
# ============================================================================

@pytest.mark.asyncio
async def test_qwen_remains_the_default():
    router = ModelRouter()
    decision = await router.route(
        task_type=TaskType.GENERAL_REASONING,
        prompt="Tell me about the solar system",
    )
    assert decision.selected_model == "qwen2.5:7b"
    assert decision.provider == "OLLAMA"
    assert decision.local_or_remote == "local"
    assert decision.reason_code == "LOCAL_DEFAULT"
    assert decision.remote_allowed is False


# ============================================================================
# 2. SIMPLE TASKS ROUTE LOCALLY
# ============================================================================

@pytest.mark.asyncio
async def test_simple_tasks_route_locally():
    router = ModelRouter()
    simple_prompts = [
        "What is 2 + 2?",
        "Say hi",
        "Summarize: The quick brown fox jumps over the lazy dog.",
    ]
    for p in simple_prompts:
        dec = await router.route(task_type=TaskType.GENERAL_REASONING, prompt=p)
        assert dec.selected_model == "qwen2.5:7b"
        assert dec.provider == "OLLAMA"
        assert dec.estimated_complexity == "LOW"
        assert dec.local_or_remote == "local"


# ============================================================================
# 3. CODING AND PLANNING ROUTES PRESERVE COMPATIBILITY
# ============================================================================

@pytest.mark.asyncio
async def test_coding_and_planning_routes_preserve_compatibility():
    router = ModelRouter()
    for task in (TaskType.CODE, TaskType.PLANNING):
        dec = await router.route(task_type=task, prompt="Write a python script to parse CSV")
        assert dec.selected_model == "qwen2.5:7b"
        assert dec.provider == "OLLAMA"
        assert dec.local_or_remote == "local"
        assert dec.fallback == "qwen2.5:7b"


# ============================================================================
# 4. VISION CAPABILITY MATCHING WORKS
# ============================================================================

@pytest.mark.asyncio
async def test_vision_capability_matching():
    registry = ModelRegistry()
    # Uninstalled by default -> falls back to Qwen with explanation
    router = ModelRouter(registry=registry)
    dec_uninstalled = await router.route(task_type=TaskType.VISION, prompt="What is in this image?")
    assert dec_uninstalled.selected_model == "qwen2.5:7b"
    assert dec_uninstalled.reason_code == "LOCAL_DEFAULT"

    # Simulate installing a local vision model
    registry.set_installed("llava:7b", True)
    dec_installed = await router.route(task_type=TaskType.VISION, prompt="Inspect UI")
    assert dec_installed.selected_model == "llava:7b"
    assert dec_installed.provider == "OLLAMA"
    assert dec_installed.reason_code == "TASK_CAPABILITY_MATCH"


# ============================================================================
# 5. COMPLEXITY CLASSIFICATION IS DETERMINISTIC
# ============================================================================

def test_complexity_classification_is_deterministic():
    # Low complexity
    tier1, ctx1, lat1 = estimate_task_complexity("Hello", TaskType.GENERAL_REASONING)
    tier2, ctx2, lat2 = estimate_task_complexity("Hello", TaskType.GENERAL_REASONING)
    assert tier1 == ComplexityTier.LOW
    assert tier1 == tier2
    assert ctx1 == ctx2
    assert lat1 is True  # short reasoning has low latency tolerance

    # Medium complexity
    code_prompt = "def add(a, b):\n    return a + b"
    t_code, _, _ = estimate_task_complexity(code_prompt, TaskType.CODE)
    assert t_code == ComplexityTier.MEDIUM

    # High complexity (algorithmic indicators + code snippet)
    high_prompt = (
        "def solve(n):\n"
        "    # optimize recursive dynamic programming with memoization algorithm\n"
        "    pass"
    )
    t_high, _, _ = estimate_task_complexity(high_prompt, TaskType.CODE)
    assert t_high == ComplexityTier.HIGH

    # High complexity via structured schema
    complex_schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string"},
            "b": {"type": "number"},
            "c": {"type": "array"},
            "d": {"type": "object"},
            "e": {"type": "boolean"},
        },
    }
    t_schema, _, _ = estimate_task_complexity("Generate json", TaskType.GENERAL_REASONING, complex_schema)
    assert t_schema == ComplexityTier.HIGH


# ============================================================================
# 6. REMOTE PROVIDERS REMAIN DISABLED BY DEFAULT
# ============================================================================

def test_remote_providers_remain_disabled_by_default():
    registry = ModelRegistry()
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    assert grok.enabled is False
    assert grok.sensitive_data_allowed is False

    hf = registry.get_model("hf-remote-qwen-coder")
    assert hf is not None
    assert hf.enabled is False
    assert hf.sensitive_data_allowed is False

    cfg = RouterConfig()
    assert cfg.allow_remote_inference is False


# ============================================================================
# 7. REMOTE SELECTION REQUIRES EXPLICIT AUTHORIZATION
# ============================================================================

@pytest.mark.asyncio
async def test_remote_selection_requires_explicit_authorization():
    registry = ModelRegistry()
    # Enable grok in catalog
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True

    policy = ModelSecurityPolicy(allow_remote_inference=False)
    router = ModelRouter(registry=registry, security_policy=policy)

    # 1. High complexity prompt but allow_remote=False -> remains local
    high_prompt = "optimize recursive dynamic programming algorithm with asymptotic complexity proof"
    dec = await router.route(
        task_type=TaskType.GENERAL_REASONING,
        prompt=high_prompt,
        allow_remote=False,
    )
    assert dec.selected_model == "qwen2.5:7b"
    assert dec.local_or_remote == "local"
    assert dec.reason_code == "LOCAL_DEFAULT"

    # 2. allow_remote=True AND policy.allow_remote_inference=True -> routes to remote
    policy.allow_remote_inference = True
    dec_auth = await router.route(
        task_type=TaskType.GENERAL_REASONING,
        prompt=high_prompt,
        allow_remote=True,
    )
    assert dec_auth.selected_model == "grok-2-latest"
    assert dec_auth.provider == "GROK"
    assert dec_auth.local_or_remote == "remote"
    assert dec_auth.reason_code == "REMOTE_EXPLICITLY_ALLOWED"


# ============================================================================
# 8. PRIVACY-POLICY DENIAL PREVENTS REMOTE CALLS
# ============================================================================

@pytest.mark.asyncio
async def test_privacy_policy_denial_prevents_remote_calls():
    registry = ModelRegistry()
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True

    policy = ModelSecurityPolicy(allow_remote_inference=True)
    router = ModelRouter(registry=registry, security_policy=policy)

    sensitive_prompts = [
        "optimize this algorithm: api_key = 'xai-1234567890abcdef1234567890' check security",
        "solve recurrence relation: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.token",
        "check database connection: .env DATABASE_URL=postgres://root:secret@localhost",
    ]

    for p in sensitive_prompts:
        dec = await router.route(
            task_type=TaskType.GENERAL_REASONING,
            prompt=p,
            allow_remote=True,
        )
        assert dec.selected_model == "qwen2.5:7b"
        assert dec.provider == "OLLAMA"
        assert dec.local_or_remote == "local"
        assert dec.reason_code == "PRIVACY_POLICY_BLOCKED"


# ============================================================================
# 9. PROVIDER SUCCESS AND LATENCY TRACKING
# ============================================================================

def test_provider_success_and_latency_tracking():
    tracker = ProviderHealthTracker()
    tracker.record_success(ModelProvider.OLLAMA, 120.5)
    tracker.record_success(ModelProvider.OLLAMA, 80.5)

    summary = tracker.get_health_summary(ModelProvider.OLLAMA)
    assert summary["success_count"] == 2
    assert summary["failure_count"] == 0
    assert summary["consecutive_failures"] == 0
    assert summary["consecutive_successes"] == 2
    assert summary["avg_latency_ms"] == 100.5
    assert summary["has_succeeded"] is True


# ============================================================================
# 10. PROVIDER FAILURE TRACKING
# ============================================================================

def test_provider_failure_tracking():
    tracker = ProviderHealthTracker()
    timeout_err = ProviderTimeoutError("Timed out after 60s")
    tracker.record_failure(ModelProvider.GROK, timeout_err, 60000.0)

    summary = tracker.get_health_summary(ModelProvider.GROK)
    assert summary["failure_count"] == 1
    assert summary["timeout_count"] == 1
    assert summary["consecutive_failures"] == 1
    assert summary["last_error_type"] == "PROVIDER_TIMEOUT"


# ============================================================================
# 11. CIRCUIT BREAKER OPENS AT CONFIGURED THRESHOLD
# ============================================================================

def test_circuit_breaker_opens_at_threshold():
    tracker = ProviderHealthTracker(failure_threshold=3, cooldown_seconds=60.0)

    assert tracker.is_circuit_open("GROK") is False
    tracker.record_failure("GROK", ProviderUnavailableError("Down"))
    assert tracker.is_circuit_open("GROK") is False
    tracker.record_failure("GROK", ProviderUnavailableError("Down"))
    assert tracker.is_circuit_open("GROK") is False
    tracker.record_failure("GROK", ProviderUnavailableError("Down"))

    # Trip threshold reached
    assert tracker.is_circuit_open("GROK") is True
    assert tracker.get_circuit_state("GROK") == CircuitState.OPEN


# ============================================================================
# 12. OPEN CIRCUIT SUPPRESSES ORDINARY CALLS
# ============================================================================

@pytest.mark.asyncio
async def test_open_circuit_suppresses_ordinary_calls():
    tracker = ProviderHealthTracker(failure_threshold=2)
    tracker.record_failure("GROK", ProviderUnavailableError("Fail 1"))
    tracker.record_failure("GROK", ProviderUnavailableError("Fail 2"))
    assert tracker.can_execute("GROK") is False

    registry = ModelRegistry()
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True

    policy = ModelSecurityPolicy(allow_remote_inference=True)
    router = ModelRouter(registry=registry, security_policy=policy, health_tracker=tracker)

    # When circuit is open, route suppresses Grok and returns local Qwen
    dec = await router.route(
        task_type=TaskType.GENERAL_REASONING,
        prompt="optimize recursive dynamic programming algorithm",
        allow_remote=True,
    )
    assert dec.selected_model == "qwen2.5:7b"
    assert dec.provider == "OLLAMA"
    assert dec.reason_code == "CIRCUIT_OPEN"


# ============================================================================
# 13. HALF-OPEN PROBE AND RECOVERY BEHAVIOR
# ============================================================================

def test_half_open_probe_and_recovery_behavior():
    breaker = CircuitBreaker(threshold=2, cooldown_seconds=0.05)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN
    assert breaker.can_execute() is False

    # Simulate cooldown passing deterministically
    import time
    breaker._last_failure_time = time.monotonic() - 100.0

    # State transitions to HALF_OPEN
    assert breaker.state == CircuitState.HALF_OPEN
    # First probe allowed
    assert breaker.can_execute() is True
    # Second probe rejected while probe in-flight
    assert breaker.can_execute() is False

    # Probe succeeds: breaker resets to CLOSED
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.can_execute() is True


# ============================================================================
# 14. TIMEOUT FALLBACK
# ============================================================================

@pytest.mark.asyncio
async def test_timeout_fallback():
    router = ModelRouter()
    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")
    mock_grok = MockTestAdapter(ModelProvider.GROK, "grok-2-latest")
    mock_grok.fail_with = ProviderTimeoutError("Grok request timed out")

    router.register_adapter("OLLAMA", mock_ollama)
    router.register_adapter("GROK", mock_grok)

    # Force route to Grok via preference
    registry = router.registry
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt("Explain gravity")
    resp = await router.execute(req, preferred_model_id="grok-2-latest", allow_remote=True)

    assert resp.content == "Test response content"
    assert resp.model_id == "qwen2.5:7b"
    assert resp.metadata.get("fallback_occurred") is True
    assert resp.metadata.get("fallback_from_provider") == "GROK"
    assert resp.metadata.get("fallback_reason_code") == "PROVIDER_TIMEOUT"


# ============================================================================
# 15. PROVIDER-UNAVAILABLE FALLBACK
# ============================================================================

@pytest.mark.asyncio
async def test_provider_unavailable_fallback():
    router = ModelRouter()
    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")
    mock_grok = MockTestAdapter(ModelProvider.GROK, "grok-2-latest")
    mock_grok.fail_with = ProviderUnavailableError("Grok endpoint unreachable")

    router.register_adapter("OLLAMA", mock_ollama)
    router.register_adapter("GROK", mock_grok)

    registry = router.registry
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt("Write a poem")
    resp = await router.execute(req, preferred_model_id="grok-2-latest", allow_remote=True)

    assert resp.content == "Test response content"
    assert resp.metadata.get("fallback_occurred") is True
    assert resp.metadata.get("fallback_from_provider") == "GROK"


# ============================================================================
# 16. APPROPRIATE RATE-LIMIT BEHAVIOR
# ============================================================================

@pytest.mark.asyncio
async def test_appropriate_rate_limit_fallback():
    router = ModelRouter()
    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")
    mock_grok = MockTestAdapter(ModelProvider.GROK, "grok-2-latest")
    mock_grok.fail_with = RateLimitError("Rate limit exceeded 429")

    router.register_adapter("OLLAMA", mock_ollama)
    router.register_adapter("GROK", mock_grok)

    registry = router.registry
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt("Quick analysis")
    resp = await router.execute(req, preferred_model_id="grok-2-latest", allow_remote=True)

    assert resp.content == "Test response content"
    assert resp.metadata.get("fallback_occurred") is True
    assert resp.metadata.get("fallback_reason_code") == "RATE_LIMIT_EXCEEDED"


# ============================================================================
# 17. NO FALLBACK ON AUTHENTICATION FAILURE
# ============================================================================

@pytest.mark.asyncio
async def test_no_fallback_on_authentication_failure():
    router = ModelRouter()
    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")
    mock_grok = MockTestAdapter(ModelProvider.GROK, "grok-2-latest")
    mock_grok.fail_with = AuthenticationError("Invalid xAI API Key")

    router.register_adapter("OLLAMA", mock_ollama)
    router.register_adapter("GROK", mock_grok)

    registry = router.registry
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt("Explain relativity")
    with pytest.raises(AuthenticationError):
        await router.execute(req, preferred_model_id="grok-2-latest", allow_remote=True)

    # Ensure fallback adapter was NOT invoked
    assert len(mock_ollama.generate_calls) == 0


# ============================================================================
# 18. NO FALLBACK ON SECURITY VIOLATION
# ============================================================================

@pytest.mark.asyncio
async def test_no_fallback_on_security_violation():
    router = ModelRouter()
    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")
    mock_grok = MockTestAdapter(ModelProvider.GROK, "grok-2-latest")
    mock_grok.fail_with = SecurityViolationError("Data security boundary crossed")

    router.register_adapter("OLLAMA", mock_ollama)
    router.register_adapter("GROK", mock_grok)

    registry = router.registry
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt("Process file")
    with pytest.raises(SecurityViolationError):
        await router.execute(req, preferred_model_id="grok-2-latest", allow_remote=True)

    assert len(mock_ollama.generate_calls) == 0


# ============================================================================
# 19. NO FALLBACK ON CANCELLATION
# ============================================================================

@pytest.mark.asyncio
async def test_no_fallback_on_cancellation():
    router = ModelRouter()
    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")
    mock_grok = MockTestAdapter(ModelProvider.GROK, "grok-2-latest")
    mock_grok.fail_with = asyncio.CancelledError()

    router.register_adapter("OLLAMA", mock_ollama)
    router.register_adapter("GROK", mock_grok)

    registry = router.registry
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt("User cancelled mid-execution")
    with pytest.raises(asyncio.CancelledError):
        await router.execute(req, preferred_model_id="grok-2-latest", allow_remote=True)

    assert len(mock_ollama.generate_calls) == 0


# ============================================================================
# 20. FALLBACK DEPTH NEVER EXCEEDS ONE
# ============================================================================

@pytest.mark.asyncio
async def test_fallback_depth_never_exceeds_one():
    router = ModelRouter()
    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")
    mock_ollama.fail_with = ProviderUnavailableError("Ollama also down")
    mock_grok = MockTestAdapter(ModelProvider.GROK, "grok-2-latest")
    mock_grok.fail_with = ProviderUnavailableError("Grok down")

    router.register_adapter("OLLAMA", mock_ollama)
    router.register_adapter("GROK", mock_grok)

    registry = router.registry
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt("Test failure cascade")
    # Must attempt Grok (depth 0), fallback to Ollama (depth 1), and then fail
    with pytest.raises(ProviderUnavailableError) as exc:
        await router.execute(req, preferred_model_id="grok-2-latest", allow_remote=True)

    assert "Ollama also down" in str(exc.value)
    assert len(mock_grok.generate_calls) == 1
    assert len(mock_ollama.generate_calls) == 1  # exactly 1 fallback attempt, no further retry


# ============================================================================
# 21. NO FALLBACK CYCLES
# ============================================================================

@pytest.mark.asyncio
async def test_no_fallback_cycles():
    router = ModelRouter()
    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")
    mock_ollama.fail_with = ProviderUnavailableError("Ollama down")

    router.register_adapter("OLLAMA", mock_ollama)

    # When Ollama is primary and fails, fallback cannot cycle back to Ollama
    req = AIRequest.from_prompt("Hello")
    with pytest.raises(ProviderUnavailableError):
        await router.execute(req)

    # Only 1 execution occurred, no infinite fallback cycle
    assert len(mock_ollama.generate_calls) == 1


# ============================================================================
# 22. STABLE SANITIZED ERRORS
# ============================================================================

def test_stable_sanitized_errors():
    secret_msg = "Connection failed to xai-super_secret_key_12345678"
    err = ProviderUnavailableError(secret_msg)
    assert "xai-super_secret_key_12345678" not in err.message
    assert "[REDACTED_API_KEY]" in err.message
    assert err.error_code == "PROVIDER_UNAVAILABLE"


# ============================================================================
# 23. ROUTING DECISION COMPATIBILITY
# ============================================================================

def test_routing_decision_compatibility():
    # Legacy instantiation without Phase 3 fields must work seamlessly
    dec = RoutingDecision(
        selected_model="qwen2.5:7b",
        provider="OLLAMA",
        task_type=TaskType.GENERAL_REASONING,
        reason="Default test",
    )
    assert dec.reason_code == "LOCAL_DEFAULT"
    assert dec.fallback_eligible is True
    assert dec.fallback_model == "qwen2.5:7b"
    assert dec.estimated_complexity == "LOW"

    # Serialization compatibility
    dump = dec.model_dump()
    assert dump["selected_model"] == "qwen2.5:7b"
    assert dump["reason_code"] == "LOCAL_DEFAULT"


# ============================================================================
# 24. NO SECRET LEAKAGE IN HEALTH RECORDS
# ============================================================================

def test_no_secret_leakage_in_health_records():
    tracker = ProviderHealthTracker()
    secret_err = ProviderUnavailableError("Failed with secret Bearer my_secret_token_12345678")
    tracker.record_failure("GROK", secret_err)

    summary = tracker.get_health_summary("GROK")
    summary_str = str(summary)
    assert "my_secret_token_12345678" not in summary_str
    assert "Bearer" not in summary_str
    assert summary["last_error_type"] == "PROVIDER_UNAVAILABLE"


# ============================================================================
# 25. NO UNBOUNDED HISTORY
# ============================================================================

def test_no_unbounded_history():
    rec = ProviderHealthRecord("OLLAMA", max_latencies=10)
    for i in range(50):
        rec.record_success(float(i))

    summary = rec.get_summary()
    assert summary["recent_samples_count"] == 10
    assert summary["success_count"] == 50


# ============================================================================
# 26. NO DUPLICATE ROUTER OR REGISTRY
# ============================================================================

def test_no_duplicate_router_or_registry():
    # model_router is the single global authoritative singleton
    from app.ai.router import model_router as default_mr
    from app.ai.registry import model_registry as default_reg

    assert default_mr.registry is default_reg
    assert isinstance(default_mr, ModelRouter)


# ============================================================================
# 27. STREAMING COMPATIBILITY
# ============================================================================

@pytest.mark.asyncio
async def test_streaming_compatibility():
    router = ModelRouter()
    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")
    router.register_adapter("OLLAMA", mock_ollama)

    req = AIRequest.from_prompt("Stream test")
    chunks = []
    async for chunk in router.stream(req):
        chunks.append(chunk)

    assert len(chunks) >= 3
    assert chunks[0].is_start is True
    assert any(c.is_delta and c.delta for c in chunks)
    assert chunks[-1].is_done is True


# ============================================================================
# 28. NO PROVIDER SWITCHING AFTER VISIBLE STREAM OUTPUT
# ============================================================================

@pytest.mark.asyncio
async def test_no_provider_switching_after_visible_stream_output():
    router = ModelRouter()
    mock_grok = MockTestAdapter(ModelProvider.GROK, "grok-2-latest")
    # Fail after 2 chunks have already been emitted
    mock_grok.stream_fail_after_chunks = 2
    mock_grok.fail_with = ProviderTimeoutError("Stream timed out mid-response")

    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")

    router.register_adapter("GROK", mock_grok)
    router.register_adapter("OLLAMA", mock_ollama)

    registry = router.registry
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt("Stream with failure")
    chunks = []
    with pytest.raises(ProviderTimeoutError):
        async for chunk in router.stream(req, preferred_model_id="grok-2-latest", allow_remote=True):
            chunks.append(chunk)

    # Must contain delta chunks from Grok and terminal error chunk
    assert any(c.is_delta for c in chunks)
    assert chunks[-1].is_error is True
    # Ollama must NOT have been called mid-stream
    assert len(mock_ollama.stream_calls) == 0


# ============================================================================
# 29. EXISTING GENERATE BEHAVIOR REMAINS COMPATIBLE
# ============================================================================

@pytest.mark.asyncio
async def test_existing_generate_behavior_remains_compatible():
    # UnifiedAIProvider.generate() works compatibly
    mock_ollama = MagicMock()
    mock_ollama.model = "qwen2.5:7b"
    mock_ollama.generate = AsyncMock(
        return_value=AIResponse(
            content="Unified generation response",
            model_id="qwen2.5:7b",
            provider=ModelProvider.OLLAMA,
        )
    )

    unified = UnifiedAIProvider(ollama_provider=mock_ollama)
    resp = await unified.generate(prompt="Hello Unified")
    assert resp.content == "Unified generation response"
    assert resp.model == "qwen2.5:7b"


# ============================================================================
# 30. OFFLINE OPERATION REMAINS LOCAL
# ============================================================================

@pytest.mark.asyncio
async def test_offline_operation_remains_local():
    policy = ModelSecurityPolicy(allow_remote_inference=False)
    router = ModelRouter(security_policy=policy)

    for task in TaskType:
        dec = await router.route(task_type=task, prompt="Offline query")
        assert dec.local_or_remote == "local"
        assert dec.remote_allowed is False


# ============================================================================
# 31. FALLBACK-DISABLED CONFIGURATION WORKS
# ============================================================================

@pytest.mark.asyncio
async def test_fallback_disabled_configuration_works():
    cfg = RouterConfig(fallback_enabled=False)
    router = ModelRouter(config=cfg)

    mock_ollama = MockTestAdapter(ModelProvider.OLLAMA, "qwen2.5:7b")
    mock_grok = MockTestAdapter(ModelProvider.GROK, "grok-2-latest")
    mock_grok.fail_with = ProviderUnavailableError("Grok unavailable")

    router.register_adapter("OLLAMA", mock_ollama)
    router.register_adapter("GROK", mock_grok)

    registry = router.registry
    grok = registry.get_model("grok-2-latest")
    assert grok is not None
    grok.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt("Fallback test")
    with pytest.raises(ProviderUnavailableError):
        await router.execute(req, preferred_model_id="grok-2-latest", allow_remote=True)

    assert len(mock_ollama.generate_calls) == 0


# ============================================================================
# 32. INVALID CONFIGURATION IS REJECTED SAFELY
# ============================================================================

def test_invalid_configuration_is_rejected_safely():
    with pytest.raises(ValueError, match="capped at 1"):
        RouterConfig(max_fallback_depth=2)

    with pytest.raises(ValueError, match="negative"):
        RouterConfig(max_fallback_depth=-1)

    with pytest.raises(ValueError, match="threshold"):
        RouterConfig(circuit_breaker_threshold=0)

    with pytest.raises(ValueError, match="cooldown"):
        RouterConfig(circuit_breaker_cooldown_seconds=0.0)

    with pytest.raises(ValueError, match="Timeout for provider"):
        RouterConfig(provider_timeouts={"ollama": -5.0})

    with pytest.raises(ValueError, match="History buffer size"):
        RouterConfig(history_buffer_size=1000)
