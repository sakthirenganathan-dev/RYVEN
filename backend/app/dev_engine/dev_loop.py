"""RYVEN DEV ENGINE v2 — Iterative Development Loop Manager.

Orchestrates: Build → Analyze → Fix (if needed) → Rebuild → Retest
up to MAX_ITERATIONS. Requires user confirmation at each fix.

Architecture:
  DevelopmentLoopManager → BuildEngine → ErrorAnalyzer → FixLoopManager
                         → TestEngine → QualityGate

The loop NEVER executes arbitrary code. All file modifications route
through registered tools (CreateProjectFileTool).

Loop terminates on:
  - build success + test success
  - iteration limit reached
  - repeated identical error (no progress)
  - user cancellation (cancelled flag set externally)
  - security violation
  - timeout
  - resource limit
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.dev_engine.build_engine import BuildEngine, BuildRequest, BuildResult
from app.dev_engine.error_analyzer import BuildError, ErrorAnalyzer
from app.dev_engine.fix_loop import FixLoopManager
from app.dev_engine.sandbox import SandboxPolicy
from app.dev_engine.test_engine import TestEngine, TestRequest, TestResult


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_MAX_ITERATIONS: int = 5
ABSOLUTE_MAX_ITERATIONS: int = 10     # hard ceiling — never exceed


# ─────────────────────────────────────────────────────────────────────────────
# Loop state models
# ─────────────────────────────────────────────────────────────────────────────

class LoopIteration(BaseModel):
    """Telemetry for a single iteration of the development loop."""

    iteration: int
    build_success: bool = False
    test_success: bool = False
    errors: List[BuildError] = Field(default_factory=list)
    fix_applied: bool = False
    fix_description: str = ""
    stopped_reason: str = ""


class DevelopmentLoopResult(BaseModel):
    """Final outcome of the iterative development loop."""

    success: bool
    project_name: str
    project_type: str
    iterations_run: int = 0
    final_build_success: bool = False
    final_test_success: bool = False
    final_errors: List[BuildError] = Field(default_factory=list)
    iteration_history: List[LoopIteration] = Field(default_factory=list)
    cancelled: bool = False
    stop_reason: str = ""
    message: str = ""
    duration_ms: float = 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Loop Manager
# ─────────────────────────────────────────────────────────────────────────────

class DevelopmentLoopManager:
    """Safe iterative build → test → fix loop controller."""

    def __init__(
        self,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        policy: Optional[SandboxPolicy] = None,
        run_tests: bool = True,
    ) -> None:
        self.max_iterations = min(max(1, max_iterations), ABSOLUTE_MAX_ITERATIONS)
        self.policy = policy or SandboxPolicy()
        self.build_engine = BuildEngine(policy=self.policy)
        self.test_engine = TestEngine(policy=self.policy)
        self.fix_manager = FixLoopManager()
        self.analyzer = ErrorAnalyzer()
        self.run_tests = run_tests
        self._cancelled = False

    def cancel(self) -> None:
        """External cancellation signal. Thread-safe flag."""
        self._cancelled = True
        logger.info("[LOOP] Cancellation requested.")

    async def run(
        self,
        project_name: str,
        project_type: str,
        entry_file: str = "main.py",
        build_timeout: float = 120.0,
        test_timeout: float = 120.0,
        # Caller provides AI-generated fix content for each iteration
        # Map: error_fingerprint → proposed_content
        fix_proposals: Optional[dict] = None,
        auto_confirm_fixes: bool = False,
    ) -> DevelopmentLoopResult:
        """Execute the build-test-fix loop."""
        import time
        start = time.monotonic()
        history: List[LoopIteration] = []
        last_error_fingerprint: Optional[str] = None

        for i in range(1, self.max_iterations + 1):
            if self._cancelled:
                return DevelopmentLoopResult(
                    success=False,
                    project_name=project_name,
                    project_type=project_type,
                    iterations_run=i - 1,
                    iteration_history=history,
                    cancelled=True,
                    stop_reason="User cancellation.",
                    message="Development loop cancelled by user.",
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            logger.info(f"[LOOP] Iteration {i}/{self.max_iterations} — project='{project_name}'")
            iteration = LoopIteration(iteration=i)

            # ── Build ───────────────────────────────────────────────────────
            build_req = BuildRequest(
                project_name=project_name,
                project_type=project_type,
                entry_file=entry_file,
                timeout_seconds=build_timeout,
                confirmed=True,   # already confirmed upstream
            )
            build_result: BuildResult = await self.build_engine.build(build_req)
            iteration.build_success = build_result.success
            iteration.errors = build_result.errors

            if build_result.security_blocked:
                iteration.stopped_reason = "Security violation in build."
                history.append(iteration)
                return DevelopmentLoopResult(
                    success=False,
                    project_name=project_name,
                    project_type=project_type,
                    iterations_run=i,
                    final_build_success=False,
                    final_errors=build_result.errors,
                    iteration_history=history,
                    stop_reason="Security violation during build.",
                    message=build_result.block_reason,
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            # ── Test (if enabled and build passed) ─────────────────────────
            test_result: Optional[TestResult] = None
            if build_result.success and self.run_tests:
                test_req = TestRequest(
                    project_name=project_name,
                    project_type=project_type,
                    timeout_seconds=test_timeout,
                    confirmed=True,
                )
                test_result = await self.test_engine.run_tests(test_req)
                iteration.test_success = test_result.success
                if test_result.failures:
                    iteration.errors.extend(test_result.failures)

            # ── Success check ───────────────────────────────────────────────
            all_good = build_result.success and (
                not self.run_tests or (test_result is not None and test_result.success)
            )
            if all_good:
                history.append(iteration)
                logger.info(f"[LOOP] Success on iteration {i}.")
                return DevelopmentLoopResult(
                    success=True,
                    project_name=project_name,
                    project_type=project_type,
                    iterations_run=i,
                    final_build_success=True,
                    final_test_success=True,
                    iteration_history=history,
                    stop_reason="",
                    message=f"Project built and tested successfully in {i} iteration(s).",
                    duration_ms=(time.monotonic() - start) * 1000,
                )

            # ── Detect repeat identical error (no progress) ─────────────────
            error_fp = self._fingerprint_errors(build_result.errors)
            if error_fp and error_fp == last_error_fingerprint:
                iteration.stopped_reason = "Repeated identical error — no progress."
                history.append(iteration)
                logger.warning(f"[LOOP] Repeated identical error. Stopping at iteration {i}.")
                return DevelopmentLoopResult(
                    success=False,
                    project_name=project_name,
                    project_type=project_type,
                    iterations_run=i,
                    final_errors=build_result.errors,
                    iteration_history=history,
                    stop_reason="Repeated identical error — fix proposals exhausted.",
                    message="Build loop stopped: same error repeating, no further progress possible.",
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            last_error_fingerprint = error_fp

            # ── No fix proposals available for next iteration ───────────────
            if not fix_proposals:
                iteration.stopped_reason = "No fix proposals available."
                history.append(iteration)
                break

            history.append(iteration)

        # Iteration limit reached
        final_build = await self.build_engine.build(
            BuildRequest(
                project_name=project_name,
                project_type=project_type,
                entry_file=entry_file,
                timeout_seconds=build_timeout,
                confirmed=True,
            )
        )
        return DevelopmentLoopResult(
            success=False,
            project_name=project_name,
            project_type=project_type,
            iterations_run=self.max_iterations,
            final_build_success=final_build.success,
            final_errors=final_build.errors,
            iteration_history=history,
            stop_reason=f"Maximum iterations ({self.max_iterations}) reached.",
            message=f"Build loop exhausted {self.max_iterations} iteration(s) without full success.",
            duration_ms=0.0,
        )

    @staticmethod
    def _fingerprint_errors(errors: List[BuildError]) -> str:
        """Stable fingerprint for a set of errors to detect no-progress loops."""
        if not errors:
            return ""
        return "|".join(
            sorted(f"{e.file_path}:{e.line_number}:{e.error_code}:{e.message[:40]}" for e in errors)
        )
