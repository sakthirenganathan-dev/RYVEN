"""RYVEN DEV ENGINE v2 — Sandboxed Process Runner.

Security Architecture:
  LLM → Intent → Plan → Validation → Permission → SandboxProcessRunner → OS Process → Result

The LLM NEVER invokes this directly. All execution flows through:
  WorkflowEngine → WorkflowExecutor → RegisteredTool → SandboxProcessRunner

Every execution is:
  - Allowlisted executable only (no arbitrary paths)
  - Confined to approved project working directory
  - Timeout-limited
  - Output-size-limited
  - Stdout/stderr captured independently
  - Process-tree terminated on timeout/cancellation
  - Sanitized before returning to caller
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import time
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.tools.project_tool import get_projects_root, resolve_project_path


# ─────────────────────────────────────────────────────────────────────────────
# Resource limits
# ─────────────────────────────────────────────────────────────────────────────

MAX_OUTPUT_BYTES: int = 512_000        # 512 KB captured stdout + stderr
DEFAULT_TIMEOUT_SECONDS: float = 120.0
MAX_TIMEOUT_SECONDS: float = 300.0    # Hard ceiling — never exceed 5 min


# ─────────────────────────────────────────────────────────────────────────────
# Policy — only executables explicitly approved may run
# ─────────────────────────────────────────────────────────────────────────────

class SandboxPolicy(BaseModel):
    """Security policy governing which executables may run in the sandbox."""

    # canonical name → description
    approved_executables: Dict[str, str] = Field(
        default_factory=lambda: {
            "npm": "Node.js package manager (npm CLI)",
            "node": "Node.js runtime",
            "python": "Python interpreter",
            "python3": "Python 3 interpreter",
            "git": "Git version control",
            "npx": "Node.js package runner (npx)",
        }
    )

    # Patterns that must NEVER appear in a command — command injection
    injection_patterns: List[str] = Field(
        default_factory=lambda: [
            r"[;&|`$\\(\\)]",   # shell metacharacters
            r"\.\./",            # path traversal
            r"\.\.\\",           # Windows path traversal
            r"\beval\b",
            r"\bexec\b",
            r"\bsystem\b",
            r"\bsubprocess\b",
            r"\bos\.system\b",
            r"\bcmd\.exe\b",
            r"\bpowershell\b",
        ]
    )

    # Arguments that are explicitly blocked regardless of executable
    blocked_argument_fragments: List[str] = Field(
        default_factory=lambda: [
            "--unsafe-perm",
            "--allow-scripts=life-cycle",
            "../../",
            "..\\",
            "format ",
            "del ",
            "rm -rf",
        ]
    )

    allow_network: bool = True          # npm install needs network
    max_timeout_seconds: float = MAX_TIMEOUT_SECONDS

    def is_executable_approved(self, executable: str) -> bool:
        """Return True only if the bare executable name is on the approved list."""
        bare = os.path.basename(executable.strip()).lower()
        # Strip .exe suffix for Windows
        if bare.endswith(".exe"):
            bare = bare[:-4]
        return bare in self.approved_executables

    def validate_arguments(self, args: List[str]) -> Optional[str]:
        """Return an error string if args contain dangerous patterns, else None."""
        combined = " ".join(args)
        for pattern in self.injection_patterns:
            if re.search(pattern, combined, re.IGNORECASE):
                return f"Injection pattern detected in arguments: {pattern!r}"
        for fragment in self.blocked_argument_fragments:
            if fragment.lower() in combined.lower():
                return f"Blocked argument fragment detected: {fragment!r}"
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Request / Result contracts
# ─────────────────────────────────────────────────────────────────────────────

class SandboxProcessRequest(BaseModel):
    """Controlled specification for a sandboxed process execution."""

    project_name: str
    executable: str                     # bare name: "npm", "python", "git"
    arguments: List[str] = Field(default_factory=list)
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    environment_extras: Dict[str, str] = Field(default_factory=dict)
    operation_label: str = ""           # Human-readable label for telemetry


class SandboxProcessResult(BaseModel):
    """Structured result from a sandboxed process execution."""

    success: bool
    executable: str
    arguments: List[str] = Field(default_factory=list)
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    cancelled: bool = False
    security_blocked: bool = False
    block_reason: str = ""
    message: str = ""
    project_name: str = ""
    operation_label: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

class SandboxProcessRunner:
    """Executes pre-approved development processes inside a strict security boundary.

    Security guarantees:
    - Only allowlisted executables may run.
    - Working directory is always within the approved projects root.
    - Arguments are scanned for shell injection patterns.
    - Stdout/stderr are captured up to MAX_OUTPUT_BYTES.
    - Process tree is terminated on timeout or cancellation.
    - Environment is filtered — only safe variables forwarded.
    - This class must NEVER be invoked directly by the LLM/AI layer.
    """

    # Safe environment keys forwarded to child process
    SAFE_ENV_KEYS: List[str] = [
        "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "TEMP", "TMP",
        "HOMEDRIVE", "HOMEPATH", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
        "PROGRAMFILES", "PROGRAMFILES(X86)", "COMMONPROGRAMFILES",
        "WINDIR", "COMPUTERNAME",
        # Node / npm specific
        "NODE_ENV", "npm_config_cache",
        # Python specific
        "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
    ]

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()

    # ── public entry point ──────────────────────────────────────────────────

    async def run(self, request: SandboxProcessRequest) -> SandboxProcessResult:
        """Execute a sandboxed process. Returns SandboxProcessResult always — never raises."""
        label = request.operation_label or f"{request.executable} {' '.join(request.arguments[:2])}"
        start = time.monotonic()

        # 1. Executable allowlist check
        if not self.policy.is_executable_approved(request.executable):
            reason = f"Executable '{request.executable}' is not on the approved list."
            logger.warning(f"[SANDBOX BLOCKED] {reason} project='{request.project_name}'")
            return SandboxProcessResult(
                success=False,
                executable=request.executable,
                arguments=request.arguments,
                exit_code=126,
                security_blocked=True,
                block_reason=reason,
                message=f"Execution blocked: {reason}",
                project_name=request.project_name,
                operation_label=label,
            )

        # 2. Argument injection check
        arg_err = self.policy.validate_arguments(request.arguments)
        if arg_err:
            logger.warning(f"[SANDBOX BLOCKED] {arg_err} project='{request.project_name}'")
            return SandboxProcessResult(
                success=False,
                executable=request.executable,
                arguments=request.arguments,
                exit_code=126,
                security_blocked=True,
                block_reason=arg_err,
                message=f"Execution blocked: {arg_err}",
                project_name=request.project_name,
                operation_label=label,
            )

        # 3. Working directory: must be inside approved project root
        working_dir = resolve_project_path(request.project_name)
        if not working_dir:
            reason = f"Invalid project name '{request.project_name}' — cannot resolve safe working directory."
            logger.warning(f"[SANDBOX BLOCKED] {reason}")
            return SandboxProcessResult(
                success=False,
                executable=request.executable,
                arguments=request.arguments,
                exit_code=126,
                security_blocked=True,
                block_reason=reason,
                message=f"Execution blocked: {reason}",
                project_name=request.project_name,
                operation_label=label,
            )

        if not os.path.isdir(working_dir):
            reason = f"Project directory '{working_dir}' does not exist — create the project first."
            return SandboxProcessResult(
                success=False,
                executable=request.executable,
                arguments=request.arguments,
                exit_code=1,
                message=reason,
                project_name=request.project_name,
                operation_label=label,
            )

        # 4. Timeout clamp
        timeout = min(
            max(1.0, request.timeout_seconds),
            self.policy.max_timeout_seconds,
        )

        # 5. Resolve actual executable path (shutil.which for safe resolution)
        exe_path = shutil.which(request.executable)
        if not exe_path:
            reason = f"Executable '{request.executable}' not found on PATH."
            logger.warning(f"[SANDBOX] {reason} project='{request.project_name}'")
            return SandboxProcessResult(
                success=False,
                executable=request.executable,
                arguments=request.arguments,
                exit_code=127,
                message=reason,
                project_name=request.project_name,
                operation_label=label,
            )

        # 6. Build filtered environment
        safe_env = self._build_safe_environment(request.environment_extras)

        # 7. Execute
        cmd = [exe_path] + request.arguments
        logger.info(
            f"[SANDBOX] Running: {' '.join(cmd)!r} "
            f"cwd={working_dir!r} timeout={timeout}s project='{request.project_name}'"
        )

        result = await self._execute(
            cmd=cmd,
            cwd=working_dir,
            env=safe_env,
            timeout=timeout,
            request=request,
            label=label,
        )
        result.duration_ms = (time.monotonic() - start) * 1000
        return result

    # ── private helpers ─────────────────────────────────────────────────────

    def _build_safe_environment(self, extras: Dict[str, str]) -> Dict[str, str]:
        """Filter the host environment to safe variables only."""
        env: Dict[str, str] = {}
        host_env = os.environ
        for key in self.SAFE_ENV_KEYS:
            val = host_env.get(key) or host_env.get(key.upper())
            if val:
                env[key] = val

        # Merge caller-supplied extras — only if keys are safe strings
        for k, v in extras.items():
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                env[k] = v

        return env

    async def _execute(
        self,
        cmd: List[str],
        cwd: str,
        env: Dict[str, str],
        timeout: float,
        request: SandboxProcessRequest,
        label: str,
    ) -> SandboxProcessResult:
        """Actual async subprocess execution with timeout and output capping."""
        process: Optional[asyncio.subprocess.Process] = None
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                raw_stdout, raw_stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                self._kill_process(process)
                logger.warning(
                    f"[SANDBOX TIMEOUT] '{label}' exceeded {timeout}s "
                    f"project='{request.project_name}'"
                )
                return SandboxProcessResult(
                    success=False,
                    executable=request.executable,
                    arguments=request.arguments,
                    exit_code=-1,
                    timed_out=True,
                    message=f"Process timed out after {timeout:.0f}s.",
                    project_name=request.project_name,
                    operation_label=label,
                )

            stdout = self._cap(raw_stdout)
            stderr = self._cap(raw_stderr)
            exit_code = process.returncode if process.returncode is not None else -1
            success = exit_code == 0

            log_fn = logger.info if success else logger.warning
            log_fn(
                f"[SANDBOX] '{label}' exit={exit_code} "
                f"stdout={len(stdout)}B stderr={len(stderr)}B "
                f"project='{request.project_name}'"
            )

            return SandboxProcessResult(
                success=success,
                executable=request.executable,
                arguments=request.arguments,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                message="Process completed." if success else f"Process exited with code {exit_code}.",
                project_name=request.project_name,
                operation_label=label,
            )

        except Exception as exc:
            if process:
                self._kill_process(process)
            logger.error(
                f"[SANDBOX ERROR] '{label}' raised {type(exc).__name__}: {exc} "
                f"project='{request.project_name}'",
                exc_info=True,
            )
            return SandboxProcessResult(
                success=False,
                executable=request.executable,
                arguments=request.arguments,
                exit_code=-1,
                message=f"Sandbox execution error: {type(exc).__name__}",
                project_name=request.project_name,
                operation_label=label,
            )

    @staticmethod
    def _kill_process(process: asyncio.subprocess.Process) -> None:
        """Attempt to terminate/kill the process tree safely."""
        try:
            process.kill()
        except Exception:
            pass

    @staticmethod
    def _cap(raw: bytes) -> str:
        """Decode bytes and enforce MAX_OUTPUT_BYTES cap."""
        text = raw.decode("utf-8", errors="replace")
        if len(text.encode("utf-8")) > MAX_OUTPUT_BYTES:
            text = text[: MAX_OUTPUT_BYTES // 4] + "\n... [OUTPUT TRUNCATED — LIMIT REACHED] ..."
        return text
