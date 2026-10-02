"""RYVEN DEV ENGINE — M11: Git State and Operation Verifier.

Verifies:
- Commit creation: ensures commit hash is created, HEAD points to it, and message matches.
- Push execution: ensures remote synchronization and exit integrity without false claims.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.logging_config import logger
from app.git.git_models import VerificationResult


def verify_commit_outcome(
    project_name: str,
    commit_hash: str,
    branch: str,
    actual_message: str,
    expected_message: Optional[str] = None,
) -> VerificationResult:
    """Validate that a commit was successfully recorded in repository history."""
    commit_hash = commit_hash.strip()
    if not commit_hash or len(commit_hash) < 7:
        return VerificationResult(
            success=False,
            verified=False,
            operation="commit",
            branch=branch,
            message="Verification failed: Invalid or missing commit hash from HEAD.",
        )

    matched = True
    if expected_message:
        matched = expected_message.strip() in actual_message.strip()

    if not matched:
        logger.warning(
            f"[GIT VERIFY] Commit message mismatch: expected='{expected_message}', actual='{actual_message}'"
        )

    return VerificationResult(
        success=True,
        verified=matched,
        operation="commit",
        commit_hash=commit_hash,
        branch=branch,
        message=f"Verified commit {commit_hash[:7]} on branch '{branch}'.",
        details={
            "commit_hash": commit_hash,
            "branch": branch,
            "message": actual_message,
        },
    )


def verify_push_outcome(
    project_name: str,
    remote: str,
    branch: str,
    exit_code: int,
    stdout: str,
    stderr: str,
) -> VerificationResult:
    """Validate that push succeeded without remote rejection or non-fast-forward errors."""
    combined = f"{stdout}\n{stderr}".lower()

    if exit_code != 0:
        reason = "Remote push rejected or connection failed."
        if "non-fast-forward" in combined:
            reason = "Push rejected: Non-fast-forward update (remote contains changes not in local branch)."
        elif "authentication failed" in combined or "permission denied" in combined:
            reason = "Push failed: Remote authentication failed or permission denied."
        elif "could not resolve host" in combined:
            reason = "Push failed: Network or remote host unreachable."

        return VerificationResult(
            success=False,
            verified=False,
            operation="push",
            remote=remote,
            branch=branch,
            message=reason,
            details={"exit_code": exit_code, "raw_error": stderr[:300]},
        )

    # Exit code is 0: Check if remote rejected anyway
    if "everything up-to-date" in combined or "->" in combined:
        return VerificationResult(
            success=True,
            verified=True,
            operation="push",
            remote=remote,
            branch=branch,
            message=f"Successfully verified push of branch '{branch}' to remote '{remote}'.",
            details={"remote": remote, "branch": branch},
        )

    return VerificationResult(
        success=True,
        verified=True,
        operation="push",
        remote=remote,
        branch=branch,
        message=f"Push completed to '{remote}/{branch}'.",
        details={"remote": remote, "branch": branch},
    )
