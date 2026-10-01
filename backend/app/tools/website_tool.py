"""OpenWebsiteTool for launching validated web destinations in the default browser."""

import re
import urllib.parse
import webbrowser
from typing import Any, Dict, Optional
from app.core.logging_config import logger
from app.tools.base import BaseTool


class OpenWebsiteTool(BaseTool):
    """Safely opens validated websites in the default web browser."""

    name = "open_website"
    description = (
        "Opens an approved or validated web URL in the system default web browser. "
        "Strictly enforces http/https schemes and rejects malicious protocols."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Destination website URL or approved website name (e.g. 'github', 'youtube')",
            }
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    # Known safe shortcuts
    KNOWN_SITES: Dict[str, str] = {
        "github": "https://github.com",
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "reddit": "https://www.reddit.com",
        "stackoverflow": "https://stackoverflow.com",
        "stack overflow": "https://stackoverflow.com",
        "portfolio": "https://github.com",
        "chatgpt": "https://chatgpt.com",
        "wikipedia": "https://www.wikipedia.org",
    }

    def _resolve_and_validate_url(self, raw_input: str) -> Optional[str]:
        """Validate and resolve target input into a safe web URL."""
        clean = raw_input.strip()
        lower = clean.lower()

        # Check known shortcuts first
        if lower in self.KNOWN_SITES:
            return self.KNOWN_SITES[lower]
        for key, target in self.KNOWN_SITES.items():
            if lower == f"open {key}" or lower == key:
                return target

        # Reject dangerous schemes and injection patterns
        if re.search(r"^(?:file|javascript|data|vbscript):", clean, re.IGNORECASE):
            logger.warning(f"Unsafe URL scheme rejected: {clean}")
            return None

        # Reject shell metacharacters
        if re.search(r"[;&|`$<>\x00\r\n]", clean):
            logger.warning(f"Illegal characters in URL rejected: {clean}")
            return None

        # Prepend https if user provided domain like "github.com" or "example.org"
        candidate = clean
        if not candidate.startswith(("http://", "https://")):
            candidate = f"https://{candidate}"

        try:
            parsed = urllib.parse.urlparse(candidate)
            if parsed.scheme not in ("http", "https"):
                return None
            if not parsed.netloc:
                return None
            return candidate
        except Exception as e:
            logger.error(f"URL parsing exception for {clean}: {e}")
            return None

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Execute the browser navigation safely."""
        raw_url = kwargs.get("url", "").strip()
        if not raw_url:
            return {
                "success": False,
                "tool": self.name,
                "message": "No destination URL or website name provided.",
                "url": None,
            }

        resolved_url = self._resolve_and_validate_url(raw_url)
        if not resolved_url:
            return {
                "success": False,
                "tool": self.name,
                "message": f"Security restriction: '{raw_url}' is not a valid or safe http/https URL.",
                "url": raw_url,
            }

        try:
            logger.info(f"Opening validated website: {resolved_url}")
            opened = webbrowser.open(resolved_url)
            if opened:
                return {
                    "success": True,
                    "tool": self.name,
                    "url": resolved_url,
                    "message": f"Opening {resolved_url} in your default browser.",
                }
            else:
                return {
                    "success": False,
                    "tool": self.name,
                    "url": resolved_url,
                    "message": f"Could not launch browser for {resolved_url}.",
                }
        except Exception as exc:
            logger.error(f"Error opening website {resolved_url}: {exc}")
            return {
                "success": False,
                "tool": self.name,
                "url": resolved_url,
                "message": f"Failed to open website: {str(exc)}",
            }
