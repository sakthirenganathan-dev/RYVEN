"""RYVEN DEV ENGINE — M11: Git Security and Sensitive Information Protection.

Protects against:
- Leaking secrets, credentials, tokens, and private keys.
- Force pushes and destructive Git commands.
- Command injections and shell metacharacters in Git arguments.
- Credential disclosure in URLs, outputs, or error logs.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.logging_config import logger


# ─────────────────────────────────────────────────────────────────────────────
# Prohibited and Sensitive Patterns
# ─────────────────────────────────────────────────────────────────────────────

# File name patterns that must never be staged, committed, or pushed
SENSITIVE_FILENAME_PATTERNS: List[re.Pattern] = [
    re.compile(r"^\.env(?:\..+)?$", re.IGNORECASE),
    re.compile(r"^id_(?:rsa|ed25519|dsa|ecdsa)(?:\.pub)?$", re.IGNORECASE),
    re.compile(r".*\.(?:pem|key|p12|pfx|pkcs12|kdbx|keystore)$", re.IGNORECASE),
    re.compile(r".*(?:credential|secret|token|private_key).*", re.IGNORECASE),
    re.compile(r".*service[-_]?account.*\.json$", re.IGNORECASE),
    re.compile(r"^\.aws[/\\].*", re.IGNORECASE),
    re.compile(r"^\.ssh[/\\].*", re.IGNORECASE),
    re.compile(r"^\.ryven[/\\].*", re.IGNORECASE),
]


# In-content sensitive patterns (scanned before staging/committing text files)
IN_CONTENT_SECRET_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("Private Key Header", re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC|DSA|PGP)? ?PRIVATE KEY-----", re.IGNORECASE)),
    ("AWS Access Key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub Personal Access Token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}\b")),
    ("GitHub Fine-Grained Token", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{82}\b")),
    ("Generic Bearer Secret", re.compile(r"""(?:api[_-]?key|secret[_-]?key|auth[_-]?token)\s*[:=]\s*['"][A-Za-z0-9_\-\.]{16,}['"]""", re.IGNORECASE)),
    ("Slack API Token", re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{10,}\b")),
]

# Destructive commands that are permanently blocked in M11
DESTRUCTIVE_GIT_PATTERNS: List[Tuple[str, re.Pattern]] = [
    ("Force Push", re.compile(r"^(?:--force|-f|--force-with-lease|--force-if-includes)$", re.IGNORECASE)),
    ("Remote Branch Deletion", re.compile(r"^(?:--delete|:[a-zA-Z0-9_\-\./]+)$", re.IGNORECASE)),
    ("Hard Reset", re.compile(r"^--hard$", re.IGNORECASE)),
    ("Destructive Clean", re.compile(r"^-[a-z]*[fx][a-z]*$", re.IGNORECASE)),
    ("Force Branch Delete", re.compile(r"^-[a-z]*D[a-z]*$")),
]

# Shell metacharacters forbidden anywhere in Git arguments
SHELL_INJECTION_RE = re.compile(r"[;&|`$\\(\\)\<\>\n\r\x00]")

# URL Credential scrubbing pattern
URL_CREDENTIAL_RE = re.compile(r"(https?://)(?:[^:@\s]+(?::[^@\s]*)?@)", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# Functions
# ─────────────────────────────────────────────────────────────────────────────

def is_sensitive_filename(filename: str) -> bool:
    """Return True if filename matches any protected sensitive file pattern."""
    basename = os.path.basename(filename)
    norm = filename.replace("\\", "/")
    for pat in SENSITIVE_FILENAME_PATTERNS:
        if pat.search(basename) or pat.search(norm):
            return True
    return False


def scan_file_for_secrets(file_path: str) -> Optional[str]:
    """Scan a local file for secret patterns without leaking secret contents.
    
    Returns description of detected pattern if blocked, or None.
    """
    if not os.path.isfile(file_path):
        return None

    # Skip files over 2MB to prevent DoS
    try:
        if os.path.getsize(file_path) > 2 * 1024 * 1024:
            return None
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(500000)
            for name, pattern in IN_CONTENT_SECRET_PATTERNS:
                if pattern.search(content):
                    return f"Detected potential {name}"
    except Exception as exc:
        logger.warning(f"Failed to scan file content '{file_path}': {exc}")
    return None


def scan_sensitive_files(
    project_path: str,
    files_to_check: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Inspect files intended for staging, commit, or push.
    
    Security: NEVER dumps secret values.
    Returns:
        {
            "blocked": bool,
            "reason": str,
            "files": List[str]
        }
    """
    flagged_files: List[str] = []

    targets = files_to_check or []
    if not targets and os.path.isdir(project_path):
        # Scan root files and common config dirs if no explicit list
        try:
            for root, _, filenames in os.walk(project_path):
                # Don't scan .git directory
                if ".git" in root.split(os.sep):
                    continue
                for fname in filenames:
                    rel = os.path.relpath(os.path.join(root, fname), project_path)
                    if is_sensitive_filename(rel):
                        flagged_files.append(rel)
                if len(flagged_files) > 50:
                    break
        except Exception as exc:
            logger.error(f"Error scanning directory for sensitive files: {exc}")

    for rel_path in targets:
        if is_sensitive_filename(rel_path):
            flagged_files.append(rel_path)
            continue
        # Check file content
        abs_p = os.path.join(project_path, rel_path)
        secret_type = scan_file_for_secrets(abs_p)
        if secret_type:
            flagged_files.append(rel_path)

    # Deduplicate
    unique_flagged = list(dict.fromkeys(flagged_files))

    if unique_flagged:
        logger.warning(f"[GIT SECURITY] Blocked sensitive file(s): {unique_flagged}")
        return {
            "blocked": True,
            "reason": "Sensitive files or potential credentials detected in fileset.",
            "files": unique_flagged,
        }

    return {
        "blocked": False,
        "reason": "",
        "files": [],
    }


def redact_credentials_from_url(url: str) -> str:
    """Redact embedded username, token, or password from a Git remote URL."""
    if not url:
        return ""
    return URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", url)


def sanitize_git_output(text: str) -> str:
    """Sanitize any stdout/stderr output to remove URLs with credentials and private keys."""
    if not text:
        return ""
    # 1. Redact URLs
    cleaned = URL_CREDENTIAL_RE.sub(r"\1[REDACTED]@", text)

    # 2. Redact private key snippets
    cleaned = re.sub(
        r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+ PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY BLOCK]",
        cleaned,
        flags=re.IGNORECASE,
    )

    # 3. Redact common secret assignments
    cleaned = re.sub(
        r"""((?:token|secret|password|api[_-]?key)\s*[:=]\s*['"]?)[A-Za-z0-9_\-\.]{12,}(['"]?)""",
        r"\1[REDACTED]\2",
        cleaned,
        flags=re.IGNORECASE,
    )

    return cleaned


def is_force_push_argument(arg: str) -> bool:
    """Return True if argument is an explicit force push flag."""
    cleaned = arg.strip().lower()
    return cleaned in ("--force", "-f", "--force-with-lease", "--force-if-includes")


def check_destructive_command(subcommand: str, args: List[str]) -> Tuple[bool, str]:
    """Check if a Git subcommand and argument list contains destructive actions."""
    sub = subcommand.lower().strip()

    # Blocked subcommands entirely
    if sub in ("clean", "reset", "rebase", "filter-branch"):
        return True, f"Git subcommand '{sub}' is prohibited by safety policy."

    for arg in args:
        # Check force push
        if is_force_push_argument(arg):
            return True, "Force push operations (--force, -f, --force-with-lease) are strictly prohibited."

        # Check refspec deletion
        if arg.startswith(":") and len(arg) > 1:
            return True, f"Remote ref deletion syntax '{arg}' is strictly prohibited."

        # Check destructive flags
        for desc, pat in DESTRUCTIVE_GIT_PATTERNS:
            if pat.search(arg):
                return True, f"Prohibited destructive Git operation detected: {desc} ({arg})"

        # Check shell injection
        if SHELL_INJECTION_RE.search(arg):
            return True, f"Shell metacharacters detected in argument: {arg!r}"

    return False, ""


is_destructive_git = check_destructive_command
