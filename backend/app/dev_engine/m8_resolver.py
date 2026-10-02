"""RYVEN DEV ENGINE — M8: ProjectResolver.

Safely maps a user-supplied project reference (name, path hint) to a
confirmed, contained path inside the approved projects root.

NEVER trusts raw user-supplied paths. All resolution goes through:
  sanitize_project_name() → resolve_project_path() → containment check

Blocked:
  - Path traversal (../../)
  - Absolute unauthorized paths
  - Windows system directories
  - Paths outside approved project root
"""

from __future__ import annotations

import os
import re
from typing import List, Optional

from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.tools.project_tool import get_projects_root, resolve_project_path, sanitize_project_name


# ─────────────────────────────────────────────────────────────────────────────
# Blocked path patterns
# ─────────────────────────────────────────────────────────────────────────────

_BLOCKED_PATH_PATTERNS = [
    re.compile(r"\.\./", re.IGNORECASE),           # Unix traversal
    re.compile(r"\.\.[/\\]", re.IGNORECASE),       # Windows traversal
    re.compile(r"^[A-Za-z]:\\", re.IGNORECASE),    # Absolute Windows drive path
    re.compile(r"^/", re.IGNORECASE),              # Absolute Unix path
]

_BLOCKED_SYSTEM_PATHS = [
    "windows", "system32", "program files", "program files (x86)",
    "programdata", "users\\default", "appdata", "winnt",
]


def _is_path_safe(raw: str) -> bool:
    """Return False if the path string contains any blocked pattern."""
    for pattern in _BLOCKED_PATH_PATTERNS:
        if pattern.search(raw):
            return False
    lower = raw.lower()
    for blocked in _BLOCKED_SYSTEM_PATHS:
        if blocked in lower:
            return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Result model
# ─────────────────────────────────────────────────────────────────────────────

class ResolvedProject(BaseModel):
    """Result of a ProjectResolver lookup."""

    found: bool
    project_name: str = ""
    absolute_path: str = ""
    reason: str = ""
    security_blocked: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Resolver
# ─────────────────────────────────────────────────────────────────────────────

class ProjectResolver:
    """Safely resolves a project name/hint to a canonical absolute path.

    Approved root: projects/ directory (same policy as all DEV ENGINE tools).
    All other paths are unconditionally rejected.
    """

    def resolve(self, project_name_or_hint: str) -> ResolvedProject:
        """Attempt to resolve the given hint to a real project directory.

        Strategy:
          1. Security check — reject traversal / absolute paths immediately.
          2. Sanitize the project name component.
          3. Resolve via the canonical projects root.
          4. Verify the directory exists on disk.
        """
        raw = (project_name_or_hint or "").strip()
        if not raw:
            return ResolvedProject(found=False, reason="Empty project name provided.")

        # 1. Security check on raw input
        if not _is_path_safe(raw):
            logger.warning(f"[PROJECT RESOLVER] Security blocked: {raw!r}")
            return ResolvedProject(
                found=False,
                reason=f"Rejected: path contains unsafe or unauthorized pattern: {raw!r}",
                security_blocked=True,
            )

        # 2. Extract basename (handle case where user typed a partial path)
        #    e.g. "myapp/src" → "myapp" (we only work at project root level)
        bare = os.path.basename(raw.rstrip("/\\"))

        # 3. Sanitize the project name
        clean_name = sanitize_project_name(bare)
        if not clean_name:
            return ResolvedProject(
                found=False,
                reason=f"Invalid project name: {bare!r}. Only alphanumeric, dash, underscore allowed.",
                security_blocked=True,
            )

        # 4. Resolve via canonical root
        abs_path = resolve_project_path(clean_name)
        if not abs_path:
            return ResolvedProject(
                found=False,
                reason=f"Path containment check failed for '{clean_name}'.",
                security_blocked=True,
            )

        # 5. Must exist on disk
        if not os.path.isdir(abs_path):
            return ResolvedProject(
                found=False,
                project_name=clean_name,
                absolute_path=abs_path,
                reason=f"Project '{clean_name}' does not exist in the approved workspace. "
                       f"Use 'create project' to build a new project first.",
            )

        logger.info(f"[PROJECT RESOLVER] Resolved '{raw}' → '{abs_path}'")
        return ResolvedProject(
            found=True,
            project_name=clean_name,
            absolute_path=abs_path,
            reason="Project found in approved workspace.",
        )

    def list_available_projects(self) -> List[str]:
        """Return names of all existing projects in the approved workspace."""
        projects_root = get_projects_root()
        try:
            return [
                entry
                for entry in os.listdir(projects_root)
                if os.path.isdir(os.path.join(projects_root, entry))
                and sanitize_project_name(entry) is not None
            ]
        except Exception as exc:
            logger.error(f"[PROJECT RESOLVER] Cannot list projects: {exc}")
            return []
