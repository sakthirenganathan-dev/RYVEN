"""RYVEN DEV ENGINE — M11: Git Commit Preparation and Preview.

Enforces:
- Human-in-the-loop preview of changes, branch, files, and commit message.
- Explicit user confirmation requirement.
- Commit message validation (length, characters, injection prevention).
- Pre-commit sensitive file and credential scans.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.core.logging_config import logger
from app.git.git_models import CommitPreview, GitStatusResult
from app.git.git_security import scan_sensitive_files
from app.git.git_validator import validate_commit_message, validate_git_repository


def generate_commit_preview(
    project_name: str,
    status: GitStatusResult,
    commit_message: str,
    author_name: Optional[str] = None,
    author_email: Optional[str] = None,
) -> CommitPreview:
    """Generate structured commit preview for human inspection and confirmation."""
    # Run sensitive scan on staged files
    staged_files = status.staged
    scan_res = scan_sensitive_files(status.repository_path, files_to_check=staged_files)
    sensitive_findings = scan_res.get("files", [])

    ready = (
        bool(staged_files)
        and len(sensitive_findings) == 0
        and validate_commit_message(commit_message) is None
    )

    return CommitPreview(
        repository=project_name,
        branch=status.branch,
        files=staged_files,
        added=[f for f in staged_files if f in status.added],
        modified=[f for f in staged_files if f in status.modified],
        deleted=[f for f in staged_files if f in status.deleted],
        sensitive_findings=sensitive_findings,
        commit_message=commit_message,
        author_name=author_name,
        author_email=author_email,
        ready_to_commit=ready,
        requires_confirmation=True,
    )


def prepare_commit_arguments(
    project_name: str,
    project_path: str,
    message: str,
    confirmed: bool,
    staged_files: List[str],
) -> Tuple[bool, List[str], str]:
    """Validate commit preconditions and return safe argv array."""
    if not confirmed:
        return False, [], "Git commit is a write operation and requires explicit user confirmation."

    err = validate_commit_message(message)
    if err:
        return False, [], f"Invalid commit message: {err}"

    if not staged_files:
        return False, [], "No files staged for commit. Please stage desired files first."

    # Final sensitive file gate
    scan_res = scan_sensitive_files(project_path, files_to_check=staged_files)
    if scan_res.get("blocked"):
        flagged = ", ".join(scan_res.get("files", []))
        return False, [], f"Commit blocked: Sensitive files or secrets detected in staging area: {flagged}"

    args = ["commit", "-m", message]
    return True, args, ""
