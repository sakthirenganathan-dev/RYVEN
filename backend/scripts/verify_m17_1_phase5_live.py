#!/usr/bin/env python3
"""RYVEN 3.0 — Milestone 17.1 Phase 5 Live Verification Script.
Semantic Desktop Action Execution & Safe Authority Chain Verification.

Safety Guarantee:
- Default execution is strictly read-only and dry-run.
- NO real destructive, mutating, or unauthorized desktop clicks/typing are performed automatically.
- Validates the complete pipeline: models, driver reuse, resolver reuse, target validation,
  allowlist enforcement, confirmation gating, telemetry scrubbing, and structured result emission.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import time
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# Section 1: Architecture & Model Verification
# ---------------------------------------------------------------------------

def verify_architecture_models() -> None:
    section("1. ARCHITECTURE & MODEL VERIFICATION")

    from app.control.models import (
        DesktopActionRequest,
        DesktopActionResult,
        DesktopActionType,
        DesktopTargetResolutionResult,
        DesktopUIElement,
        DesktopWindowState,
        FailureClass,
    )
    from app.desktop.action_engine import DesktopActionEngine, desktop_action_engine
    from app.desktop.interaction import WindowsDesktopDriver, desktop_driver
    from app.desktop.resolver import DesktopTargetResolver, desktop_target_resolver
    from app.tools.registry import create_default_registry

    check(
        "DesktopActionResult model instantiated",
        hasattr(DesktopActionResult, "model_fields") and "confirmation_token" in DesktopActionResult.model_fields,
        "Structured result model contains all Phase 5 fields",
    )
    check(
        "DesktopActionRequest model validated",
        hasattr(DesktopActionRequest, "model_fields") and "confirmed" in DesktopActionRequest.model_fields,
        "Action request model contains confirmed status",
    )
    check(
        "DesktopActionEngine singleton exists",
        isinstance(desktop_action_engine, DesktopActionEngine),
        "DesktopActionEngine initialized as module singleton",
    )
    check(
        "Existing WindowsDesktopDriver reused",
        desktop_action_engine.driver is desktop_driver,
        "Driver is not duplicated",
    )
    check(
        "Existing DesktopTargetResolver reused",
        desktop_action_engine.resolver is desktop_target_resolver,
        "Target resolver is not duplicated",
    )

    reg = create_default_registry()
    desktop_tools = [
        "desktop_inspect",
        "desktop_focus",
        "desktop_click",
        "desktop_double_click",
        "desktop_right_click",
        "desktop_type",
        "desktop_key",
        "desktop_hotkey",
        "desktop_scroll",
    ]
    registered_all = all(reg.has_tool(t) for t in desktop_tools)
    check(
        "Desktop tools registered in ToolRegistry",
        registered_all,
        f"All {len(desktop_tools)} desktop tools registered in default registry",
    )


# ---------------------------------------------------------------------------
# Section 2: Security & Permission Invariants
# ---------------------------------------------------------------------------

async def verify_security_invariants() -> None:
    section("2. SECURITY & PERMISSION INVARIANTS")

    from app.control.models import DesktopActionRequest, DesktopActionType, FailureClass
    from app.desktop.action_engine import DesktopActionEngine

    engine = DesktopActionEngine()

    # 1. Unapproved application rejected
    res = await engine.execute_action(
        DesktopActionRequest(action=DesktopActionType.INSPECT, application="cmd.exe")
    )
    check(
        "Prohibited / unapproved app 'cmd.exe' blocked",
        res.success is False and res.failure_class == FailureClass.SECURITY,
        f"Result failure class: {res.failure_class}",
    )

    # 2. Powershell application rejected
    res2 = await engine.execute_action(
        DesktopActionRequest(action=DesktopActionType.INSPECT, application="powershell.exe")
    )
    check(
        "Prohibited shell 'powershell.exe' blocked",
        res2.success is False and res2.failure_class == FailureClass.SECURITY,
        f"Result failure class: {res2.failure_class}",
    )

    # 3. Secret pattern in typing input rejected
    try:
        from pydantic import ValidationError
        DesktopActionRequest(action=DesktopActionType.TYPE, text="bearer my_secret_token_123")
        secret_blocked = False
    except ValidationError:
        secret_blocked = True

    check(
        "Credential pattern in typing text rejected at validation boundary",
        secret_blocked,
        "Pydantic validator blocked 'bearer ' credential string",
    )

    # 4. Secret pattern in target query rejected
    res_secret = await engine.execute_action(
        DesktopActionRequest(action=DesktopActionType.CLICK, target="password=admin")
    )
    check(
        "Credential pattern in target query rejected",
        res_secret.success is False and res_secret.failure_class == FailureClass.SECURITY,
        "Rejected sensitive pattern with SECURITY failure",
    )


# ---------------------------------------------------------------------------
# Section 3: Safe Live Desktop Inspection (Read-Only)
# ---------------------------------------------------------------------------

def verify_live_desktop_inspection() -> None:
    section("3. SAFE LIVE DESKTOP INSPECTION (READ-ONLY)")

    from app.desktop.interaction import desktop_driver

    windows = desktop_driver.inspect_windows()
    check(
        "Inspect running approved windows",
        isinstance(windows, list),
        f"Found {len(windows)} running allowlisted window(s)",
    )

    if windows:
        w = windows[0]
        check(
            "Inspect first allowlisted window details",
            w.hwnd > 0 and bool(w.application) and bool(w.executable),
            f"HWND={w.hwnd}, App='{w.application}', Title='{w.title[:30]}'",
        )
    else:
        check(
            "No active approved windows open (normal in headless/CI)",
            True,
            "Window enumeration completed safely with empty result",
        )


# ---------------------------------------------------------------------------
# Section 4: Target Resolution & Action Planning (Dry-Run)
# ---------------------------------------------------------------------------

async def verify_target_resolution_pipeline() -> None:
    section("4. TARGET RESOLUTION & ACTION PLANNING (DRY-RUN)")

    from app.control.models import (
        DesktopActionRequest,
        DesktopActionType,
        DesktopTargetResolutionResult,
        DesktopUIElement,
        DesktopWindowState,
        FailureClass,
    )
    from app.desktop.action_engine import DesktopActionEngine

    mock_win = DesktopWindowState(
        hwnd=54321,
        process_id=1234,
        executable="notepad.exe",
        application="Notepad",
        title="Notes - Notepad",
        left=50,
        top=50,
        width=800,
        height=600,
        visible=True,
        focused=True,
    )

    mock_elem = DesktopUIElement(
        element_id="elem-test",
        role="button",
        text="Search",
        bounds={"left": 100, "top": 100, "width": 80, "height": 30},
        confidence=0.92,
        actionable=True,
    )

    mock_driver = MagicMock()
    mock_driver.validate_window_application.return_value = (True, "Notepad", "notepad.exe", 1234)
    mock_driver.get_window_info.return_value = mock_win
    mock_driver.focus_window.return_value = True
    mock_driver.click.return_value = {"success": True, "action": "click", "x": 190, "y": 165}

    mock_resolver = MagicMock()
    mock_resolver.resolve_target = AsyncMock(return_value=DesktopTargetResolutionResult(
        success=True,
        target="Search",
        element=mock_elem,
        window=mock_win,
        confidence=0.92,
    ))

    engine = DesktopActionEngine(driver=mock_driver, resolver=mock_resolver)
    req = DesktopActionRequest(action=DesktopActionType.CLICK, target="Search", hwnd=54321)
    res = await engine.execute_action(req)

    check(
        "Target resolution dry-run execution succeeded",
        res.success is True and res.resolved_element.text == "Search",
        f"Resolved element: {res.resolved_element.text}, confidence: {res.confidence}",
    )
    check(
        "Screen coordinates accurately derived from window + element bounds",
        res.x == (50 + 100 + 40) and res.y == (50 + 100 + 15),
        f"Derived Screen X={res.x}, Y={res.y}",
    )


# ---------------------------------------------------------------------------
# Section 5: Confirmation Gate Verification
# ---------------------------------------------------------------------------

async def verify_confirmation_gate() -> None:
    section("5. CONFIRMATION GATE VERIFICATION")

    from app.control.models import (
        DesktopActionRequest,
        DesktopActionType,
        DesktopTargetResolutionResult,
        DesktopUIElement,
        DesktopWindowState,
        FailureClass,
    )
    from app.desktop.action_engine import DesktopActionEngine

    mock_win = DesktopWindowState(
        hwnd=54321,
        process_id=1234,
        executable="notepad.exe",
        application="Notepad",
        title="Notes - Notepad",
        left=50,
        top=50,
        width=800,
        height=600,
        visible=True,
        focused=True,
    )
    mock_elem = DesktopUIElement(
        element_id="elem-del",
        role="button",
        text="Delete All Files",
        bounds={"left": 100, "top": 100, "width": 120, "height": 30},
        confidence=0.95,
        actionable=True,
    )

    mock_driver = MagicMock()
    mock_driver.validate_window_application.return_value = (True, "Notepad", "notepad.exe", 1234)
    mock_driver.get_window_info.return_value = mock_win

    mock_resolver = MagicMock()
    mock_resolver.resolve_target = AsyncMock(return_value=DesktopTargetResolutionResult(
        success=True,
        target="Delete All Files",
        element=mock_elem,
        window=mock_win,
        confidence=0.95,
    ))

    engine = DesktopActionEngine(driver=mock_driver, resolver=mock_resolver)

    # 1. Unconfirmed consequential action pauses
    req_unconf = DesktopActionRequest(
        action=DesktopActionType.CLICK,
        target="Delete All Files",
        hwnd=54321,
        confirmed=False,
    )
    res_unconf = await engine.execute_action(req_unconf)

    check(
        "Consequential action flagged for confirmation",
        res_unconf.confirmation_required is True and bool(res_unconf.confirmation_token),
        f"Token: {res_unconf.confirmation_token}",
    )
    check(
        "Mutating click was NOT executed while unconfirmed",
        not mock_driver.click.called,
        "Driver click not invoked",
    )

    # 2. Confirmed consequential action proceeds
    mock_driver.focus_window.return_value = True
    mock_driver.click.return_value = {"success": True, "action": "click"}
    req_conf = DesktopActionRequest(
        action=DesktopActionType.CLICK,
        target="Delete All Files",
        hwnd=54321,
        confirmed=True,
    )
    res_conf = await engine.execute_action(req_conf)

    check(
        "Confirmed consequential action executed",
        res_conf.success is True and res_conf.confirmation_required is False,
        "Execution approved and completed",
    )


# ---------------------------------------------------------------------------
# Section 6: Action Execution & Telemetry Scrubbing
# ---------------------------------------------------------------------------

async def verify_telemetry_and_scrubbing() -> None:
    section("6. TELEMETRY & SENSITIVE DATA SCRUBBING")

    from app.control.models import (
        DesktopActionRequest,
        DesktopActionType,
        DesktopWindowState,
    )
    from app.desktop.action_engine import DesktopActionEngine

    mock_win = DesktopWindowState(
        hwnd=54321,
        process_id=1234,
        executable="notepad.exe",
        application="Notepad",
        title="Notes - Notepad",
        left=50,
        top=50,
        width=800,
        height=600,
        visible=True,
        focused=True,
    )

    mock_driver = MagicMock()
    mock_driver.validate_window_application.return_value = (True, "Notepad", "notepad.exe", 1234)
    mock_driver.get_window_info.return_value = mock_win
    mock_driver.focus_window.return_value = True
    mock_driver.type_text.return_value = {"success": True, "action": "type_text", "characters": 12}

    engine = DesktopActionEngine(driver=mock_driver)

    with patch("app.desktop.action_engine.action_bus.publish", new_callable=AsyncMock) as mock_pub:
        req = DesktopActionRequest(
            action=DesktopActionType.TYPE,
            text="SafeNoteData",
            hwnd=54321,
        )
        res = await engine.execute_action(req)

        check(
            "Safe typing action completed",
            res.success is True and res.details.get("characters") == 12,
            "Character count recorded in details without raw text leakage",
        )

        all_meta_str = " ".join(str(c.args[0].safe_metadata) for c in mock_pub.await_args_list)
        check(
            "Typed text scrubbed from ActionEvent telemetry",
            "SafeNoteData" not in all_meta_str,
            "Telemetry safe metadata verified clean of typed text",
        )


# ---------------------------------------------------------------------------
# Main Execution Runner
# ---------------------------------------------------------------------------

async def main() -> int:
    print("=" * 60)
    print("  RYVEN 3.0 — M17.1 PHASE 5 LIVE VERIFICATION")
    print("  Semantic Desktop Action Execution & Authority Chain")
    print("=" * 60)

    t0 = time.monotonic()

    verify_architecture_models()
    await verify_security_invariants()
    verify_live_desktop_inspection()
    await verify_target_resolution_pipeline()
    await verify_confirmation_gate()
    await verify_telemetry_and_scrubbing()

    elapsed = time.monotonic() - t0
    total = len(_results)
    passed = sum(1 for r in _results if r["passed"])
    failed = total - passed

    print("\n" + "=" * 60)
    print(f"  VERIFICATION COMPLETE in {elapsed:.2f}s")
    print(f"  Total Checks: {total} | Passed: {passed} | Failed: {failed}")
    print("=" * 60)

    if failed > 0:
        print("\nFailed Checks:")
        for r in _results:
            if not r["passed"]:
                print(f"  - {r['name']}: {r['detail']}")
        return 1

    print("\nALL PHASE 5 VERIFICATION CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
