"""RYVEN DEV ENGINE — M11: Controlled Git Push.

Enforces:
- Strict Human-in-the-Loop push confirmation and push preview.
- Absolute ban on force push (--force, -f, --force-with-lease).
- Absolute ban on remote branch deletion (:branch or --delete).
- Validated remote and branch parameters.
- Pre-push sensitive file inspection.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.core.logging_config import logger
from app.git.git_models import GitStatusResult, PushPreview
from app.git.git_security import check_destructive_command, scan_sensitive_files
from app.git.git_validator import validate_branch_name, validate_remote_name


def generate_push_preview(
    project_name: str,
    status: GitStatusResult,
    remote: str = "origin",
    commits_to_push: Optional[List[str]] = None,
    files_affected: Optional[List[str]] = None,
) -> PushPreview:
    """Generate a push preview before requesting explicit confirmation."""
    scan_res = scan_sensitive_files(status.repository_path)
    sensitive_passed = not scan_res.get("blocked", False)

    ready = (
        sensitive_passed
        and validate_remote_name(remote) is None
        and validate_branch_name(status.branch) is None
    )

    return PushPreview(
        repository=project_name,
        branch=status.branch,
        remote=remote,
        upstream=status.upstream,
        commits_to_push=commits_to_push or [],
        files_affected=files_affected or [],
        sensitive_scan_passed=sensitive_passed,
        force_push=False,  # Force push is never supported
        ready_to_push=ready,
        requires_confirmation=True,
    )


def prepare_push_arguments(
    project_name: str,
    remote: str,
    branch: str,
    confirmed: bool,
    extra_flags: Optional[List[str]] = None,
) -> Tuple[bool, List[str], str]:
    """Validate push arguments and ensure no destructive flags or force pushes are present."""
    if not confirmed:
        return False, [], "Git push is a high-risk operation and requires explicit user confirmation."

    # Validate remote name
    remote_err = validate_remote_name(remote)
    if remote_err:
        return False, [], f"Invalid remote name: {remote_err}"

    # Validate branch name
    branch_err = validate_branch_name(branch)
    if branch_err:
        return False, [], f"Invalid branch name: {branch_err}"

    # Inspect any extra flags for destructive options
    flags = extra_flags or []
    is_destructive, reason = check_destructive_command("push", flags)
    if is_destructive:
        return False, [], f"Push blocked: {reason}"

    # Clean argv array
    args = ["push", remote, branch]
    return True, args, ""
