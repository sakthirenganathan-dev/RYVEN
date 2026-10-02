"""Security and URL validator for M14.3 Controlled Browser Engine.

Enforces SSRF prevention, protocol allowlisting, search query sanitization,
credential masking, and human-in-the-loop confirmation boundaries.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any, Dict, Optional, Tuple
from app.core.logging_config import logger
from app.health.health_security import HealthSecurityValidator


class BrowserSecurityValidator:
    """Security validator for controlled browser automation."""

    ALLOWED_SCHEMES = {"http", "https"}

    DISALLOWED_SCHEMES_REGEX = re.compile(
        r"^(?:file|javascript|data|vbscript|ftp|chrome|about|view-source|blob):",
        re.IGNORECASE,
    )

    SENSITIVE_FIELD_NAMES = {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "card_number",
        "cvv",
        "ssn",
        "auth",
        "cookie",
    }

    CONFIRMATION_REQUIRED_ACTIONS = {
        "submit_form",
        "download_file",
        "upload_file",
        "delete_content",
        "checkout",
        "pay",
        "transfer",
        "create_account",
        "send_message",
    }

    CONFIRMATION_ELEMENT_TAGS = {"form", "button", "input"}
    CONFIRMATION_BUTTON_KEYWORDS = {
        "submit",
        "delete",
        "remove",
        "pay",
        "buy",
        "purchase",
        "confirm",
        "order",
        "checkout",
        "send",
        "transfer",
        "subscribe",
    }

    @classmethod
    def validate_url(cls, target: str, allow_search: bool = True) -> Tuple[bool, str, str]:
        """Validate and sanitize a navigation target URL.
        
        Args:
            target: Destination URL or search query.
            allow_search: If True and target is not a URL, converts it to safe search URL.
            
        Returns:
            (is_safe: bool, validated_url: str, reason: str)
        """
        if not target or not isinstance(target, str):
            return False, "", "URL or target cannot be empty."

        clean = target.strip()

        # 1. Check for explicitly prohibited schemes
        if cls.DISALLOWED_SCHEMES_REGEX.search(clean):
            return False, "", f"Security policy rejects prohibited protocol scheme in '{clean}'."

        # 2. Check for shell or injection control characters
        if re.search(r"[;&|`$<>\x00\r\n]", clean):
            return False, "", "Security policy rejects illegal control characters in navigation target."

        # 3. Detect plain search queries if requested
        if allow_search and not clean.startswith(("http://", "https://")) and (" " in clean or "." not in clean):
            encoded_query = urllib.parse.quote_plus(clean)
            safe_search_url = f"https://www.google.com/search?q={encoded_query}"
            return True, safe_search_url, ""

        # 4. Normalise domain without scheme
        candidate = clean
        if not candidate.startswith(("http://", "https://")):
            candidate = f"https://{candidate}"

        # 5. Reuse HealthSecurityValidator for deep SSRF & IP checking
        is_safe, sanitized, err = HealthSecurityValidator.validate_url(candidate, resolve_dns=True)
        if not is_safe:
            return False, "", f"SSRF/Security block: {err}"

        return True, sanitized, ""

    @classmethod
    def is_confirmation_required(
        cls,
        action_name: str,
        element_text: str = "",
        element_type: str = "",
        element_tag: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Determine whether a browser operation requires human confirmation."""
        clean_action = (action_name or "").lower().strip()
        if clean_action in cls.CONFIRMATION_REQUIRED_ACTIONS:
            return True

        if details and details.get("requires_confirmation"):
            return True

        # Check button text for destructive / impactful keywords
        combined = f"{element_text} {element_type}".lower()
        for kw in cls.CONFIRMATION_BUTTON_KEYWORDS:
            if kw in combined:
                return True

        if element_type.lower() == "submit" or element_tag.lower() == "form":
            return True

        return False

    @classmethod
    def redact_credentials(cls, data: Any) -> Any:
        """Recursively redact sensitive credential keys from payloads and logs."""
        if isinstance(data, dict):
            redacted = {}
            for k, v in data.items():
                if any(sens in k.lower() for sens in cls.SENSITIVE_FIELD_NAMES):
                    redacted[k] = "[REDACTED_CREDENTIAL]"
                else:
                    redacted[k] = cls.redact_credentials(v)
            return redacted
        elif isinstance(data, list):
            return [cls.redact_credentials(item) for item in data]
        elif isinstance(data, str):
            # Check for token or secret assignment patterns
            if re.search(r"(?:bearer\s+[a-z0-9_\-\.]{20,}|ghp_[a-z0-9]{30,}|sk-[a-z0-9]{30,})", data, re.IGNORECASE):
                return "[REDACTED_TOKEN]"
            return data
        return data
