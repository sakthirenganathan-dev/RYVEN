"""RYVEN DEV ENGINE v2 — Quality Gate.

Enforces a go/no-go checkpoint before any project is declared complete,
committed to git, or deployed.

Checks:
  1. All expected files exist and are non-empty on disk.
  2. Dependencies manifest exists (package.json or requirements.txt).
  3. Build succeeded (BuildResult.success).
  4. Tests passed (TestResult.success) when available.
  5. No unresolved critical build errors.
  6. No active security violations.

States:
  PASS    — all required checks passed.
  FAIL    — one or more checks failed (fixable).
  BLOCKED — a security violation or infrastructure error prevents any
            meaningful evaluation.
"""

from __future__ import annotations

import os
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.dev_engine.build_engine import BuildResult
from app.dev_engine.error_analyzer import BuildError
from app.dev_engine.test_engine import TestResult
from app.tools.project_tool import resolve_project_path


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class QualityCheckItem(BaseModel):
    """Result of a single quality check."""

    name: str
    passed: bool
    message: str


class QualityGateResult(BaseModel):
    """Overall outcome of the quality gate evaluation."""

    state: str = "FAIL"      # "PASS" | "FAIL" | "BLOCKED"
    project_name: str = ""
    checks: List[QualityCheckItem] = Field(default_factory=list)
    critical_errors: List[BuildError] = Field(default_factory=list)
    message: str = ""

    @property
    def passed(self) -> bool:
        return self.state == "PASS"


# ─────────────────────────────────────────────────────────────────────────────
# Gate
# ─────────────────────────────────────────────────────────────────────────────

class QualityGate:
    """Evaluates whether a project meets quality criteria before release/deploy."""

    def evaluate(
        self,
        project_name: str,
        expected_files: List[str],
        build_result: Optional[BuildResult] = None,
        test_result: Optional[TestResult] = None,
    ) -> QualityGateResult:
        """Run all quality checks and return QualityGateResult."""
        checks: List[QualityCheckItem] = []
        critical_errors: List[BuildError] = []
        blocked = False

        # 1. Project directory exists
        project_dir = resolve_project_path(project_name)
        if not project_dir or not os.path.isdir(project_dir):
            return QualityGateResult(
                state="BLOCKED",
                project_name=project_name,
                checks=[QualityCheckItem(
                    name="Project Directory",
                    passed=False,
                    message="Project directory does not exist — cannot evaluate quality.",
                )],
                message="Quality Gate BLOCKED: project directory not found.",
            )

        # 2. Expected files present and non-empty
        missing_files = []
        empty_files = []
        for rel_path in expected_files:
            full_path = os.path.join(project_dir, rel_path.replace("/", os.sep))
            if not os.path.isfile(full_path):
                missing_files.append(rel_path)
            elif os.path.getsize(full_path) == 0:
                empty_files.append(rel_path)

        files_ok = not missing_files and not empty_files
        file_msg = "All expected files present and non-empty." if files_ok else (
            f"Missing: {missing_files}" if missing_files else f"Empty: {empty_files}"
        )
        checks.append(QualityCheckItem(name="File Presence", passed=files_ok, message=file_msg))

        # 3. Dependency manifest exists
        has_package_json = os.path.isfile(os.path.join(project_dir, "package.json"))
        has_requirements = os.path.isfile(os.path.join(project_dir, "requirements.txt"))
        dep_ok = has_package_json or has_requirements
        dep_msg = (
            "Dependency manifest found."
            if dep_ok
            else "No package.json or requirements.txt found."
        )
        checks.append(QualityCheckItem(name="Dependency Manifest", passed=dep_ok, message=dep_msg))

        # 4. Build result
        if build_result is not None:
            if build_result.security_blocked:
                blocked = True
                checks.append(QualityCheckItem(
                    name="Build",
                    passed=False,
                    message=f"Build BLOCKED: {build_result.block_reason}",
                ))
            else:
                build_ok = build_result.success
                checks.append(QualityCheckItem(
                    name="Build",
                    passed=build_ok,
                    message="Build succeeded." if build_ok else f"Build failed (exit={build_result.exit_code}).",
                ))
                if not build_ok:
                    critical_errors.extend(build_result.errors)
        else:
            checks.append(QualityCheckItem(
                name="Build",
                passed=False,
                message="Build has not been run yet.",
            ))

        # 5. Test result
        if test_result is not None:
            if test_result.security_blocked:
                blocked = True
                checks.append(QualityCheckItem(
                    name="Tests",
                    passed=False,
                    message=f"Tests BLOCKED: {test_result.block_reason}",
                ))
            else:
                test_ok = test_result.success
                checks.append(QualityCheckItem(
                    name="Tests",
                    passed=test_ok,
                    message=(
                        f"Tests passed: {test_result.tests_passed}/{test_result.tests_total}."
                        if test_ok
                        else f"Tests failed: {test_result.tests_failed}/{test_result.tests_total}."
                    ),
                ))
                if not test_ok:
                    critical_errors.extend(test_result.failures)
        # If no test_result: skip test check (tests may not be configured)

        # 6. No critical unresolved errors
        crit_ok = len(critical_errors) == 0
        checks.append(QualityCheckItem(
            name="Critical Errors",
            passed=crit_ok,
            message=f"No critical errors." if crit_ok else f"{len(critical_errors)} critical error(s) unresolved.",
        ))

        # Determine overall state
        if blocked:
            state = "BLOCKED"
            msg = "Quality Gate BLOCKED due to security violation."
        elif all(c.passed for c in checks):
            state = "PASS"
            msg = f"Quality Gate PASSED — {project_name} is ready."
        else:
            state = "FAIL"
            failed_names = [c.name for c in checks if not c.passed]
            msg = f"Quality Gate FAILED — failed checks: {', '.join(failed_names)}."

        logger.info(f"[QUALITY GATE] {state} — project='{project_name}' checks={len(checks)}")
        return QualityGateResult(
            state=state,
            project_name=project_name,
            checks=checks,
            critical_errors=critical_errors,
            message=msg,
        )
