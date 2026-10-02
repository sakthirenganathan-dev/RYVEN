"""Security, sanitization, path containment, and secret protection for the Deployment Engine."""

from __future__ import annotations

import os
import re
from typing import List, Optional


# Patterns of secrets that must be redacted from all outputs, logs, and errors
DEPLOYMENT_SECRET_PATTERNS = [
    # Vercel tokens
    (re.compile(r"(?i)(?:vercel[_-]?(?:token|secret|key))\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{8,})['\"]?"), "VERCEL_TOKEN=[REDACTED]"),
    (re.compile(r"\bvercel_[a-zA-Z0-9_\-]{16,}\b"), "[REDACTED_VERCEL_TOKEN]"),
    # Railway tokens
    (re.compile(r"(?i)(?:railway[_-]?(?:token|secret|key|api[_-]?token))\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{8,})['\"]?"), "RAILWAY_TOKEN=[REDACTED]"),
    # Render API keys
    (re.compile(r"(?i)(?:render[_-]?(?:api[_-]?key|secret|token))\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{8,})['\"]?"), "RENDER_API_KEY=[REDACTED]"),
    (re.compile(r"\brnd_[a-zA-Z0-9_\-]{16,}\b"), "[REDACTED_RENDER_KEY]"),
    # GitHub tokens
    (re.compile(r"\bghp_[a-zA-Z0-9]{36,}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bgithub_pat_[a-zA-Z0-9_]{30,}\b"), "[REDACTED_GH_PAT]"),
    # Generic bearer and API tokens
    (re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{16,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-]{8,})['\"]?"), "API_KEY=[REDACTED]"),

    # AWS keys
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    # Private keys
    (re.compile(r"-----BEGIN (?:RSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA )?PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
]

# Sensitive file basenames that should never be uploaded or exposed
SENSITIVE_FILE_PATTERNS = [
    re.compile(r"^\.env(?:\..+)?$", re.IGNORECASE),
    re.compile(r".*id_rsa.*", re.IGNORECASE),
    re.compile(r".*id_ed25519.*", re.IGNORECASE),
    re.compile(r".*\.pem$", re.IGNORECASE),
    re.compile(r".*\.key$", re.IGNORECASE),
    re.compile(r".*service[-_]account.*\.json$", re.IGNORECASE),
    re.compile(r".*credentials?\.json$", re.IGNORECASE),
]


def sanitize_deployment_output(text: Optional[str]) -> str:
    """Sanitize deployment logs, error messages, and output streams."""
    if not text:
        return ""
    sanitized = text
    for pattern, replacement in DEPLOYMENT_SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def scan_deployment_sensitive_files(project_dir: str) -> List[str]:
    """Scan a project directory for sensitive credential files that should not be deployed."""
    if not os.path.isdir(project_dir):
        return []

    sensitive: List[str] = []
    # Only scan top 3 levels to avoid deep node_modules or venv
    for root, dirs, files in os.walk(project_dir):
        # Skip node_modules, .git, venv, __pycache__
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build")]
        rel_root = os.path.relpath(root, project_dir)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        if depth > 3:
            continue

        for filename in files:
            for pattern in SENSITIVE_FILE_PATTERNS:
                if pattern.match(filename):
                    rel_path = os.path.relpath(os.path.join(root, filename), project_dir).replace("\\", "/")
                    sensitive.append(rel_path)
                    break

    return sorted(sensitive)


def is_safe_deployment_path(project_path: str) -> bool:
    """Validate that the project path is strictly contained within approved projects root."""
    if not project_path or not isinstance(project_path, str):
        return False

    # Block path traversal sequences
    if ".." in project_path or project_path.startswith("//") or project_path.startswith("\\\\"):
        return False

    # Reject system drive roots
    clean_p = project_path.strip().lower()
    if clean_p in ("c:", "c:\\", "c:/", "d:", "d:\\", "d:/", "e:", "e:\\", "e:/"):
        return False

    from app.tools.project_tool import get_projects_root
    projects_root = os.path.abspath(get_projects_root()).lower()
    abs_path = os.path.abspath(project_path).lower()

    return abs_path.startswith(projects_root) and abs_path != projects_root



def extract_required_env_vars(project_dir: str) -> List[str]:
    """Inspect project files (like .env.example) to extract variable names only.
    
    Never reads or exposes secret values.
    """
    if not os.path.isdir(project_dir):
        return []

    found_vars: set[str] = set()

    # 1. Check .env.example or example.env
    for candidate in [".env.example", "example.env", ".env.sample", ".env.template"]:
        p = os.path.join(project_dir, candidate)
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            var_name = line.split("=", 1)[0].strip()
                            if var_name and re.match(r"^[A-Z0-9_]+$", var_name):
                                found_vars.add(var_name)
            except Exception:
                pass

    return sorted(list(found_vars))
