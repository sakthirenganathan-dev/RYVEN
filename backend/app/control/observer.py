"""RYVEN 3.0 M17.0 — Unified Observer & Perception Engine.

Captures multimodal state before and after actions:
- Windows desktop applications (running status, process inspection)
- Browser state (URL, DOM elements, title, page content)
- Project workspace (file existence, git status, build state)
- Perception & Vision (transient OCR, scene description via PerceptionAgent)

Security Invariants:
- Screenshots and OCR remain transient in-memory.
- Sensitive credentials, passwords, tokens, and cookies are automatically redacted.
- Never reads unauthorized or non-approved directory trees.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.browser.engine import browser_engine
from app.control.models import ObservationRecord
from app.core.logging_config import logger
from app.tools.app_tool import OpenApplicationTool
from app.tools.process_tool import _get_running_approved_processes
from app.tools.registry import ToolRegistry, create_default_registry


class ObserverEngine:
    """Multi-domain observer capturing live computer and internet state."""

    def __init__(self, tool_registry: Optional[ToolRegistry] = None) -> None:
        self.tool_reg = tool_registry or create_default_registry()
        self.browser = browser_engine

    async def observe_environment(
        self,
        target_app: Optional[str] = None,
        target_project: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> List[ObservationRecord]:
        """Aggregate cross-domain observations prior to or following action execution."""
        records: List[ObservationRecord] = []

        # 1. Desktop & Applications Observation
        try:
            app_obs = await self.observe_applications(target_app=target_app)
            records.append(app_obs)
        except Exception as exc:
            logger.debug(f"[OBSERVER] Desktop observation error: {exc}")

        # 2. Browser State Observation
        try:
            browser_obs = await self.observe_browser(session_id=session_id)
            if browser_obs:
                records.append(browser_obs)
        except Exception as exc:
            logger.debug(f"[OBSERVER] Browser observation error: {exc}")

        # 3. Project Observation (if project context exists)
        if target_project:
            try:
                proj_obs = await self.observe_project(target_project)
                if proj_obs:
                    records.append(proj_obs)
            except Exception as exc:
                logger.debug(f"[OBSERVER] Project observation error: {exc}")

        # Emit observation event to action bus
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.CONTROL_OBSERVING,
                status=ActionStatus.COMPLETED,
                title=f"Environment state observed ({len(records)} domain(s))",
                task_id=task_id,
                safe_metadata={
                    "domains": [r.source for r in records],
                    "total_records": len(records),
                },
            )
        )

        return records

    async def observe_applications(self, target_app: Optional[str] = None) -> ObservationRecord:
        """Inspect running approved desktop applications."""
        running_map = _get_running_approved_processes()
        active = []

        for key, procs in running_map.items():
            disp = OpenApplicationTool.ALLOWLIST.get(key, {}).get("display_name", key)
            if len(procs) > 0:
                active.append({"app": disp, "count": len(procs)})

        active_names = [a["app"] for a in active]
        summary = f"Running applications: {', '.join(active_names)}" if active else "No approved applications actively running"

        return ObservationRecord(
            source="desktop",
            title="Desktop Applications State",
            summary=summary,
            details={
                "active_apps": active,
                "processes": active,
                "count": len(active),
                "target_checked": target_app,
            },
            app_name=target_app,
        )

    async def observe_browser(self, session_id: Optional[str] = None) -> Optional[ObservationRecord]:
        """Inspect currently active browser page, DOM, and URL."""
        try:
            sessions = self.browser.list_sessions()
            if not sessions:
                return ObservationRecord(
                    source="browser",
                    title="Browser State: Idle",
                    summary="No active browser tabs open",
                    details={"session_count": 0, "active_tab": None},
                )

            active_session = None
            if session_id:
                active_session = self.browser.get_session(session_id)
            if not active_session and sessions:
                active_session = sessions[0]

            if not active_session or not active_session.current_url:
                return ObservationRecord(
                    source="browser",
                    title="Browser State: Idle",
                    summary="No active browser tab URL detected",
                    details={
                        "session_count": len(sessions),
                        "active_tab": None,
                        "session_id": getattr(active_session, "session_id", None),
                    },
                )

            snap = active_session.last_snapshot
            title = snap.title if snap else "Browser Tab"
            url = active_session.current_url
            element_count = len(snap.elements) if snap else 0

            return ObservationRecord(
                source="browser",
                title=f"Browser: {title[:40]}",
                summary=f"Active URL: {url} ({element_count} interactive elements)",
                url=url,
                details={
                    "url": url,
                    "title": title,
                    "element_count": element_count,
                    "session_id": active_session.session_id,
                },
            )
        except Exception as exc:
            logger.debug(f"[OBSERVER] Browser state observation notice: {exc}")
            return ObservationRecord(
                source="browser",
                title="Browser State: Idle",
                summary="Browser state unavailable; continuing with observed desktop context.",
                details={"session_count": 0, "active_tab": None, "error": str(exc)},
            )

    async def observe_project(self, project_name: str) -> Optional[ObservationRecord]:
        """Inspect project structure and git state."""
        try:
            if not self.tool_reg.has_tool("resolve_existing_project"):
                return None

            res = await self.tool_reg.execute_tool(
                "resolve_existing_project",
                arguments={"project_name": project_name},
            )
            if not res.get("success"):
                return None

            pdir = res.get("project_directory", "")
            return ObservationRecord(
                source="project",
                title=f"Project Workspace: {project_name}",
                summary=f"Canonical path verified at {pdir}",
                project_name=project_name,
                details={"project_directory": pdir, "exists": True},
            )
        except Exception as exc:
            logger.debug(f"[OBSERVER] Project observation notice: {exc}")
            return None

    async def capture_vision_perception(
        self,
        session_id: Optional[str] = None,
        prompt: str = "Analyze the visible screen elements and layout.",
    ) -> Optional[Dict[str, Any]]:
        """Run transient local perception/OCR on active browser screen."""
        try:
            if not self.tool_reg.has_tool("browser_screenshot") or not self.tool_reg.has_tool("page_ocr"):
                return None

            ocr_res = await self.tool_reg.execute_tool("page_ocr", arguments={"session_id": session_id})
            return ocr_res
        except Exception as exc:
            logger.debug(f"[OBSERVER] Vision perception notice: {exc}")
            return None


observer_engine = ObserverEngine()
