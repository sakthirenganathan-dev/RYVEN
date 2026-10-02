"""RYVEN DEV ENGINE — M11: Git Diff Inspector and Sanitizer.

Executes diff inspections, sanitizes secret output, and summarizes modifications.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from app.core.logging_config import logger
from app.git.git_models import GitDiffResult
from app.git.git_security import sanitize_git_output


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)")
_STAT_SUMMARY_RE = re.compile(r"(?P<files>\d+)\s+files?\s+changed(?:,\s+(?P<ins>\d+)\s+insertions?\(\+\))?(?:,\s+(?P<del>\d+)\s+deletions?\(-\))?")


def parse_git_diff(
    project_name: str,
    raw_diff: str,
    staged: bool = False,
) -> GitDiffResult:
    """Parse raw Git diff text, sanitize secret patterns, and extract file lists and stats."""
    # 1. Sanitize text for security
    sanitized_text = sanitize_git_output(raw_diff)

    files_changed: List[str] = []
    insertions = 0
    deletions = 0
    redaction_count = sanitized_text.count("[REDACTED")

    lines = raw_diff.splitlines()
    for line in lines:
        m_head = _DIFF_HEADER_RE.match(line)
        if m_head:
            target = m_head.group("b")
            if target not in files_changed:
                files_changed.append(target)
            continue

        if line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1

    return GitDiffResult(
        project_name=project_name,
        diff_text=sanitized_text,
        files_changed=files_changed,
        staged=staged,
        total_files=len(files_changed),
        insertions=insertions,
        deletions=deletions,
        sensitive_redactions_count=redaction_count,
    )
