"""RYVEN DEV ENGINE v2 / M11 — Controlled Git Engine (Forwarding Module).

Re-exports core Git components from `app.git` for backward compatibility.
"""

from __future__ import annotations

from app.git.git_engine import GitEngine
from app.git.git_models import GitResult
from app.git.git_validator import (
    MAX_COMMIT_MSG_LEN,
    _validate_branch,
    _validate_commit_message,
    validate_branch_name,
    validate_commit_message,
)

__all__ = [
    "GitEngine",
    "GitResult",
    "validate_commit_message",
    "validate_branch_name",
    "_validate_commit_message",
    "_validate_branch",
    "MAX_COMMIT_MSG_LEN",
]
