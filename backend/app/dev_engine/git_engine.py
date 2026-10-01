"""RYVEN DEV ENGINE v2 — Controlled Git Engine.

Provides safe, approved git operations via SandboxProcessRunner.

Read operations (always safe):
  git status
  git diff

Write operations (always require confirmed=True):
  git add
  git commit

NOT implemented:
  git push  — planned in Milestone 11 (Remote Git), requires separate gate
  git reset, git clean, git checkout — too destructive without further context

Security:
  - git executable goes through SandboxPolicy allowlist.
  - All operations execute in the approved project directory only.
  - Commit messages are sanitized (no shell metacharacters).
  - Branch names are validated.
  - Never prints credentials, tokens, or remote URLs with embedded passwords.
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
# Validation helpers
# ─────────────────────────────────────────────────────────────────────────────

_SAFE_COMMIT_MSG_RE = re.compile(r"^[a-zA-Z0-9 \-_\.\,\:\!\(\)\[\]\/\#]+$")
_SAFE_BRANCH_RE = re.compile(r"^[a-zA-Z0-9_\-\/\.]+$")
MAX_COMMIT_MSG_LEN = 200


def _validate_commit_message(msg: str) -> Optional[str]:
    """Return error string or None."""
    msg = msg.strip()
    if not msg:
        return "Commit message cannot be empty."
    if len(msg) > MAX_COMMIT_MSG_LEN:
        return f"Commit message too long ({len(msg)} > {MAX_COMMIT_MSG_LEN} chars)."
    if not _SAFE_COMMIT_MSG_RE.match(msg):
        return f"Commit message contains disallowed characters: {msg!r}"
    return None


def _validate_branch(branch: str) -> Optional[str]:
    if not branch or not _SAFE_BRANCH_RE.match(branch):
        return f"Invalid branch name: {branch!r}"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Models
# ─────────────────────────────────────────────────────────────────────────────

class GitResult(BaseModel):
    """Outcome of a git operation."""

    success: bool
    operation: str
    project_name: str
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    duration_ms: float = 0.0
    security_blocked: bool = False
    block_reason: str = ""
    message: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Engine
# ─────────────────────────────────────────────────────────────────────────────

class GitEngine:
    """Executes safe, policy-controlled git operations."""

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()
        self.runner = SandboxProcessRunner(policy=self.policy)

    # ── Read operations — always permitted ──────────────────────────────────

    async def status(self, project_name: str) -> GitResult:
        """Run git status --short."""
        return await self._run(
            project_name=project_name,
            args=["status", "--short"],
            operation="git status",
        )

    async def diff(self, project_name: str, staged: bool = False) -> GitResult:
        """Run git diff (unstaged) or git diff --cached (staged)."""
        args = ["diff", "--stat"]
        if staged:
            args = ["diff", "--cached", "--stat"]
        return await self._run(
            project_name=project_name,
            args=args,
            operation="git diff",
        )

    # ── Write operations — require confirmed=True ────────────────────────────

    async def add(
        self,
        project_name: str,
        paths: Optional[List[str]] = None,
        confirmed: bool = False,
    ) -> GitResult:
        """Run git add <paths> or git add ."""
        if not confirmed:
            return self._blocked("git add", project_name, "git add requires user confirmation.")

        # Validate paths — no absolute paths, no traversal
        args = ["add"]
        if paths:
            for p in paths:
                if ".." in p or re.search(r"^[A-Za-z]:\\|^/", p):
                    return self._blocked(
                        "git add", project_name,
                        f"Path traversal or absolute path rejected: {p!r}",
                    )
            args.extend(paths)
        else:
            args.append(".")

        return await self._run(project_name=project_name, args=args, operation="git add")

    async def commit(
        self,
        project_name: str,
        message: str,
        confirmed: bool = False,
    ) -> GitResult:
        """Run git commit -m <message>."""
        if not confirmed:
            return self._blocked("git commit", project_name, "git commit requires user confirmation.")

        err = _validate_commit_message(message)
        if err:
            return self._blocked("git commit", project_name, err)

        args = ["commit", "-m", message]
        return await self._run(project_name=project_name, args=args, operation="git commit")

    async def init(self, project_name: str, confirmed: bool = False) -> GitResult:
        """Initialize a new git repository in the project directory."""
        if not confirmed:
            return self._blocked("git init", project_name, "git init requires user confirmation.")
        return await self._run(project_name=project_name, args=["init"], operation="git init")

    async def log(self, project_name: str, n: int = 10) -> GitResult:
        """Read-only: git log --oneline -n <n>."""
        n = min(max(1, n), 100)
        return await self._run(
            project_name=project_name,
            args=["log", "--oneline", f"-{n}"],
            operation="git log",
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    async def _run(self, project_name: str, args: List[str], operation: str) -> GitResult:
        req = SandboxProcessRequest(
            project_name=project_name,
            executable="git",
            arguments=args,
            timeout_seconds=30.0,
            operation_label=operation,
        )
        result: SandboxProcessResult = await self.runner.run(req)
        return GitResult(
            success=result.success,
            operation=operation,
            project_name=project_name,
            stdout=result.stdout,
            stderr=self._sanitize_git_stderr(result.stderr),
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            security_blocked=result.security_blocked,
            block_reason=result.block_reason,
            message=result.message,
        )

    @staticmethod
    def _blocked(operation: str, project_name: str, reason: str) -> GitResult:
        logger.warning(f"[GIT BLOCKED] {operation} — {reason} project='{project_name}'")
        return GitResult(
            success=False,
            operation=operation,
            project_name=project_name,
            security_blocked=True,
            block_reason=reason,
            message=f"Git operation blocked: {reason}",
        )

    @staticmethod
    def _sanitize_git_stderr(stderr: str) -> str:
        """Redact any credential-looking tokens from git stderr."""
        # Remove https://username:token@... patterns
        sanitized = re.sub(
            r"https?://[^:@\s]+:[^@\s]+@", "https://[REDACTED]@", stderr
        )
        return sanitized
