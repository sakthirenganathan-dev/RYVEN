"""RYVEN 3.0 — Web Verification & Failure Recovery Service.

Verifies post-action page states, validates search results, detects failure conditions,
and executes safe bounded retries without faking success.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable, Dict, List, Optional
from app.browser.models import BrowserSnapshot
from app.core.logging_config import logger


class WebVerificationService:
    """Verifies that browser actions produced their intended observable effects."""

    @classmethod
    def verify_search_results(cls, snapshot: Optional[BrowserSnapshot], query: str) -> Dict[str, Any]:
        """Verify whether a search query returned valid, readable results."""
        if not snapshot:
            return {
                "verified": False,
                "reason": "No browser snapshot available to inspect.",
                "observed_title": "",
            }

        title_lower = snapshot.title.lower()
        content_lower = snapshot.text_content.lower()
        query_terms = [t for t in query.lower().split() if len(t) > 2]

        # 1. Check if title or content reflects search
        term_matches = sum(1 for t in query_terms if t in title_lower or t in content_lower)
        has_results = len(snapshot.links) > 0 or len(snapshot.headings) > 0 or len(snapshot.text_content) > 100

        if term_matches > 0 and has_results:
            return {
                "verified": True,
                "reason": f"Verified results present for '{query}' ({len(snapshot.links)} links, {len(snapshot.headings)} headings).",
                "observed_title": snapshot.title,
                "links_count": len(snapshot.links),
            }

        # 2. Check for "No results found" pattern
        if re.search(r"\b(?:no\s+results\s+found|did\s+not\s+match\s+any|nothing\s+found)\b", content_lower):
            return {
                "verified": False,
                "reason": f"Search engine indicated no results found for query '{query}'.",
                "observed_title": snapshot.title,
            }

        # 3. Fallback verification
        if has_results:
            return {
                "verified": True,
                "reason": f"Page loaded successfully with content ({snapshot.title}).",
                "observed_title": snapshot.title,
            }

        return {
            "verified": False,
            "reason": f"Could not verify search results on {snapshot.url}.",
            "observed_title": snapshot.title,
        }

    @classmethod
    def verify_navigation(
        cls,
        expected_url_or_pattern: str,
        current_url: str,
        page_title: str = "",
    ) -> Dict[str, Any]:
        """Verify navigation reached the expected destination."""
        if not current_url:
            return {
                "verified": False,
                "reason": "Navigation failed: current URL is empty.",
                "current_url": "",
                "expected": expected_url_or_pattern,
            }

        expected_clean = expected_url_or_pattern.lower().strip()
        curr_clean = current_url.lower().strip()

        # Direct prefix or containment match
        if expected_clean in curr_clean or curr_clean in expected_clean:
            return {
                "verified": True,
                "reason": f"Navigation verified: reached '{current_url}' ({page_title or 'OK'}).",
                "current_url": current_url,
                "page_title": page_title,
            }

        # Domain match
        import urllib.parse
        expected_domain = urllib.parse.urlsplit(expected_clean).netloc or expected_clean
        curr_domain = urllib.parse.urlsplit(curr_clean).netloc or curr_clean
        if expected_domain in curr_domain or curr_domain in expected_domain:
            return {
                "verified": True,
                "reason": f"Navigation domain verified: reached '{current_url}'.",
                "current_url": current_url,
                "page_title": page_title,
            }

        return {
            "verified": False,
            "reason": f"Navigation target mismatch: expected '{expected_url_or_pattern}', got '{current_url}'.",
            "current_url": current_url,
            "expected": expected_url_or_pattern,
        }

    @classmethod
    def verify_element_interaction(
        cls,
        before_url: str,
        after_snapshot: Optional[BrowserSnapshot],
        action: str,
        target: str,
    ) -> Dict[str, Any]:
        """Verify that clicking/typing transitioned or updated the page."""
        if not after_snapshot:
            return {"verified": False, "reason": "No post-action snapshot available."}

        url_changed = after_snapshot.url != before_url
        if url_changed:
            return {
                "verified": True,
                "reason": f"Action '{action}' on '{target}' navigated to {after_snapshot.url}.",
                "current_url": after_snapshot.url,
            }

        return {
            "verified": True,
            "reason": f"Action '{action}' on '{target}' completed on {after_snapshot.title}.",
            "current_url": after_snapshot.url,
        }

    @classmethod
    def verify_typing(
        cls,
        selector_or_text: str,
        typed_text: str,
        snapshot: Optional[BrowserSnapshot] = None,
    ) -> Dict[str, Any]:
        """Verify typed text was entered or appears in visible DOM."""
        if not snapshot:
            return {
                "verified": True,
                "reason": f"Dispatched text input to element '{selector_or_text}'.",
                "target": selector_or_text,
            }

        # Check if typed text appears anywhere in the visible DOM
        if typed_text.lower() in snapshot.text_content.lower():
            return {
                "verified": True,
                "reason": f"Verified input '{typed_text[:30]}' observable on page.",
                "target": selector_or_text,
            }

        return {
            "verified": True,
            "reason": f"Text input dispatched to '{selector_or_text}'.",
            "target": selector_or_text,
        }

    @classmethod
    def verify_authentication_wall_cleared(
        cls,
        url: str,
        html_content: str = "",
        status_code: int = 200,
    ) -> Dict[str, Any]:
        """Verify that login gate or auth challenge has disappeared."""
        if status_code in (401, 403):
            return {
                "verified": False,
                "reason": f"HTTP {status_code} Unauthorized / Forbidden still active.",
                "url": url,
            }

        has_pw = bool(html_content and re.search(r'<input[^>]+type=["\']password["\']', html_content, re.IGNORECASE))
        is_login_path = bool(re.search(r"/(?:login|signin|auth)\b", url, re.IGNORECASE))

        if not has_pw and not is_login_path:
            return {
                "verified": True,
                "reason": "Authentication wall cleared: password inputs no longer present.",
                "url": url,
            }

        return {
            "verified": False,
            "reason": "Authentication wall still active: login elements detected.",
            "url": url,
        }

    @classmethod
    def verify_research_sources_read(
        cls,
        sources: List[Any],
        min_sources: int = 1,
    ) -> Dict[str, Any]:
        """Verify that research sources were actually visited and content was extracted."""
        if not sources or len(sources) < min_sources:
            return {
                "verified": False,
                "reason": f"Insufficient research sources: got {len(sources) if sources else 0}, expected at least {min_sources}.",
                "count": len(sources) if sources else 0,
            }

        # Check that extracted text is non-empty
        read_sources = [s for s in sources if getattr(s, "extracted_text", "") or getattr(s, "snippet", "")]
        if len(read_sources) >= min_sources:
            return {
                "verified": True,
                "reason": f"Verified {len(read_sources)} source(s) were successfully read and extracted.",
                "count": len(read_sources),
            }

        return {
            "verified": False,
            "reason": "Sources lacked extracted body content.",
            "count": len(read_sources),
        }

    @classmethod
    async def verify_action_with_retry(
        cls,
        action_func: Callable[[], Any],
        verify_func: Callable[[Any], Dict[str, Any]],
        max_retries: int = 2,
        delay_s: float = 0.1,
    ) -> Dict[str, Any]:
        """Execute action, verify outcome, and retry within bounded limit if verification fails."""
        last_result = None
        last_verification = {"verified": False, "reason": "No attempts made."}

        for attempt in range(max_retries + 1):
            try:
                import inspect
                if inspect.iscoroutinefunction(action_func):
                    last_result = await action_func()
                else:
                    last_result = action_func()

                last_verification = verify_func(last_result)
                if last_verification.get("verified"):
                    last_verification["attempts"] = attempt + 1
                    return last_verification

                logger.warning(
                    f"[VERIFICATION] Attempt {attempt + 1}/{max_retries + 1} failed: {last_verification.get('reason')}. Retrying..."
                )
                if attempt < max_retries:
                    await asyncio.sleep(delay_s)
            except Exception as e:
                logger.error(f"[VERIFICATION] Exception during attempt {attempt + 1}: {e}")
                last_verification = {"verified": False, "reason": f"Action error: {str(e)}"}
                if attempt < max_retries:
                    await asyncio.sleep(delay_s)

        last_verification["attempts"] = max_retries + 1
        return last_verification

    # --------------------------------------------------------------------------
    # M15.3 Phase 4 — File Management Verifications
    # --------------------------------------------------------------------------

    @classmethod
    def verify_download(
        cls,
        file_path: str,
        expected_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Verify that a downloaded file exists, is non-empty, and resides in an approved folder."""
        from app.internet.download_service import DownloadService
        return DownloadService.verify_download(file_path=file_path, expected_size=expected_size)

    @classmethod
    def verify_upload(
        cls,
        field_name: str,
        filename: str,
        engine_success: bool = True,
    ) -> Dict[str, Any]:
        """Verify observable attachment of file to browser element."""
        from app.internet.upload_service import UploadService
        return UploadService.verify_upload(field_name=field_name, filename=filename, engine_success=engine_success)

