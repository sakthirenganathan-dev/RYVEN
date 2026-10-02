"""RYVEN DEV ENGINE — M11: Remote Git Engine Models.

Defines strongly-typed data structures for Git operations, requests,
statuses, diffs, previews, and verification outcomes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class GitOperation(str, Enum):
    """Supported, controlled Git operations."""

    STATUS = "STATUS"
    DIFF = "DIFF"
    BRANCH = "BRANCH"
    REMOTE = "REMOTE"
    LOG = "LOG"
    STAGE = "STAGE"
    UNSTAGE = "UNSTAGE"
    COMMIT = "COMMIT"
    PUSH = "PUSH"
    INIT = "INIT"


class GitFileState(str, Enum):
    """File status in working tree or staging index."""

    MODIFIED = "modified"
    ADDED = "added"
    DELETED = "deleted"
    RENAMED = "renamed"
    UNTRACKED = "untracked"
    STAGED = "staged"


# ─────────────────────────────────────────────────────────────────────────────
# Structured Data Models
# ─────────────────────────────────────────────────────────────────────────────

class GitFileChange(BaseModel):
    """Describes a single file modification in the repository."""

    path: str
    status: GitFileState
    staged: bool = False
    old_path: Optional[str] = None


class GitStatusResult(BaseModel):
    """Structured inspection of a Git repository status."""

    project_name: str
    repository_path: str
    branch: str = "main"
    clean: bool = True
    modified: List[str] = Field(default_factory=list)
    added: List[str] = Field(default_factory=list)
    deleted: List[str] = Field(default_factory=list)
    renamed: List[str] = Field(default_factory=list)
    untracked: List[str] = Field(default_factory=list)
    staged: List[str] = Field(default_factory=list)
    ahead: int = 0
    behind: int = 0
    upstream: Optional[str] = None
    has_remote: bool = False
    raw_output: str = ""


class GitDiffResult(BaseModel):
    """Structured outcome of a Git diff operation with sanitized output."""

    project_name: str
    diff_text: str = ""
    files_changed: List[str] = Field(default_factory=list)
    staged: bool = False
    total_files: int = 0
    insertions: int = 0
    deletions: int = 0
    sensitive_redactions_count: int = 0


class GitBranchResult(BaseModel):
    """Inspection of repository branches."""

    project_name: str
    current_branch: str
    local_branches: List[str] = Field(default_factory=list)
    remote_branches: List[str] = Field(default_factory=list)
    upstream: Optional[str] = None


class GitRemoteInfo(BaseModel):
    """Information for a single Git remote with redacted credentials."""

    name: str
    fetch_url: str
    push_url: str


class GitRemoteResult(BaseModel):
    """List of configured remotes for a project."""

    project_name: str
    remotes: List[GitRemoteInfo] = Field(default_factory=list)


class GitLogEntry(BaseModel):
    """Single commit log item."""

    commit_hash: str
    author: str = ""
    date: str = ""
    message: str = ""


class GitLogResult(BaseModel):
    """History of recent commits."""

    project_name: str
    commits: List[GitLogEntry] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Previews & Verification
# ─────────────────────────────────────────────────────────────────────────────

class CommitPreview(BaseModel):
    """Human-in-the-loop preview displayed before a commit is created."""

    repository: str
    branch: str
    files: List[str] = Field(default_factory=list)
    added: List[str] = Field(default_factory=list)
    modified: List[str] = Field(default_factory=list)
    deleted: List[str] = Field(default_factory=list)
    sensitive_findings: List[str] = Field(default_factory=list)
    commit_message: str
    author_name: Optional[str] = None
    author_email: Optional[str] = None
    ready_to_commit: bool = True
    requires_confirmation: bool = True


class PushPreview(BaseModel):
    """Human-in-the-loop preview displayed before pushing to a remote."""

    repository: str
    branch: str
    remote: str
    upstream: Optional[str] = None
    commits_to_push: List[str] = Field(default_factory=list)
    files_affected: List[str] = Field(default_factory=list)
    sensitive_scan_passed: bool = True
    force_push: bool = False
    ready_to_push: bool = True
    requires_confirmation: bool = True


class VerificationResult(BaseModel):
    """Verification outcome of commit or push operation."""

    success: bool
    verified: bool
    operation: str
    commit_hash: Optional[str] = None
    branch: Optional[str] = None
    remote: Optional[str] = None
    message: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Request & Unified Engine Result
# ─────────────────────────────────────────────────────────────────────────────

class GitRequest(BaseModel):
    """Controlled specification for a Git operation."""

    operation: GitOperation
    project_name: str
    files: List[str] = Field(default_factory=list)
    commit_message: Optional[str] = None
    branch: Optional[str] = None
    remote: Optional[str] = None
    staged: bool = False
    confirmed: bool = False
    expected_state: Optional[Dict[str, Any]] = None


class GitResult(BaseModel):
    """Unified outcome of a Git operation (backward-compatible with v2 GitResult)."""

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
    data: Optional[Dict[str, Any]] = None
