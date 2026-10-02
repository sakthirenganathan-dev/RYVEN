"""RYVEN DEV ENGINE v2 — Test Engine.

Runs approved test suites via SandboxProcessRunner.
Parses output into structured TestResult with counts.

Supported test runners per project type:
  python_app / python_api  → pytest
  react_ts / react         → npm test (if configured)

Test results always reflect reality. No fake pass is ever reported.
"""

from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.dev_engine.error_analyzer import BuildError, ErrorAnalyzer
from app.dev_engine.sandbox import (
    SandboxPolicy,
    SandboxProcessRequest,
    SandboxProcessRunner,
)
from app.tools.project_tool import resolve_project_path


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class TestRequest(BaseModel):
    """Controlled specification for a test run."""
    __test__ = False

    project_name: str
    project_type: str
    test_path: str = "."               # relative path inside project; "." = run all
    timeout_seconds: float = 120.0
    confirmed: bool = False


class TestResult(BaseModel):
    """Structured outcome of a test run."""
    __test__ = False

    success: bool
    project_name: str
    project_type: str
    tests_total: int = 0
    tests_passed: int = 0
    tests_failed: int = 0
    tests_skipped: int = 0
    failures: List[BuildError] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    security_blocked: bool = False
    block_reason: str = ""
    message: str = ""


# Regex to parse pytest summary line:
# e.g.  "5 passed, 1 failed, 2 skipped in 0.45s"
_PYTEST_SUMMARY_RE = re.compile(
    r"(?:(\d+) passed)?[,\s]*(?:(\d+) failed)?[,\s]*(?:(\d+) skipped)?[,\s]*(?:(\d+) error(?:s)?)?",
    re.IGNORECASE,
)


class TestEngine:
    """Runs project tests inside the SandboxProcessRunner."""
    __test__ = False

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()
        self.runner = SandboxProcessRunner(policy=self.policy)
        self.analyzer = ErrorAnalyzer()

    async def run_tests(self, request: TestRequest) -> TestResult:
        """Run tests. confirmed must be True."""
        if not request.confirmed:
            reason = "Test execution requires prior user confirmation."
            logger.warning(f"[TEST BLOCKED] {reason} project='{request.project_name}'")
            return TestResult(
                success=False,
                project_name=request.project_name,
                project_type=request.project_type,
                message=reason,
                security_blocked=True,
                block_reason=reason,
            )

        ptype = request.project_type.lower().strip()

        if ptype in {"python_app", "python_api", "python"}:
            return await self._run_pytest(request)
        elif ptype in {"react_ts", "react"}:
            return await self._run_npm_test(request)
        else:
            reason = f"No approved test runner for project type '{request.project_type}'."
            return TestResult(
                success=False,
                project_name=request.project_name,
                project_type=request.project_type,
                message=reason,
                security_blocked=True,
                block_reason=reason,
            )

    # ── pytest ──────────────────────────────────────────────────────────────

    async def _run_pytest(self, request: TestRequest) -> TestResult:
        import os
        project_dir = resolve_project_path(request.project_name)
        if not project_dir:
            return TestResult(
                success=False,
                project_name=request.project_name,
                project_type=request.project_type,
                message="Invalid project directory.",
            )

        # pytest -v --tb=short inside project directory
        args = ["-m", "pytest", "-v", "--tb=short", "-q"]

        # Allow scoped test path (must be relative, no traversal)
        test_path = request.test_path.strip()
        if test_path and test_path != ".":
            # security: no absolute paths or traversal
            if ".." in test_path or os.path.isabs(test_path):
                return TestResult(
                    success=False,
                    project_name=request.project_name,
                    project_type=request.project_type,
                    message=f"Invalid test path: {test_path!r}",
                    security_blocked=True,
                    block_reason="Test path traversal rejected.",
                )
            args.append(test_path)

        proc_req = SandboxProcessRequest(
            project_name=request.project_name,
            executable="python",
            arguments=args,
            timeout_seconds=request.timeout_seconds,
            operation_label=f"pytest — {request.project_name}",
        )
        result = await self.runner.run(proc_req)

        total, passed, failed, skipped = self._parse_pytest_summary(
            result.stdout + result.stderr
        )
        failures = self.analyzer.analyze_pytest(result)

        return TestResult(
            success=result.success and failed == 0,
            project_name=request.project_name,
            project_type=request.project_type,
            tests_total=total,
            tests_passed=passed,
            tests_failed=failed,
            tests_skipped=skipped,
            failures=failures,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            security_blocked=result.security_blocked,
            block_reason=result.block_reason,
            message=result.message,
        )

    # ── npm test ─────────────────────────────────────────────────────────────

    async def _run_npm_test(self, request: TestRequest) -> TestResult:
        proc_req = SandboxProcessRequest(
            project_name=request.project_name,
            executable="npm",
            arguments=["test", "--", "--watchAll=false"],
            timeout_seconds=request.timeout_seconds,
            operation_label=f"npm test — {request.project_name}",
        )
        result = await self.runner.run(proc_req)
        failures = self.analyzer.analyze_pytest(result)  # generic parse

        return TestResult(
            success=result.success,
            project_name=request.project_name,
            project_type=request.project_type,
            failures=failures,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
            timed_out=result.timed_out,
            security_blocked=result.security_blocked,
            block_reason=result.block_reason,
            message=result.message,
        )

    # ── parsing helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_pytest_summary(output: str):
        """Extract (total, passed, failed, skipped) from pytest output."""
        passed, failed, skipped = 0, 0, 0
        for line in reversed(output.splitlines()):
            m = _PYTEST_SUMMARY_RE.search(line)
            if m and any(m.groups()):
                passed = int(m.group(1) or 0)
                failed = int(m.group(2) or 0)
                skipped = int(m.group(3) or 0)
                break
        return passed + failed + skipped, passed, failed, skipped
