"""RYVEN DEV ENGINE v2 — Controlled Dependency Manager.

Installs dependencies for approved project types only:
  - React/Node   → npm install
  - Python       → pip install  (into project venv only)

Security model:
  Every invocation requires:
    1. approved project type
    2. approved package manager
    3. RYVEN-generated package list (no user-supplied raw strings)
    4. working directory confined to project root
    5. SandboxPolicy permits it
    6. user confirmation already received upstream (WorkflowEngine gate)

Package name validation rejects:
  - shell metacharacters
  - relative paths
  - version ranges with injection tokens
  - private registry redirects that look malicious
"""

from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.dev_engine.sandbox import (
    SandboxPolicy,
    SandboxProcessRequest,
    SandboxProcessResult,
    SandboxProcessRunner,
)
from app.tools.project_tool import resolve_project_path


# ─────────────────────────────────────────────────────────────────────────────
# Package name validation
# ─────────────────────────────────────────────────────────────────────────────

# npm package name spec (simplified): lowercase, hyphens, @scope/pkg
NPM_PACKAGE_RE = re.compile(
    r"^(@[a-zA-Z0-9_\-]+/)?[a-zA-Z0-9_\-\.]+(@[\^~]?[\d\.]+[a-zA-Z0-9\.\-]*)?$"
)

# PyPI package names: letters, digits, hyphens, underscores, dots
PYPI_PACKAGE_RE = re.compile(
    r"^[a-zA-Z0-9]([a-zA-Z0-9_\-\.]*[a-zA-Z0-9])?([\[\]]?[a-zA-Z0-9,_\-]*[\[\]]?)?(>=?[\d\.]+)?(,<[\d\.]+)?$"
)

# Hard-blocked package name substrings (supply-chain attack patterns)
BLOCKED_PACKAGE_FRAGMENTS = [
    "../../", ".\\", "/etc/", "C:\\", "%",
    ";", "&", "|", "`", "$", "(", ")",
]


def validate_npm_package(name: str) -> Optional[str]:
    """Return error string if package name is invalid, else None."""
    name = name.strip()
    if not name:
        return "Empty package name."
    for frag in BLOCKED_PACKAGE_FRAGMENTS:
        if frag in name:
            return f"Blocked fragment {frag!r} in package name: {name!r}"
    if not NPM_PACKAGE_RE.match(name):
        return f"Invalid npm package name format: {name!r}"
    return None


def validate_pypi_package(name: str) -> Optional[str]:
    """Return error string if package name is invalid, else None."""
    name = name.strip()
    if not name:
        return "Empty package name."
    for frag in BLOCKED_PACKAGE_FRAGMENTS:
        if frag in name:
            return f"Blocked fragment {frag!r} in package name: {name!r}"
    if not PYPI_PACKAGE_RE.match(name):
        return f"Invalid PyPI package name format: {name!r}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Request / Result models
# ─────────────────────────────────────────────────────────────────────────────

class DependencyRequest(BaseModel):
    """Controlled specification for a dependency installation."""

    project_name: str
    project_type: str                   # "react_ts" | "python_app" | "python_api"
    packages: List[str] = Field(default_factory=list)
    # If True, install packages from the project's own lock/requirements file instead
    install_from_manifest: bool = False
    timeout_seconds: float = 120.0
    confirmed: bool = False             # Must be True; enforced at call site


class DependencyResult(BaseModel):
    """Outcome of a dependency installation operation."""

    success: bool
    project_name: str
    project_type: str
    packages_requested: List[str] = Field(default_factory=list)
    packages_validated: List[str] = Field(default_factory=list)
    packages_rejected: List[str] = Field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_ms: float = 0.0
    message: str = ""
    security_blocked: bool = False
    block_reason: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Manager
# ─────────────────────────────────────────────────────────────────────────────

# Project types that use npm
_NPM_TYPES = {"react_ts", "react", "vanilla_web", "web", "node"}
# Project types that use pip
_PIP_TYPES = {"python_app", "python_api", "python"}


class DependencyManager:
    """Orchestrates safe, policy-controlled dependency installation.

    Architecture:
      DependencyManager → validates → SandboxProcessRunner → npm / pip → Result

    This class does NOT execute arbitrary commands. It constructs specifically
    shaped, allowlisted commands and passes them to SandboxProcessRunner.
    """

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()
        self.runner = SandboxProcessRunner(policy=self.policy)

    async def install(self, request: DependencyRequest) -> DependencyResult:
        """Install dependencies for the given project. Requires prior user confirmation."""
        # 0. Confirmation gate — this is enforced structurally; the WorkflowEngine
        #    sets confirmed=True only after the user has confirmed. We double-check here.
        if not request.confirmed:
            reason = "Dependency installation requires explicit user confirmation."
            logger.warning(f"[DEPS BLOCKED] {reason} project='{request.project_name}'")
            return DependencyResult(
                success=False,
                project_name=request.project_name,
                project_type=request.project_type,
                packages_requested=request.packages,
                message=reason,
                security_blocked=True,
                block_reason=reason,
            )

        # 1. Route to correct package manager
        ptype = request.project_type.lower().strip()
        if ptype in _NPM_TYPES:
            return await self._install_npm(request)
        elif ptype in _PIP_TYPES:
            return await self._install_pip(request)
        else:
            reason = f"No approved package manager for project type '{request.project_type}'."
            logger.warning(f"[DEPS BLOCKED] {reason}")
            return DependencyResult(
                success=False,
                project_name=request.project_name,
                project_type=request.project_type,
                packages_requested=request.packages,
                message=reason,
                security_blocked=True,
                block_reason=reason,
            )

    # ── npm ─────────────────────────────────────────────────────────────────

    async def _install_npm(self, request: DependencyRequest) -> DependencyResult:
        project_dir = resolve_project_path(request.project_name)

        if request.install_from_manifest:
            # npm install with no args — reads package.json
            import os
            if not os.path.isfile(os.path.join(project_dir or "", "package.json")):
                return DependencyResult(
                    success=False,
                    project_name=request.project_name,
                    project_type=request.project_type,
                    message="package.json not found — cannot install from manifest.",
                )
            args = ["install", "--no-audit", "--no-fund", "--prefer-offline"]
            validated = []
            rejected = []
        else:
            validated, rejected = self._validate_npm_packages(request.packages)
            if rejected:
                logger.warning(
                    f"[DEPS] Rejected npm packages: {rejected} project='{request.project_name}'"
                )
            if not validated:
                return DependencyResult(
                    success=False,
                    project_name=request.project_name,
                    project_type=request.project_type,
                    packages_requested=request.packages,
                    packages_rejected=rejected,
                    message=f"All requested packages were rejected by validation: {rejected}",
                    security_blocked=True,
                )
            args = ["install", "--no-audit", "--no-fund"] + validated

        logger.info(
            f"[DEPS] npm {' '.join(args)} project='{request.project_name}'"
        )
        proc_req = SandboxProcessRequest(
            project_name=request.project_name,
            executable="npm",
            arguments=args,
            timeout_seconds=request.timeout_seconds,
            operation_label=f"npm install — {request.project_name}",
        )
        result: SandboxProcessResult = await self.runner.run(proc_req)
        return DependencyResult(
            success=result.success,
            project_name=request.project_name,
            project_type=request.project_type,
            packages_requested=request.packages,
            packages_validated=validated if not request.install_from_manifest else [],
            packages_rejected=rejected if not request.install_from_manifest else [],
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            message=result.message,
            security_blocked=result.security_blocked,
            block_reason=result.block_reason,
        )

    # ── pip ─────────────────────────────────────────────────────────────────

    async def _install_pip(self, request: DependencyRequest) -> DependencyResult:
        import os
        project_dir = resolve_project_path(request.project_name)

        if request.install_from_manifest:
            req_file = os.path.join(project_dir or "", "requirements.txt")
            if not os.path.isfile(req_file):
                return DependencyResult(
                    success=False,
                    project_name=request.project_name,
                    project_type=request.project_type,
                    message="requirements.txt not found — cannot install from manifest.",
                )
            args = ["-m", "pip", "install", "-r", "requirements.txt", "--no-input"]
            executable = "python"
            validated: List[str] = []
            rejected: List[str] = []
        else:
            validated, rejected = self._validate_pypi_packages(request.packages)
            if rejected:
                logger.warning(
                    f"[DEPS] Rejected pip packages: {rejected} project='{request.project_name}'"
                )
            if not validated:
                return DependencyResult(
                    success=False,
                    project_name=request.project_name,
                    project_type=request.project_type,
                    packages_requested=request.packages,
                    packages_rejected=rejected,
                    message=f"All requested packages were rejected by validation: {rejected}",
                    security_blocked=True,
                )
            args = ["-m", "pip", "install", "--no-input"] + validated
            executable = "python"

        logger.info(
            f"[DEPS] python {' '.join(args)} project='{request.project_name}'"
        )
        proc_req = SandboxProcessRequest(
            project_name=request.project_name,
            executable=executable,
            arguments=args,
            timeout_seconds=request.timeout_seconds,
            operation_label=f"pip install — {request.project_name}",
        )
        result: SandboxProcessResult = await self.runner.run(proc_req)
        return DependencyResult(
            success=result.success,
            project_name=request.project_name,
            project_type=request.project_type,
            packages_requested=request.packages,
            packages_validated=validated,
            packages_rejected=rejected,
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            message=result.message,
            security_blocked=result.security_blocked,
            block_reason=result.block_reason,
        )

    # ── validation helpers ───────────────────────────────────────────────────

    @staticmethod
    def _validate_npm_packages(packages: List[str]):
        validated, rejected = [], []
        for pkg in packages:
            err = validate_npm_package(pkg)
            if err:
                rejected.append(pkg)
                logger.warning(f"[DEPS] NPM package rejected: {err}")
            else:
                validated.append(pkg.strip())
        return validated, rejected

    @staticmethod
    def _validate_pypi_packages(packages: List[str]):
        validated, rejected = [], []
        for pkg in packages:
            err = validate_pypi_package(pkg)
            if err:
                rejected.append(pkg)
                logger.warning(f"[DEPS] PyPI package rejected: {err}")
            else:
                validated.append(pkg.strip())
        return validated, rejected
