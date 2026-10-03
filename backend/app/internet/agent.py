"""RYVEN 3.0 — Unified Internet Agent Control Plane Facade.

Integrates Web Search, Multi-Source Research, Browser Automation, Element Interaction,
Multi-Step Web Tasks, Authenticated Session Pause/Resume, and Safety Verification into
ONE central first-class agent abstraction.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.browser.engine import BrowserEngine, browser_engine
from app.core.logging_config import logger
from app.internet.auth_manager import AuthenticationSessionManager
from app.internet.element_service import ElementService
from app.internet.models import (
    AuthSession,
    AuthStatus,
    InternetAgentState,
    SearchResult,
    WebFormInfo,
    WebPageState,
    WebResearchResult,
    WebTaskPlan,
    WebTaskStatus,
)
from app.internet.download_service import DownloadService
from app.internet.file_models import DEFAULT_MAX_DOWNLOAD_SIZE_BYTES, DEFAULT_MAX_UPLOAD_SIZE_BYTES
from app.internet.form_interaction_service import FormInteractionService
from app.internet.navigation_service import NavigationService
from app.internet.page_reader import PageReader
from app.internet.search_service import SearchService
from app.internet.security import InternetSecurityPolicy
from app.internet.task_service import WebTaskService
from app.internet.upload_service import UploadService
from app.internet.verification_service import WebVerificationService


class InternetAgent:
    """The unified Internet Agent control plane for RYVEN 3.0.

    Exposes high-level controlled operations for:
    - search
    - navigate
    - read_page
    - find_element
    - click
    - type
    - keyboard
    - scroll
    - browser_state
    - research
    - web_task_execution
    - verification
    - authentication pause/resume
    """

    def __init__(self) -> None:
        self.browser_engine: BrowserEngine = browser_engine
        self.search_service: SearchService = SearchService()
        self.page_reader: PageReader = PageReader()
        self.navigation_service: NavigationService = NavigationService(self.browser_engine)
        self.element_service: ElementService = ElementService(self.browser_engine)
        self.auth_manager: AuthenticationSessionManager = AuthenticationSessionManager()
        self.verification_service: WebVerificationService = WebVerificationService()
        self.task_service: WebTaskService = WebTaskService(
            engine=self.browser_engine,
            search_service=self.search_service,
            auth_manager=self.auth_manager,
        )
        self.form_service: FormInteractionService = FormInteractionService(self.browser_engine)
        self.download_service: DownloadService = DownloadService(engine=self.browser_engine)
        self.upload_service: UploadService = UploadService(engine=self.browser_engine)

        self._state = InternetAgentState()

    def get_state(self) -> InternetAgentState:
        """Retrieve the live observable state of the Internet Agent."""
        session = self.browser_engine.get_session()
        if session:
            self._state.current_url = session.current_url
            self._state.page_title = session.page_title
            self._state.history_urls = session.history
        return self._state

    # --------------------------------------------------------------------------
    # 1. SEARCH & RESEARCH CAPABILITY
    # --------------------------------------------------------------------------

    async def search(self, query: str, max_results: int = 5) -> Dict[str, Any]:
        """Perform normal ranked web search."""
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.SEARCH_STARTED,
                status=ActionStatus.STARTED,
                title=f"Web Search: '{query}'",
                description="Executing web search and ranking results",
            )
        )
        self._state.active_action = "SEARCH"
        self._state.last_search_query = query

        results: List[SearchResult] = await self.search_service.search(query, max_results=max_results)

        action_bus.emit(
            ActionEvent(
                action_type=ActionType.SEARCH_COMPLETED,
                status=ActionStatus.COMPLETED,
                title="Web Search Completed",
                description=f"Retrieved {len(results)} ranked result(s)",
            )
        )
        self._state.active_action = None

        return {
            "success": True,
            "query": query,
            "count": len(results),
            "results": [r.model_dump() for r in results],
            "message": f"Found {len(results)} search result(s) for '{query}'.",
        }

    async def research(self, topic: str, max_sources: int = 3) -> Dict[str, Any]:
        """Execute comprehensive multi-source web research with citations."""
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.INTERNET_TASK_STARTED,
                status=ActionStatus.STARTED,
                title=f"Web Research: '{topic}'",
                description=f"Collecting and reading top {max_sources} authoritative sources",
            )
        )
        self._state.active_action = "RESEARCH"

        report: WebResearchResult = await self.search_service.research_topic(topic, max_sources=max_sources)
        self._state.last_research_result = report
        self._state.active_action = None

        # Verify sources were read
        v = self.verification_service.verify_research_sources_read(report.sources, min_sources=1)

        action_bus.emit(
            ActionEvent(
                action_type=ActionType.INTERNET_TASK_COMPLETED if v.get("verified") else ActionType.INTERNET_TASK_FAILED,
                status=ActionStatus.COMPLETED if v.get("verified") else ActionStatus.FAILED,
                title="Web Research Completed" if v.get("verified") else "Web Research Unverified",
                description=f"Synthesized research report from {len(report.sources)} sources",
            )
        )

        return {
            "success": True,
            "topic": topic,
            "summary": report.summary,
            "key_findings": report.key_findings,
            "sources_count": len(report.sources),
            "sources": [s.model_dump() for s in report.sources],
            "verified": v.get("verified", True),
            "message": f"Completed research on '{topic}' across {len(report.sources)} authoritative sources.",
        }

    # --------------------------------------------------------------------------
    # 2. BROWSER & NAVIGATION CAPABILITY
    # --------------------------------------------------------------------------

    async def navigate(self, url: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Navigate controlled browser to target URL with SSRF protection."""
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.WEB_ACTION_STARTED,
                status=ActionStatus.STARTED,
                title=f"Browser Navigate: '{url[:50]}'",
                description="Navigating with SSRF validation",
            )
        )
        res = await self.navigation_service.open_url(target=url, session_id=session_id)
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.WEB_ACTION_COMPLETED,
                status=ActionStatus.COMPLETED if res.get("success") else ActionStatus.FAILED,
                title=f"Browser Navigate {'Success' if res.get('success') else 'Failed'}",
                description=res.get("message", ""),
            )
        )
        return res

    async def browser_navigate(self, url: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Alias for navigate."""
        return await self.navigate(url=url, session_id=session_id)

    async def read_page(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Read and extract clean, readable text from current browser page."""
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.PAGE_READ_STARTED,
                status=ActionStatus.STARTED,
                title="Reading Browser Page",
                description="Extracting structured text and links",
            )
        )
        res = await self.browser_engine.read_page(session_id=session_id)
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.PAGE_READ_COMPLETED,
                status=ActionStatus.COMPLETED if res.get("success") else ActionStatus.FAILED,
                title="Browser Page Read",
                description=f"Extracted {res.get('total_length', 0)} characters from '{res.get('title', '')}'",
            )
        )
        return res

    async def browser_state(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Query current browser state."""
        return await self.navigation_service.get_state(session_id=session_id)

    async def perceive_page(
        self,
        session_id: Optional[str] = None,
        prompt: str = "Analyze this page structure, interactive elements, and visible text.",
    ) -> Dict[str, Any]:
        """Optional visual perception of the current page using local vision & OCR models."""
        from app.ai.vision import perception_agent

        state = self.browser_engine._get_or_create_session(session_id)
        snap = state.last_snapshot

        if not snap or not snap.screenshot_b64:
            capture_res = await self.browser_engine.capture_screenshot(session_id=session_id)
            if not capture_res.get("success"):
                return {
                    "success": False,
                    "error": capture_res.get("error", "Failed to capture page screenshot"),
                }
            snap = state.last_snapshot

        if not snap or not snap.screenshot_b64:
            return {"success": False, "error": "No screenshot available for perception."}

        perception = await perception_agent.perceive(
            image_b64=snap.screenshot_b64,
            prompt=prompt,
            url=snap.url,
            allow_remote=False,
        )
        return {
            "success": True,
            "url": snap.url,
            "perception": perception.model_dump(),
        }

    # --------------------------------------------------------------------------
    # 3. ELEMENT INTERACTION CAPABILITY
    # --------------------------------------------------------------------------

    async def find_element(self, selector_or_text: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Locate element matching selector, ID, or visible text."""
        return await self.element_service.find(selector_or_text=selector_or_text, session_id=session_id)

    async def click(
        self,
        selector_or_text: str,
        session_id: Optional[str] = None,
        confirmed: bool = False,
    ) -> Dict[str, Any]:
        """Click element, enforcing confirmation policy if sensitive."""
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.WEB_ACTION_STARTED,
                status=ActionStatus.STARTED,
                title=f"Click Element: '{selector_or_text}'",
                description="Dispatching click with confirmation validation",
            )
        )
        res = await self.element_service.click(
            selector_or_text=selector_or_text,
            session_id=session_id,
            confirmed=confirmed,
        )
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.WEB_ACTION_COMPLETED,
                status=ActionStatus.COMPLETED if res.get("success") else ActionStatus.FAILED,
                title=f"Click {'Success' if res.get('success') else 'Failed'}",
                description=res.get("message", ""),
            )
        )
        return res

    async def type(
        self,
        selector_or_input: str,
        text: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Type text into field with secret redaction in action records."""
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.WEB_ACTION_STARTED,
                status=ActionStatus.STARTED,
                title=f"Type into '{selector_or_input}'",
                description="Dispatching text entry with secret redaction",
            )
        )
        res = await self.element_service.type(
            selector_or_input=selector_or_input,
            text=text,
            session_id=session_id,
        )
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.WEB_ACTION_COMPLETED,
                status=ActionStatus.COMPLETED if res.get("success") else ActionStatus.FAILED,
                title=f"Type {'Success' if res.get('success') else 'Failed'}",
                description=res.get("message", ""),
            )
        )
        return res

    async def keyboard(self, key: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Dispatch keyboard key (Enter, Escape, Tab, etc.)."""
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.WEB_ACTION_STARTED,
                status=ActionStatus.STARTED,
                title=f"Press Key: '{key}'",
                description="Dispatching keyboard event",
            )
        )
        res = await self.element_service.press_key(key=key, session_id=session_id)
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.WEB_ACTION_COMPLETED,
                status=ActionStatus.COMPLETED if res.get("success") else ActionStatus.FAILED,
                title=f"Key '{key}' dispatched",
                description=res.get("message", ""),
            )
        )
        return res

    async def scroll(
        self,
        direction: str = "down",
        amount: int = 500,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Scroll viewport."""
        return await self.element_service.scroll(direction=direction, amount=amount, session_id=session_id)

    # --------------------------------------------------------------------------
    # 4. MULTI-STEP WEB TASK CAPABILITY
    # --------------------------------------------------------------------------

    async def execute_task(
        self,
        goal: str,
        session_id: Optional[str] = None,
        auto_confirm: bool = False,
    ) -> Dict[str, Any]:
        """Plan and execute a multi-step internet task (Observe -> Act -> Verify)."""
        self._state.current_goal = goal
        self._state.status = WebTaskStatus.PLANNING

        plan: WebTaskPlan = self.task_service.plan_task(goal)
        self._state.current_task_id = plan.plan_id
        self._state.status = WebTaskStatus.EXECUTING

        result = await self.task_service.execute_plan(
            plan=plan,
            session_id=session_id,
            auto_confirm=auto_confirm,
        )

        self._state.status = result.get("status", WebTaskStatus.COMPLETED)
        if result.get("auth_session"):
            self._state.active_auth_session = AuthSession(**result["auth_session"])

        return result

    async def web_task_execution(
        self,
        goal: str,
        session_id: Optional[str] = None,
        auto_confirm: bool = False,
    ) -> Dict[str, Any]:
        """Alias for execute_task."""
        return await self.execute_task(goal=goal, session_id=session_id, auto_confirm=auto_confirm)

    # --------------------------------------------------------------------------
    # 5. AUTHENTICATION PAUSE / RESUME CAPABILITY
    # --------------------------------------------------------------------------

    def pause_authentication(
        self,
        task_id: str,
        session_id: str,
        current_url: str,
        reason: str,
    ) -> AuthSession:
        """Pause task for human authentication and emit event."""
        auth_session = self.auth_manager.pause_for_authentication(
            task_id=task_id,
            session_id=session_id,
            current_url=current_url,
            reason=reason,
        )
        self._state.active_auth_session = auth_session
        self._state.status = WebTaskStatus.WAITING_AUTHENTICATION

        action_bus.emit(
            ActionEvent(
                task_id=task_id,
                action_type=ActionType.AUTHENTICATION_REQUIRED,
                status=ActionStatus.WAITING_CONFIRMATION,
                title="Authentication Required",
                description=auth_session.prompt_message,
            )
        )
        return auth_session

    async def resume_authentication(self, resume_token: str) -> Dict[str, Any]:
        """Resume task after user manually completed browser authentication."""
        auth_session = self.auth_manager.resume_authentication(resume_token)
        if not auth_session:
            return {
                "success": False,
                "message": f"Invalid or expired authentication resume token '{resume_token}'.",
            }

        self._state.active_auth_session = None
        self._state.status = WebTaskStatus.EXECUTING

        action_bus.emit(
            ActionEvent(
                task_id=auth_session.task_id,
                action_type=ActionType.AUTHENTICATION_RESUMED,
                status=ActionStatus.PROGRESS,
                title="Authentication Resumed",
                description=f"Confirmed login for {auth_session.target_service}.",
            )
        )

        logger.info(f"[INTERNET_AGENT] Resumed task '{auth_session.task_id}' after human authentication.")
        return {
            "success": True,
            "task_id": auth_session.task_id,
            "service": auth_session.target_service,
            "message": f"Authentication confirmed for {auth_session.target_service}. Task execution resumed.",
        }

    # --------------------------------------------------------------------------
    # 6. TAB MANAGEMENT & VERIFICATION
    # --------------------------------------------------------------------------

    async def manage_tabs(
        self,
        action: str = "list",
        tab_url: Optional[str] = None,
        tab_index: Optional[int] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Manage multi-tab browser sessions."""
        return await self.navigation_service.manage_tabs(
            action=action,
            tab_url=tab_url,
            tab_index=tab_index,
            session_id=session_id,
        )

    async def verification(self, query: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Verify presence of content or search results on active page."""
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.WEB_VERIFICATION_STARTED,
                status=ActionStatus.STARTED,
                title=f"Web Verification: '{query}'",
                description="Inspecting active DOM snapshot for target content",
            )
        )
        state = self.browser_engine.get_session(session_id)
        snapshot = state.last_snapshot if state else None
        res = self.verification_service.verify_search_results(snapshot, query)
        action_bus.emit(
            ActionEvent(
                action_type=ActionType.WEB_VERIFICATION_COMPLETED,
                status=ActionStatus.COMPLETED if res.get("verified") else ActionStatus.FAILED,
                title=f"Web Verification {'Passed' if res.get('verified') else 'Failed'}",
                description=res.get("reason", ""),
            )
        )
        return res

    async def verify_page(self, query: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Alias for verification."""
        return await self.verification_service_verify(query=query, session_id=session_id)

    async def verification_service_verify(self, query: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        return await self.verification(query=query, session_id=session_id)

    # --------------------------------------------------------------------------
    # 7. M15.3 Phase 2 — Unified Page State Inspection
    # --------------------------------------------------------------------------

    async def inspect_page_state(
        self,
        session_id: Optional[str] = None,
        include_perception: bool = False,
    ) -> Dict[str, Any]:
        """Return a structured, read-only WebPageState for the current page.

        Combines:
        - BrowserSnapshot fields (url, title, text, headings, links)
        - Structured WebFormInfo list via BrowserEngine.extract_forms()
        - Optional PerceptionResult summary from M15.2 (include_perception=True)

        SECURITY CONTRACT:
        - This method is READ-ONLY.  It performs no navigation or mutation.
        - Password field values are NEVER included in the returned state.
        - Credential patterns in text_content are redacted via BrowserSecurityValidator.
        """
        from app.browser.security import BrowserSecurityValidator

        t0 = time.monotonic()

        # --- 1. Require an active snapshot -----------------------------------
        browser_state = self.browser_engine.get_session(session_id)
        if not browser_state or not browser_state.last_snapshot:
            return {
                "success": False,
                "error": "No active browser page to inspect. Navigate to a URL first.",
            }

        snap = browser_state.last_snapshot

        # --- 2. Redact credentials from visible text -------------------------
        safe_text = BrowserSecurityValidator.redact_credentials(snap.text_content)

        # --- 3. Extract forms ------------------------------------------------
        forms_result = await self.browser_engine.extract_forms(session_id=session_id)
        forms: List[WebFormInfo] = [
            WebFormInfo(**f) for f in forms_result.get("forms", [])
        ]

        # --- 4. Optional perception ------------------------------------------
        perception_summary: Optional[str] = None
        detected_elements_count: int = 0

        if include_perception:
            try:
                perc_result = await self.perceive_page(session_id=session_id)
                if perc_result.get("success"):
                    perc = perc_result.get("perception", {})
                    perception_summary = perc.get("summary") or perc.get("description")
                    detected_elements_count = len(perc.get("elements", []))
            except Exception as exc:
                logger.warning(f"[InternetAgent] inspect_page_state perception skipped: {exc}")

        # --- 5. Assemble WebPageState ----------------------------------------
        page_state = WebPageState(
            url=snap.url,
            title=snap.title,
            captured_at=snap.captured_at,
            text_content=safe_text[:4000],
            headings=snap.headings,
            links=snap.links,
            forms=forms,
            perception_summary=perception_summary,
            detected_elements_count=detected_elements_count,
            interactive_elements_count=snap.interactive_elements_count,
            page_load_status="loaded",
        )

        duration_ms = (time.monotonic() - t0) * 1000

        action_bus.emit(
            ActionEvent(
                action_type=ActionType.PAGE_READ_COMPLETED,
                status=ActionStatus.COMPLETED,
                title="Page State Inspected",
                description=(
                    f"Captured WebPageState for '{snap.title}' — "
                    f"{page_state.forms_count} form(s), "
                    f"{page_state.interactive_elements_count} interactive element(s)"
                ),
            )
        )

        return {
            "success": True,
            "page_state": page_state.model_dump(),
            "forms_count": page_state.forms_count,
            "has_password_form": any(f.has_password_field for f in forms),
            "message": (
                f"Inspected '{snap.title}' — "
                f"{page_state.forms_count} form(s) found."
            ),
            "duration_ms": duration_ms,
        }

    # --------------------------------------------------------------------------
    # 10. M15.3 PHASE 3 — ADVANCED FORM INTERACTIONS
    # --------------------------------------------------------------------------

    async def fill_form_field(
        self,
        target: str,
        value: str,
        form: Optional[WebFormInfo] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Type safe text into a form input (text, email, number, url, etc.)."""
        return await self.form_service.fill_text_field(
            target=target,
            value=value,
            form=form,
            session_id=session_id,
        )

    async def select_form_option(
        self,
        target: str,
        option: str,
        form: Optional[WebFormInfo] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Select an option in a <select> element by value or label."""
        return await self.form_service.select_option(
            target=target,
            option=option,
            form=form,
            session_id=session_id,
        )

    async def toggle_form_checkbox(
        self,
        target: str,
        checked: Optional[bool] = None,
        form: Optional[WebFormInfo] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Check, uncheck, or toggle a checkbox."""
        return await self.form_service.toggle_checkbox(
            target=target,
            checked=checked,
            form=form,
            session_id=session_id,
        )

    async def select_form_radio(
        self,
        target: str,
        value: str,
        form: Optional[WebFormInfo] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Select a radio button within a radio group by value or label."""
        return await self.form_service.select_radio(
            target=target,
            value=value,
            form=form,
            session_id=session_id,
        )

    async def submit_form(
        self,
        form: Optional[WebFormInfo] = None,
        form_id: Optional[str] = None,
        submit_label: Optional[str] = None,
        confirmed: bool = False,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit a form — only after explicit user confirmation."""
        return await self.form_service.submit_form(
            form=form,
            form_id=form_id,
            submit_label=submit_label,
            confirmed=confirmed,
            session_id=session_id,
        )

    def resolve_form_target(
        self,
        target: str,
        form: WebFormInfo,
    ) -> Dict[str, Any]:
        """Resolve form target returning all candidate matches."""
        return self.form_service.resolve_target(target=target, form=form)

    # --------------------------------------------------------------------------
    # 11. M15.3 PHASE 4 — DOWNLOAD & UPLOAD MANAGEMENT
    # --------------------------------------------------------------------------

    async def download_file(
        self,
        url: str,
        destination_folder: Optional[str] = "downloads",
        custom_filename: Optional[str] = None,
        confirmed: bool = False,
        max_size_bytes: int = DEFAULT_MAX_DOWNLOAD_SIZE_BYTES,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Download a file from a web URL to an approved local folder."""
        return await self.download_service.download_file(
            url=url,
            destination_folder=destination_folder,
            custom_filename=custom_filename,
            confirmed=confirmed,
            max_size_bytes=max_size_bytes,
            session_id=session_id,
        )

    async def upload_file(
        self,
        source_path: str,
        target_field: str,
        form: Optional[WebFormInfo] = None,
        form_id: Optional[str] = None,
        confirmed: bool = False,
        max_size_bytes: int = DEFAULT_MAX_UPLOAD_SIZE_BYTES,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload an approved local file to a web form file input."""
        return await self.upload_service.upload_file(
            source_path=source_path,
            target_field=target_field,
            form=form,
            form_id=form_id,
            confirmed=confirmed,
            max_size_bytes=max_size_bytes,
            session_id=session_id,
        )

    def verify_download(
        self,
        file_path: str,
        expected_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Verify that a downloaded file exists and is within approved locations."""
        return self.verification_service.verify_download(
            file_path=file_path,
            expected_size=expected_size,
        )

    def verify_upload(
        self,
        field_name: str,
        filename: str,
        engine_success: bool = True,
    ) -> Dict[str, Any]:
        """Verify that an upload file was attached to the target field."""
        return self.verification_service.verify_upload(
            field_name=field_name,
            filename=filename,
            engine_success=engine_success,
        )


# Global singleton instance
internet_agent = InternetAgent()
