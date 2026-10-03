"""RYVEN 3.0 — Authentication Session Manager.

Detects authentication walls (login pages, password forms, OAuth redirects), pauses
task execution safely, prompts the user via HUD, and resumes without ever touching credentials.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Optional, Tuple
from app.core.logging_config import logger
from app.internet.models import AuthSession, AuthStatus


class AuthenticationSessionManager:
    """Manages human authentication pause and resume workflows."""

    # Common authentication URL indicators
    AUTH_URL_PATTERNS = [
        re.compile(r"/(?:login|signin|sign-in|auth|oauth|session|authenticate)\b", re.IGNORECASE),
        re.compile(r"^(?:accounts\.google\.com|github\.com/login|gitlab\.com/users/sign_in)", re.IGNORECASE),
    ]

    def __init__(self) -> None:
        self._pending_auth_sessions: Dict[str, AuthSession] = {}

    def is_login_required(self, url: str, html_content: str, status_code: int = 200) -> Tuple[bool, str]:
        """Detect whether a page requires user authentication."""
        # 1. HTTP 401 / 403
        if status_code in (401, 403):
            return True, f"HTTP {status_code} Unauthorized / Forbidden"

        # 2. URL patterns
        for pattern in self.AUTH_URL_PATTERNS:
            if pattern.search(url):
                service_name = self._identify_service(url)
                return True, f"{service_name} login page detected"

        # 3. Password input field presence in DOM
        if html_content and re.search(r'<input[^>]+type=["\']password["\']', html_content, re.IGNORECASE):
            service_name = self._identify_service(url)
            return True, f"Authentication form detected on {service_name}"

        return False, ""

    def pause_for_authentication(
        self,
        task_id: str,
        session_id: str,
        current_url: str,
        reason: str,
    ) -> AuthSession:
        """Pause the current task and generate a resume token for the user."""
        service_name = self._identify_service(current_url)
        token = f"auth-{uuid.uuid4().hex[:8]}"

        prompt = (
            f"Authentication required for {service_name}. "
            f"Please complete login in the browser window, then click Resume."
        )

        auth_session = AuthSession(
            session_id=session_id,
            task_id=task_id,
            target_service=service_name,
            login_url=current_url,
            auth_status=AuthStatus.REQUIRED,
            prompt_message=prompt,
            resume_token=token,
        )

        self._pending_auth_sessions[token] = auth_session
        logger.info(f"[AUTH_MANAGER] Paused task '{task_id}' for authentication on {service_name} (token: {token})")
        return auth_session

    def resume_authentication(self, token: str) -> Optional[AuthSession]:
        """Validate token and mark authentication as complete."""
        session = self._pending_auth_sessions.pop(token, None)
        if session:
            session.auth_status = AuthStatus.AUTHENTICATED
            logger.info(f"[AUTH_MANAGER] Authentication confirmed for {session.target_service} (task: {session.task_id})")
            return session
        return None

    def get_pending_auth(self, token: str) -> Optional[AuthSession]:
        """Look up a pending authentication session."""
        return self._pending_auth_sessions.get(token)

    @staticmethod
    def _identify_service(url: str) -> str:
        """Extract a readable service name from the destination URL."""
        if "github.com" in url:
            return "GitHub"
        elif "vercel.com" in url:
            return "Vercel"
        elif "railway.app" in url:
            return "Railway"
        elif "render.com" in url:
            return "Render"
        elif "google.com" in url:
            return "Google"
        elif "youtube.com" in url:
            return "YouTube"
        elif "linkedin.com" in url:
            return "LinkedIn"
        return "Web Application"
