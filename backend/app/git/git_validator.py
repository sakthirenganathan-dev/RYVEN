"""RYVEN DEV ENGINE — M11: Git Validation and Containment Rules.

Enforces:
- Strict project root path containment within approved workspaces.
- Real Git repository detection (verifying .git existence and integrity).
- Safe commit message validation (length, characters, no injection).
- Safe branch and remote naming conventions.
- Subcommand allowlisting (prohibiting unapproved or arbitrary Git commands).
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from app.core.logging_config import logger
from app.dev_engine.m8_resolver import ProjectResolver
from app.git.git_security import check_destructive_command
from app.tools.project_tool import resolve_project_file_path, resolve_project_path


# ─────────────────────────────────────────────────────────────────────────────
# Regex Patterns
# ─────────────────────────────────────────────────────────────────────────────

_SAFE_COMMIT_MSG_RE = re.compile(r"^[a-zA-Z0-9 \-_\.\,\:\!\(\)\[\]\/\#]+$")
_SAFE_BRANCH_RE = re.compile(r"^[a-zA-Z0-9_\-\/\.]+$")
_SAFE_REMOTE_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
_SAFE_PATH_CHAR_RE = re.compile(r"^[a-zA-Z0-9_\-\.\/\\]+$")

MAX_COMMIT_MSG_LEN = 200

# Subcommands approved for execution via SandboxProcessRunner
APPROVED_GIT_SUBCOMMANDS = {
    "status",
    "diff",
    "branch",
    "remote",
    "log",
    "rev-parse",
    "show",
    "add",
    "restore",
    "commit",
    "push",
    "init",
}


# ─────────────────────────────────────────────────────────────────────────────
# Validators
# ─────────────────────────────────────────────────────────────────────────────

def validate_commit_message(msg: str) -> Optional[str]:
    """Validate a proposed Git commit message.
    
    Returns error description string if invalid, or None if valid.
    """
    if not isinstance(msg, str):
        return "Commit message must be a string."
    msg = msg.strip()
    if not msg:
        return "Commit message cannot be empty."
    if len(msg) > MAX_COMMIT_MSG_LEN:
        return f"Commit message too long ({len(msg)} > {MAX_COMMIT_MSG_LEN} chars)."
    if not _SAFE_COMMIT_MSG_RE.match(msg):
        return f"Commit message contains disallowed characters: {msg!r}"
    return None


# Backward-compatible alias for existing tests
_validate_commit_message = validate_commit_message


def validate_branch_name(branch: str) -> Optional[str]:
    """Validate a Git branch name for safe operations."""
    if not branch or not isinstance(branch, str):
        return "Branch name cannot be empty."
    branch = branch.strip()
    if len(branch) > 100:
        return "Branch name too long (max 100 chars)."
    if ".." in branch:
        return f"Branch name contains disallowed traversal sequence '..': {branch!r}"
    if not _SAFE_BRANCH_RE.match(branch):
        return f"Invalid branch name: {branch!r}"
    return None


# Backward-compatible alias for existing tests
_validate_branch = validate_branch_name


def validate_remote_name(remote: str) -> Optional[str]:
    """Validate a remote name (e.g. 'origin', 'upstream')."""
    if not remote or not isinstance(remote, str):
        return "Remote name cannot be empty."
    remote = remote.strip()
    if not _SAFE_REMOTE_RE.match(remote):
        return f"Invalid remote name: {remote!r}"
    return None


def validate_file_path_for_git(project_name: str, relative_path: str) -> Tuple[bool, str]:
    """Verify that a relative file path is strictly contained within the project."""
    if not relative_path or not isinstance(relative_path, str):
        return False, "File path cannot be empty."
    
    # Path traversal detection
    if ".." in relative_path or relative_path.startswith("/") or relative_path.startswith("\\"):
        return False, f"Path traversal or leading slash rejected: {relative_path!r}"

    # Windows drive letter detection
    if re.search(r"^[A-Za-z]:", relative_path):
        return False, f"Absolute path rejected: {relative_path!r}"

    # Verify containment via resolve_project_file_path
    resolved = resolve_project_file_path(project_name, relative_path)
    if not resolved:
        return False, f"Path '{relative_path}' escapes project boundary for '{project_name}'."

    return True, resolved


def validate_git_repository(project_name: str) -> Tuple[bool, str, str]:
    """Verify that project exists and contains a valid, accessible Git repository.
    
    Returns:
        (is_valid: bool, error_reason: str, project_dir: str)
    """
    # 1. Resolve project directory within approved projects sandbox
    project_dir = resolve_project_path(project_name)
    if not project_dir or not os.path.isdir(project_dir):
        return False, f"Project '{project_name}' directory not found or outside sandbox.", ""

    # 2. Check for .git directory or file (file for git worktree/submodule)
    git_dir = os.path.join(project_dir, ".git")
    if not os.path.exists(git_dir):
        return False, f"Project '{project_name}' is not a Git repository (.git not found).", project_dir

    if not (os.path.isdir(git_dir) or os.path.isfile(git_dir)):
        return False, f"Project '{project_name}' has an invalid .git path entry.", project_dir

    return True, "", project_dir


def validate_git_arguments(subcommand: str, args: List[str]) -> Tuple[bool, str]:
    """Verify that a Git subcommand and arguments conform to allowlist policy."""
    sub = subcommand.lower().strip()
    if sub not in APPROVED_GIT_SUBCOMMANDS:
        return False, f"Git subcommand '{sub}' is not permitted by RYVEN security policy."

    is_destructive, reason = check_destructive_command(sub, args)
    if is_destructive:
        return False, reason

    return True, ""
