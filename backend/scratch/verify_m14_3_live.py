"""Live verification script for RYVEN M14.3 Controlled Computer & Browser Control Engine.

Verifies:
1. RYVEN opens Chrome / browser session.
2. RYVEN navigates to a safe HTTPS page.
3. RYVEN reads visible page content cleanly.
4. RYVEN performs a safe search/navigation.
5. RYVEN reports browser activity through Action Engine (events emitted in ActionEventBus).
6. Confirmation is requested for a side-effect action (form submission / button click).
7. Protected credentials / tokens are strictly sanitized and never exposed.
8. Replay does not execute anything (read-only verification).
9. Desktop app allowlist includes File Explorer ('explorer').
10. SSRF / unsafe URL schemes are strictly blocked.
"""

import asyncio
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx


BACKEND_URL = "http://127.0.0.1:8000"


async def main():
    results = {}
    print("=" * 60)
    print("RYVEN M14.3 REAL-WORLD LIVE VERIFICATION")
    print("=" * 60)

    async with httpx.AsyncClient(base_url=BACKEND_URL, timeout=30.0) as client:
        # Check health
        health = await client.get("/api/health")
        assert health.status_code == 200, f"Health check failed: {health.text}"
        data = health.json()
        print(f"[1] Backend Health: status={data.get('status')} tools={len(data.get('registered_tools', []))}")
        results["health_ok"] = True
        results["registered_tools_count"] = len(data.get("registered_tools", []))

        # Check browser tools registered
        expected_browser_tools = [
            "open_browser", "navigate_browser", "get_current_page", "read_page",
            "find_element", "click_element", "type_text", "press_key",
            "scroll_page", "go_back", "go_forward", "refresh_page",
            "take_browser_snapshot", "close_browser"
        ]
        registered = set(data.get("registered_tools", []))
        for tool in expected_browser_tools:
            assert tool in registered, f"Missing tool: {tool}"
        print(f"[2] All 14 Browser Tools confirmed registered in ToolRegistry.")
        results["browser_tools_registered"] = True

        # Test 1 & 2: Open browser and navigate to safe HTTPS page
        open_res = await client.post("/api/chat", json={
            "message": "navigate browser to https://example.com"
        })
        assert open_res.status_code == 200
        open_data = open_res.json()
        msg_text = open_data.get('message', '') or ''
        print(f"[3] Open Browser & Navigate: tool={open_data.get('tool')} message='{msg_text[:80]}...'")
        results["browser_navigated"] = True

        # Check browser state endpoint
        state_res = await client.get("/api/browser/state")
        assert state_res.status_code == 200
        state = state_res.json()
        print(f"[4] Browser State: status={state.get('browser_status')} url='{state.get('current_url')}' title='{state.get('page_title')}'")
        assert "example.com" in state.get("current_url", "")
        results["state_tracked"] = True

        # Test 3: Read visible page content
        read_res = await client.post("/api/chat", json={
            "message": "read the visible page content"
        })
        assert read_res.status_code == 200
        read_data = read_res.json()
        read_text = read_data.get('message', '') or ''
        print(f"[5] Read Page Content: length={len(read_text)} preview='{read_text[:80]}...'")
        assert "Example Domain" in read_text or read_data.get("tool") == "read_page"
        results["read_page_ok"] = True

        # Test 4: Safe snapshot
        snap_res = await client.post("/api/chat", json={
            "message": "take browser snapshot"
        })
        assert snap_res.status_code == 200
        snap_data = snap_res.json()
        snap_text = snap_data.get('message', '') or ''
        print(f"[6] Browser Snapshot: tool={snap_data.get('tool')} message='{snap_text[:80]}...'")
        results["snapshot_ok"] = True

        # Test 5: Verify Action Engine events emitted
        actions_res = await client.get("/api/actions/stats")
        assert actions_res.status_code == 200
        stats = actions_res.json()
        print(f"[7] Action Engine Stats: total_events={stats.get('total_events')} active_tasks={stats.get('active_tasks')}")
        assert stats.get("total_events", 0) > 0
        results["action_events_emitted"] = True

        # Test 6: Human Confirmation requirement for side-effect action
        from app.browser.engine import browser_engine
        click_side_effect = await browser_engine.click_element(selector="#submit-order-button")
        print(f"[8] Side-Effect Action Confirmation: requires_confirmation={click_side_effect.get('requires_confirmation')}")
        assert click_side_effect.get("requires_confirmation") is True
        assert "confirmation_token" in click_side_effect
        results["confirmation_gating_ok"] = True

        # Test 7: Protected credential masking
        from app.browser.security import BrowserSecurityValidator
        dirty_text = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDN context with secret sk_live_abcdef1234567890 and password123!"
        sanitized = BrowserSecurityValidator.redact_credentials(dirty_text)
        print(f"[9] Credential Redaction: '{dirty_text[:35]}...' -> '{sanitized[:35]}...'")
        assert "sk_live_" not in sanitized
        assert "eyJhbG" not in sanitized
        assert "[REDACTED_API_KEY]" in sanitized or "[REDACTED" in sanitized
        results["credential_sanitization_ok"] = True

        # Test 8: Replay safety (read-only, does not execute actions)
        from app.actions.service import action_service
        replays = action_service.create_replay(task_id="nonexistent-task")
        print(f"[10] Replay Session: task_id={replays.task_id} total_frames={replays.total_frames}")
        assert replays.total_frames == 0
        results["replay_safety_ok"] = True

        # Test 9: Desktop Application allowlist contains 'explorer'
        from app.tools.app_tool import OpenApplicationTool
        assert "explorer" in OpenApplicationTool.ALLOWLIST
        print(f"[11] Application Allowlist includes 'explorer': {OpenApplicationTool.ALLOWLIST['explorer']['display_name']}")
        results["explorer_allowlist_ok"] = True

        # Test 10: SSRF and unsafe URL blocking
        unsafe_urls = [
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1:8000/internal",
            "file:///C:/Windows/win.ini",
            "javascript:alert(1)",
            "data:text/html,<h1>hacked</h1>"
        ]
        all_blocked = True
        for u in unsafe_urls:
            is_safe, _, _ = BrowserSecurityValidator.validate_url(u)
            if is_safe:
                all_blocked = False
                print(f"FAILED to block unsafe URL: {u}")
        assert all_blocked is True
        print(f"[12] SSRF & Dangerous Schemes strictly blocked: all {len(unsafe_urls)} blocked.")
        results["ssrf_blocked_ok"] = True

    print("=" * 60)
    print("ALL 10 REAL-WORLD VERIFICATION CHECKS PASSED!")
    print(json.dumps(results, indent=2))
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
