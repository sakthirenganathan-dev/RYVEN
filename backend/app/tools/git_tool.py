"""RYVEN DEV ENGINE — M11: Registered Git Tools for ToolRegistry.

Provides strictly bounded, policy-controlled Git inspection and mutation tools:
- GitStatusTool (`git_status`): Read-only inspection of branches, dirty state, and files.
- GitDiffTool (`git_diff`): Read-only diff inspection with credential and token sanitization.
- GitBranchTool (`git_branch`): Read-only branch inspection.
- GitRemoteTool (`git_remote`): Read-only remote inspection with URL credential redaction.
- GitLogTool (`git_log`): Read-only commit log inspection.
- GitStageTool (`git_stage`): Controlled staging of validated, non-sensitive files.
- GitUnstageTool (`git_unstage`): Safe unstaging affecting the index only.
- GitCommitTool (`git_commit`): Write operation requiring explicit confirmation and commit verification.
- GitPushTool (`git_push`): High-risk operation requiring explicit confirmation, banning force pushes.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.core.logging_config import logger
from app.git.git_engine import GitEngine
from app.git.git_validator import validate_git_repository
from app.tools.base import BaseTool


# ─────────────────────────────────────────────────────────────────────────────
# 1. GitStatusTool
# ─────────────────────────────────────────────────────────────────────────────

class GitStatusTool(BaseTool):
    """Inspects Git repository working tree and index status without modification."""

    name = "git_status"
    description = (
        "Inspects Git status for an approved project. Returns current branch, clean/dirty state, "
        "modified, added, deleted, untracked, and staged files. Read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Name of the contained project directory",
            },
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[GitEngine] = None) -> None:
        self._engine = engine or GitEngine()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        if not project_name:
            return {"success": False, "tool": self.name, "message": "project_name is required."}

        success, status, err = await self._engine.get_structured_status(project_name)
        if not success or not status:
            return {
                "success": False,
                "tool": self.name,
                "project_name": project_name,
                "message": err,
                "security_blocked": "outside sandbox" in err or "escapes" in err,
            }

        return {
            "success": True,
            "tool": self.name,
            "project_name": project_name,
            "branch": status.branch,
            "clean": status.clean,
            "modified": status.modified,
            "added": status.added,
            "deleted": status.deleted,
            "renamed": status.renamed,
            "untracked": status.untracked,
            "staged": status.staged,
            "ahead": status.ahead,
            "behind": status.behind,
            "upstream": status.upstream,
            "has_remote": status.has_remote,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 2. GitDiffTool
# ─────────────────────────────────────────────────────────────────────────────

class GitDiffTool(BaseTool):
    """Inspects Git diffs with automatic secret sanitization."""

    name = "git_diff"
    description = (
        "Inspects Git diff (working tree or staged) with automatic secret redaction. "
        "Protects tokens, credentials, and private keys. Read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "staged": {"type": "boolean", "default": False},
            "file_path": {"type": "string"},
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[GitEngine] = None) -> None:
        self._engine = engine or GitEngine()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        staged = bool(kwargs.get("staged", False))
        file_path = kwargs.get("file_path")

        if not project_name:
            return {"success": False, "tool": self.name, "message": "project_name is required."}

        success, diff_res, err = await self._engine.get_structured_diff(
            project_name=project_name,
            staged=staged,
            file_path=file_path,
        )
        if not success or not diff_res:
            return {
                "success": False,
                "tool": self.name,
                "project_name": project_name,
                "message": err,
                "security_blocked": "outside sandbox" in err or "escapes" in err,
            }

        return {
            "success": True,
            "tool": self.name,
            "project_name": project_name,
            "diff_text": diff_res.diff_text,
            "files_changed": diff_res.files_changed,
            "staged": diff_res.staged,
            "insertions": diff_res.insertions,
            "deletions": diff_res.deletions,
            "total_files": diff_res.total_files,
            "sensitive_redactions_count": diff_res.sensitive_redactions_count,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 3. GitBranchTool
# ─────────────────────────────────────────────────────────────────────────────

class GitBranchTool(BaseTool):
    """Inspects Git branches. Destructive branch deletion is blocked."""

    name = "git_branch"
    description = (
        "Inspects local and remote Git branches for a project. "
        "Destructive operations like force checkout or branch deletion are blocked. Read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[GitEngine] = None) -> None:
        self._engine = engine or GitEngine()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        if not project_name:
            return {"success": False, "tool": self.name, "message": "project_name is required."}

        success, branch_res, err = await self._engine.branch(project_name)
        if not success or not branch_res:
            return {
                "success": False,
                "tool": self.name,
                "project_name": project_name,
                "message": err,
            }

        return {
            "success": True,
            "tool": self.name,
            "project_name": project_name,
            "current_branch": branch_res.current_branch,
            "local_branches": branch_res.local_branches,
            "remote_branches": branch_res.remote_branches,
            "upstream": branch_res.upstream,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 4. GitRemoteTool
# ─────────────────────────────────────────────────────────────────────────────

class GitRemoteTool(BaseTool):
    """Inspects Git remotes with automatic credential redaction from URLs."""

    name = "git_remote"
    description = (
        "Inspects Git remotes for a project. Automatically redacts embedded tokens, "
        "passwords, and credentials from fetch and push URLs. Read-only."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[GitEngine] = None) -> None:
        self._engine = engine or GitEngine()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        if not project_name:
            return {"success": False, "tool": self.name, "message": "project_name is required."}

        success, remote_res, err = await self._engine.remote(project_name)
        if not success or not remote_res:
            return {
                "success": False,
                "tool": self.name,
                "project_name": project_name,
                "message": err,
            }

        return {
            "success": True,
            "tool": self.name,
            "project_name": project_name,
            "remotes": [r.model_dump() for r in remote_res.remotes],
        }


# ─────────────────────────────────────────────────────────────────────────────
# 5. GitLogTool
# ─────────────────────────────────────────────────────────────────────────────

class GitLogTool(BaseTool):
    """Inspects recent Git commit history."""

    name = "git_log"
    description = "Inspects recent commit history for an approved project. Read-only."
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "max_count": {"type": "integer", "default": 10},
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[GitEngine] = None) -> None:
        self._engine = engine or GitEngine()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        count = int(kwargs.get("max_count", 10))

        if not project_name:
            return {"success": False, "tool": self.name, "message": "project_name is required."}

        res = await self._engine.log(project_name, n=count)
        return {
            "success": res.success,
            "tool": self.name,
            "project_name": project_name,
            "stdout": res.stdout,
            "stderr": res.stderr,
            "message": res.message,
        }


# ─────────────────────────────────────────────────────────────────────────────
# 6. GitStageTool
# ─────────────────────────────────────────────────────────────────────────────

class GitStageTool(BaseTool):
    """Controlled staging of explicitly requested files. Never stages all blindly."""

    name = "git_stage"
    description = (
        "Stages explicitly specified files into the Git index. Path containment is verified "
        "and sensitive files (.env, keys, credentials) are strictly blocked from staging."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Explicit list of relative file paths to stage",
            },
        },
        "required": ["project_name", "files"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[GitEngine] = None) -> None:
        self._engine = engine or GitEngine()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        files = kwargs.get("files") or []

        if not project_name:
            return {"success": False, "tool": self.name, "message": "project_name is required."}
        if not files:
            return {
                "success": False,
                "tool": self.name,
                "message": "files parameter is required. Blind staging of all files is blocked.",
            }

        res = await self._engine.stage(project_name=project_name, files=files, confirmed=True)
        return {
            "success": res.success,
            "tool": self.name,
            "project_name": project_name,
            "staged_files": files if res.success else [],
            "security_blocked": res.security_blocked,
            "message": res.message or (f"Staged {len(files)} file(s) successfully." if res.success else res.stderr),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 7. GitUnstageTool
# ─────────────────────────────────────────────────────────────────────────────

class GitUnstageTool(BaseTool):
    """Safely unstages files from index without altering working tree contents."""

    name = "git_unstage"
    description = (
        "Safely removes files from the Git index using `restore --staged`. "
        "Does not discard or modify working tree changes."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of files to unstage. If omitted, unstages all staged files.",
            },
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = False

    def __init__(self, engine: Optional[GitEngine] = None) -> None:
        self._engine = engine or GitEngine()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        files = kwargs.get("files")

        if not project_name:
            return {"success": False, "tool": self.name, "message": "project_name is required."}

        res = await self._engine.unstage(project_name=project_name, files=files)
        return {
            "success": res.success,
            "tool": self.name,
            "project_name": project_name,
            "message": res.message or ("Unstaged files successfully." if res.success else res.stderr),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 8. GitCommitTool
# ─────────────────────────────────────────────────────────────────────────────

class GitCommitTool(BaseTool):
    """Creates a Git commit with preview and mandatory confirmation."""

    name = "git_commit"
    description = (
        "Creates a Git commit with a validated commit message. "
        "Generates a preview, checks sensitive files, and requires explicit confirmation. "
        "Verifies commit hash from HEAD."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "message": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["project_name", "message"],
        "additionalProperties": True,
    }
    requires_confirmation = True

    def __init__(self, engine: Optional[GitEngine] = None) -> None:
        self._engine = engine or GitEngine()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        message = kwargs.get("message") or kwargs.get("commit_message") or ""
        raw_confirmed = kwargs.get("confirmed", False)
        if isinstance(raw_confirmed, str):
            confirmed = raw_confirmed.lower() in ("true", "1", "yes")
        else:
            confirmed = bool(raw_confirmed)

        if not project_name:
            return {"success": False, "tool": self.name, "message": "project_name is required."}
        if not message:
            return {"success": False, "tool": self.name, "message": "message is required."}

        # Generate commit preview
        p_ok, preview, p_err = await self._engine.preview_commit(project_name, message)
        if not p_ok or not preview:
            return {
                "success": False,
                "tool": self.name,
                "project_name": project_name,
                "message": p_err,
            }

        # If not confirmed, return preview and security pause
        if not confirmed:
            return {
                "success": False,
                "tool": self.name,
                "project_name": project_name,
                "requires_confirmation": True,
                "security_blocked": True,
                "commit_preview": preview.model_dump(),
                "message": "Commit requires explicit user confirmation before writing to repository history.",
            }

        # Confirmed execution with verification
        res, verif = await self._engine.commit_and_verify(
            project_name=project_name,
            message=message,
            confirmed=True,
        )

        return {
            "success": res.success,
            "tool": self.name,
            "project_name": project_name,
            "commit_hash": verif.commit_hash if verif else None,
            "branch": verif.branch if verif else None,
            "verified": verif.verified if verif else False,
            "security_blocked": res.security_blocked,
            "message": res.message or (verif.message if verif else res.stderr),
        }


# ─────────────────────────────────────────────────────────────────────────────
# 9. GitPushTool
# ─────────────────────────────────────────────────────────────────────────────

class GitPushTool(BaseTool):
    """Pushes a branch to a remote. Force pushes are permanently blocked."""

    name = "git_push"
    description = (
        "Pushes commits to a remote Git repository. "
        "Requires explicit confirmation. Force push (--force, -f) and destructive branch deletions "
        "are strictly prohibited. Verifies remote synchronization."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {"type": "string"},
            "remote": {"type": "string", "default": "origin"},
            "branch": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["project_name"],
        "additionalProperties": True,
    }
    requires_confirmation = True

    def __init__(self, engine: Optional[GitEngine] = None) -> None:
        self._engine = engine or GitEngine()

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        project_name = kwargs.get("project_name") or kwargs.get("project_name_hint") or ""
        remote = kwargs.get("remote") or "origin"
        branch = kwargs.get("branch")
        raw_confirmed = kwargs.get("confirmed", False)
        if isinstance(raw_confirmed, str):
            confirmed = raw_confirmed.lower() in ("true", "1", "yes")
        else:
            confirmed = bool(raw_confirmed)

        if not project_name:
            return {"success": False, "tool": self.name, "message": "project_name is required."}

        # Generate push preview
        p_ok, preview, p_err = await self._engine.preview_push(project_name, remote=remote)
        if not p_ok or not preview:
            return {
                "success": False,
                "tool": self.name,
                "project_name": project_name,
                "message": p_err,
            }

        # If not confirmed, halt with preview
        if not confirmed:
            return {
                "success": False,
                "tool": self.name,
                "project_name": project_name,
                "requires_confirmation": True,
                "security_blocked": True,
                "push_preview": preview.model_dump(),
                "message": "Git push is a high-risk operation and requires explicit user confirmation.",
            }

        # Execute push
        res, verif = await self._engine.push(
            project_name=project_name,
            remote=remote,
            branch=branch,
            confirmed=True,
        )

        return {
            "success": res.success,
            "tool": self.name,
            "project_name": project_name,
            "remote": remote,
            "branch": verif.branch if verif else branch,
            "verified": verif.verified if verif else False,
            "security_blocked": res.security_blocked,
            "message": res.message or (verif.message if verif else res.stderr),
        }
