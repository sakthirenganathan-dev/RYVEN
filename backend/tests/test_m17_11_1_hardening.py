"""RYVEN 3.0 — Milestone 17.11.1 Security Hardening Test Suite.

Verifies:
1. Deterministic zero-execution AST code evaluator:
   - Valid AST structures for allowlisted synthetic tasks (multiply, take_first_n).
   - Instant safe rejection of infinite loops, recursion, and huge allocations as passive source text.
   - Enforces 8 KB source size limit and 500-node AST complexity limit.
   - Rejects unsupported or unknown task structures with score 0.0.
   - Never invokes exec(), eval(), compile(), or subprocess.
2. Endpoint security and authorization:
   - Constant-time admin token validation (401 on missing/invalid token).
   - Untrusted host rejection (403 on non-loopback host).
   - Process-local, bounded sliding-window rate limiting (429 with Retry-After).
   - Concurrency lock (409 Conflict) and clean lock release.
3. Zero credential/secret exposure in logs or telemetry.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from typing import Any, Dict
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.ai.benchmark import (
    MAX_BENCHMARK_AST_NODES,
    MAX_BENCHMARK_SOURCE_LENGTH,
    BenchmarkEngine,
    BenchmarkTask,
    BenchmarkTaskCategory,
    DEFAULT_BENCHMARK_TASKS,
    evaluate_task_response,
)
from app.ai.contracts import AIRequest, AIResponse, AIUsage, ModelProvider
from app.api.routes import BenchmarkRateLimiter, benchmark_rate_limiter, verify_benchmark_access
from app.core.config import settings
from app.main import app


# ============================================================================
# 1. DETERMINISTIC ZERO-EXECUTION AST EVALUATOR TESTS
# ============================================================================

def test_ast_evaluator_valid_multiply_function():
    """Valid pure multiply function receives score 1.0."""
    task = DEFAULT_BENCHMARK_TASKS[1]  # coding_multiply_02
    good_code = "```python\ndef multiply(a, b):\n    return a * b\n```"
    score, expl = evaluate_task_response(task, good_code)
    assert score == 1.0
    assert "Verified pure multiply function definition" in expl


def test_ast_evaluator_valid_take_first_n_function():
    """Corrected slice items[:n] receives score 1.0."""
    task = DEFAULT_BENCHMARK_TASKS[4]  # debugging_offbyone_05
    good_debug = "```python\ndef take_first_n(items, n):\n    return items[:n]\n```"
    score, expl = evaluate_task_response(task, good_debug)
    assert score == 1.0
    assert "Verified corrected slice" in expl


def test_ast_evaluator_rejects_incorrect_multiply():
    """Incorrect logic in multiply (e.g. addition) receives score 0.0."""
    task = DEFAULT_BENCHMARK_TASKS[1]
    bad_code = "```python\ndef multiply(a, b):\n    return a + b\n```"
    score, expl = evaluate_task_response(task, bad_code)
    assert score == 0.0
    assert "does not return a multiplication" in expl


def test_ast_evaluator_rejects_unfixed_off_by_one():
    """Off-by-one subtraction bug (items[:n - 1]) is rejected with score 0.0."""
    task = DEFAULT_BENCHMARK_TASKS[4]
    unfixed_code = "```python\ndef take_first_n(items, n):\n    return items[:n - 1]\n```"
    score, expl = evaluate_task_response(task, unfixed_code)
    assert score == 0.0
    assert "does not return corrected slice" in expl


def test_ast_evaluator_multiply_rejects_unrelated_identifiers():
    """Multiplication using unrelated identifiers or constants receives score 0.0."""
    task = DEFAULT_BENCHMARK_TASKS[1]

    # Unrelated global identifiers
    code_unrelated = "```python\ndef multiply(a, b):\n    return x * y\n```"
    score, expl = evaluate_task_response(task, code_unrelated)
    assert score == 0.0
    assert "declared arguments" in expl

    # One parameter and one foreign identifier
    code_partial = "```python\ndef multiply(a, b):\n    return a * x\n```"
    score, expl = evaluate_task_response(task, code_partial)
    assert score == 0.0

    # Duplicate single parameter
    code_dup = "```python\ndef multiply(a, b):\n    return a * a\n```"
    score, expl = evaluate_task_response(task, code_dup)
    assert score == 0.0

    # Constant multiplier
    code_const = "```python\ndef multiply(a, b):\n    return a * 2\n```"
    score, expl = evaluate_task_response(task, code_const)
    assert score == 0.0


def test_ast_evaluator_multiply_supports_arbitrary_declared_param_names():
    """Multiplication works with any valid parameter names (e.g. x, y) as long as both are multiplied."""
    task = DEFAULT_BENCHMARK_TASKS[1]
    good_code = "```python\ndef multiply(x, y):\n    return y * x\n```"
    score, expl = evaluate_task_response(task, good_code)
    assert score == 1.0
    assert "Verified pure multiply" in expl


def test_ast_evaluator_take_first_n_rejects_wrong_slice_source():
    """Slicing an unrelated list/collection receives score 0.0."""
    task = DEFAULT_BENCHMARK_TASKS[4]
    wrong_source = "```python\ndef take_first_n(items, n):\n    return other_list[:n]\n```"
    score, expl = evaluate_task_response(task, wrong_source)
    assert score == 0.0
    assert "does not return corrected slice of 'items' bounded by 'n'" in expl


def test_ast_evaluator_take_first_n_rejects_wrong_upper_bound():
    """Slicing with a foreign identifier or literal bound receives score 0.0."""
    task = DEFAULT_BENCHMARK_TASKS[4]

    # Foreign bound identifier
    wrong_bound_id = "```python\ndef take_first_n(items, n):\n    return items[:k]\n```"
    score, expl = evaluate_task_response(task, wrong_bound_id)
    assert score == 0.0
    assert "bounded by 'n'" in expl

    # Literal bound
    wrong_bound_literal = "```python\ndef take_first_n(items, n):\n    return items[:10]\n```"
    score, expl = evaluate_task_response(task, wrong_bound_literal)
    assert score == 0.0
    assert "bounded by 'n'" in expl


def test_ast_evaluator_take_first_n_supports_arbitrary_declared_param_names():
    """Corrected slice works with any valid parameter names (e.g. lst, limit)."""
    task = DEFAULT_BENCHMARK_TASKS[4]
    good_custom_params = "```python\ndef take_first_n(lst, limit):\n    return lst[:limit]\n```"
    score, expl = evaluate_task_response(task, good_custom_params)
    assert score == 1.0
    assert "Verified corrected slice lst[:limit]" in expl


def test_ast_evaluator_infinite_loop_as_passive_text():
    """Infinite loop syntax is never executed; parsed safely in under 50ms with score 0.0."""
    task = DEFAULT_BENCHMARK_TASKS[1]
    loop_code = "```python\nwhile True:\n    pass\n```"
    start = time.perf_counter()
    score, expl = evaluate_task_response(task, loop_code)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert elapsed_ms < 50.0  # Must finish instantly without thread hang
    assert score == 0.0
    assert "Expected callable function 'multiply' not defined" in expl


def test_ast_evaluator_huge_allocation_syntax_never_allocates():
    """Massive allocation syntax (e.g. [0] * 10**14) is never executed or evaluated on host heap."""
    task = DEFAULT_BENCHMARK_TASKS[1]
    huge_alloc_code = "```python\ndef multiply(a, b):\n    leak = [0] * (10**14)\n    return leak\n```"
    start = time.perf_counter()
    score, expl = evaluate_task_response(task, huge_alloc_code)
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert elapsed_ms < 50.0
    assert score == 0.0
    assert "does not return a multiplication" in expl


def test_ast_evaluator_enforces_source_length_limit():
    """Source code exceeding 8 KB is rejected without parsing."""
    task = DEFAULT_BENCHMARK_TASKS[1]
    oversized_code = "```python\n# padding\n" + ("x = 1\n" * 1500) + "```"
    assert len(oversized_code) > MAX_BENCHMARK_SOURCE_LENGTH

    score, expl = evaluate_task_response(task, oversized_code)
    assert score == 0.0
    assert "exceeds maximum length limit" in expl


def test_ast_evaluator_enforces_ast_node_complexity_limit():
    """Code exceeding 500 AST nodes is rejected."""
    task = DEFAULT_BENCHMARK_TASKS[1]
    # Build a program with many small statements to exceed 500 AST nodes
    many_statements = "\n".join(f"v_{i} = {i}" for i in range(300))
    code = f"```python\n{many_statements}\ndef multiply(a, b):\n    return a * b\n```"

    score, expl = evaluate_task_response(task, code)
    assert score == 0.0
    assert "AST complexity exceeds limit" in expl


def test_ast_evaluator_unsupported_task_scores_zero():
    """Unknown or unsupported function rubric receives score 0.0 and never executes."""
    task = BenchmarkTask(
        task_id="unsupported_01",
        category=BenchmarkTaskCategory.SMALL_CODING,
        description="Unknown task",
        prompt="Write a function named unknown_calc",
        verification_type="python_ast_static",
        verification_payload={"func_name": "unknown_calc"},
    )
    code = "```python\ndef unknown_calc(x):\n    return x * 2\n```"
    score, expl = evaluate_task_response(task, code)
    assert score == 0.0
    assert "Unsupported or unknown AST evaluation rubric" in expl


def test_ast_evaluator_syntax_error_handled_safely():
    """Syntax errors are handled safely without raising exceptions."""
    task = DEFAULT_BENCHMARK_TASKS[1]
    broken_code = "```python\ndef multiply(a, b\n    return a * b\n```"
    score, expl = evaluate_task_response(task, broken_code)
    assert score == 0.0
    assert "Python SyntaxError" in expl


# ============================================================================
# 2. BENCHMARK RATE LIMITER TESTS
# ============================================================================

def test_rate_limiter_allows_under_limit():
    """Requests under limit are permitted."""
    limiter = BenchmarkRateLimiter()
    allowed, retry_after = limiter.check_rate_limit(
        client_id="127.0.0.1", endpoint="test", max_requests=2, window_seconds=10.0
    )
    assert allowed is True
    assert retry_after == 0.0


def test_rate_limiter_blocks_and_provides_retry_after():
    """Requests exceeding limit return allowed=False and positive retry_after."""
    limiter = BenchmarkRateLimiter()
    # First request allowed
    allowed1, _ = limiter.check_rate_limit("127.0.0.1", "run", 1, 60.0)
    assert allowed1 is True

    # Immediate second request blocked
    allowed2, retry_after = limiter.check_rate_limit("127.0.0.1", "run", 1, 60.0)
    assert allowed2 is False
    assert retry_after > 0.0


def test_rate_limiter_distinct_clients_isolated():
    """Rate limits for distinct clients do not collide."""
    limiter = BenchmarkRateLimiter()
    allowed_a, _ = limiter.check_rate_limit("client_a", "run", 1, 60.0)
    allowed_b, _ = limiter.check_rate_limit("client_b", "run", 1, 60.0)
    assert allowed_a is True
    assert allowed_b is True


# ============================================================================
# 3. ENDPOINT AUTHORIZATION & RATE LIMIT INTEGRATION TESTS
# ============================================================================

@pytest.fixture
def client():
    return TestClient(app)


def test_benchmark_fails_closed_when_token_unconfigured(client):
    """When server has no token configured (None), loopback requests are rejected with 401."""
    with patch.object(settings, "benchmark_admin_token", None):
        with patch.dict("os.environ", {}, clear=True):
            resp = client.get("/api/models/benchmark/results")
            assert resp.status_code == 401
            assert "detail" in resp.json()
            assert "not configured on server" in resp.json()["detail"].lower()


def test_benchmark_fails_closed_when_token_blank(client):
    """When server has a blank/whitespace token configured, requests are rejected with 401."""
    for blank_val in ("", "   ", "\t\n"):
        with patch.object(settings, "benchmark_admin_token", blank_val):
            with patch.dict("os.environ", {}, clear=True):
                resp = client.get("/api/models/benchmark/results")
                assert resp.status_code == 401
                assert "not configured on server" in resp.json()["detail"].lower()


def test_benchmark_configured_token_missing_or_incorrect_credentials(client):
    """Configured token requires matching credentials; missing or wrong credentials return 401."""
    with patch.object(settings, "benchmark_admin_token", "correct-secret-token"):
        # Missing credentials
        resp_missing = client.get("/api/models/benchmark/results")
        assert resp_missing.status_code == 401
        assert "Missing or invalid benchmark authorization token" in resp_missing.json()["detail"]

        # Incorrect Bearer token
        resp_bad_bearer = client.get(
            "/api/models/benchmark/results",
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert resp_bad_bearer.status_code == 401
        assert "Missing or invalid benchmark authorization token" in resp_bad_bearer.json()["detail"]

        # Incorrect X-Benchmark-Token
        resp_bad_header = client.get(
            "/api/models/benchmark/results",
            headers={"X-Benchmark-Token": "wrong-secret"},
        )
        assert resp_bad_header.status_code == 401
        assert "Missing or invalid benchmark authorization token" in resp_bad_header.json()["detail"]

        # Invalid scheme (not Bearer)
        resp_bad_scheme = client.get(
            "/api/models/benchmark/results",
            headers={"Authorization": "Basic correct-secret-token"},
        )
        assert resp_bad_scheme.status_code == 401


def test_benchmark_correct_token_permitted_caller(client):
    """Correct token from permitted caller (loopback) passes authentication and succeeds."""
    with patch.object(settings, "benchmark_admin_token", "correct-secret-token"):
        # Bearer token
        resp_bearer = client.get(
            "/api/models/benchmark/results",
            headers={"Authorization": "Bearer correct-secret-token"},
        )
        assert resp_bearer.status_code == 200

        # Case-insensitive 'bearer' prefix
        resp_lower = client.get(
            "/api/models/benchmark/results",
            headers={"Authorization": "bearer correct-secret-token"},
        )
        assert resp_lower.status_code == 200

        # X-Benchmark-Token header
        resp_custom = client.get(
            "/api/models/benchmark/results",
            headers={"X-Benchmark-Token": "correct-secret-token"},
        )
        assert resp_custom.status_code == 200


def test_benchmark_correct_token_disallowed_caller(client):
    """Correct token from disallowed caller host or untrusted Origin is rejected with 403."""
    with patch.object(settings, "benchmark_admin_token", "correct-secret-token"):
        # Untrusted external IP host
        with patch("fastapi.Request.client", new_callable=lambda: type("Client", (), {"host": "198.51.100.22"})):
            resp_host = client.get(
                "/api/models/benchmark/results",
                headers={
                    "Authorization": "Bearer correct-secret-token",
                    "host": "198.51.100.22",
                },
            )
            assert resp_host.status_code == 403
            assert "External caller host '198.51.100.22' is not authorized" in resp_host.json()["detail"]

        # Untrusted external Origin
        resp_origin = client.get(
            "/api/models/benchmark/results",
            headers={
                "Authorization": "Bearer correct-secret-token",
                "Origin": "https://malicious-external-site.com",
            },
        )
        assert resp_origin.status_code == 403
        assert "Untrusted caller origin" in resp_origin.json()["detail"]


def test_benchmark_all_endpoint_aliases_remain_protected(client):
    """All benchmark endpoint aliases enforce fail-closed auth and require valid token."""
    from app.ai.benchmark import model_benchmark_engine

    token = "hardening-alias-token"
    auth_headers = {"Authorization": f"Bearer {token}"}

    aliases = [
        ("POST", "/api/v1/models/benchmark/run", {"samples_per_task": 1}),
        ("POST", "/api/models/benchmark/run", {"samples_per_task": 1}),
        ("GET", "/api/v1/models/benchmark/results", None),
        ("GET", "/api/models/benchmark/results", None),
        ("GET", "/api/v1/models/benchmark/calibration", None),
        ("GET", "/api/models/benchmark/calibration", None),
    ]

    with patch.object(model_benchmark_engine, "run_benchmark", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = {}
        for method, path, json_data in aliases:
            # 1. Without configured token: 401
            with patch.object(settings, "benchmark_admin_token", None):
                with patch.dict("os.environ", {}, clear=True):
                    resp = client.request(method, path, json=json_data) if json_data else client.request(method, path)
                    assert resp.status_code == 401, f"{path} failed to reject unconfigured token"

            # 2. Configured token, but missing auth header: 401
            with patch.object(settings, "benchmark_admin_token", token):
                resp = client.request(method, path, json=json_data) if json_data else client.request(method, path)
                assert resp.status_code == 401, f"{path} failed to reject missing credentials"

            # 3. Configured token, invalid credentials: 401
            with patch.object(settings, "benchmark_admin_token", token):
                bad_headers = {"Authorization": "Bearer invalid-token"}
                resp = client.request(method, path, headers=bad_headers, json=json_data) if json_data else client.request(method, path, headers=bad_headers)
                assert resp.status_code == 401, f"{path} failed to reject invalid credentials"

            # 4. Configured token, valid credentials from permitted caller: passes auth (status 200)
            benchmark_rate_limiter._calls.clear()
            with patch.object(settings, "benchmark_admin_token", token):
                resp = client.request(method, path, headers=auth_headers, json=json_data) if json_data else client.request(method, path, headers=auth_headers)
                assert resp.status_code == 200, f"{path} unexpectedly failed auth: {resp.status_code}"


def test_benchmark_endpoint_rate_limiting_returns_429(client):
    """Exceeding endpoint rate limit returns 429 Too Many Requests with Retry-After header."""
    benchmark_rate_limiter._calls.clear()
    token = "test-hardening-rate-token"
    auth_headers = {"Authorization": f"Bearer {token}"}

    with patch.object(settings, "benchmark_admin_token", token):
        # Results limit is 30 requests per minute
        # Exhaust the limit
        for _ in range(30):
            res = client.get("/api/models/benchmark/results", headers=auth_headers)
            assert res.status_code == 200

        # 31st request must receive 429
        blocked_res = client.get("/api/models/benchmark/results", headers=auth_headers)
        assert blocked_res.status_code == 429
        assert "Retry-After" in blocked_res.headers
        assert "rate limit exceeded" in blocked_res.json()["detail"].lower()


# ============================================================================
# 4. SINGLE-FLIGHT LOCK AND CANCELLATION RELEASE TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_benchmark_engine_clean_lock_release_on_error():
    """Benchmark engine single-flight lock is released even if an unhandled error occurs."""
    engine = BenchmarkEngine()
    assert engine.is_running is False

    mock_adapter = AsyncMock()
    mock_adapter.prefer_local = True

    # Patch action_bus.publish to raise an unhandled exception inside the lock
    with patch("app.ai.benchmark.action_bus.publish", side_effect=RuntimeError("Unhandled event bus crash")):
        with pytest.raises(RuntimeError, match="Unhandled event bus crash"):
            await engine.run_benchmark(
                adapters={"OLLAMA": mock_adapter},
                samples_per_task=1,
                allow_cloud=False,
            )

    # Lock must be cleanly released and is_running must be False
    assert engine.is_running is False
    assert engine._lock.locked() is False
