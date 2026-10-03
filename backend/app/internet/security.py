"""RYVEN 3.0 — Internet Security Policy.

Enforces deep SSRF protection, protocol allowlisting, file download/upload restrictions,
credential masking, and human confirmation boundaries for all internet agent operations.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from app.browser.security import BrowserSecurityValidator
from app.core.logging_config import logger
from app.health.health_security import HealthSecurityValidator


class InternetSecurityPolicy:
    """Central security policy enforcement for the Unified Internet Agent."""

    ALLOWED_SCHEMES = {"http", "https"}

    DISALLOWED_SCHEMES_REGEX = re.compile(
        r"^(?:file|javascript|data|vbscript|ftp|chrome|about|view-source|blob):",
        re.IGNORECASE,
    )

    DISALLOWED_CONTROL_CHARS = re.compile(r"[;&|`$<>\x00\r\n]")

    # Executable or dangerous download extensions requiring mandatory confirmation
    DANGEROUS_DOWNLOAD_EXTENSIONS = {
        ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".sh", ".msi", ".dll", ".scr", ".reg"
    }

    # High-risk web actions requiring mandatory human confirmation
    HIGH_RISK_ACTIONS = {
        "submit_form",
        "post_message",
        "delete_resource",
        "checkout",
        "purchase",
        "payment",
        "create_account",
        "transfer_funds",
        "upload_file",
        "change_password",
    }

    @classmethod
    def validate_target_url(cls, target: str, allow_search: bool = True) -> Tuple[bool, str, str]:
        """Validate URL for protocol safety, injection characters, and SSRF avoidance."""
        if not target or not isinstance(target, str):
            return False, "", "Navigation target cannot be empty."

        clean = target.strip()

        # 1. Scheme checks
        if cls.DISALLOWED_SCHEMES_REGEX.search(clean):
            return False, "", f"Security policy rejects disallowed protocol in '{clean}'."

        # 2. Control character checks
        if cls.DISALLOWED_CONTROL_CHARS.search(clean):
            return False, "", "Security policy rejects illegal control characters in URL."

        # 3. Handle natural search query input
        if allow_search and not clean.startswith(("http://", "https://")) and (" " in clean or "." not in clean):
            encoded = urllib.parse.quote_plus(clean)
            return True, f"https://www.google.com/search?q={encoded}", ""

        candidate = clean
        if not candidate.startswith(("http://", "https://")):
            candidate = f"https://{candidate}"

        # 4. Canonical SSRF validation using HealthSecurityValidator
        is_safe, sanitized, err = HealthSecurityValidator.validate_url(candidate, resolve_dns=True)
        if not is_safe:
            return False, "", f"SSRF Protection: {err}"

        return True, sanitized, ""

    @classmethod
    def is_confirmation_required(
        cls,
        action_name: str,
        target_description: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Evaluate if an action crosses a security or external side-effect boundary."""
        action_lower = (action_name or "").lower().strip()

        # Explicit action check
        if action_lower in cls.HIGH_RISK_ACTIONS:
            return True

        # Check browser security validator keywords
        if BrowserSecurityValidator.is_confirmation_required(
            action_name=action_name,
            element_text=target_description,
            details=details,
        ):
            return True

        # Check for destructive keywords in description
        destructive_keywords = {"delete", "remove", "pay", "buy", "purchase", "order", "transfer", "subscribe", "checkout"}
        desc_lower = target_description.lower()
        if any(kw in desc_lower for kw in destructive_keywords):
            return True

        return False

    @classmethod
    def validate_download_file(cls, filename: str, destination_dir: str) -> Tuple[bool, str]:
        """Validate file download destination and safety profile."""
        clean_name = os.path.basename(filename.strip())
        _, ext = os.path.splitext(clean_name.lower())

        if ext in cls.DANGEROUS_DOWNLOAD_EXTENSIONS:
            return False, f"Direct download of executable extension '{ext}' is prohibited without explicit authorization."

        # Enforce destination containment (no path traversal)
        real_dest = os.path.realpath(destination_dir)
        target_path = os.path.realpath(os.path.join(real_dest, clean_name))
        if not (target_path == real_dest or target_path.startswith(real_dest + os.sep)):
            return False, "Destination path traversal detected."

        return True, target_path

    @classmethod
    def redact_secrets(cls, text_or_data: Any) -> Any:
        """Recursively redact credentials, auth tokens, and session keys."""
        return BrowserSecurityValidator.redact_credentials(text_or_data)
