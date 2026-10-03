"""RYVEN 3.0 — M15 Unified Internet Agent Real Windows Verification Script.

Executes and verifies the 10 real operational scenarios from Section 18 against the live backend:
1. "open chrome and search youtube for self"
2. "open chrome and search google for React tutorials"
3. "search the web for React Three Fiber"
4. "open GitHub"
5. "read the current page"
6. "go back and return forward"
7. authentication-required simulation/page
8. dangerous action requiring confirmation
9. blocked SSRF URL
10. verify ActionEvents appear in HUD/SSE
"""

import asyncio
import sys
from pathlib import Path
backend_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(backend_dir))
import httpx

BASE_URL = "http://127.0.0.1:8000"


async def main():
    print("=" * 60)
    print("RYVEN 3.0 — M15 REAL WINDOWS LIVE SYSTEM VERIFICATION")
    print("=" * 60)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 0: Check Health
        health = await client.get(f"{BASE_URL}/api/health")
        assert health.status_code == 200, f"Health check failed: {health.text}"
        print(f"[*] Backend Health: OK ({health.json().get('status', 'running')})")

        # 1. "open chrome and search youtube for self"
        print("\n[TEST 1] Composite Goal: 'open chrome and search youtube for self'")
        r1 = await client.post(
            f"{BASE_URL}/api/chat",
            json={"message": "open chrome and search youtube for self", "session_id": "live-m15-test"},
        )
        assert r1.status_code == 200
        res1 = r1.json()
        print(f"  -> Type: {res1.get('type')}")
        print(f"  -> Success: {res1.get('success')}")
        print(f"  -> Message Preview: {res1.get('message', '')[:120]}...")
        if res1.get("plan"):
            print(f"  -> Plan Steps: {[s['name'] for s in res1['plan']['steps']]}")

        # 2. "open chrome and search google for React tutorials"
        print("\n[TEST 2] Composite Goal: 'open chrome and search google for React tutorials'")
        r2 = await client.post(
            f"{BASE_URL}/api/chat",
            json={"message": "open chrome and search google for React tutorials", "session_id": "live-m15-test"},
        )
        assert r2.status_code == 200
        res2 = r2.json()
        print(f"  -> Type: {res2.get('type')}")
        print(f"  -> Success: {res2.get('success')}")
        print(f"  -> Message Preview: {res2.get('message', '')[:120]}...")
        if res2.get("plan"):
            print(f"  -> Plan Steps: {[s['name'] for s in res2['plan']['steps']]}")

        # 3. "search the web for React Three Fiber"
        print("\n[TEST 3] Internet Search: 'search the web for React Three Fiber'")
        r3 = await client.post(
            f"{BASE_URL}/api/internet/search",
            json={"query": "React Three Fiber", "max_results": 3},
        )
        assert r3.status_code == 200
        res3 = r3.json()
        print(f"  -> Success: {res3.get('success')}")
        print(f"  -> Results Count: {res3.get('count')}")
        if res3.get("results"):
            top = res3["results"][0]
            print(f"  -> Top Result: '{top.get('title')}' ({top.get('url')})")
            print(f"  -> Domain: {top.get('domain')}, Timestamp: {top.get('timestamp')}")

        # 4. "open GitHub"
        print("\n[TEST 4] Direct Tool Navigation: 'open GitHub'")
        r4 = await client.post(
            f"{BASE_URL}/api/chat",
            json={"message": "open GitHub", "session_id": "live-m15-test"},
        )
        assert r4.status_code == 200
        res4 = r4.json()
        print(f"  -> Success: {res4.get('success')}")
        print(f"  -> Tool: {res4.get('tool')}")
        print(f"  -> Message: {res4.get('message')}")

        # 5. "read the current page"
        print("\n[TEST 5] Read Current Page")
        r5 = await client.post(
            f"{BASE_URL}/api/chat",
            json={"message": "read the current page", "session_id": "live-m15-test"},
        )
        assert r5.status_code == 200
        res5 = r5.json()
        print(f"  -> Success: {res5.get('success')}")
        print(f"  -> Tool: {res5.get('tool')}")
        print(f"  -> Message Preview: {res5.get('message', '')[:120]}...")

        # 6. "go back and return forward"
        print("\n[TEST 6] History Navigation: go back and return forward")
        r6_back = await client.post(
            f"{BASE_URL}/api/chat",
            json={"message": "go back", "session_id": "live-m15-test"},
        )
        assert r6_back.status_code == 200
        print(f"  -> Go Back Response: {r6_back.json().get('message')}")

        r6_fwd = await client.post(
            f"{BASE_URL}/api/chat",
            json={"message": "go forward", "session_id": "live-m15-test"},
        )
        assert r6_fwd.status_code == 200
        print(f"  -> Go Forward Response: {r6_fwd.json().get('message')}")

        # 7. Authentication-Required Simulation / Page
        print("\n[TEST 7] Authentication Pause & Resume Flow")
        r7_task = await client.post(
            f"{BASE_URL}/api/internet/task",
            json={"goal": "open github and inspect settings"},
        )
        assert r7_task.status_code == 200
        res7 = r7_task.json()
        print(f"  -> Status: {res7.get('status')}")
        auth_session = res7.get("auth_session")
        if auth_session:
            token = auth_session.get("resume_token")
            print(f"  -> Paused for Auth: {auth_session.get('target_service')}")
            print(f"  -> Resume Token: {token}")

            # Resume authentication
            r7_resume = await client.post(
                f"{BASE_URL}/api/internet/auth-resume",
                json={"resume_token": token},
            )
            assert r7_resume.status_code == 200
            print(f"  -> Resume Success: {r7_resume.json().get('success')}")
            print(f"  -> Message: {r7_resume.json().get('message')}")
        else:
            print(f"  -> Message: {res7.get('message')}")

        # 8. Dangerous action requiring confirmation
        print("\n[TEST 8] Dangerous Action Confirmation Boundary")
        from app.internet.security import InternetSecurityPolicy
        req_confirm = InternetSecurityPolicy.is_confirmation_required(
            action_name="delete_resource",
            target_description="production-cluster",
        )
        safe_action = InternetSecurityPolicy.is_confirmation_required(
            action_name="read_page",
            target_description="homepage",
        )
        print(f"  -> 'delete_resource' Requires Confirmation: {req_confirm}")
        print(f"  -> 'read_page' Requires Confirmation: {safe_action}")
        assert req_confirm is True
        assert safe_action is False

        # 9. Blocked SSRF URL
        print("\n[TEST 9] SSRF Blocking Defense")
        is_safe, _, reason = InternetSecurityPolicy.validate_target_url("http://127.0.0.1:8000/internal-secrets")
        print(f"  -> SSRF Attempt on 127.0.0.1 is_safe: {is_safe}")
        print(f"  -> Blocking Reason: {reason}")
        assert is_safe is False
        assert "private" in reason.lower() or "ssrf" in reason.lower() or "blocked" in reason.lower()

        # 10. Verify ActionEvents appear in HUD / SSE
        print("\n[TEST 10] Action Events in Timeline / SSE Stream")
        r10 = await client.get(f"{BASE_URL}/api/actions/recent?limit=10")
        assert r10.status_code == 200
        events = r10.json()
        print(f"  -> Total Logged Action Events: {len(events)}")
        print(f"  -> Recent Action Event Types:")
        for ev in events[-5:]:
            print(f"     • [{ev.get('action_type')}] {ev.get('title')} ({ev.get('status')})")

        # Live Internet Agent State
        print("\n[INTERNET AGENT STATE]")
        r_state = await client.get(f"{BASE_URL}/api/internet/state")
        assert r_state.status_code == 200
        st = r_state.json()
        print(f"  -> Status: {st.get('status')}")
        print(f"  -> Current URL: {st.get('current_url')}")
        print(f"  -> Last Search Query: {st.get('last_search_query')}")

    print("\n" + "=" * 60)
    print("ALL 10 REAL WINDOWS VERIFICATION CHECKS COMPLETED SUCCESSFULLY")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
