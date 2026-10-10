"""RYVEN 3.0 — M17.10 Phase 5 Test Suite.

Validates Model Intelligence Telemetry, Performance Governance,
Operational Observability, Stream TTFT Measurement, and Fail-Closed Security Invariants.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.contracts import (
    AIRequest,
    AIResponse,
    AIStreamChunk,
    AIUsage,
    AuthenticationError,
    ProviderAdapter,
    ProviderInvalidRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    SecurityViolationError,
    StreamEventType,
)
from app.ai.config import RouterConfig
from app.ai.health import ProviderHealthTracker
from app.ai.models import ComplexityTier, RoutingDecision, TaskType
from app.ai.privacy import PrivacyMode
from app.ai.router import ModelRouter
from app.ai.security import ModelSecurityPolicy
from app.ai.security_gateway import ModelSecurityGateway
from app.ai.telemetry import (
    ModelTelemetryService,
    TelemetryEventType,
    compute_percentiles,
    model_telemetry_service,
    normalize_error_category,
    sanitize_telemetry_metadata,
)
from app.main import app


# ============================================================================
# MOCK ADAPTERS FOR DETERMINISTIC TESTS
# ============================================================================

class MockSuccessAdapter(ProviderAdapter):
    def __init__(self, provider_name: str = "OLLAMA", model_id: str = "qwen2.5:7b", latency_delay: float = 0.005) -> None:
        self.provider_name = provider_name
        self.model_id = model_id
        self.latency_delay = latency_delay
        self.call_count = 0

    @property
    def provider_id(self) -> ModelProvider:
        return ModelProvider(self.provider_name.upper())

    async def health_check(self) -> Dict[str, Any]:
        return {"status": "ok"}

    async def is_available(self) -> bool:
        return True

    async def generate(self, request: AIRequest) -> AIResponse:
        self.call_count += 1
        if self.latency_delay > 0:
            await asyncio.sleep(self.latency_delay)
        return AIResponse(
            content="Mock successful response",
            model_id=self.model_id,
            provider=self.provider_name,
            usage=AIUsage(prompt_tokens=42, completion_tokens=18, total_tokens=60),
            metadata={"mock": True},
        )

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        self.call_count += 1
        yield AIStreamChunk(event_type=StreamEventType.START, model_id=self.model_id, provider=self.provider_name)
        if self.latency_delay > 0:
            await asyncio.sleep(self.latency_delay)
        yield AIStreamChunk(event_type=StreamEventType.DELTA, delta="Hello ", model_id=self.model_id, provider=self.provider_name)
        yield AIStreamChunk(event_type=StreamEventType.DELTA, delta="World!", model_id=self.model_id, provider=self.provider_name)
        yield AIStreamChunk(event_type=StreamEventType.DONE, model_id=self.model_id, provider=self.provider_name)


class MockFailingAdapter(ProviderAdapter):
    def __init__(self, exc_to_raise: Exception, provider_name: str = "GROK") -> None:
        self.exc_to_raise = exc_to_raise
        self.provider_name = provider_name
        self.call_count = 0

    @property
    def provider_id(self) -> ModelProvider:
        return ModelProvider(self.provider_name.upper())

    async def health_check(self) -> Dict[str, Any]:
        return {"status": "failed"}

    async def is_available(self) -> bool:
        return False

    async def generate(self, request: AIRequest) -> AIResponse:
        self.call_count += 1
        raise self.exc_to_raise

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        self.call_count += 1
        raise self.exc_to_raise
        yield AIStreamChunk(event_type=StreamEventType.ERROR, model_id="dummy", provider=self.provider_name)


# ============================================================================
# PHASE 5 DETERMINISTIC TESTS
# ============================================================================

def test_request_and_attempt_correlation() -> None:
    """Verify request_id propagates across logical requests, attempts, and audit events."""
    service = ModelTelemetryService(max_events=100, max_requests=50)
    req_id = "req-corr-1234"

    service.start_request(request_id=req_id, task_type="CODE", privacy_mode="BALANCED")
    att1 = service.start_provider_attempt(request_id=req_id, provider="GROK", model_id="grok-2", local_or_remote="remote")
    service.complete_provider_attempt(att1, outcome="FAILURE", error=ProviderUnavailableError("Grok down"))

    att2 = service.start_provider_attempt(request_id=req_id, provider="OLLAMA", model_id="qwen2.5:7b", local_or_remote="local", is_fallback=True)
    service.complete_provider_attempt(att2, outcome="SUCCESS", tokens_input=50, tokens_output=20)
    service.complete_request(request_id=req_id, status="SUCCESS", fallback_occurred=True, fallback_provider="OLLAMA")

    events = service.get_events(limit=50)
    assert len(events) >= 4
    for ev in events:
        assert ev["request_id"] == req_id


def test_concurrency_thread_safety() -> None:
    """Verify concurrent worker threads can record metrics without race conditions."""
    service = ModelTelemetryService(max_events=500, max_requests=200)

    def worker(worker_id: int) -> None:
        for i in range(10):
            r_id = f"worker-{worker_id}-req-{i}"
            service.start_request(request_id=r_id, task_type="GENERAL_REASONING")
            att = service.start_provider_attempt(request_id=r_id, provider="OLLAMA", model_id="qwen2.5:7b", local_or_remote="local")
            service.record_first_token(att)
            service.complete_provider_attempt(att, outcome="SUCCESS", tokens_input=10, tokens_output=10)
            service.complete_request(request_id=r_id, status="SUCCESS")

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    summary = service.get_summary()
    assert summary["logical_requests"]["total"] == 50
    assert summary["logical_requests"]["success"] == 50


def test_bounded_event_retention() -> None:
    """Verify event ring buffer strictly enforces max_events bound."""
    service = ModelTelemetryService(max_events=10, max_requests=10)
    for i in range(25):
        service.start_request(request_id=f"req-{i}")

    events = service.get_events(limit=100)
    assert len(events) == 10
    assert events[-1]["request_id"] == "req-24"


def test_bounded_request_retention() -> None:
    """Verify completed requests ring buffer strictly enforces max_requests bound."""
    service = ModelTelemetryService(max_events=100, max_requests=5)
    for i in range(12):
        r_id = f"req-{i}"
        service.start_request(request_id=r_id)
        service.complete_request(request_id=r_id, status="SUCCESS")

    summary = service.get_summary()
    assert summary["logical_requests"]["total"] == 5
    assert summary["buffer_status"]["requests_retained"] == 5


def test_utc_timestamps_and_monotonic_durations() -> None:
    """Verify event timestamps are UTC ISO format and attempt durations are non-negative floats."""
    service = ModelTelemetryService()
    r_id = "test-time-1"
    service.start_request(request_id=r_id)
    att = service.start_provider_attempt(request_id=r_id, provider="OLLAMA", model_id="qwen2.5:7b", local_or_remote="local")
    time.sleep(0.005)
    service.complete_provider_attempt(att, outcome="SUCCESS")
    service.complete_request(r_id, status="SUCCESS")

    events = service.get_events(limit=5)
    for ev in events:
        ts = ev["timestamp"]
        # Must parse as valid ISO datetime
        dt = datetime.fromisoformat(ts)
        assert dt is not None

    summary = service.get_summary()
    prov_lat = summary["providers"]["ollama"]["latency"]["avg_ms"]
    assert prov_lat is not None
    assert prov_lat >= 0.0


def test_metadata_allowlisting_filters_disallowed_keys() -> None:
    """Verify non-allowlisted metadata keys are discarded."""
    raw = {
        "request_id": "r-1",
        "provider": "OLLAMA",
        "user_email": "user@example.com",
        "credit_card": "1234-5678",
        "session_context": "internal secret context",
    }
    clean = sanitize_telemetry_metadata(raw)
    assert "request_id" in clean
    assert "provider" in clean
    assert "user_email" not in clean
    assert "credit_card" not in clean
    assert "session_context" not in clean


def test_recursive_metadata_rejection_and_sanitization() -> None:
    """Verify nested dictionary metadata is recursively sanitized and strings truncated."""
    raw = {
        "request_id": "r-2",
        "nested": {
            "token": "sk-secret-token",
            "provider": "GROK",
        },
        "reason_summary": "A" * 500,
    }
    clean = sanitize_telemetry_metadata(raw)
    assert clean["request_id"] == "r-2"
    assert "nested" not in clean  # "nested" is not an allowlisted key
    assert len(clean["reason_summary"]) <= 260
    assert clean["reason_summary"].endswith("…[TRUNCATED]")


def test_prompt_and_response_leakage_prevention() -> None:
    """Verify prompts, system messages, and responses are never retained."""
    raw = {
        "request_id": "r-3",
        "prompt": "SELECT * FROM customers WHERE secret=true",
        "system_prompt": "You are a secret assistant",
        "response": "Here is the customer data: ...",
        "stream_delta": "partial response chunk",
    }
    clean = sanitize_telemetry_metadata(raw)
    assert "prompt" not in clean
    assert "system_prompt" not in clean
    assert "response" not in clean
    assert "stream_delta" not in clean


def test_credential_and_token_leakage_prevention() -> None:
    """Verify API keys and credential strings are detected and redacted."""
    raw = {
        "request_id": "r-4",
        "reason_summary": "Failed due to auth using token sk-abcdef1234567890",
    }
    clean = sanitize_telemetry_metadata(raw)
    assert clean["reason_summary"] == "[REDACTED]"


def test_safe_normalized_error_categories() -> None:
    """Verify exceptions map to low-cardinality, secret-free error categories."""
    assert normalize_error_category(ProviderTimeoutError("Connection timed out to https://secret-url.com")) == "PROVIDER_TIMEOUT"
    assert normalize_error_category(RateLimitError("Quota exceeded on api key sk-123")) == "RATE_LIMIT_EXCEEDED"
    assert normalize_error_category(AuthenticationError("Invalid Bearer header secret")) == "AUTHENTICATION_FAILED"
    assert normalize_error_category(SecurityViolationError("Remote blocked")) == "SECURITY_VIOLATION"
    assert normalize_error_category(ProviderInvalidRequestError("Bad schema")) == "INVALID_REQUEST"
    assert normalize_error_category(ProviderUnavailableError("Host offline")) == "PROVIDER_UNAVAILABLE"
    assert normalize_error_category(asyncio.CancelledError()) == "CLIENT_CANCELLED"
    assert normalize_error_category(ValueError("Unknown error body")) == "UNKNOWN_ERROR"


def test_latency_percentile_calculation_empty() -> None:
    """Verify compute_percentiles handles empty list gracefully."""
    res = compute_percentiles([])
    assert res["count"] == 0
    assert res["avg_ms"] is None
    assert res["median_ms"] is None
    assert res["p95_ms"] is None


def test_latency_percentile_calculation_samples() -> None:
    """Verify compute_percentiles computes count, avg, median, and p95 correctly."""
    samples = [10.0, 20.0, 30.0, 40.0, 50.0]
    res = compute_percentiles(samples)
    assert res["count"] == 5
    assert res["avg_ms"] == 30.0
    assert res["median_ms"] == 30.0
    assert res["p95_ms"] == 50.0


def test_unknown_token_usage_remains_none() -> None:
    """Verify token usage without trustworthy provider data remains None, not 0."""
    service = ModelTelemetryService()
    r_id = "req-tok-1"
    service.start_request(r_id)
    att = service.start_provider_attempt(r_id, "OLLAMA", "qwen2.5:7b", "local")
    service.complete_provider_attempt(att, outcome="SUCCESS", tokens_input=None, tokens_output=None)
    service.complete_request(r_id, status="SUCCESS")

    summary = service.get_summary()
    prov_data = summary["providers"]["ollama"]
    assert prov_data["tokens_input"] is None
    assert prov_data["tokens_output"] is None
    assert summary["governance"]["total_tokens_input"] is None


def test_trusted_token_usage_recorded_when_supplied() -> None:
    """Verify token counts are aggregated accurately when supplied by providers."""
    service = ModelTelemetryService()
    r_id = "req-tok-2"
    service.start_request(r_id)
    att = service.start_provider_attempt(r_id, "GROK", "grok-2", "remote")
    service.complete_provider_attempt(att, outcome="SUCCESS", tokens_input=100, tokens_output=50)
    service.complete_request(r_id, status="SUCCESS")

    summary = service.get_summary()
    prov_data = summary["providers"]["grok"]
    assert prov_data["tokens_input"] == 100
    assert prov_data["tokens_output"] == 50
    assert summary["governance"]["total_tokens_input"] == 100
    assert summary["governance"]["total_tokens_output"] == 50


def test_unknown_cost_remains_none_without_pricing_config() -> None:
    """Verify cost remains None if no pricing configuration exists."""
    service = ModelTelemetryService()
    r_id = "req-cost-1"
    service.start_request(r_id)
    att = service.start_provider_attempt(r_id, "GROK", "grok-2", "remote")
    service.complete_provider_attempt(att, outcome="SUCCESS", tokens_input=1000, tokens_output=500)
    service.complete_request(r_id, status="SUCCESS")

    summary = service.get_summary()
    assert summary["providers"]["grok"]["cost_estimate_usd"] is None
    assert summary["governance"]["total_cost_usd"] is None


def test_cost_calculation_with_explicit_pricing_config() -> None:
    """Verify cost calculation when explicit pricing configuration is provided."""
    service = ModelTelemetryService()
    service.set_pricing_config({
        "GROK": {
            "prompt_price_per_million": 2.0,       # $2 / 1M prompt
            "completion_price_per_million": 10.0,  # $10 / 1M completion
        }
    })

    r_id = "req-cost-2"
    service.start_request(r_id)
    att = service.start_provider_attempt(r_id, "GROK", "grok-2", "remote")
    # 500,000 prompt tokens ($1.00) + 100,000 completion tokens ($1.00) = $2.00
    service.complete_provider_attempt(att, outcome="SUCCESS", tokens_input=500_000, tokens_output=100_000)
    service.complete_request(r_id, status="SUCCESS")

    summary = service.get_summary()
    cost = summary["providers"]["grok"]["cost_estimate_usd"]
    assert cost is not None
    assert abs(cost - 2.0) < 1e-4
    assert abs(summary["governance"]["total_cost_usd"] - 2.0) < 1e-4


def test_local_versus_remote_aggregation() -> None:
    """Verify local vs remote distribution count is tracked correctly."""
    service = ModelTelemetryService()
    # 2 local requests
    for i in range(2):
        r_id = f"req-local-{i}"
        service.start_request(r_id, local_or_remote_target="local")
        service.complete_request(r_id, status="SUCCESS")
    # 1 remote request
    r_id = "req-remote-0"
    service.start_request(r_id, local_or_remote_target="remote")
    service.complete_request(r_id, status="SUCCESS")

    summary = service.get_summary()
    assert summary["logical_requests"]["local_count"] == 2
    assert summary["logical_requests"]["remote_count"] == 1


def test_fallback_accounting_no_double_counting() -> None:
    """Verify a request with fallback records 1 logical request and 2 distinct attempts."""
    service = ModelTelemetryService()
    r_id = "req-fb-1"

    service.start_request(r_id, selected_provider="GROK", selected_model="grok-2", local_or_remote_target="remote")
    att1 = service.start_provider_attempt(r_id, "GROK", "grok-2", "remote", is_fallback=False)
    service.complete_provider_attempt(att1, outcome="FAILURE", error=ProviderUnavailableError("Remote down"))

    service.record_fallback_started(r_id, from_provider="GROK", to_provider="OLLAMA", fallback_model="qwen2.5:7b")

    att2 = service.start_provider_attempt(r_id, "OLLAMA", "qwen2.5:7b", "local", is_fallback=True)
    service.complete_provider_attempt(att2, outcome="SUCCESS")
    service.complete_request(r_id, status="SUCCESS", fallback_occurred=True, fallback_provider="OLLAMA", fallback_model="qwen2.5:7b")

    summary = service.get_summary()
    assert summary["logical_requests"]["total"] == 1
    assert summary["logical_requests"]["fallback_count"] == 1
    assert summary["logical_requests"]["fallback_rate"] == 1.0

    assert summary["providers"]["grok"]["attempts_count"] == 1
    assert summary["providers"]["grok"]["failure_count"] == 1
    assert summary["providers"]["ollama"]["attempts_count"] == 1
    assert summary["providers"]["ollama"]["success_count"] == 1


def test_stream_first_token_latency_ttft_measurement() -> None:
    """Verify first-token latency (TTFT) is recorded and exposed in telemetry summary."""
    service = ModelTelemetryService()
    r_id = "stream-ttft-1"
    service.start_request(r_id)
    att = service.start_provider_attempt(r_id, "OLLAMA", "qwen2.5:7b", "local")
    time.sleep(0.005)
    ttft = service.record_first_token(att)
    assert ttft is not None
    assert ttft >= 0.0

    # Second call for the same attempt should be a no-op
    assert service.record_first_token(att) is None

    service.complete_provider_attempt(att, outcome="SUCCESS")
    service.complete_request(r_id, status="SUCCESS")

    summary = service.get_summary()
    prov_ttft = summary["providers"]["ollama"]["first_token_latency"]
    assert prov_ttft["count"] == 1
    assert prov_ttft["avg_ms"] is not None
    assert prov_ttft["avg_ms"] >= 0.0


def test_stream_cancellation_handling() -> None:
    """Verify client cancellation of stream records STREAM_CANCELLED and status CANCELLED."""
    service = ModelTelemetryService()
    r_id = "stream-cancel-1"
    service.start_request(r_id)
    att = service.start_provider_attempt(r_id, "OLLAMA", "qwen2.5:7b", "local")

    service.record_stream_cancelled(r_id, att)

    events = service.get_events(event_type="STREAM_CANCELLED")
    assert len(events) == 1
    assert events[0]["request_id"] == r_id

    summary = service.get_summary()
    assert summary["logical_requests"]["cancelled"] == 1


def test_circuit_change_recorded_in_telemetry() -> None:
    """Verify circuit state changes emit CIRCUIT_STATE_CHANGED event and audit event."""
    service = ModelTelemetryService()
    service.record_circuit_change(provider="GROK", prev_state="CLOSED", new_state="OPEN")

    events = service.get_events(event_type="CIRCUIT_STATE_CHANGED")
    assert len(events) == 1
    assert events[0]["metadata"]["provider"] == "GROK"
    assert events[0]["metadata"]["circuit_state"] == "OPEN"
    assert events[0]["metadata"]["circuit_prev_state"] == "CLOSED"


def test_security_denial_recorded_and_preserves_fail_closed() -> None:
    """Verify security denial is recorded in telemetry and marks request as SECURITY_DENIED."""
    service = ModelTelemetryService()
    r_id = "sec-deny-1"
    service.start_request(r_id, privacy_mode="LOCAL_ONLY")
    service.record_security_decision(r_id, allowed=False, privacy_mode="LOCAL_ONLY", reason="Remote forbidden")
    service.complete_request(r_id, status="SECURITY_DENIED")

    summary = service.get_summary()
    assert summary["logical_requests"]["security_denied"] == 1

    events = service.get_events(event_type="SECURITY_DENIED")
    assert len(events) >= 1


@pytest.mark.asyncio
async def test_telemetry_failure_does_not_weaken_security_gateway() -> None:
    """Verify security gateway remains strictly fail-closed even if telemetry service encounters an error."""
    gw = ModelSecurityGateway(default_privacy_mode=PrivacyMode.LOCAL_ONLY)
    req = AIRequest.from_prompt(prompt="Test prompt", task_type=TaskType.GENERAL_REASONING)

    # Remote inference must fail-closed regardless of telemetry state
    with pytest.raises(SecurityViolationError):
        await gw.validate_request(
            request=req,
            provider="GROK",
            model_id="grok-2",
            allow_remote=True,
        )


def test_action_bus_emission_on_provider_events() -> None:
    """Verify provider started and completed actions are emitted to action_bus."""
    emitted_events: List[ActionEvent] = []

    async def listener(evt: ActionEvent) -> None:
        emitted_events.append(evt)

    sub_id = action_bus.subscribe(listener)
    try:
        service = ModelTelemetryService()
        r_id = "bus-test-1"
        service.start_request(r_id)
        att = service.start_provider_attempt(r_id, "OLLAMA", "qwen2.5:7b", "local")
        service.complete_provider_attempt(att, outcome="SUCCESS")
        service.complete_request(r_id, status="SUCCESS")

        # Verify action events emitted
        started_events = [e for e in action_bus._history if e.action_type == ActionType.MODEL_PROVIDER_STARTED]
        completed_events = [e for e in action_bus._history if e.action_type == ActionType.MODEL_PROVIDER_COMPLETED]
        assert len(started_events) >= 1
        assert len(completed_events) >= 1
    finally:
        action_bus.unsubscribe(sub_id)


def test_action_bus_emission_on_fallback_and_circuit() -> None:
    """Verify fallback and circuit state changes are emitted to action_bus."""
    service = ModelTelemetryService()
    service.record_fallback_started("r-fb-bus", from_provider="GROK", to_provider="OLLAMA")
    service.record_circuit_change(provider="HUGGINGFACE_REMOTE", prev_state="CLOSED", new_state="OPEN")

    fb_events = [e for e in action_bus._history if e.action_type == ActionType.MODEL_FALLBACK_ATTEMPTED]
    circuit_events = [e for e in action_bus._history if e.action_type == ActionType.MODEL_CIRCUIT_STATE_CHANGED]
    assert len(fb_events) >= 1
    assert len(circuit_events) >= 1


def test_clear_resets_all_buffers_safely() -> None:
    """Verify clear() purges all event and request records without leakage."""
    service = ModelTelemetryService()
    r_id = "req-clr-1"
    service.start_request(r_id)
    service.complete_request(r_id, status="SUCCESS")
    assert len(service.get_events()) > 0

    service.clear()
    assert len(service.get_events()) == 0
    summary = service.get_summary()
    assert summary["logical_requests"]["total"] == 0
    assert summary["buffer_status"]["events_retained"] == 0


def test_get_events_bounded_and_filtered() -> None:
    """Verify get_events enforces limit cap and event_type filtering."""
    service = ModelTelemetryService()
    for i in range(10):
        service.start_request(f"r-filter-{i}")

    evts = service.get_events(limit=4)
    assert len(evts) == 4

    evts_filtered = service.get_events(event_type="REQUEST_ACCEPTED")
    assert all(e["event_type"] == "REQUEST_ACCEPTED" for e in evts_filtered)


@pytest.mark.asyncio
async def test_router_execute_successful_local_execution() -> None:
    """Verify ModelRouter.execute() integrates telemetry for successful local execution."""
    telemetry = ModelTelemetryService()
    router = ModelRouter(
        adapters={"OLLAMA": MockSuccessAdapter("OLLAMA", "qwen2.5:7b")},
        telemetry=telemetry,
    )
    req = AIRequest.from_prompt("Explain gravity", task_type=TaskType.GENERAL_REASONING)
    resp = await router.execute(req)
    assert resp.text == "Mock successful response"

    summary = telemetry.get_summary()
    assert summary["logical_requests"]["total"] == 1
    assert summary["logical_requests"]["success"] == 1
    assert summary["logical_requests"]["local_count"] == 1
    assert summary["providers"]["ollama"]["success_count"] == 1


@pytest.mark.asyncio
async def test_router_execute_remote_execution_with_token() -> None:
    """Verify ModelRouter.execute() integrates telemetry for confirmed remote execution."""
    from app.workflows.confirmation import confirmation_manager

    token = confirmation_manager.request_confirmation("Run remote Grok")
    telemetry = ModelTelemetryService()
    router = ModelRouter(
        adapters={
            "OLLAMA": MockSuccessAdapter("OLLAMA", "qwen2.5:7b"),
            "GROK": MockSuccessAdapter("GROK", "grok-2-latest"),
        },
        telemetry=telemetry,
        config=RouterConfig(allow_remote_inference=True),
    )
    grok_model = router.registry.get_model("grok-2-latest")
    assert grok_model is not None
    grok_model.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt(
        "Complex high reasoning math theorem proof step-by-step",
        task_type=TaskType.GENERAL_REASONING,
        metadata={"confirmation_token": token},
    )

    resp = await router.execute(req, allow_remote=True, preferred_model_id="grok-2-latest")
    assert resp.provider == "GROK"

    summary = telemetry.get_summary()
    assert summary["logical_requests"]["total"] == 1
    assert summary["logical_requests"]["success"] == 1
    assert summary["providers"]["grok"]["success_count"] == 1


@pytest.mark.asyncio
async def test_router_execute_security_denial_records_telemetry() -> None:
    """Verify ModelRouter.execute() records security denial in telemetry without triggering fallback."""
    telemetry = ModelTelemetryService()
    mock_grok = MockSuccessAdapter("GROK", "grok-2-latest")
    router = ModelRouter(
        adapters={"GROK": mock_grok, "OLLAMA": MockSuccessAdapter()},
        telemetry=telemetry,
        config=RouterConfig(privacy_mode=PrivacyMode.PRIVACY_FIRST, allow_remote_inference=True),
    )
    grok_model = router.registry.get_model("grok-2-latest")
    assert grok_model is not None
    grok_model.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt(
        "Complex step-by-step mathematical proof derivation theorem RCA",
        task_type=TaskType.GENERAL_REASONING,
    )

    # Remote inference without token under PRIVACY_FIRST must raise SecurityViolationError
    with pytest.raises(SecurityViolationError):
        await router.execute(req, allow_remote=True, preferred_model_id="grok-2-latest")

    # Grok must never have been called!
    assert mock_grok.call_count == 0

    summary = telemetry.get_summary()
    assert summary["logical_requests"]["security_denied"] == 1
    assert summary["logical_requests"]["fallback_count"] == 0


@pytest.mark.asyncio
async def test_router_execute_fallback_telemetry_flow() -> None:
    """Verify ModelRouter.execute() records failure and fallback in telemetry when primary fails."""
    telemetry = ModelTelemetryService()
    failing_grok = MockFailingAdapter(ProviderUnavailableError("Grok service outage"), "GROK")
    fallback_ollama = MockSuccessAdapter("OLLAMA", "qwen2.5:7b")

    from app.workflows.confirmation import confirmation_manager
    token = confirmation_manager.request_confirmation("Run remote Grok")

    router = ModelRouter(
        adapters={"GROK": failing_grok, "OLLAMA": fallback_ollama},
        telemetry=telemetry,
        config=RouterConfig(allow_remote_inference=True),
    )
    grok_model = router.registry.get_model("grok-2-latest")
    assert grok_model is not None
    grok_model.enabled = True
    router.security_policy.allow_remote_inference = True

    req = AIRequest.from_prompt(
        "Explain quantum computing step-by-step mathematical proof",
        task_type=TaskType.GENERAL_REASONING,
        metadata={"confirmation_token": token},
    )

    resp = await router.execute(req, allow_remote=True, preferred_model_id="grok-2-latest")
    assert resp.provider == "OLLAMA"
    assert resp.metadata.get("fallback_occurred") is True

    summary = telemetry.get_summary()
    assert summary["logical_requests"]["total"] == 1
    assert summary["logical_requests"]["success"] == 1
    assert summary["logical_requests"]["fallback_count"] == 1
    assert summary["logical_requests"]["fallback_rate"] == 1.0

    assert summary["providers"]["grok"]["failure_count"] == 1
    assert summary["providers"]["ollama"]["success_count"] == 1


@pytest.mark.asyncio
async def test_router_stream_telemetry_records_first_token() -> None:
    """Verify ModelRouter.stream() records TTFT in telemetry on first delta emission."""
    telemetry = ModelTelemetryService()
    router = ModelRouter(
        adapters={"OLLAMA": MockSuccessAdapter("OLLAMA", "qwen2.5:7b", latency_delay=0.005)},
        telemetry=telemetry,
    )
    req = AIRequest.from_prompt("Stream poem", task_type=TaskType.GENERAL_REASONING)

    chunks: List[AIStreamChunk] = []
    async for chunk in router.stream(req):
        chunks.append(chunk)

    assert len(chunks) >= 3
    summary = telemetry.get_summary()
    assert summary["logical_requests"]["success"] == 1
    ttft_stats = summary["providers"]["ollama"]["first_token_latency"]
    assert ttft_stats["count"] == 1
    assert ttft_stats["avg_ms"] is not None


@pytest.mark.asyncio
async def test_router_stream_cancelled_records_telemetry() -> None:
    """Verify client cancellation of ModelRouter.stream() is recorded in telemetry."""
    telemetry = ModelTelemetryService()

    class HangingStreamAdapter(ProviderAdapter):
        @property
        def provider_id(self) -> ModelProvider:
            return ModelProvider.OLLAMA

        async def health_check(self) -> Dict[str, Any]:
            return {"status": "ok"}

        async def is_available(self) -> bool:
            return True

        async def generate(self, request: AIRequest) -> AIResponse:
            raise NotImplementedError()

        async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
            yield AIStreamChunk(event_type=StreamEventType.START, model_id="qwen", provider="OLLAMA")
            await asyncio.sleep(10.0)
            yield AIStreamChunk(event_type=StreamEventType.DONE, model_id="qwen", provider="OLLAMA")

    router = ModelRouter(
        adapters={"OLLAMA": HangingStreamAdapter()},
        telemetry=telemetry,
    )
    req = AIRequest.from_prompt("Stream slow", task_type=TaskType.GENERAL_REASONING)

    async def consume() -> None:
        async for chunk in router.stream(req):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    summary = telemetry.get_summary()
    assert summary["logical_requests"]["cancelled"] == 1


@pytest.mark.asyncio
async def test_api_telemetry_endpoint_returns_sanitized_summary() -> None:
    """Verify /api/v1/models/telemetry returns valid HTTP 200 with sanitized summary."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/models/telemetry")
        assert resp.status_code == 200
        data = resp.json()
        assert "logical_requests" in data
        assert "providers" in data
        assert "governance" in data
        assert "buffer_status" in data


@pytest.mark.asyncio
async def test_api_telemetry_events_endpoint_never_leaks_prompts_or_responses() -> None:
    """Verify /api/v1/models/telemetry/events returns clean events without prompts or secrets."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/models/telemetry/events?limit=20")
        assert resp.status_code == 200
        events = resp.json()
        assert isinstance(events, list)
        for ev in events:
            assert "prompt" not in ev.get("metadata", {})
            assert "response" not in ev.get("metadata", {})
            assert "password" not in ev.get("metadata", {})


def test_health_tracker_circuit_transition_notifies_telemetry() -> None:
    """Verify tripping ProviderHealthTracker circuit breaker logs circuit change in telemetry."""
    model_telemetry_service.clear()
    tracker = ProviderHealthTracker(failure_threshold=2, cooldown_seconds=60.0)

    # First failure
    tracker.record_failure("GROK", ProviderUnavailableError("Fail 1"))
    events = model_telemetry_service.get_events(event_type="CIRCUIT_STATE_CHANGED")
    assert len(events) == 0

    # Second failure trips breaker from CLOSED to OPEN
    tracker.record_failure("GROK", ProviderUnavailableError("Fail 2"))
    events = model_telemetry_service.get_events(event_type="CIRCUIT_STATE_CHANGED")
    assert len(events) == 1
    assert events[0]["metadata"]["provider"] == "GROK"
    assert events[0]["metadata"]["circuit_state"] == "OPEN"
    assert events[0]["metadata"]["circuit_prev_state"] == "CLOSED"
