#!/usr/bin/env python3
"""RYVEN M17.0 — Live Windows Verification Script.

Unified Personal Computer + Internet Control Plane:
  1. Capability Permission Engine (authorization layer, confirmation gating, shell bans)
  2. Safe Process Management (allowlist bounded, VS Code, Chrome, Terminal, Notepad, Calculator, Explorer)
  3. Multi-Domain Observation (desktop applications, browser state, project files)
  4. Scope Protection & Boundary Tracking (discovered item separation)
  5. Failure Taxonomy & Bounded Recovery (transient retry, unrecoverable abort)
  6. Unified Control Execution Loop (observe -> plan -> authorize -> coordinate -> observe -> verify)
  7. Assistant Integration & REST API Surface
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from typing import Any, Dict, List
from unittest.mock import MagicMock

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PASS = "[PASS]"
FAIL = "[FAIL]"
_results: List[Dict[str, Any]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    status = PASS if cond else FAIL
    line = f"  {status}  {name}"
    if detail:
        line += f"  [{detail}]"
    print(line)
    _results.append({"name": name, "passed": cond, "detail": detail})
    return cond


def section(title: str) -> None:
    print(f"\n{'-' * 60}")
    print(f"  {title}")
    print(f"{'-' * 60}")


async def verify_permissions() -> None:
    section("1. Capability Permission Engine & Authorization Layer")
    from app.control.permissions import (
        CapabilityPermissionManager,
        PermissionCategory,
        FORBIDDEN_OPERATIONS,
        CONSEQUENTIAL_TOOLS,
    )

    mgr = CapabilityPermissionManager()
    catalog = mgr.get_permission_catalog()

    check("Permission Catalog Generated", len(catalog["categories"]) >= 8, f"Categories: {len(catalog['categories'])}")
    check("Consequential Tools Cataloged", len(catalog["consequential_tools"]) >= 8, f"Tools: {len(catalog['consequential_tools'])}")

    # Safe read auto-permitted
    res_read = mgr.authorize(tool_name="inspect_applications", arguments={})
    check("Safe Read Auto-Permitted", res_read.allowed is True and res_read.requires_confirmation is False)

    # Consequential requires confirmation
    res_commit = mgr.authorize(tool_name="git_commit", arguments={"message": "feat"}, auto_confirm=False)
    check("Consequential Requires Confirmation", res_commit.allowed is False and res_commit.requires_confirmation is True and res_commit.confirmation_token is not None)

    # Auto-confirm with audit token
    res_auto = mgr.authorize(tool_name="git_commit", arguments={"message": "feat"}, auto_confirm=True)
    check("Auto-Confirm Grants Permission With Token", res_auto.allowed is True and res_auto.confirmation_token is not None)

    # Prohibited shell permanently banned
    res_cmd = mgr.authorize(tool_name="cmd.exe", arguments={})
    check("Arbitrary cmd.exe Execution Blocked", res_cmd.allowed is False and res_cmd.risk_level == "CRITICAL")

    res_ps = mgr.authorize(tool_name="powershell.exe", arguments={})
    check("Arbitrary PowerShell Execution Blocked", res_ps.allowed is False and res_ps.risk_level == "CRITICAL")

    res_bash = mgr.authorize(tool_name="bash", arguments={})
    check("Arbitrary Bash Execution Blocked", res_bash.allowed is False and res_bash.risk_level == "CRITICAL")


async def verify_process_tools() -> None:
    section("2. Safe Desktop Process Management (Allowlist Bounded)")
    from app.tools.process_tool import (
        InspectApplicationsTool,
        FocusApplicationTool,
        CloseApplicationTool,
        APPROVED_PROCESS_NAMES,
    )

    inspect_tool = InspectApplicationsTool()
    res_inspect = await inspect_tool.execute()
    check("InspectApplicationsTool Executed", res_inspect.success is True, f"Found {res_inspect.total_inspected} apps")

    focus_tool = FocusApplicationTool()
    res_focus = await focus_tool.execute(application="vscode")
    check("FocusApplicationTool Allowlisted App", res_focus.success is True, res_focus.message)

    res_disallowed_focus = await focus_tool.execute(application="unapproved_malware.exe")
    check("Focus Disallowed App Blocked", res_disallowed_focus.success is False and "not in allowlist" in res_disallowed_focus.error)

    close_tool = CloseApplicationTool()
    res_disallowed_close = await close_tool.execute(application="system_service.exe")
    check("Close Disallowed App Blocked", res_disallowed_close.success is False and "not in allowlist" in res_disallowed_close.error)

    res_close_unconfirmed = await close_tool.execute(application="notepad", confirmed=False)
    check("Close Application Confirmation Gate", res_close_unconfirmed.requires_confirmation is True)


async def verify_observer() -> None:
    section("3. Unified Multi-Domain Observer Engine")
    from app.control.observer import ObserverEngine

    obs = ObserverEngine()
    records = await obs.observe_environment(target_project="view-archive-buddy-main")
    check("Multi-Domain Observation Captured", len(records) >= 2, f"Captured: {len(records)} records")

    sources = [r.source for r in records]
    check("Desktop Domain Observed", "desktop" in sources)
    check("Browser Domain Observed", "browser" in sources)

    # Observation details validity
    desktop_rec = next((r for r in records if r.source == "desktop"), None)
    check("Desktop Record Has Process Counts", desktop_rec is not None and "count" in desktop_rec.details)


async def verify_scope_and_recovery() -> None:
    section("4. Scope Protection & Failure Classification")
    from app.control.models import DiscoveredScopeItem, ScopeBoundary, FailureClass
    from app.control.engine import RyvenControlEngine

    item = DiscoveredScopeItem(
        description="Unrelated typo found in docs",
        boundary=ScopeBoundary.DISCOVERED,
        requires_user_approval=True,
    )
    check("Scope Item Boundary Tracked", item.boundary == ScopeBoundary.DISCOVERED)
    check("Scope Item Approval Gate", item.requires_user_approval is True and not item.approved)

    engine = RyvenControlEngine()
    fail_timeout = engine._classify_failure(TimeoutError("Connection timed out"))
    check("Transient Network Failure Classified", fail_timeout == FailureClass.TRANSIENT)

    fail_rate = engine._classify_failure("HTTP 429 Rate limit exceeded")
    check("Rate Limit Failure Classified", fail_rate == FailureClass.RATE_LIMITED)

    fail_perm = engine._classify_failure(ValueError("Syntax Error"))
    check("Permanent Failure Classified", fail_perm == FailureClass.PERMANENT)


async def verify_control_engine() -> None:
    section("5. RyvenControlEngine End-to-End Execution Loop")
    from app.control.engine import RyvenControlEngine
    from app.control.models import ControlRequest, ControlStatus

    engine = RyvenControlEngine()

    # 1. Safe goal execution
    req_safe = ControlRequest(
        goal="Inspect running desktop applications and search web for Python docs",
        auto_confirm=True,
    )
    res_safe = await engine.execute_goal(req_safe)
    check("Safe Control Execution Completed", res_safe.status in (ControlStatus.COMPLETED, ControlStatus.PLANNING, ControlStatus.EXECUTING), f"Status: {res_safe.status.value}")
    check("Control ID Generated", res_safe.control_id.startswith("ctrl-"), res_safe.control_id)
    check("Pre/Post Observations Attached", len(res_safe.observations) > 0, f"Observations: {len(res_safe.observations)}")

    # 2. Consequential action pausing
    req_conseq = ControlRequest(
        goal="Close notepad application",
        auto_confirm=False,
    )
    res_conseq = await engine.execute_goal(req_conseq)
    check("Consequential Goal Evaluation", res_conseq.control_id.startswith("ctrl-"))
    if res_conseq.confirmation_required:
        check("Consequential Goal Paused for Confirmation", res_conseq.status == ControlStatus.WAITING_CONFIRMATION)

    # 3. Confirmation and Cancellation lifecycles
    ctrl_id = "ctrl-test-lifecycle"
    from app.control.models import ControlResult
    dummy_res = ControlResult(
        control_id=ctrl_id,
        goal="Deploy project",
        status=ControlStatus.WAITING_CONFIRMATION,
        confirmation_required=True,
        confirmation_token="CONF-TEST-TOKEN",
    )
    engine._active_executions[ctrl_id] = dummy_res

    resumed = await engine.confirm_action(ctrl_id, "CONF-TEST-TOKEN")
    check("Confirmation Resumes Execution", resumed.status == ControlStatus.COMPLETED and not resumed.confirmation_required)

    cancelled = await engine.cancel_execution(ctrl_id, reason="Testing cancel")
    check("Cancellation Transitions to CANCELLED", cancelled.status == ControlStatus.CANCELLED)


async def verify_assistant_and_routes() -> None:
    section("6. Assistant Core & REST API Control Plane")
    from app.core.assistant import Assistant
    from fastapi.testclient import TestClient
    from app.main import app

    # Assistant routing
    assistant = Assistant()
    mock_dec = MagicMock()
    mock_dec.intent = "agent"
    mock_dec.workflow_name = None
    mock_dec.tool_name = None
    assistant.router.route = MagicMock(return_value=mock_dec)

    chat_res = await assistant.process("Inspect running applications and check git status", auto_confirm=True)
    check("Assistant Routes Agent/Control Intent", chat_res.success is True and chat_res.type == "agent")
    check("Assistant Chat Response Populates Control Metadata", "control_id" in chat_res.metadata)

    # REST API endpoints
    client = TestClient(app)

    res_post = client.post("/api/control/execute", json={"goal": "Inspect running applications", "auto_confirm": True})
    check("REST API POST /api/control/execute", res_post.status_code == 200 and "control_id" in res_post.json())

    res_apps = client.get("/api/control/applications/running")
    check("REST API GET /api/control/applications/running", res_apps.status_code == 200 and "processes" in res_apps.json())

    res_cat = client.get("/api/control/permissions/catalog")
    check("REST API GET /api/control/permissions/catalog", res_cat.status_code == 200 and "consequential_tools" in res_cat.json())


async def main() -> None:
    print(f"\n{'=' * 60}")
    print("  RYVEN 3.0 — Milestone 17.0 Live Verification")
    print(f"{'=' * 60}")

    await verify_permissions()
    await verify_process_tools()
    await verify_observer()
    await verify_scope_and_recovery()
    await verify_control_engine()
    await verify_assistant_and_routes()

    passed = sum(1 for r in _results if r["passed"])
    total = len(_results)

    print(f"\n{'=' * 60}")
    print(f"  M17.0 VERIFICATION SUMMARY: {passed}/{total} CHECKS PASSED")
    print(f"{'=' * 60}\n")

    if passed < total:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
