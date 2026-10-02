"""RYVEN DEV ENGINE — M11: Git Branch Inspector.

Inspects local and remote branch states with strict validation.
Destructive operations (force checkout, branch deletion) are strictly prohibited.
"""

from __future__ import annotations

import re
from typing import List, Optional

from app.core.logging_config import logger
from app.git.git_models import GitBranchResult
from app.git.git_validator import validate_branch_name


def parse_git_branches(project_name: str, raw_output: str) -> GitBranchResult:
    """Parse output from `git branch -a -vv` into GitBranchResult."""
    current_branch = "HEAD"
    local_branches: List[str] = []
    remote_branches: List[str] = []
    upstream: Optional[str] = None

    lines = raw_output.strip().splitlines()
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue

        is_current = cleaned.startswith("*")
        if is_current:
            cleaned = cleaned[1:].strip()

        # Split branch name from commit hash / tracking
        parts = cleaned.split()
        if not parts:
            continue

        name = parts[0].strip()

        # Handle detached HEAD or symbolic refs
        if name in ("(HEAD", "->"):
            continue

        if name.startswith("remotes/"):
            remote_branches.append(name)
        else:
            local_branches.append(name)

        if is_current:
            current_branch = name
            # Check for upstream in brackets e.g. [origin/main]
            m_track = re.search(r"\[([^:\]]+)", line)
            if m_track:
                upstream = m_track.group(1).strip()

    return GitBranchResult(
        project_name=project_name,
        current_branch=current_branch,
        local_branches=list(dict.fromkeys(local_branches)),
        remote_branches=list(dict.fromkeys(remote_branches)),
        upstream=upstream,
    )
