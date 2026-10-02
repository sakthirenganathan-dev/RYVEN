"""RYVEN DEV ENGINE — M11: Remote Git Engine.

Provides safe, policy-controlled Git operations via SandboxProcessRunner.
Integrates status parsing, secret sanitization, sensitive file scanning,
controlled staging, commit and push previews, confirmation gates, and state verification.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging_config import logger
from app.dev_engine.sandbox import (
    SandboxPolicy,
    SandboxProcessRequest,
    SandboxProcessResult,
    SandboxProcessRunner,
)
from app.git.git_branch import parse_git_branches
from app.git.git_commit import generate_commit_preview, prepare_commit_arguments
from app.git.git_diff import parse_git_diff
from app.git.git_models import (
    CommitPreview,
    GitBranchResult,
    GitDiffResult,
    GitLogEntry,
    GitLogResult,
    GitRemoteResult,
    GitResult,
    GitStatusResult,
    PushPreview,
    VerificationResult,
)
from app.git.git_push import generate_push_preview, prepare_push_arguments
from app.git.git_remote import parse_git_remotes
from app.git.git_security import (
    check_destructive_command,
    is_force_push_argument,
    sanitize_git_output,
    scan_sensitive_files,
)
from app.git.git_stage import prepare_staging_arguments, prepare_unstage_arguments
from app.git.git_status import parse_git_status
from app.git.git_validator import (
    _validate_branch,
    _validate_commit_message,
    validate_branch_name,
    validate_commit_message,
    validate_git_arguments,
    validate_git_repository,
    validate_remote_name,
)
from app.git.git_verifier import verify_commit_outcome, verify_push_outcome
from app.tools.project_tool import resolve_project_path


class GitEngine:
    """Security-first Git engine executing sandboxed, policy-controlled Git operations."""

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()
        self.runner = SandboxProcessRunner(policy=self.policy)

    # ─────────────────────────────────────────────────────────────────────────
    # Status Operations
    # ─────────────────────────────────────────────────────────────────────────

    async def status(self, project_name: str) -> GitResult:
        """Run `git status --short` (backward-compatible)."""
        return await self._run(
            project_name=project_name,
            args=["status", "--short"],
            operation="git status",
        )

    async def get_structured_status(self, project_name: str) -> Tuple[bool, Optional[GitStatusResult], str]:
        """Inspect repository and return a structured GitStatusResult."""
        is_valid, err, project_dir = validate_git_repository(project_name)
        if not is_valid:
            return False, None, err

        res = await self._run(
            project_name=project_name,
            args=["status", "--porcelain=v1", "-b"],
            operation="git status",
        )
        if not res.success:
            return False, None, res.message or res.stderr

        parsed = parse_git_status(project_name, project_dir, res.stdout)
        return True, parsed, ""

    # ─────────────────────────────────────────────────────────────────────────
    # Diff Operations
    # ─────────────────────────────────────────────────────────────────────────

    async def diff(self, project_name: str, staged: bool = False) -> GitResult:
        """Run `git diff --stat` or `git diff --cached --stat` (backward-compatible)."""
        args = ["diff", "--stat"]
        if staged:
            args = ["diff", "--cached", "--stat"]
        return await self._run(
            project_name=project_name,
            args=args,
            operation="git diff",
        )

    async def get_structured_diff(
        self,
        project_name: str,
        staged: bool = False,
        file_path: Optional[str] = None,
    ) -> Tuple[bool, Optional[GitDiffResult], str]:
        """Inspect working tree or staged diff, redacting secrets."""
        is_valid, err, _ = validate_git_repository(project_name)
        if not is_valid:
            return False, None, err

        args = ["diff"]
        if staged:
            args.append("--cached")
        if file_path:
            args.extend(["--", file_path])

        res = await self._run(
            project_name=project_name,
            args=args,
            operation="git diff",
        )
        if not res.success:
            return False, None, res.message or res.stderr

        parsed = parse_git_diff(project_name, res.stdout, staged=staged)
        return True, parsed, ""

    # ─────────────────────────────────────────────────────────────────────────
    # Branch and Remote Operations
    # ─────────────────────────────────────────────────────────────────────────

    async def branch(self, project_name: str) -> Tuple[bool, Optional[GitBranchResult], str]:
        """Inspect local and remote branch states."""
        is_valid, err, _ = validate_git_repository(project_name)
        if not is_valid:
            return False, None, err

        res = await self._run(
            project_name=project_name,
            args=["branch", "-a", "-vv"],
            operation="git branch",
        )
        if not res.success:
            return False, None, res.message or res.stderr

        parsed = parse_git_branches(project_name, res.stdout)
        return True, parsed, ""

    async def remote(self, project_name: str) -> Tuple[bool, Optional[GitRemoteResult], str]:
        """Inspect configured remotes with sanitized URLs."""
        is_valid, err, _ = validate_git_repository(project_name)
        if not is_valid:
            return False, None, err

        res = await self._run(
            project_name=project_name,
            args=["remote", "-v"],
            operation="git remote",
        )
        if not res.success:
            return False, None, res.message or res.stderr

        parsed = parse_git_remotes(project_name, res.stdout)
        return True, parsed, ""

    # ─────────────────────────────────────────────────────────────────────────
    # Log Operations
    # ─────────────────────────────────────────────────────────────────────────

    async def log(self, project_name: str, n: int = 10) -> GitResult:
        """Run `git log --oneline -n <n>`."""
        n = min(max(1, n), 100)
        return await self._run(
            project_name=project_name,
            args=["log", "--oneline", f"-{n}"],
            operation="git log",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Staging Operations
    # ─────────────────────────────────────────────────────────────────────────

    async def add(
        self,
        project_name: str,
        paths: Optional[List[str]] = None,
        confirmed: bool = False,
    ) -> GitResult:
        """Run git add (backward-compatible method)."""
        if not confirmed:
            return self._blocked("git add", project_name, "git add requires user confirmation.")

        is_valid, err, project_dir = validate_git_repository(project_name)
        if not is_valid:
            return self._blocked("git add", project_name, err)

        # If paths is None or empty, backward compatibility with v2 tests
        if not paths:
            args = ["add", "."]
            return await self._run(project_name=project_name, args=args, operation="git add")

        allowed, safe_args, err_msg = prepare_staging_arguments(project_name, project_dir, paths)
        if not allowed:
            return self._blocked("git add", project_name, err_msg)

        return await self._run(project_name=project_name, args=safe_args, operation="git add")

    async def stage(
        self,
        project_name: str,
        files: List[str],
        confirmed: bool = False,
    ) -> GitResult:
        """Controlled staging of explicitly requested files."""
        return await self.add(project_name=project_name, paths=files, confirmed=confirmed)

    async def unstage(
        self,
        project_name: str,
        files: Optional[List[str]] = None,
    ) -> GitResult:
        """Safe unstage modifying index only (never discards working tree changes)."""
        is_valid, err, _ = validate_git_repository(project_name)
        if not is_valid:
            return self._blocked("git unstage", project_name, err)

        allowed, safe_args, err_msg = prepare_unstage_arguments(project_name, files or [])
        if not allowed:
            return self._blocked("git unstage", project_name, err_msg)

        return await self._run(project_name=project_name, args=safe_args, operation="git restore --staged")

    # ─────────────────────────────────────────────────────────────────────────
    # Commit Operations
    # ─────────────────────────────────────────────────────────────────────────

    async def preview_commit(
        self,
        project_name: str,
        message: str,
    ) -> Tuple[bool, Optional[CommitPreview], str]:
        """Generate structured commit preview for user review."""
        success, status, err = await self.get_structured_status(project_name)
        if not success or not status:
            return False, None, err

        preview = generate_commit_preview(project_name, status, message)
        return True, preview, ""

    async def commit(
        self,
        project_name: str,
        message: str,
        confirmed: bool = False,
    ) -> GitResult:
        """Execute a commit with confirmation and message validation."""
        if not confirmed:
            return self._blocked("git commit", project_name, "git commit requires user confirmation.")

        is_valid, err, project_dir = validate_git_repository(project_name)
        if not is_valid:
            return self._blocked("git commit", project_name, err)

        val_err = validate_commit_message(message)
        if val_err:
            return self._blocked("git commit", project_name, val_err)

        # Re-check status for staged files
        status_ok, status, _ = await self.get_structured_status(project_name)
        staged_files = status.staged if status else []

        allowed, safe_args, prep_err = prepare_commit_arguments(
            project_name=project_name,
            project_path=project_dir,
            message=message,
            confirmed=confirmed,
            staged_files=staged_files,
        )
        if not allowed:
            return self._blocked("git commit", project_name, prep_err)

        return await self._run(project_name=project_name, args=safe_args, operation="git commit")

    async def commit_and_verify(
        self,
        project_name: str,
        message: str,
        confirmed: bool = False,
    ) -> Tuple[GitResult, Optional[VerificationResult]]:
        """Execute commit and verify that commit hash was registered."""
        res = await self.commit(project_name=project_name, message=message, confirmed=confirmed)
        if not res.success:
            return res, None

        # Verify HEAD
        head_res = await self._run(project_name=project_name, args=["rev-parse", "HEAD"], operation="git rev-parse")
        log_res = await self._run(project_name=project_name, args=["log", "-1", "--pretty=%B"], operation="git log")

        branch_res = await self.branch(project_name)
        branch_name = branch_res[1].current_branch if branch_res[1] else "main"

        verification = verify_commit_outcome(
            project_name=project_name,
            commit_hash=head_res.stdout.strip(),
            branch=branch_name,
            actual_message=log_res.stdout.strip(),
            expected_message=message,
        )

        res.data = verification.model_dump()
        return res, verification

    # ─────────────────────────────────────────────────────────────────────────
    # Push Operations
    # ─────────────────────────────────────────────────────────────────────────

    async def preview_push(
        self,
        project_name: str,
        remote: str = "origin",
    ) -> Tuple[bool, Optional[PushPreview], str]:
        """Generate structured push preview before requesting approval."""
        success, status, err = await self.get_structured_status(project_name)
        if not success or not status:
            return False, None, err

        # Inspect outgoing commits
        commits_to_push: List[str] = []
        if status.upstream:
            log_res = await self._run(
                project_name=project_name,
                args=["log", f"{status.upstream}..HEAD", "--oneline"],
                operation="git log outgoing",
            )
            if log_res.success and log_res.stdout.strip():
                commits_to_push = log_res.stdout.strip().splitlines()

        preview = generate_push_preview(
            project_name=project_name,
            status=status,
            remote=remote,
            commits_to_push=commits_to_push,
            files_affected=status.staged + status.modified,
        )
        return True, preview, ""

    async def push(
        self,
        project_name: str,
        remote: str = "origin",
        branch: Optional[str] = None,
        confirmed: bool = False,
        extra_flags: Optional[List[str]] = None,
    ) -> Tuple[GitResult, Optional[VerificationResult]]:
        """Safely push branch to remote. Force pushes are permanently blocked."""
        if not confirmed:
            return (
                self._blocked("git push", project_name, "git push requires user confirmation."),
                None,
            )

        is_valid, err, _ = validate_git_repository(project_name)
        if not is_valid:
            return self._blocked("git push", project_name, err), None

        # Resolve branch
        if not branch:
            b_ok, b_info, _ = await self.branch(project_name)
            branch = b_info.current_branch if b_info else "main"

        allowed, safe_args, prep_err = prepare_push_arguments(
            project_name=project_name,
            remote=remote,
            branch=branch,
            confirmed=confirmed,
            extra_flags=extra_flags,
        )
        if not allowed:
            return self._blocked("git push", project_name, prep_err), None

        res = await self._run(project_name=project_name, args=safe_args, operation="git push")

        verification = verify_push_outcome(
            project_name=project_name,
            remote=remote,
            branch=branch,
            exit_code=res.exit_code,
            stdout=res.stdout,
            stderr=res.stderr,
        )

        res.data = verification.model_dump()
        return res, verification

    # ─────────────────────────────────────────────────────────────────────────
    # Init Operations
    # ─────────────────────────────────────────────────────────────────────────

    async def init(self, project_name: str, confirmed: bool = False) -> GitResult:
        """Initialize a new git repository in the project directory."""
        if not confirmed:
            return self._blocked("git init", project_name, "git init requires user confirmation.")
        return await self._run(project_name=project_name, args=["init"], operation="git init")

    # ─────────────────────────────────────────────────────────────────────────
    # Process Execution & Security Guarding
    # ─────────────────────────────────────────────────────────────────────────

    async def _run(self, project_name: str, args: List[str], operation: str) -> GitResult:
        """Run a validated Git command inside SandboxProcessRunner."""
        # 1. Project path containment
        project_dir = resolve_project_path(project_name)
        if not project_dir:
            return self._blocked(operation, project_name, f"Project '{project_name}' escapes sandbox.")

        # 2. Subcommand argument check
        subcommand = args[0] if args else "status"
        valid_args, reason = validate_git_arguments(subcommand, args[1:])
        if not valid_args:
            return self._blocked(operation, project_name, reason)

        req = SandboxProcessRequest(
            project_name=project_name,
            executable="git",
            arguments=args,
            timeout_seconds=30.0,
            operation_label=operation,
        )
        result: SandboxProcessResult = await self.runner.run(req)

        clean_stdout = sanitize_git_output(result.stdout)
        clean_stderr = sanitize_git_output(result.stderr)

        return GitResult(
            success=result.success,
            operation=operation,
            project_name=project_name,
            stdout=clean_stdout,
            stderr=clean_stderr,
            exit_code=result.exit_code,
            duration_ms=result.duration_ms,
            security_blocked=result.security_blocked,
            block_reason=result.block_reason,
            message=result.message,
        )

    def _blocked(self, operation: str, project_name: str, reason: str) -> GitResult:
        logger.warning(f"[GIT BLOCKED] {operation} — {reason} project='{project_name}'")
        return GitResult(
            success=False,
            operation=operation,
            project_name=project_name,
            security_blocked=True,
            block_reason=reason,
            message=f"Git operation blocked: {reason}",
        )
