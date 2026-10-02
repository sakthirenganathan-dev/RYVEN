"""RYVEN DEV ENGINE — M11: Git Status Parser and Inspector.

Parses Git porcelain status output to produce structured, safe representations
of working tree and index states.
"""

from __future__ import annotations

import os
import re
from typing import List, Tuple

from app.core.logging_config import logger
from app.git.git_models import GitStatusResult


def parse_git_status(
    project_name: str,
    project_path: str,
    raw_status: str,
) -> GitStatusResult:
    """Parse output of `git status --porcelain=v1 -b` into GitStatusResult."""
    branch = "HEAD"
    upstream = None
    ahead = 0
    behind = 0
    clean = True

    modified: List[str] = []
    added: List[str] = []
    deleted: List[str] = []
    renamed: List[str] = []
    untracked: List[str] = []
    staged: List[str] = []

    lines = raw_status.strip().splitlines()
    for line in lines:
        if not line:
            continue

        # Branch header: ## main...origin/main [ahead 1, behind 2]
        if line.startswith("## "):
            branch_info = line[3:].strip()
            # Branch name and tracking
            if "..." in branch_info:
                parts = branch_info.split("...")
                branch = parts[0].strip()
                remainder = parts[1].strip()
                # Check tracking branch and ahead/behind counts
                m_track = re.match(r"^([^\s\[]+)(?:\s+\[(.*)\])?", remainder)
                if m_track:
                    upstream = m_track.group(1).strip()
                    counts = m_track.group(2) or ""
                    m_ahead = re.search(r"ahead\s+(\d+)", counts)
                    m_behind = re.search(r"behind\s+(\d+)", counts)
                    if m_ahead:
                        ahead = int(m_ahead.group(1))
                    if m_behind:
                        behind = int(m_behind.group(1))
            else:
                branch = branch_info.split()[0].strip()
            continue

        # File change lines: XY PATH
        if len(line) < 3:
            continue

        clean = False
        index_status = line[0]
        worktree_status = line[1]
        path_part = line[3:].strip()

        # Handle renames: OLD -> NEW
        if " -> " in path_part:
            _, new_path = path_part.split(" -> ", 1)
            target_path = new_path.strip().strip('"')
            renamed.append(target_path)
            staged.append(target_path)
            continue

        target_path = path_part.strip().strip('"')

        # Staged changes (Index status is non-space and non-?)
        if index_status not in (" ", "?"):
            staged.append(target_path)
            if index_status == "A":
                added.append(target_path)
            elif index_status == "M":
                modified.append(target_path)
            elif index_status == "D":
                deleted.append(target_path)
            elif index_status == "R":
                renamed.append(target_path)

        # Worktree changes
        if worktree_status == "?":
            untracked.append(target_path)
        elif worktree_status == "M":
            if target_path not in modified:
                modified.append(target_path)
        elif worktree_status == "D":
            if target_path not in deleted:
                deleted.append(target_path)

    # Deduplicate lists
    return GitStatusResult(
        project_name=project_name,
        repository_path=project_path,
        branch=branch,
        clean=clean,
        modified=list(dict.fromkeys(modified)),
        added=list(dict.fromkeys(added)),
        deleted=list(dict.fromkeys(deleted)),
        renamed=list(dict.fromkeys(renamed)),
        untracked=list(dict.fromkeys(untracked)),
        staged=list(dict.fromkeys(staged)),
        ahead=ahead,
        behind=behind,
        upstream=upstream,
        has_remote=bool(upstream),
        raw_output=raw_status,
    )
