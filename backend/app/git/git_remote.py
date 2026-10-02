"""RYVEN DEV ENGINE — M11: Git Remote Inspector.

Inspects remote repositories and redacts sensitive credentials or tokens from URLs.
"""

from __future__ import annotations

import re
from typing import Dict, List

from app.core.logging_config import logger
from app.git.git_models import GitRemoteInfo, GitRemoteResult
from app.git.git_security import redact_credentials_from_url


def parse_git_remotes(project_name: str, raw_output: str) -> GitRemoteResult:
    """Parse output of `git remote -v` into GitRemoteResult with redacted URLs."""
    remote_map: Dict[str, Dict[str, str]] = {}

    lines = raw_output.strip().splitlines()
    for line in lines:
        parts = line.strip().split()
        if len(parts) >= 2:
            name = parts[0]
            raw_url = parts[1]
            url_type = parts[2].strip("()") if len(parts) >= 3 else "fetch"

            if name not in remote_map:
                remote_map[name] = {"fetch": "", "push": ""}

            clean_url = redact_credentials_from_url(raw_url)
            if "fetch" in url_type.lower():
                remote_map[name]["fetch"] = clean_url
            elif "push" in url_type.lower():
                remote_map[name]["push"] = clean_url

    remotes: List[GitRemoteInfo] = []
    for name, urls in remote_map.items():
        remotes.append(
            GitRemoteInfo(
                name=name,
                fetch_url=urls.get("fetch") or urls.get("push", ""),
                push_url=urls.get("push") or urls.get("fetch", ""),
            )
        )

    return GitRemoteResult(
        project_name=project_name,
        remotes=remotes,
    )
