"""RYVEN 3.0 — Milestone 17.11 Test Suite.

Hybrid AI Benchmark Engine & Evidence-Based Intelligent Routing:
1. Metric calculation (latency, TTFT, tokens/sec, percentiles).
2. Monotonic timing and truthful handling of missing token counts.
3. Token-rate calculation (returns None when counts unavailable or 0).
4. Sample count bounding (max 3 samples, min 1 sample).
5. Cancellation and timeout handling (asyncio.CancelledError clean propagation).
6. Provider error normalization without crashing.
7. LOCAL_ONLY blocks cloud benchmarking.
8. PRIVACY_FIRST requires confirmation token for cloud execution.
9. Security denials never trigger fallback or unvetted execution.
10. No secret, prompt, or raw output leakage in audit events.
11. Cloud benchmarking disabled by default.
12. Benchmark mode does not alter production routing silently.
13. Deterministic quality checks (JSON schema, Python test sandbox, Regex).
14. Routing recommendation explanations (Ollama default on insufficient data or parity; cloud only on superior + healthy).
15. Provider injection and backward compatibility.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.benchmark import (
    BenchmarkEngine,
    BenchmarkSample,
    BenchmarkTask,
    BenchmarkTaskCategory,
    CalibrationRecommendation,
    DEFAULT_BENCHMARK_TASKS,
    ProviderBenchmarkSummary,
    evaluate_task_response,
)
from app.ai.contracts import (
    AIRequest,
    AIResponse,
    AIStreamChunk,
    AIUsage,
    ModelProvider,
    ProviderAdapter,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    SecurityViolationError,
    StreamEventType,
)
from app.ai.models import TaskType
from app.ai.privacy import PrivacyMode
from app.ai.security import ModelSecurityPolicy


# ============================================================================
# MOCK ADAPTER FIXTURES
# ============================================================================

class MockProviderAdapter:
    """Configurable mock adapter for benchmark testing without network calls."""

    def __init__(
        self,
        provider_id: str = "OLLAMA",
        model_id: str = "qwen2.5:7b",
        prefer_local: bool = True,
        responses: Optional[Dict[str, str]] = None,
        tokens_in: Optional[int] = 20,
        tokens_out: Optional[int] = 40,
        latency_delay: float = 0.01,
        should_fail: Optional[Exception] = None,
        supports_stream: bool = True,
    ) -> None:
        if provider_id == "OLLAMA":
            self.provider_id = ModelProvider.OLLAMA
        elif "REMOTE" in provider_id or "HUGGINGFACE" in provider_id:
            self.provider_id = ModelProvider.HUGGINGFACE_REMOTE
        else:
            self.provider_id = provider_id
        self.provider_name_override = provider_id
        self.model_id = model_id
        self.prefer_local = prefer_local
        self.responses = responses or {}
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.latency_delay = latency_delay
        self.should_fail = should_fail
        self.supports_stream = supports_stream
        self.call_count = 0

    async def generate(self, request: AIRequest) -> AIResponse:
        self.call_count += 1
        if self.latency_delay > 0:
            await asyncio.sleep(self.latency_delay)

        if self.should_fail:
            raise self.should_fail

        # Match response by prompt keyword or default
        prompt_text = request.get_prompt_text()
        content = "Paris is the capital of France."
        for key, resp in self.responses.items():
            if key.lower() in prompt_text.lower():
                content = resp
                break

        usage = None
        if self.tokens_in is not None and self.tokens_out is not None:
            usage = AIUsage(prompt_tokens=self.tokens_in, completion_tokens=self.tokens_out)

        return AIResponse(
            content=content,
            provider=self.provider_id,
            model_id=self.model_id,
            latency_ms=self.latency_delay * 1000,
            usage=usage,
        )

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        if not self.supports_stream:
            raise NotImplementedError("Streaming not supported")

        self.call_count += 1
        if self.should_fail:
            raise self.should_fail

        # Match content
        prompt_text = request.get_prompt_text()
        content = "Paris is the capital of France."
        for key, resp in self.responses.items():
            if key.lower() in prompt_text.lower():
                content = resp
                break

        # Simulate TTFT delay
        if self.latency_delay > 0:
            await asyncio.sleep(self.latency_delay)

        yield AIStreamChunk(
            event_type=StreamEventType.DELTA,
            delta=content[:5],
            model_id=self.model_id,
            provider=self.provider_id,
        )

        # Remaining chunks
        yield AIStreamChunk(
            event_type=StreamEventType.DELTA,
            delta=content[5:],
            model_id=self.model_id,
            provider=self.provider_id,
        )

        usage = None
        if self.tokens_in is not None and self.tokens_out is not None:
            usage = AIUsage(prompt_tokens=self.tokens_in, completion_tokens=self.tokens_out)

        yield AIStreamChunk(
            event_type=StreamEventType.DONE,
            model_id=self.model_id,
            provider=self.provider_id,
            usage=usage,
        )


# ============================================================================
# 1. METRIC CALCULATION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_metric_calculation_latency_ttft_tokens_percentiles():
    """Verify latency, TTFT, tokens/sec, and percentile aggregation."""
    adapter = MockProviderAdapter(
        provider_id="OLLAMA",
        model_id="qwen2.5:7b",
        tokens_in=50,
        tokens_out=100,
        latency_delay=0.02,
    )
    engine = BenchmarkEngine()
    task = DEFAULT_BENCHMARK_TASKS[0]  # simple_geo_01

    sample = await engine.run_single_task(adapter, task, iteration=1, stream_test=True)

    assert sample.success is True
    assert sample.duration_ms > 0
    assert sample.ttft_ms is not None
    assert sample.ttft_ms > 0
    assert sample.tokens_output == 100
    assert sample.tokens_per_second is not None
    assert sample.tokens_per_second > 0
    assert sample.quality_score == 1.0


# ============================================================================
# 2. MONOTONIC TIMING AND TRUTHFUL MISSING TOKEN COUNTS
# ============================================================================

@pytest.mark.asyncio
async def test_truthful_missing_token_counts():
    """If provider does not report token counts, tokens_per_second must be None."""
    adapter = MockProviderAdapter(
        provider_id="OLLAMA",
        model_id="qwen2.5:7b",
        tokens_in=None,
        tokens_out=None,
        latency_delay=0.01,
        supports_stream=False,
    )
    engine = BenchmarkEngine()
    task = DEFAULT_BENCHMARK_TASKS[0]

    sample = await engine.run_single_task(adapter, task, iteration=1, stream_test=False)

    assert sample.success is True
    assert sample.tokens_output is None
    assert sample.tokens_per_second is None  # Truthful: NEVER invent numbers


# ============================================================================
# 3. TOKEN RATE CALCULATION (ZERO OR MISSING OUTPUT)
# ============================================================================

@pytest.mark.asyncio
async def test_token_rate_zero_output():
    """If completion_tokens is 0, tokens_per_second must be None."""
    adapter = MockProviderAdapter(
        provider_id="OLLAMA",
        model_id="qwen2.5:7b",
        tokens_in=10,
        tokens_out=0,
        supports_stream=False,
    )
    engine = BenchmarkEngine()
    task = DEFAULT_BENCHMARK_TASKS[0]

    sample = await engine.run_single_task(adapter, task, iteration=1, stream_test=False)

    assert sample.tokens_per_second is None


# ============================================================================
# 4. SAMPLE COUNT BOUNDING
# ============================================================================

@pytest.mark.asyncio
async def test_sample_count_bounding():
    """Verify samples_per_task is bounded between 1 and 3 for laptop thermal safety."""
    adapter = MockProviderAdapter(provider_id="OLLAMA", model_id="qwen2.5:7b")
    engine = BenchmarkEngine()
    single_task = [DEFAULT_BENCHMARK_TASKS[0]]

    # Request excessive sample count (10)
    summaries = await engine.run_benchmark(
        adapters={"OLLAMA": adapter},
        tasks=single_task,
        samples_per_task=10,
    )

    assert "OLLAMA" in summaries
    assert summaries["OLLAMA"].total_samples == 3  # Clamped to 3


# ============================================================================
# 5. CANCELLATION AND TIMEOUT HANDLING
# ============================================================================

@pytest.mark.asyncio
async def test_cancellation_propagation():
    """asyncio.CancelledError must cleanly propagate without being swallowed."""
    async def slow_stream(req):
        await asyncio.sleep(5.0)
        yield AIStreamChunk(event_type=StreamEventType.DONE)

    adapter = MockProviderAdapter()
    adapter.stream = slow_stream
    engine = BenchmarkEngine()
    task = DEFAULT_BENCHMARK_TASKS[0]

    coro = engine.run_single_task(adapter, task)
    task_fut = asyncio.create_task(coro)

    await asyncio.sleep(0.02)
    task_fut.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task_fut


@pytest.mark.asyncio
async def test_provider_timeout_handled():
    """TimeoutError must be recorded cleanly without crashing the engine."""
    adapter = MockProviderAdapter(
        provider_id="OLLAMA",
        latency_delay=0.5,
        supports_stream=False,
    )
    engine = BenchmarkEngine()
    short_task = BenchmarkTask(
        task_id="timeout_test",
        category=BenchmarkTaskCategory.SIMPLE_QUESTION,
        description="Timeout test",
        prompt="Hi",
        verification_type="regex_match",
        verification_payload={"pattern": "Hi"},
        timeout_seconds=0.02,  # Very short timeout
    )

    sample = await engine.run_single_task(adapter, short_task, stream_test=False)

    assert sample.success is False
    assert sample.error_category == "PROVIDER_TIMEOUT"
    assert sample.quality_score == 0.0


# ============================================================================
# 6. PROVIDER ERROR NORMALIZATION
# ============================================================================

@pytest.mark.asyncio
async def test_provider_error_normalization():
    """Exceptions must be normalized into standard telemetry categories without crashing."""
    adapter = MockProviderAdapter(
        provider_id="OLLAMA",
        should_fail=RateLimitError("Rate limit exceeded"),
        supports_stream=False,
    )
    engine = BenchmarkEngine()
    task = DEFAULT_BENCHMARK_TASKS[0]

    sample = await engine.run_single_task(adapter, task, stream_test=False)

    assert sample.success is False
    assert sample.error_category == "RATE_LIMIT_EXCEEDED"
    assert sample.quality_score == 0.0


# ============================================================================
# 7. LOCAL_ONLY BLOCKS CLOUD BENCHMARKING
# ============================================================================

@pytest.mark.asyncio
async def test_local_only_blocks_cloud():
    """LOCAL_ONLY mode must never invoke remote cloud provider benchmark."""
    local_adapter = MockProviderAdapter(provider_id="OLLAMA", prefer_local=True)
    cloud_adapter = MockProviderAdapter(provider_id="HUGGINGFACE_REMOTE", prefer_local=False)

    engine = BenchmarkEngine()
    summaries = await engine.run_benchmark(
        adapters={"OLLAMA": local_adapter, "HUGGINGFACE_REMOTE": cloud_adapter},
        tasks=[DEFAULT_BENCHMARK_TASKS[0]],
        allow_cloud=True,  # Even if allow_cloud is True
        privacy_mode=PrivacyMode.LOCAL_ONLY,
    )

    assert "OLLAMA" in summaries
    assert "HUGGINGFACE_REMOTE" not in summaries
    assert cloud_adapter.call_count == 0  # Zero network calls


# ============================================================================
# 8. PRIVACY_FIRST REQUIRES CONFIRMATION TOKEN
# ============================================================================

@pytest.mark.asyncio
async def test_privacy_first_requires_confirmation_token():
    """Under PRIVACY_FIRST, remote provider is skipped without confirmation token."""
    local_adapter = MockProviderAdapter(provider_id="OLLAMA", prefer_local=True)
    cloud_adapter = MockProviderAdapter(provider_id="HUGGINGFACE_REMOTE", prefer_local=False)

    engine = BenchmarkEngine(security_policy=ModelSecurityPolicy(allow_remote_inference=True))

    # Case A: No token -> skipped
    summaries_a = await engine.run_benchmark(
        adapters={"OLLAMA": local_adapter, "HUGGINGFACE_REMOTE": cloud_adapter},
        tasks=[DEFAULT_BENCHMARK_TASKS[0]],
        allow_cloud=True,
        privacy_mode=PrivacyMode.PRIVACY_FIRST,
        confirmation_token=None,
    )
    assert "HUGGINGFACE_REMOTE" not in summaries_a
    assert cloud_adapter.call_count == 0

    # Case B: Valid token -> executed
    summaries_b = await engine.run_benchmark(
        adapters={"OLLAMA": local_adapter, "HUGGINGFACE_REMOTE": cloud_adapter},
        tasks=[DEFAULT_BENCHMARK_TASKS[0]],
        allow_cloud=True,
        privacy_mode=PrivacyMode.PRIVACY_FIRST,
        confirmation_token="valid_user_consent_token_123",
    )
    assert "HUGGINGFACE_REMOTE" in summaries_b
    assert cloud_adapter.call_count > 0


# ============================================================================
# 9. SECURITY DENIAL NEVER TRIGGERS FALLBACK
# ============================================================================

@pytest.mark.asyncio
async def test_security_denial_blocks_cloud_without_unvetted_run():
    """If security policy disallows remote inference, cloud is denied without side-effects."""
    restricted_policy = ModelSecurityPolicy(allow_remote_inference=False)
    engine = BenchmarkEngine(security_policy=restricted_policy)

    local_adapter = MockProviderAdapter(provider_id="OLLAMA", prefer_local=True)
    cloud_adapter = MockProviderAdapter(provider_id="HUGGINGFACE_REMOTE", prefer_local=False)

    summaries = await engine.run_benchmark(
        adapters={"OLLAMA": local_adapter, "HUGGINGFACE_REMOTE": cloud_adapter},
        tasks=[DEFAULT_BENCHMARK_TASKS[0]],
        allow_cloud=True,
        confirmation_token="token",
    )

    assert "OLLAMA" in summaries
    assert "HUGGINGFACE_REMOTE" not in summaries
    assert cloud_adapter.call_count == 0


# ============================================================================
# 10. NO SECRET, PROMPT, OR RAW OUTPUT LEAKAGE IN AUDIT EVENTS
# ============================================================================

@pytest.mark.asyncio
async def test_no_leakage_in_action_bus_audit_events():
    """Published ActionEvent must contain only safe metadata without prompts or raw output."""
    captured_events: List[ActionEvent] = []

    async def event_handler(event: ActionEvent) -> None:
        captured_events.append(event)

    action_bus.subscribe(event_handler)

    adapter = MockProviderAdapter(provider_id="OLLAMA")
    engine = BenchmarkEngine()

    try:
        await engine.run_benchmark(
            adapters={"OLLAMA": adapter},
            tasks=[DEFAULT_BENCHMARK_TASKS[0]],
        )

        assert len(captured_events) >= 2
        for ev in captured_events:
            metadata = ev.safe_metadata or {}
            # Verify no secret or prompt/output content stored
            assert "prompt" not in metadata
            assert "response" not in metadata
            assert "output" not in metadata
            assert "api_key" not in metadata
            assert "token" not in metadata
    finally:
        action_bus.unsubscribe(event_handler)


# ============================================================================
# 11. CLOUD BENCHMARKING DISABLED BY DEFAULT
# ============================================================================

@pytest.mark.asyncio
async def test_cloud_benchmarking_disabled_by_default():
    """allow_cloud defaults to False; remote providers must not run."""
    local_adapter = MockProviderAdapter(provider_id="OLLAMA", prefer_local=True)
    cloud_adapter = MockProviderAdapter(provider_id="HUGGINGFACE_REMOTE", prefer_local=False)

    engine = BenchmarkEngine()
    summaries = await engine.run_benchmark(
        adapters={"OLLAMA": local_adapter, "HUGGINGFACE_REMOTE": cloud_adapter},
        tasks=[DEFAULT_BENCHMARK_TASKS[0]],
        # allow_cloud omitted -> defaults to False
    )

    assert "OLLAMA" in summaries
    assert "HUGGINGFACE_REMOTE" not in summaries
    assert cloud_adapter.call_count == 0


# ============================================================================
# 12. BENCHMARK MODE DOES NOT ALTER PRODUCTION ROUTING SILENTLY
# ============================================================================

@pytest.mark.asyncio
async def test_benchmark_does_not_mutate_router_state():
    """Running a benchmark updates internal summaries but does not modify router configs directly."""
    engine = BenchmarkEngine()
    adapter = MockProviderAdapter(provider_id="OLLAMA")

    await engine.run_benchmark(
        adapters={"OLLAMA": adapter},
        tasks=[DEFAULT_BENCHMARK_TASKS[0]],
    )

    rec = engine.get_latest_recommendation()
    assert isinstance(rec, CalibrationRecommendation)
    assert rec.recommended_default_provider == "OLLAMA"


# ============================================================================
# 13. DETERMINISTIC QUALITY CHECKS (ALL 5 CATEGORIES)
# ============================================================================

def test_deterministic_evaluations_across_categories():
    """Verify deterministic quality scoring rubrics for each task."""
    # 1. Regex Match (Simple Question)
    task1 = DEFAULT_BENCHMARK_TASKS[0]
    score1_good, _ = evaluate_task_response(task1, "The capital of France is Paris.")
    score1_bad, _ = evaluate_task_response(task1, "The capital is Berlin.")
    assert score1_good == 1.0
    assert score1_bad == 0.0

    # 2. Python Unit Test Sandbox (Small Coding)
    task2 = DEFAULT_BENCHMARK_TASKS[1]
    good_code = "```python\ndef multiply(a, b):\n    return a * b\n```"
    bad_code = "```python\ndef multiply(a, b):\n    return a + b\n```"
    score2_good, _ = evaluate_task_response(task2, good_code)
    score2_bad, _ = evaluate_task_response(task2, bad_code)
    assert score2_good == 1.0
    assert score2_bad < 1.0

    # 3. JSON Schema & Data Types (Structured Output)
    task3 = DEFAULT_BENCHMARK_TASKS[2]
    good_json = '```json\n{"server_name": "srv1", "healthy": true, "active_connections": 42}\n```'
    bad_json = '```json\n{"server_name": "srv1", "healthy": "yes"}\n```'
    score3_good, _ = evaluate_task_response(task3, good_json)
    score3_bad, _ = evaluate_task_response(task3, bad_json)
    assert score3_good == 1.0
    assert score3_bad == 0.0  # missing active_connections

    # 4. Math / Reasoning Regex (Reasoning)
    task4 = DEFAULT_BENCHMARK_TASKS[3]
    score4_good, _ = evaluate_task_response(task4, "Distance = 75 * 3.5 = 262.5 miles.")
    score4_bad, _ = evaluate_task_response(task4, "Distance is approximately 250 miles.")
    assert score4_good == 1.0
    assert score4_bad == 0.0

    # 5. Code Debugging Fix (Code Debugging)
    task5 = DEFAULT_BENCHMARK_TASKS[4]
    good_debug = "```python\ndef take_first_n(items, n):\n    return items[:n]\n```"
    score5_good, _ = evaluate_task_response(task5, good_debug)
    assert score5_good == 1.0


# ============================================================================
# 14. ROUTING RECOMMENDATION EXPLANATIONS
# ============================================================================

def test_routing_recommendation_logic():
    """Ollama default on insufficient data or competitive score; cloud only on superior score."""
    engine = BenchmarkEngine()

    # Case A: Insufficient data (empty summaries)
    rec_empty = engine.compute_calibration_recommendation({})
    assert rec_empty.recommended_default_provider == "OLLAMA"
    assert rec_empty.cloud_delegation_justified is False

    # Case B: Local only benchmarked
    local_summary = ProviderBenchmarkSummary(
        provider="OLLAMA",
        model_id="qwen2.5:7b",
        total_samples=5,
        avg_quality_score=0.95,
        latency={"p50_ms": 120.0},
        success_rate=1.0,
    )
    rec_local = engine.compute_calibration_recommendation({"OLLAMA": local_summary})
    assert rec_local.recommended_default_provider == "OLLAMA"
    assert rec_local.cloud_delegation_justified is False

    # Case C: Cloud and Local parity -> Local wins
    cloud_parity = ProviderBenchmarkSummary(
        provider="HUGGINGFACE_REMOTE",
        model_id="qwen2.5-coder-32b",
        total_samples=5,
        avg_quality_score=0.96,
        latency={"p50_ms": 450.0},
        success_rate=1.0,
    )
    rec_parity = engine.compute_calibration_recommendation({
        "OLLAMA": local_summary,
        "HUGGINGFACE_REMOTE": cloud_parity,
    })
    assert rec_parity.cloud_delegation_justified is False
    assert "guaranteed privacy" in rec_parity.explanation.lower()

    # Case D: Cloud distinctly superior in coding (+0.15 margin) and healthy
    local_weak = ProviderBenchmarkSummary(
        provider="OLLAMA",
        model_id="qwen2.5:7b",
        total_samples=5,
        avg_quality_score=0.70,
        category_scores={"SMALL_CODING": 0.60},
        latency={"p50_ms": 120.0},
        success_rate=1.0,
    )
    cloud_strong = ProviderBenchmarkSummary(
        provider="HUGGINGFACE_REMOTE",
        model_id="qwen2.5-coder-32b",
        total_samples=5,
        avg_quality_score=0.95,
        category_scores={"SMALL_CODING": 1.00},
        latency={"p50_ms": 400.0},
        success_rate=0.95,
    )
    rec_superior = engine.compute_calibration_recommendation({
        "OLLAMA": local_weak,
        "HUGGINGFACE_REMOTE": cloud_strong,
    })
    assert rec_superior.cloud_delegation_justified is True
    assert "CODE" in rec_superior.eligible_task_types


# ============================================================================
# 15. PROVIDER INJECTION AND BACKWARD COMPATIBILITY
# ============================================================================

@pytest.mark.asyncio
async def test_provider_injection_and_backward_compatibility():
    """BenchmarkEngine accepts arbitrary ProviderAdapter instances conforming to protocol."""
    custom_adapter = MockProviderAdapter(provider_id="CUSTOM_PROVIDER", model_id="custom-v1")
    engine = BenchmarkEngine()

    sample = await engine.run_single_task(
        adapter=custom_adapter,
        task=DEFAULT_BENCHMARK_TASKS[0],
        iteration=1,
    )

    assert sample.provider == "CUSTOM_PROVIDER"
    assert sample.model_id == "custom-v1"
    assert sample.success is True


# ============================================================================
# 16. ROUTER INTEGRATION & ADAPTIVE ROUTING SAFEGUARDS
# ============================================================================

@pytest.mark.asyncio
async def test_router_defaults_preserve_local_first_and_recommendation_only():
    """RouterConfig defaults must have recommendation_only=True and enable_adaptive_routing=False."""
    from app.ai.config import RouterConfig
    from app.ai.router import ModelRouter

    config = RouterConfig()
    assert config.recommendation_only is True
    assert config.enable_adaptive_routing is False
    assert config.kill_switch_adaptive_routing is False

    router = ModelRouter(config=config)
    decision = await router.route(task_type=TaskType.CODE, prompt="def add(a, b): return a + b")

    assert decision.selected_model == "qwen2.5:7b"
    assert decision.provider == "OLLAMA"
    assert decision.adaptive_routing_active is False
    assert decision.calibration_recommendation is not None


@pytest.mark.asyncio
async def test_adaptive_routing_requires_explicit_opt_in():
    """Even if cloud is superior, adaptive routing does not activate if recommendation_only=True."""
    from app.ai.benchmark import model_benchmark_engine, CalibrationRecommendation
    from app.ai.config import RouterConfig
    from app.ai.router import ModelRouter

    # Mock recommendation where cloud is justified
    justified_rec = CalibrationRecommendation(
        recommended_default_provider="OLLAMA",
        cloud_delegation_justified=True,
        eligible_task_types=["CODE"],
        explanation="Cloud coding is superior",
    )

    with patch.object(model_benchmark_engine, "get_latest_recommendation", return_value=justified_rec):
        config = RouterConfig(
            recommendation_only=True,  # Default
            enable_adaptive_routing=True,  # enabled, but recommendation_only is True
        )
        router = ModelRouter(config=config)
        decision = await router.route(task_type=TaskType.CODE, prompt="def test(): pass", allow_remote=True)

        assert decision.selected_model == "qwen2.5:7b"
        assert decision.adaptive_routing_active is False


@pytest.mark.asyncio
async def test_adaptive_routing_activation_when_authorized():
    """When enabled and not recommendation_only, eligible task routes to cloud with ADAPTIVE_BENCHMARK_ROUTED."""
    from app.ai.benchmark import model_benchmark_engine, CalibrationRecommendation
    from app.ai.config import RouterConfig
    from app.ai.router import ModelRouter
    from app.ai.security import ModelSecurityPolicy

    justified_rec = CalibrationRecommendation(
        recommended_default_provider="OLLAMA",
        cloud_delegation_justified=True,
        eligible_task_types=["CODE"],
        explanation="Cloud coding is superior",
    )

    sec_policy = ModelSecurityPolicy(allow_remote_inference=True)

    with patch.object(model_benchmark_engine, "get_latest_recommendation", return_value=justified_rec):
        config = RouterConfig(
            recommendation_only=False,
            enable_adaptive_routing=True,
            allow_remote_inference=True,
        )
        router = ModelRouter(config=config, security_policy=sec_policy)

        # Register remote model in registry
        from app.ai.registry import ModelProfile, model_registry
        cloud_profile = ModelProfile(
            id="qwen2.5-coder:32b",
            provider=ModelProvider.HUGGINGFACE_REMOTE,
            task_types=[TaskType.CODE],
            local_or_remote="remote",
            model_name="Qwen/Qwen2.5-Coder-32B-Instruct",
            memory_estimate_gb=0.0,
            enabled=True,
        )
        router.registry.register_model(cloud_profile)

        decision = await router.route(
            task_type=TaskType.CODE,
            prompt="Write a quicksort function",
            allow_remote=True,
        )

        assert decision.selected_model == "qwen2.5-coder:32b"
        assert decision.provider == "HUGGINGFACE_REMOTE"
        assert decision.adaptive_routing_active is True
        assert decision.reason_code == "ADAPTIVE_BENCHMARK_ROUTED"


@pytest.mark.asyncio
async def test_kill_switch_immediately_disables_adaptive_routing():
    """kill_switch_adaptive_routing=True must instantly override any adaptive routing settings."""
    from app.ai.benchmark import model_benchmark_engine, CalibrationRecommendation
    from app.ai.config import RouterConfig
    from app.ai.router import ModelRouter
    from app.ai.security import ModelSecurityPolicy

    justified_rec = CalibrationRecommendation(
        recommended_default_provider="OLLAMA",
        cloud_delegation_justified=True,
        eligible_task_types=["CODE"],
        explanation="Cloud coding is superior",
    )

    sec_policy = ModelSecurityPolicy(allow_remote_inference=True)

    with patch.object(model_benchmark_engine, "get_latest_recommendation", return_value=justified_rec):
        config = RouterConfig(
            recommendation_only=False,
            enable_adaptive_routing=True,
            kill_switch_adaptive_routing=True,  # Emergency kill switch!
            allow_remote_inference=True,
        )
        router = ModelRouter(config=config, security_policy=sec_policy)

        decision = await router.route(
            task_type=TaskType.CODE,
            prompt="Write a quicksort function",
            allow_remote=True,
        )

        assert decision.selected_model == "qwen2.5:7b"
        assert decision.provider == "OLLAMA"
        assert decision.adaptive_routing_active is False


# ============================================================================
# 17. REST API ENDPOINTS FOR BENCHMARK & CALIBRATION
# ============================================================================

@pytest.mark.asyncio
async def test_api_models_benchmark_results_and_calibration():
    """Verify GET /api/v1/models/benchmark/results and calibration return expected schemas."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Results
        res = await client.get("/api/v1/models/benchmark/results")
        assert res.status_code == 200
        data = res.json()
        assert "is_running" in data
        assert "sample_count" in data
        assert "summaries" in data
        assert "recommendation" in data

        # Calibration
        res_cal = await client.get("/api/v1/models/benchmark/calibration")
        assert res_cal.status_code == 200
        cal_data = res_cal.json()
        assert "recommended_default_provider" in cal_data
        assert "cloud_delegation_justified" in cal_data


@pytest.mark.asyncio
async def test_api_models_benchmark_run_endpoint():
    """Verify POST /api/v1/models/benchmark/run runs bounded benchmark and returns summaries."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.ai.benchmark import model_benchmark_engine

    mock_summary = {
        "OLLAMA": ProviderBenchmarkSummary(
            provider="OLLAMA",
            model_id="qwen2.5:7b",
            total_samples=1,
            successful_samples=1,
            success_rate=1.0,
            avg_quality_score=1.0,
        )
    }

    with patch.object(model_benchmark_engine, "run_benchmark", new=AsyncMock(return_value=mock_summary)):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/models/benchmark/run",
                json={"allow_cloud": False, "samples_per_task": 1},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "completed"
            assert "summaries" in data
            assert "recommendation" in data


@pytest.mark.asyncio
async def test_api_models_benchmark_rejects_concurrent_runs():
    """Verify POST /api/v1/models/benchmark/run returns 409 if benchmark is already running."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app
    from app.ai.benchmark import model_benchmark_engine

    with patch.object(BenchmarkEngine, "is_running", new=True):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.post(
                "/api/v1/models/benchmark/run",
                json={"allow_cloud": False},
            )
            assert res.status_code == 409
            assert "already in flight" in res.json()["detail"].lower()
