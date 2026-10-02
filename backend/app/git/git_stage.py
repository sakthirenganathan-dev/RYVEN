"""RYVEN DEV ENGINE — M11: Controlled Git Staging and Safe Unstaging.

Enforces:
- No blind `git add .` or `git add -A` without explicit inspection.
- Individual path validation and containment verification.
- Pre-staging sensitive file scan (rejects .env, keys, credentials).
- Safe unstaging via `git restore --staged` affecting index only (never discards work).
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging_config import logger
from app.git.git_models import GitResult
from app.git.git_security import scan_sensitive_files
from app.git.git_validator import validate_file_path_for_git, validate_git_repository


def prepare_staging_arguments(
    project_name: str,
    project_path: str,
    files: List[str],
) -> Tuple[bool, List[str], str]:
    """Validate files to be staged and ensure none are sensitive or escaping project boundaries.
    
    Returns:
        (allowed: bool, safe_args: List[str], error_message: str)
    """
    if not files:
        return False, [], "No files specified for staging. Blind staging of all files is disabled for safety."

    # 1. Path containment check on all files
    validated_rel_paths: List[str] = []
    for rel_path in files:
        cleaned = rel_path.strip().replace("\\", "/")
        if not cleaned:
            continue
        is_contained, abs_p = validate_file_path_for_git(project_name, cleaned)
        if not is_contained:
            return False, [], abs_p
        validated_rel_paths.append(cleaned)

    if not validated_rel_paths:
        return False, [], "No valid file paths found for staging."

    # 2. Sensitive file and credential scanner
    scan_res = scan_sensitive_files(project_path, files_to_check=validated_rel_paths)
    if scan_res.get("blocked"):
        flagged = ", ".join(scan_res.get("files", []))
        return False, [], f"Staging blocked: Sensitive file(s) or secrets detected: {flagged}"

    # Build safe argv array using '--' to prevent flag injection
    args = ["add", "--"] + validated_rel_paths
    return True, args, ""


def prepare_unstage_arguments(
    project_name: str,
    files: List[str],
) -> Tuple[bool, List[str], str]:
    """Build safe unstage command modifying the staging index only."""
    if not files:
        # If no files specified, unstage entire index safely using `restore --staged .`
        return True, ["restore", "--staged", "."], ""

    validated_rel_paths: List[str] = []
    for rel_path in files:
        cleaned = rel_path.strip().replace("\\", "/")
        if not cleaned:
            continue
        is_contained, abs_p = validate_file_path_for_git(project_name, cleaned)
        if not is_contained:
            return False, [], abs_p
        validated_rel_paths.append(cleaned)

    args = ["restore", "--staged", "--"] + validated_rel_paths
    return True, args, ""
