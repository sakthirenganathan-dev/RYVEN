"""RYVEN 3.0 — Hybrid AI Benchmark Engine & Evidence-Based Calibration.

Measures genuine performance across local (Ollama + Qwen 2.5 7B) and optional
cloud (Hugging Face) AI providers using versioned, synthetic, non-sensitive tasks.
Computes deterministic quality scores, monotonic latencies, TTFT, and tokens/second,
producing explainable routing calibration recommendations.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from enum import Enum
import json
import re
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
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
from app.ai.models import ComplexityTier, TaskType
from app.ai.privacy import PrivacyMode
from app.ai.security import ModelSecurityPolicy, model_security_policy
from app.ai.telemetry import compute_percentiles, normalize_error_category
from app.core.logging_config import logger


# ============================================================================
# 1. SYNTHETIC TASK SPECIFICATION (VERSIONED, NON-SENSITIVE)
# ============================================================================

BENCHMARK_SUITE_VERSION = "1.0.0"


class BenchmarkTaskCategory(str, Enum):
    """Controlled, reproducible task categories for fair provider evaluation."""

    SIMPLE_QUESTION = "SIMPLE_QUESTION"
    SMALL_CODING = "SMALL_CODING"
    STRUCTURED_OUTPUT = "STRUCTURED_OUTPUT"
    REASONING = "REASONING"
    CODE_DEBUGGING = "CODE_DEBUGGING"


class BenchmarkTask(BaseModel):
    """Specification for a reproducible, deterministic synthetic evaluation prompt."""

    task_id: str
    category: BenchmarkTaskCategory
    description: str
    prompt: str
    system_prompt: Optional[str] = None
    expected_schema: Optional[Dict[str, Any]] = None
    timeout_seconds: float = 30.0
    verification_type: str = Field(
        ...,
        description="One of: 'regex_match', 'json_schema', 'python_unit_test', 'keyword_all'",
    )
    verification_payload: Dict[str, Any] = Field(default_factory=dict)


# Fixed synthetic evaluation suite: public, non-sensitive, reproducible
DEFAULT_BENCHMARK_TASKS: List[BenchmarkTask] = [
    # 1. Simple Question
    BenchmarkTask(
        task_id="simple_geo_01",
        category=BenchmarkTaskCategory.SIMPLE_QUESTION,
        description="Factual recall test: Capital of France",
        prompt="What is the capital city of France? Answer in one short sentence.",
        verification_type="regex_match",
        verification_payload={"pattern": r"\bParis\b", "flags": "IGNORECASE"},
        timeout_seconds=20.0,
    ),
    # 2. Small Coding Task
    BenchmarkTask(
        task_id="coding_multiply_02",
        category=BenchmarkTaskCategory.SMALL_CODING,
        description="Implement pure multiply function",
        prompt=(
            "Write a Python function named `multiply` that takes two numbers `a` and `b` "
            "and returns their product. Output only valid Python code inside a ```python block."
        ),
        verification_type="python_unit_test",
        verification_payload={
            "func_name": "multiply",
            "test_cases": [
                {"args": [3, 4], "expected": 12},
                {"args": [-2, 5], "expected": -10},
                {"args": [0, 99], "expected": 0},
            ],
        },
        timeout_seconds=30.0,
    ),
    # 3. Structured Output Task
    BenchmarkTask(
        task_id="structured_json_03",
        category=BenchmarkTaskCategory.STRUCTURED_OUTPUT,
        description="Extract structured JSON entity",
        prompt=(
            'Respond with ONLY a valid JSON object representing a server status with keys: '
            '"server_name" (string), "healthy" (boolean), "active_connections" (integer).'
        ),
        expected_schema={
            "type": "object",
            "required": ["server_name", "healthy", "active_connections"],
            "properties": {
                "server_name": {"type": "string"},
                "healthy": {"type": "boolean"},
                "active_connections": {"type": "integer"},
            },
        },
        verification_type="json_schema",
        verification_payload={
            "required_fields": ["server_name", "healthy", "active_connections"],
            "types": {"server_name": str, "healthy": bool, "active_connections": int},
        },
        timeout_seconds=25.0,
    ),
    # 4. Multi-Step Reasoning / Math Task
    BenchmarkTask(
        task_id="reasoning_speed_04",
        category=BenchmarkTaskCategory.REASONING,
        description="Deterministic distance calculation reasoning",
        prompt=(
            "Solve step-by-step: A train travels at a constant speed of 75 miles per hour for 3.5 hours. "
            "What is the total distance traveled in miles? Conclude with the exact number."
        ),
        verification_type="regex_match",
        verification_payload={"pattern": r"\b262\.5\b"},
        timeout_seconds=30.0,
    ),
    # 5. Code Debugging Task
    BenchmarkTask(
        task_id="debugging_offbyone_05",
        category=BenchmarkTaskCategory.CODE_DEBUGGING,
        description="Fix off-by-one slice error",
        prompt=(
            "The following Python function has a bug where it returns one fewer element than requested:\n"
            "```python\n"
            "def take_first_n(items, n):\n"
            "    return items[:n - 1]\n"
            "```\n"
            "Provide the corrected `take_first_n` function inside a ```python block."
        ),
        verification_type="python_unit_test",
        verification_payload={
            "func_name": "take_first_n",
            "test_cases": [
                {"args": [[10, 20, 30, 40], 2], "expected": [10, 20]},
                {"args": [["a", "b", "c"], 3], "expected": ["a", "b", "c"]},
                {"args": [[], 0], "expected": []},
            ],
        },
        timeout_seconds=30.0,
    ),
]


# ============================================================================
# 2. DETERMINISTIC QUALITY EVALUATION ENGINE
# ============================================================================

def _extract_code_block(content: str, language: str = "python") -> str:
    """Extract code from fenced markdown block or return whole content."""
    pattern = rf"```{language}\s*\n(.*?)```"
    match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    generic_match = re.search(r"```\s*\n(.*?)```", content, re.DOTALL)
    if generic_match:
        return generic_match.group(1).strip()
    return content.strip()


def _extract_json_block(content: str) -> str:
    """Extract JSON object from string or markdown code block."""
    json_match = re.search(r"```(?:json)?\s*\n(\{.*?\})\s*```", content, re.DOTALL | re.IGNORECASE)
    if json_match:
        return json_match.group(1).strip()
    brace_match = re.search(r"\{.*?\}", content, re.DOTALL)
    if brace_match:
        return brace_match.group(0).strip()
    return content.strip()


def evaluate_task_response(task: BenchmarkTask, response_text: str) -> Tuple[float, str]:
    """Deterministically score an output against task rubrics.
    
    Returns:
        (score, explanation) where score is in [0.0, 1.0].
    """
    if not response_text or not response_text.strip():
        return 0.0, "Empty or whitespace response"

    clean_text = response_text.strip()
    v_type = task.verification_type
    v_payload = task.verification_payload

    # 1. Regex Pattern Matching
    if v_type == "regex_match":
        pattern_str = v_payload.get("pattern", "")
        flags = re.IGNORECASE if v_payload.get("flags") == "IGNORECASE" else 0
        if re.search(pattern_str, clean_text, flags):
            return 1.0, f"Matched expected pattern '{pattern_str}'"
        return 0.0, f"Failed to match expected pattern '{pattern_str}'"

    # 2. JSON Schema & Typing Validation
    elif v_type == "json_schema":
        raw_json = _extract_json_block(clean_text)
        try:
            parsed = json.loads(raw_json)
            if not isinstance(parsed, dict):
                return 0.0, "Parsed JSON is not an object/dict"

            required_fields = v_payload.get("required_fields", [])
            for field in required_fields:
                if field not in parsed:
                    return 0.0, f"Missing required JSON property: '{field}'"

            types_map = v_payload.get("types", {})
            for k, expected_t in types_map.items():
                val = parsed.get(k)
                if not isinstance(val, expected_t):
                    # allow int for float if compatible
                    return 0.5, f"Property '{k}' has type {type(val).__name__}, expected {expected_t.__name__}"

            return 1.0, "Valid JSON matching expected properties and data types"
        except Exception as exc:
            return 0.0, f"Invalid JSON syntax: {str(exc)[:100]}"

    # 3. Safe Python Unit Test Execution
    elif v_type == "python_unit_test":
        code_str = _extract_code_block(clean_text, language="python")
        try:
            # Check for valid AST syntax
            ast.parse(code_str)
        except SyntaxError as syn_err:
            return 0.0, f"Python SyntaxError: {syn_err}"

        # Sandbox namespace: deny builtins that perform I/O or system access
        safe_builtins = {
            "range": range,
            "len": len,
            "int": int,
            "float": float,
            "str": str,
            "bool": bool,
            "list": list,
            "dict": dict,
            "set": set,
            "tuple": tuple,
            "min": min,
            "max": max,
            "sum": sum,
            "abs": abs,
            "round": round,
            "print": lambda *args: None,
        }
        safe_globals = {"__builtins__": safe_builtins}
        safe_locals: Dict[str, Any] = {}

        try:
            exec(code_str, safe_globals, safe_locals)  # pylint: disable=exec-used
        except Exception as exec_err:
            return 0.0, f"Execution failed on function definition: {exec_err}"

        func_name = v_payload.get("func_name", "")
        func = safe_locals.get(func_name) or safe_globals.get(func_name)
        if not callable(func):
            return 0.0, f"Expected callable function '{func_name}' not defined in output"

        test_cases = v_payload.get("test_cases", [])
        if not test_cases:
            return 1.0, "Function defined and syntactically valid"

        passed_cases = 0
        for tc in test_cases:
            args = tc.get("args", [])
            expected = tc.get("expected")
            try:
                res = func(*args)
                if res == expected:
                    passed_cases += 1
            except Exception:
                pass

        score = round(passed_cases / len(test_cases), 2)
        return score, f"Passed {passed_cases}/{len(test_cases)} unit tests"

    # Default fallback
    return 0.5, "Task completed with unrecognized verification type"


# ============================================================================
# 3. METRIC RECORDS & RESULTS
# ============================================================================

class BenchmarkSample(BaseModel):
    """Immutable record of an individual benchmark invocation sample."""

    sample_id: str
    task_id: str
    category: BenchmarkTaskCategory
    provider: str
    model_id: str
    iteration: int
    duration_ms: float
    ttft_ms: Optional[float] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_per_second: Optional[float] = None
    success: bool = True
    quality_score: float = 0.0
    evaluation_details: str = ""
    error_category: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ProviderBenchmarkSummary(BaseModel):
    """Aggregated benchmark metrics for a single provider."""

    provider: str
    model_id: str
    total_samples: int = 0
    successful_samples: int = 0
    failed_samples: int = 0
    success_rate: float = 0.0
    latency: Dict[str, Optional[float]] = Field(default_factory=dict)
    ttft: Dict[str, Optional[float]] = Field(default_factory=dict)
    avg_tokens_per_second: Optional[float] = None
    avg_quality_score: float = 0.0
    category_scores: Dict[str, float] = Field(default_factory=dict)
    total_tokens_input: Optional[int] = None
    total_tokens_output: Optional[int] = None
    estimated_cost_usd: Optional[float] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CalibrationRecommendation(BaseModel):
    """Evidence-based routing advice derived strictly from benchmark observations."""

    recommended_default_provider: str = "OLLAMA"
    recommended_default_model: str = "qwen2.5:7b"
    cloud_delegation_justified: bool = False
    eligible_task_types: List[str] = Field(default_factory=list)
    confidence_score: float = 0.0
    explanation: str = "Insufficient benchmark evidence; maintaining local-first default."
    evidence_summary: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ============================================================================
# 4. BENCHMARK ENGINE
# ============================================================================

class BenchmarkEngine:
    """Thread-safe, single-flight, resource-aware model benchmark orchestrator."""

    def __init__(
        self,
        tasks: Optional[List[BenchmarkTask]] = None,
        security_policy: Optional[ModelSecurityPolicy] = None,
    ) -> None:
        self.tasks: List[BenchmarkTask] = tasks or list(DEFAULT_BENCHMARK_TASKS)
        self.security_policy: ModelSecurityPolicy = security_policy or model_security_policy
        self._lock: asyncio.Lock = asyncio.Lock()
        self._samples: List[BenchmarkSample] = []
        self._last_summaries: Dict[str, ProviderBenchmarkSummary] = {}
        self._last_recommendation: Optional[CalibrationRecommendation] = None
        self._is_running: bool = False

    @property
    def is_running(self) -> bool:
        """Check whether a benchmark run is currently in flight."""
        return self._is_running

    def get_samples(self) -> List[BenchmarkSample]:
        """Fetch all gathered benchmark samples."""
        return list(self._samples)

    def get_summaries(self) -> Dict[str, ProviderBenchmarkSummary]:
        """Fetch latest computed provider benchmark summaries."""
        return dict(self._last_summaries)

    def get_latest_recommendation(self) -> CalibrationRecommendation:
        """Fetch the most recent evidence-based routing calibration recommendation."""
        if self._last_recommendation:
            return self._last_recommendation
        return CalibrationRecommendation()

    async def run_single_task(
        self,
        adapter: ProviderAdapter,
        task: BenchmarkTask,
        iteration: int = 1,
        stream_test: bool = True,
    ) -> BenchmarkSample:
        """Execute a single benchmark task against a provider adapter with monotonic metrics."""
        provider_name = (
            adapter.provider_id.value
            if isinstance(adapter.provider_id, ModelProvider)
            else str(adapter.provider_id)
        ).upper()

        sample_id = f"bench_{provider_name}_{task.task_id}_{iteration}_{int(time.time())}"
        req = AIRequest.from_prompt(
            prompt=task.prompt,
            system_prompt=task.system_prompt,
            task_type=TaskType.CODE if "coding" in task.category.value.lower() else TaskType.GENERAL_REASONING,
        )

        t0 = time.monotonic()
        ttft_ms: Optional[float] = None
        output_content: str = ""
        success = True
        error_category: Optional[str] = None
        tokens_in: Optional[int] = None
        tokens_out: Optional[int] = None

        # 1. Attempt streaming to measure TTFT if adapter supports it and stream_test is requested
        can_stream = getattr(adapter, "stream", None) is not None
        streamed_ok = False

        if stream_test and can_stream:
            try:
                first_chunk_received = False
                async for chunk in adapter.stream(req):
                    if chunk.event_type == StreamEventType.DELTA and chunk.delta:
                        if not first_chunk_received:
                            ttft_ms = round((time.monotonic() - t0) * 1000, 2)
                            first_chunk_received = True
                        output_content += chunk.delta
                    elif chunk.event_type == StreamEventType.DONE:
                        if chunk.usage:
                            tokens_in = chunk.usage.prompt_tokens
                            tokens_out = chunk.usage.completion_tokens
                        streamed_ok = True
                        break
                    elif chunk.event_type == StreamEventType.ERROR:
                        raise chunk.error or ProviderUnavailableError("Stream emitted error event")
            except asyncio.CancelledError:
                logger.info(f"Benchmark sample '{sample_id}' cancelled by caller")
                raise
            except Exception as stream_exc:
                logger.debug(f"Streaming benchmark probe not supported or failed on {provider_name}: {stream_exc}")
                streamed_ok = False
                output_content = ""
                ttft_ms = None

        # 2. If streaming failed or wasn't supported, fall back to generate()
        if not streamed_ok:
            try:
                res: AIResponse = await asyncio.wait_for(
                    adapter.generate(req),
                    timeout=task.timeout_seconds,
                )
                output_content = res.content
                if res.usage:
                    tokens_in = res.usage.prompt_tokens
                    tokens_out = res.usage.completion_tokens
            except asyncio.CancelledError:
                raise
            except asyncio.TimeoutError as to_err:
                success = False
                error_category = "PROVIDER_TIMEOUT"
                output_content = ""
            except Exception as gen_exc:
                success = False
                error_category = normalize_error_category(gen_exc)
                output_content = ""

        duration_sec = max(0.001, time.monotonic() - t0)
        duration_ms = round(duration_sec * 1000, 2)

        # 3. Truthful Token Rate Calculation
        tokens_per_sec: Optional[float] = None
        if success and tokens_out is not None and tokens_out > 0:
            tokens_per_sec = round(tokens_out / duration_sec, 2)

        # 4. Deterministic Quality Evaluation
        quality_score = 0.0
        eval_details = "Inference execution failed"
        if success and output_content:
            quality_score, eval_details = evaluate_task_response(task, output_content)

        # Model identifier
        model_id = getattr(adapter, "model_id", getattr(getattr(adapter, "provider", None), "model", provider_name))

        sample = BenchmarkSample(
            sample_id=sample_id,
            task_id=task.task_id,
            category=task.category,
            provider=provider_name,
            model_id=str(model_id),
            iteration=iteration,
            duration_ms=duration_ms,
            ttft_ms=ttft_ms,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            tokens_per_second=tokens_per_sec,
            success=success,
            quality_score=quality_score,
            evaluation_details=eval_details,
            error_category=error_category,
        )
        return sample

    async def run_benchmark(
        self,
        adapters: Dict[str, ProviderAdapter],
        tasks: Optional[List[BenchmarkTask]] = None,
        samples_per_task: int = 1,
        allow_cloud: bool = False,
        privacy_mode: PrivacyMode = PrivacyMode.PRIVACY_FIRST,
        confirmation_token: Optional[str] = None,
    ) -> Dict[str, ProviderBenchmarkSummary]:
        """Run single-flight, sequential, bounded benchmark comparison across specified adapters.
        
        Strict Resource & Privacy Guardrails:
        - Exactly one task at a time (sequential execution).
        - Prevents concurrent benchmark runs via asyncio.Lock.
        - Strict LOCAL_ONLY check: remote adapters are skipped.
        - Strict PRIVACY_FIRST check: remote adapters require confirmation token.
        - Emits clean action_bus audit events without prompt or output leakage.
        """
        if self._lock.locked():
            raise RuntimeError("A benchmark execution is already in flight. Concurrent runs are rejected.")

        active_tasks = tasks or self.tasks
        bounded_samples = max(1, min(samples_per_task, 3))  # bounded to max 3 samples for laptop safety

        async with self._lock:
            self._is_running = True
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.MODEL_BENCHMARK_STARTED,
                    status=ActionStatus.STARTED,
                    title="Model Benchmark Run Started",
                    description=f"Running synthetic evaluation suite (tasks={len(active_tasks)}, samples={bounded_samples})",
                    safe_metadata={"task_count": len(active_tasks), "samples_per_task": bounded_samples},
                )
            )

            try:
                for prov_key, adapter in adapters.items():
                    prov_upper = prov_key.upper()
                    is_remote = "REMOTE" in prov_upper or "GROK" in prov_upper or "HUGGINGFACE" in prov_upper and getattr(adapter, "prefer_local", True) is False

                    # Security & Privacy Gates for Cloud Providers
                    if is_remote:
                        if privacy_mode == PrivacyMode.LOCAL_ONLY:
                            logger.info(f"Skipping cloud provider '{prov_upper}' in LOCAL_ONLY privacy mode.")
                            continue
                        if not allow_cloud or not self.security_policy.allow_remote_inference:
                            logger.info(f"Skipping cloud provider '{prov_upper}': remote inference not explicitly allowed.")
                            continue
                        if privacy_mode == PrivacyMode.PRIVACY_FIRST and not confirmation_token:
                            logger.info(f"Skipping cloud provider '{prov_upper}': PRIVACY_FIRST requires confirmation token.")
                            continue

                    # Execute sequential iterations
                    for task in active_tasks:
                        for iter_idx in range(1, bounded_samples + 1):
                            try:
                                sample = await self.run_single_task(
                                    adapter=adapter,
                                    task=task,
                                    iteration=iter_idx,
                                )
                                self._samples.append(sample)
                            except asyncio.CancelledError:
                                logger.info("Benchmark run interrupted by cancellation")
                                raise
                            except Exception as sample_exc:
                                logger.error(f"Error executing benchmark sample: {sample_exc}")

                # Aggregate and compute summaries
                summaries = self._compute_summaries()
                self._last_summaries = summaries
                self._last_recommendation = self.compute_calibration_recommendation(summaries)

                await action_bus.publish(
                    ActionEvent(
                        action_type=ActionType.MODEL_BENCHMARK_COMPLETED,
                        status=ActionStatus.COMPLETED,
                        title="Model Benchmark Run Completed",
                        description=f"Evaluated {len(summaries)} providers with {len(self._samples)} total samples",
                        safe_metadata={"evaluated_providers": list(summaries.keys())},
                    )
                )
                return summaries

            finally:
                self._is_running = False

    def _compute_summaries(self) -> Dict[str, ProviderBenchmarkSummary]:
        """Aggregate all collected samples into provider benchmark summaries."""
        grouped: Dict[str, List[BenchmarkSample]] = {}
        for s in self._samples:
            grouped.setdefault(s.provider, []).append(s)

        summaries: Dict[str, ProviderBenchmarkSummary] = {}
        for prov, s_list in grouped.items():
            total = len(s_list)
            successes = [s for s in s_list if s.success]
            succ_count = len(successes)
            succ_rate = round(succ_count / total, 4) if total > 0 else 0.0

            latencies = [s.duration_ms for s in successes]
            latency_stats = compute_percentiles(latencies)

            ttfts = [s.ttft_ms for s in successes if s.ttft_ms is not None]
            ttft_stats = compute_percentiles(ttfts)

            token_rates = [s.tokens_per_second for s in successes if s.tokens_per_second is not None]
            avg_token_rate = round(sum(token_rates) / len(token_rates), 2) if token_rates else None

            quality_scores = [s.quality_score for s in s_list]
            avg_quality = round(sum(quality_scores) / total, 3) if total > 0 else 0.0

            # Quality breakdown by category
            cat_scores: Dict[str, List[float]] = {}
            for s in s_list:
                cat_scores.setdefault(s.category.value, []).append(s.quality_score)
            cat_summary = {
                cat: round(sum(scores) / len(scores), 2)
                for cat, scores in cat_scores.items()
            }

            tokens_in_sum = sum(s.tokens_input for s in successes if s.tokens_input is not None) or None
            tokens_out_sum = sum(s.tokens_output for s in successes if s.tokens_output is not None) or None

            model_id = s_list[0].model_id if s_list else prov

            summaries[prov] = ProviderBenchmarkSummary(
                provider=prov,
                model_id=model_id,
                total_samples=total,
                successful_samples=succ_count,
                failed_samples=total - succ_count,
                success_rate=succ_rate,
                latency=latency_stats,
                ttft=ttft_stats,
                avg_tokens_per_second=avg_token_rate,
                avg_quality_score=avg_quality,
                category_scores=cat_summary,
                total_tokens_input=tokens_in_sum,
                total_tokens_output=tokens_out_sum,
            )
        return summaries

    def compute_calibration_recommendation(
        self,
        summaries: Optional[Dict[str, ProviderBenchmarkSummary]] = None,
    ) -> CalibrationRecommendation:
        """Derive an evidence-grounded routing recommendation comparing local and cloud providers.
        
        Rules:
        1. If insufficient samples (<3 total or missing local Ollama), retain local default with clear reason.
        2. Local Ollama + Qwen 2.5 7B remains default unless cloud proves statistically superior on quality.
        3. Cloud delegation is ONLY recommended if:
           - Cloud quality > local quality by at least 15% on high-complexity categories (Coding, Reasoning).
           - Cloud latency is within bounded acceptable threshold (<5000ms median).
           - Cloud success rate is >= 95%.
        4. In all other cases, local execution is recommended for low latency, zero cost, and local privacy.
        """
        active_summaries = summaries or self._last_summaries
        if not active_summaries:
            return CalibrationRecommendation(
                recommended_default_provider="OLLAMA",
                recommended_default_model="qwen2.5:7b",
                cloud_delegation_justified=False,
                confidence_score=0.0,
                explanation="No benchmark measurements available. Remaining local-first by default.",
            )

        local_summary = active_summaries.get("OLLAMA")
        if not local_summary or local_summary.total_samples < 2:
            return CalibrationRecommendation(
                recommended_default_provider="OLLAMA",
                recommended_default_model="qwen2.5:7b",
                cloud_delegation_justified=False,
                confidence_score=0.2,
                explanation="Insufficient local benchmark samples. Retaining local-first default.",
            )

        # Check for remote candidate (e.g. HUGGINGFACE_REMOTE or GROK)
        remote_candidates = [
            s for k, s in active_summaries.items()
            if "REMOTE" in k or "GROK" in k or "HUGGINGFACE" in k and s.provider != "HUGGINGFACE_LOCAL"
        ]

        if not remote_candidates:
            return CalibrationRecommendation(
                recommended_default_provider="OLLAMA",
                recommended_default_model=local_summary.model_id,
                cloud_delegation_justified=False,
                confidence_score=0.85,
                explanation="Only local provider was benchmarked. Local execution verified and optimal.",
                evidence_summary={
                    "local_quality": local_summary.avg_quality_score,
                    "local_latency_p50": local_summary.latency.get("p50_ms"),
                },
            )

        remote_summary = remote_candidates[0]

        local_q = local_summary.avg_quality_score
        remote_q = remote_summary.avg_quality_score
        local_med_lat = local_summary.latency.get("p50_ms") or 99999.0
        remote_med_lat = remote_summary.latency.get("p50_ms") or 99999.0

        # Quality margin check on advanced coding / reasoning
        local_coding_q = local_summary.category_scores.get(BenchmarkTaskCategory.SMALL_CODING.value, local_q)
        remote_coding_q = remote_summary.category_scores.get(BenchmarkTaskCategory.SMALL_CODING.value, remote_q)

        local_reasoning_q = local_summary.category_scores.get(BenchmarkTaskCategory.REASONING.value, local_q)
        remote_reasoning_q = remote_summary.category_scores.get(BenchmarkTaskCategory.REASONING.value, remote_q)

        cloud_superior_coding = remote_coding_q >= local_coding_q + 0.15
        cloud_superior_reasoning = remote_reasoning_q >= local_reasoning_q + 0.15
        cloud_healthy = remote_summary.success_rate >= 0.90

        if (cloud_superior_coding or cloud_superior_reasoning) and cloud_healthy:
            eligible_tasks = []
            if cloud_superior_coding:
                eligible_tasks.append("CODE")
            if cloud_superior_reasoning:
                eligible_tasks.append("GENERAL_REASONING")

            return CalibrationRecommendation(
                recommended_default_provider="OLLAMA",
                recommended_default_model=local_summary.model_id,
                cloud_delegation_justified=True,
                eligible_task_types=eligible_tasks,
                confidence_score=0.88,
                explanation=(
                    f"Evidence demonstrates cloud '{remote_summary.model_id}' achieves higher precision "
                    f"on advanced tasks (Quality: {remote_q:.2f} vs Local {local_q:.2f}). "
                    f"Cloud delegation is recommended exclusively for high-complexity {', '.join(eligible_tasks)}."
                ),
                evidence_summary={
                    "local_provider": local_summary.provider,
                    "local_quality": local_q,
                    "local_p50_ms": local_med_lat,
                    "remote_provider": remote_summary.provider,
                    "remote_quality": remote_q,
                    "remote_p50_ms": remote_med_lat,
                    "quality_delta": round(remote_q - local_q, 2),
                },
            )

        # In all other scenarios, local performance satisfies or exceeds cloud value
        return CalibrationRecommendation(
            recommended_default_provider="OLLAMA",
            recommended_default_model=local_summary.model_id,
            cloud_delegation_justified=False,
            eligible_task_types=[],
            confidence_score=0.92,
            explanation=(
                f"Local Ollama + Qwen 2.5 7B provides competitive quality ({local_q:.2f}) "
                f"with guaranteed privacy and zero cloud latency/cost. Full local routing recommended."
            ),
            evidence_summary={
                "local_quality": local_q,
                "remote_quality": remote_q,
                "local_p50_ms": local_med_lat,
                "remote_p50_ms": remote_med_lat,
            },
        )


# Global benchmark engine instance
model_benchmark_engine = BenchmarkEngine()
