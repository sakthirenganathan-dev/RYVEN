"""Security validator and sensitive file exclusion for RYVEN Knowledge Graph (M11.5)."""

import os
import re
from pathlib import Path
from typing import List, Optional, Set
from app.core.logging_config import logger


class KnowledgeGraphSecurity:
    """Security rules, path containment, and sensitive file filtering."""

    # Disallowed directories that should NEVER be traversed or indexed
    EXCLUDED_DIRS: Set[str] = {
        ".git",
        ".github",
        ".vscode",
        ".idea",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        "out",
        "coverage",
        ".ryven",
        "graphify-out",
    }

    # Sensitive filenames or patterns that must NEVER be indexed or read
    SENSITIVE_FILE_PATTERNS: List[re.Pattern] = [
        re.compile(r"^\.env.*", re.IGNORECASE),
        re.compile(r".*\.pem$", re.IGNORECASE),
        re.compile(r".*\.key$", re.IGNORECASE),
        re.compile(r".*\.pfx$", re.IGNORECASE),
        re.compile(r".*\.p12$", re.IGNORECASE),
        re.compile(r".*id_rsa.*", re.IGNORECASE),
        re.compile(r".*id_ed25519.*", re.IGNORECASE),
        re.compile(r".*credentials.*\.json$", re.IGNORECASE),
        re.compile(r".*secret.*", re.IGNORECASE),
        re.compile(r".*token.*\.json$", re.IGNORECASE),
        re.compile(r".*\.kdbx$", re.IGNORECASE),
    ]

    # Blocked paths and traversal tokens
    BLOCKED_TRAVERSAL_PATTERNS: List[re.Pattern] = [
        re.compile(r"\.\.[\\/]"),
        re.compile(r"^[\\/]{2}"),  # UNC path
        re.compile(r"^~[\\/]"),    # Home directory expansion
    ]

    # Command injection or shell characters to reject in query or symbol names
    SHELL_INJECTION_PATTERNS: List[re.Pattern] = [
        re.compile(r"[;&|`$]"),
        re.compile(r"\b(?:cmd|powershell|bash|sh|exec|eval|system)\b", re.IGNORECASE),
    ]

    @classmethod
    def validate_project_root(cls, project_path: str) -> str:
        """Validate and resolve project root path safely.
        
        Raises ValueError if path is invalid, traverses outside, or doesn't exist.
        """
        if not project_path or not isinstance(project_path, str):
            raise ValueError("Project path must be a non-empty string.")

        project_str = project_path.strip()

        # Reject path traversal patterns
        for pattern in cls.BLOCKED_TRAVERSAL_PATTERNS:
            if pattern.search(project_str):
                raise ValueError(f"Security violation: path traversal detected in '{project_str}'")

        resolved = Path(project_str).resolve()
        if not resolved.exists():
            raise ValueError(f"Project directory does not exist: '{resolved}'")
        if not resolved.is_dir():
            raise ValueError(f"Project path is not a directory: '{resolved}'")

        # Disallow root drive indexing (e.g. C:\ or /)
        if len(resolved.parts) <= 1:
            raise ValueError(f"Security violation: indexing root drive is strictly prohibited: '{resolved}'")

        return str(resolved)

    @classmethod
    def is_safe_relative_path(cls, base_dir: Path, target_path: Path) -> bool:
        """Confirm that target_path is strictly contained inside base_dir."""
        try:
            target_resolved = target_path.resolve()
            base_resolved = base_dir.resolve()
            # Must start with base_resolved
            target_resolved.relative_to(base_resolved)
            return True
        except (ValueError, RuntimeError):
            return False

    @classmethod
    def is_sensitive_file(cls, filename: str) -> bool:
        """Check if a filename matches sensitive patterns (e.g. .env, private keys)."""
        name = Path(filename).name
        for pattern in cls.SENSITIVE_FILE_PATTERNS:
            if pattern.search(name):
                return True
        return False

    @classmethod
    def is_excluded_dir(cls, dirname: str) -> bool:
        """Check if directory name is in the excluded set."""
        name = Path(dirname).name.lower()
        return name in {d.lower() for d in cls.EXCLUDED_DIRS}

    @classmethod
    def validate_safe_input_text(cls, text: str, max_length: int = 500) -> str:
        """Sanitize and validate user-supplied symbol or query string."""
        if not text or not isinstance(text, str):
            raise ValueError("Input string must be non-empty.")
        cleaned = text.strip()
        if len(cleaned) > max_length:
            raise ValueError(f"Input exceeds maximum allowed length of {max_length} characters.")

        # Check for command injection attempts
        for pattern in cls.SHELL_INJECTION_PATTERNS:
            if pattern.search(cleaned):
                raise ValueError(f"Security violation: suspicious shell or execution pattern in input: '{cleaned}'")

        return cleaned
