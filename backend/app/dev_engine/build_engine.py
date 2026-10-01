"""RYVEN DEV ENGINE v2 — Real Build Engine.

Connects SafeBuildExecutor to the real SandboxProcessRunner.
Supports only approved build commands per project type.

Build commands per type:
  react_ts    → npm run build
  python_app  → python -m py_compile <entry>  (safe static check)
  python_api  → python -m py_compile main.py

Result always reflects reality — no fake success ever reported.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.dev_engine.error_analyzer import BuildError, ErrorAnalyzer
from app.dev_engine.sandbox import (
    SandboxPolicy,
    SandboxProcessRequest,
    SandboxProcessResult,
    SandboxProcessRunner,
)
from app.tools.project_tool import resolve_project_path


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class BuildRequest(BaseModel):
    """Controlled specification for a project build."""

    project_name: str
    project_type: str          # "react_ts" | "python_app" | "python_api"
    entry_file: str = "main.py"
    timeout_seconds: float = 120.0
    confirmed: bool = False


class BuildResult(BaseModel):
    """Structured outcome of a project build operation."""

    success: bool
    project_name: str
    project_type: str
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    warnings: List[str] = Field(default_factory=list)
    errors: List[BuildError] = Field(default_factory=list)
    timed_out: bool = False
    security_blocked: bool = False
    block_reason: str = ""
    message: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

_NPM_TYPES = {"react_ts", "react", "vanilla_web", "web"}
_PYTHON_TYPES = {"python_app", "python_api", "python"}


class BuildEngine:
    """Executes real project builds inside the SandboxProcessRunner.

    Security: BuildEngine never runs arbitrary commands. It maps project_type
    to a pre-approved, hard-coded command shape and routes through SandboxProcessRunner.
    """

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()
        self.runner = SandboxProcessRunner(policy=self.policy)
        self.analyzer = ErrorAnalyzer()

    async def build(self, request: BuildRequest) -> BuildResult:
        """Build the project. confirmed must be True."""
        if not request.confirmed:
            reason = "Build operation requires prior user confirmation."
            logger.warning(f"[BUILD BLOCKED] {reason} project='{request.project_name}'")
            return BuildResult(
                success=False,
                project_name=request.project_name,
                project_type=request.project_type,
                message=reason,
                security_blocked=True,
                block_reason=reason,
            )

        ptype = request.project_type.lower().strip()

        if ptype in _NPM_TYPES:
            return await self._build_npm(request)
        elif ptype in _PYTHON_TYPES:
            return await self._build_python(request)
        else:
            reason = f"No approved build command for project type '{request.project_type}'."
            return BuildResult(
                success=False,
                project_name=request.project_name,
                project_type=request.project_type,
                message=reason,
                security_blocked=True,
                block_reason=reason,
            )

    # ── React/Node build ────────────────────────────────────────────────────

    async def _build_npm(self, request: BuildRequest) -> BuildResult:
        """npm run build inside project directory."""
        project_dir = resolve_project_path(request.project_name)

        # Ensure package.json exists before attempting build
        if not project_dir or not os.path.isfile(os.path.join(project_dir, "package.json")):
            return BuildResult(
                success=False,
                project_name=request.project_name,
                project_type=request.project_type,
                message="package.json not found — run dependency install first.",
            )

        proc_req = SandboxProcessRequest(
            project_name=request.project_name,
            executable="npm",
            arguments=["run", "build"],
            timeout_seconds=request.timeout_seconds,
            operation_label=f"npm run build — {request.project_name}",
        )
        result = await self.runner.run(proc_req)
        errors = self.analyzer.analyze_npm_build(result)
        warnings = self._extract_warnings_npm(result.stdout + result.stderr)

        return BuildResult(
            success=result.success,
            project_name=request.project_name,
            project_type=request.project_type,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
            warnings=warnings,
            errors=errors,
            timed_out=result.timed_out,
            security_blocked=result.security_blocked,
            block_reason=result.block_reason,
            message=result.message,
        )

    # ── Python build / compile check ────────────────────────────────────────

    async def _build_python(self, request: BuildRequest) -> BuildResult:
        """python -m py_compile <entry> — static syntax check only. No execution."""
        project_dir = resolve_project_path(request.project_name)
        entry = request.entry_file or "main.py"

        # Validate entry file is within project
        if not project_dir or not os.path.isfile(os.path.join(project_dir, entry)):
            return BuildResult(
                success=False,
                project_name=request.project_name,
                project_type=request.project_type,
                message=f"Entry file '{entry}' not found in project directory.",
            )

        proc_req = SandboxProcessRequest(
            project_name=request.project_name,
            executable="python",
            arguments=["-m", "py_compile", entry],
            timeout_seconds=min(request.timeout_seconds, 30.0),
            operation_label=f"py_compile — {request.project_name}/{entry}",
        )
        result = await self.runner.run(proc_req)
        errors = self.analyzer.analyze_python_build(result)

        return BuildResult(
            success=result.success,
            project_name=request.project_name,
            project_type=request.project_type,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_ms=result.duration_ms,
            errors=errors,
            timed_out=result.timed_out,
            security_blocked=result.security_blocked,
            block_reason=result.block_reason,
            message=result.message,
        )

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_warnings_npm(output: str) -> List[str]:
        warnings: List[str] = []
        for line in output.splitlines():
            if re.search(r"\bwarn\b", line, re.IGNORECASE):
                warnings.append(line.strip())
        return warnings[:50]  # cap
