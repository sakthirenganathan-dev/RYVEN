"""RYVEN 3.0 — Multi-Step Web Task Service.

Transforms natural language goals into structured Observe -> Act -> Verify plans,
executes them sequentially, monitors authentication pause conditions, and emits ActionEvents.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Dict, List, Optional
import urllib.parse

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.browser.engine import BrowserEngine, browser_engine
from app.core.logging_config import logger
from app.internet.auth_manager import AuthenticationSessionManager
from app.internet.models import (
    AuthSession,
    AuthStatus,
    WebTaskPlan,
    WebTaskStatus,
    WebTaskStep,
)
from app.internet.page_reader import PageReader
from app.internet.search_service import SearchService
from app.internet.security import InternetSecurityPolicy
from app.internet.verification_service import WebVerificationService


class WebTaskService:
    """Orchestrates autonomous multi-step web workflows."""

    def __init__(
        self,
        engine: Optional[BrowserEngine] = None,
        search_service: Optional[SearchService] = None,
        auth_manager: Optional[AuthenticationSessionManager] = None,
    ) -> None:
        self._engine = engine or browser_engine
        self._search = search_service or SearchService()
        self._auth = auth_manager or AuthenticationSessionManager()

    def plan_task(self, goal: str) -> WebTaskPlan:
        """Decompose natural language web goal into structured steps."""
        plan_id = f"webtask-{uuid.uuid4().hex[:8]}"
        clean_goal = goal.strip()
        steps: List[WebTaskStep] = []

        # 1. YouTube Search Flow
        m_yt = re.search(r"\b(?:search\s+(?:on\s+)?youtube(?:\s+for)?|youtube(?:\s+and)?\s+search(?:\s+for)?)\s+['\"]?(.+?)['\"]?$", clean_goal, re.IGNORECASE)
        if not m_yt:
            m_yt = re.search(r"\bsearch\s+youtube\s+(?:for\s+)?(.+)$", clean_goal, re.IGNORECASE)

        if m_yt:
            query = re.sub(r"[.?!]+$", "", m_yt.group(1)).strip()
            encoded = urllib.parse.quote_plus(query)
            yt_url = f"https://www.youtube.com/results?search_query={encoded}"

            steps = [
                WebTaskStep(
                    step_id="step-01-open-browser",
                    order=1,
                    name="Open Browser Session",
                    action_type="open_browser",
                    arguments={"url": "https://www.youtube.com"},
                    description="Launch controlled browser and navigate to YouTube",
                ),
                WebTaskStep(
                    step_id="step-02-search-youtube",
                    order=2,
                    name=f"Search YouTube for '{query}'",
                    action_type="navigate_browser",
                    target=yt_url,
                    arguments={"url": yt_url},
                    description=f"Load YouTube search results for query '{query}'",
                ),
                WebTaskStep(
                    step_id="step-03-observe-results",
                    order=3,
                    name="Observe Results",
                    action_type="read_page",
                    description="Extract readable content from search results page",
                ),
                WebTaskStep(
                    step_id="step-04-verify",
                    order=4,
                    name="Verify Search Results",
                    action_type="verify_results",
                    arguments={"query": query},
                    description="Verify visible search results exist and report summary",
                ),
            ]
            return WebTaskPlan(plan_id=plan_id, goal=clean_goal, steps=steps, target_url=yt_url)

        # 2. Google Search & Research Flow
        m_google = re.search(r"\b(?:search\s+(?:on\s+)?google(?:\s+for)?|google(?:\s+and)?\s+search(?:\s+for)?)\s+['\"]?(.+?)['\"]?$", clean_goal, re.IGNORECASE)
        if not m_google and ("search" in clean_goal.lower() or "research" in clean_goal.lower()):
            m_google = re.search(r"\b(?:search\s+(?:for\s+)?|research\s+)(.+)$", clean_goal, re.IGNORECASE)

        if m_google:
            query = re.sub(r"[.?!]+$", "", m_google.group(1)).strip()
            encoded = urllib.parse.quote_plus(query)
            google_url = f"https://www.google.com/search?q={encoded}"

            steps = [
                WebTaskStep(
                    step_id="step-01-open-browser",
                    order=1,
                    name="Open Browser Session",
                    action_type="open_browser",
                    arguments={"url": google_url},
                    description=f"Search Google for '{query}'",
                ),
                WebTaskStep(
                    step_id="step-02-read-results",
                    order=2,
                    name="Observe Search Results",
                    action_type="read_page",
                    description="Extract ranked search entries and links",
                ),
                WebTaskStep(
                    step_id="step-03-verify",
                    order=3,
                    name="Verify Results",
                    action_type="verify_results",
                    arguments={"query": query},
                    description="Verify results are populated",
                ),
            ]
            return WebTaskPlan(plan_id=plan_id, goal=clean_goal, steps=steps, target_url=google_url)

        # 3. Direct Website / Application Flow (e.g. "open github and check issues")
        m_site = re.search(r"\b(?:open|visit|navigate\s+to|inspect)\s+(https?://[^\s]+|[a-zA-Z0-9\.\-]+\.[a-zA-Z]{2,}[^\s]*|github|vercel|render|railway)", clean_goal, re.IGNORECASE)
        dest_url = m_site.group(1).strip() if m_site else "https://www.google.com"
        if not dest_url.startswith("http"):
            if dest_url.lower() == "github":
                dest_url = "https://github.com"
            elif dest_url.lower() == "vercel":
                dest_url = "https://vercel.com"
            else:
                dest_url = f"https://{dest_url}"

        steps = [
            WebTaskStep(
                step_id="step-01-navigate",
                order=1,
                name=f"Navigate to {dest_url}",
                action_type="navigate_browser",
                target=dest_url,
                arguments={"url": dest_url},
                description=f"Open destination web application: {dest_url}",
            ),
            WebTaskStep(
                step_id="step-02-check-auth",
                order=2,
                name="Check Authentication",
                action_type="check_auth",
                description="Detect if the target page requires user login",
            ),
            WebTaskStep(
                step_id="step-03-observe-content",
                order=3,
                name="Observe Page Content",
                action_type="read_page",
                description="Read visible dashboard or page text",
            ),
        ]

        requires_auth = any(svc in dest_url.lower() for svc in ("github", "vercel", "railway", "render", "login", "gmail", "linkedin"))
        return WebTaskPlan(
            plan_id=plan_id,
            goal=clean_goal,
            steps=steps,
            target_url=dest_url,
            requires_authentication=requires_auth,
        )

    async def execute_plan(
        self,
        plan: WebTaskPlan,
        session_id: Optional[str] = None,
        auto_confirm: bool = False,
    ) -> Dict[str, Any]:
        """Execute web task steps sequentially in Observe -> Act -> Verify loop."""
        sid = session_id or f"web-session-{uuid.uuid4().hex[:6]}"
        logger.info(f"[WEB_TASK] Starting task '{plan.plan_id}' for goal: '{plan.goal}'")

        # Emit task started event
        action_bus.emit(
            ActionEvent(
                task_id=plan.plan_id,
                action_type=ActionType.INTERNET_TASK_STARTED,
                status=ActionStatus.STARTED,
                title=f"Internet Task: {plan.goal[:50]}",
                description=f"Executing {len(plan.steps)} web workflow step(s)",
            )
        )

        completed_count = 0
        final_message = ""
        current_snapshot = None

        for step in plan.steps:
            step.status = "RUNNING"
            logger.info(f"[WEB_TASK] Step {step.order}/{len(plan.steps)}: '{step.name}' ({step.action_type})")

            # Check confirmation gate if step requires confirmation
            if step.requires_confirmation and not auto_confirm:
                step.status = "WAITING_CONFIRMATION"
                token = f"confirm-{uuid.uuid4().hex[:6]}"
                return {
                    "success": False,
                    "status": WebTaskStatus.WAITING_CONFIRMATION,
                    "plan_id": plan.plan_id,
                    "step_id": step.step_id,
                    "confirmation_token": token,
                    "message": f"Action '{step.name}' requires explicit confirmation.",
                }

            # Execute Step by action type
            try:
                if step.action_type == "open_browser":
                    action_bus.emit(
                        ActionEvent(
                            task_id=plan.plan_id,
                            action_type=ActionType.WEB_ACTION_STARTED,
                            status=ActionStatus.PROGRESS,
                            title=f"Step {step.order}: {step.name}",
                            description=step.description,
                        )
                    )
                    res = await self._engine.open_browser(url=step.arguments.get("url"), session_id=sid)
                    step.observation = res.get("message")
                    completed_count += 1
                    step.status = "COMPLETED"
                    action_bus.emit(
                        ActionEvent(
                            task_id=plan.plan_id,
                            action_type=ActionType.WEB_ACTION_COMPLETED,
                            status=ActionStatus.PROGRESS,
                            title=f"Completed {step.name}",
                            description=step.observation or "",
                        )
                    )

                elif step.action_type == "navigate_browser":
                    url = step.target or step.arguments.get("url", "")
                    action_bus.emit(
                        ActionEvent(
                            task_id=plan.plan_id,
                            action_type=ActionType.WEB_ACTION_STARTED,
                            status=ActionStatus.PROGRESS,
                            title=f"Step {step.order}: Navigate to '{url[:40]}'",
                            description=step.description,
                        )
                    )
                    res = await self._engine.navigate_browser(url=url, session_id=sid)
                    if not res.get("success"):
                        step.status = "FAILED"
                        step.error = res.get("message")
                        action_bus.emit(
                            ActionEvent(
                                task_id=plan.plan_id,
                                action_type=ActionType.WEB_ACTION_COMPLETED,
                                status=ActionStatus.FAILED,
                                title=f"Navigation Failed",
                                description=step.error or "",
                            )
                        )
                        break
                    step.observation = res.get("message")
                    completed_count += 1
                    step.status = "COMPLETED"
                    action_bus.emit(
                        ActionEvent(
                            task_id=plan.plan_id,
                            action_type=ActionType.WEB_ACTION_COMPLETED,
                            status=ActionStatus.PROGRESS,
                            title=f"Navigated to '{url[:40]}'",
                            description=step.observation or "",
                        )
                    )

                elif step.action_type == "read_page":
                    action_bus.emit(
                        ActionEvent(
                            task_id=plan.plan_id,
                            action_type=ActionType.PAGE_READ_STARTED,
                            status=ActionStatus.PROGRESS,
                            title=f"Step {step.order}: Read Page",
                            description="Extracting text content from active page",
                        )
                    )
                    res = await self._engine.read_page(session_id=sid)
                    step.observation = f"Extracted {res.get('total_length', 0)} characters from '{res.get('title')}'."
                    final_message = res.get("text", "")[:300]
                    completed_count += 1
                    step.status = "COMPLETED"
                    action_bus.emit(
                        ActionEvent(
                            task_id=plan.plan_id,
                            action_type=ActionType.PAGE_READ_COMPLETED,
                            status=ActionStatus.PROGRESS,
                            title=f"Page Read: '{res.get('title', '')}'",
                            description=step.observation,
                        )
                    )

                elif step.action_type == "check_auth":
                    curr = await self._engine.get_current_page(session_id=sid)
                    st = self._engine.get_session(sid)
                    html = st.last_snapshot.raw_html_truncated if st and st.last_snapshot else ""
                    is_auth, auth_reason = self._auth.is_login_required(
                        url=curr.get("current_url", ""),
                        html_content=html or "",
                    )
                    if is_auth:
                        auth_session = self._auth.pause_for_authentication(
                            task_id=plan.plan_id,
                            session_id=sid,
                            current_url=curr.get("current_url", ""),
                            reason=auth_reason,
                        )
                        step.status = "WAITING_AUTHENTICATION"
                        action_bus.emit(
                            ActionEvent(
                                task_id=plan.plan_id,
                                action_type=ActionType.AUTHENTICATION_REQUIRED,
                                status=ActionStatus.WAITING_CONFIRMATION,
                                title="Authentication Required",
                                description=auth_session.prompt_message,
                            )
                        )
                        return {
                            "success": False,
                            "status": WebTaskStatus.WAITING_AUTHENTICATION,
                            "plan_id": plan.plan_id,
                            "auth_session": auth_session.model_dump(),
                            "message": auth_session.prompt_message,
                        }
                    step.observation = "Page is accessible without authentication."
                    completed_count += 1
                    step.status = "COMPLETED"

                elif step.action_type == "verify_results":
                    action_bus.emit(
                        ActionEvent(
                            task_id=plan.plan_id,
                            action_type=ActionType.WEB_VERIFICATION_STARTED,
                            status=ActionStatus.PROGRESS,
                            title=f"Step {step.order}: Verify Search Results",
                            description=f"Verifying results for '{step.arguments.get('query', '')}'",
                        )
                    )
                    st = self._engine.get_session(sid)
                    v_res = WebVerificationService.verify_search_results(
                        snapshot=st.last_snapshot if st else None,
                        query=step.arguments.get("query", ""),
                    )
                    step.observation = v_res.get("reason")
                    completed_count += 1
                    step.status = "COMPLETED"
                    action_bus.emit(
                        ActionEvent(
                            task_id=plan.plan_id,
                            action_type=ActionType.WEB_VERIFICATION_COMPLETED,
                            status=ActionStatus.PROGRESS if v_res.get("verified") else ActionStatus.FAILED,
                            title="Verification Completed",
                            description=v_res.get("reason", ""),
                        )
                    )

            except Exception as e:
                logger.error(f"[WEB_TASK] Step '{step.name}' failed with error: {e}")
                step.status = "FAILED"
                step.error = str(e)
                break

        is_all_success = completed_count == len(plan.steps)
        overall_status = WebTaskStatus.COMPLETED if is_all_success else WebTaskStatus.FAILED

        action_bus.emit(
            ActionEvent(
                task_id=plan.plan_id,
                action_type=ActionType.INTERNET_TASK_COMPLETED if is_all_success else ActionType.INTERNET_TASK_FAILED,
                status=ActionStatus.COMPLETED if is_all_success else ActionStatus.FAILED,
                title=f"Internet Task {'Completed' if is_all_success else 'Failed'}",
                description=f"Completed {completed_count}/{len(plan.steps)} step(s).",
            )
        )

        return {
            "success": is_all_success,
            "status": overall_status,
            "plan_id": plan.plan_id,
            "steps_total": len(plan.steps),
            "steps_completed": completed_count,
            "message": f"Successfully completed all {completed_count} step(s) for: {plan.goal}" if is_all_success else f"Task stopped at step: {plan.steps[completed_count].name if completed_count < len(plan.steps) else 'Unknown'}",
            "preview": final_message,
            "steps": [s.model_dump() for s in plan.steps],
        }
