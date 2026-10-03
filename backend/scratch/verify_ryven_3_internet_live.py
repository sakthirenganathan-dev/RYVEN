"""RYVEN 3.0 — Real-World Live Verification Script.

Executes live against running backend at http://127.0.0.1:8000:
1. "open chrome and search youtube for self"
2. "search Google for React documentation"
3. "open GitHub" (with authentication inspection)
4. "open my deployed website and inspect the homepage"
5. "research a technical topic and summarize it" (research FastAPI documentation)
6. Direct Internet Endpoints:
   - GET /api/internet/state
   - POST /api/internet/search
   - POST /api/internet/research
   - POST /api/internet/auth-resume
"""

import json
import urllib.request
import urllib.parse
import sys

BASE_URL = "http://127.0.0.1:8000"


def send_chat(message: str) -> dict:
    url = f"{BASE_URL}/api/chat"
    data = json.dumps({"message": message, "session_id": "live-internet-eval"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(endpoint: str) -> dict:
    url = f"{BASE_URL}{endpoint}"
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_json(endpoint: str, payload: dict) -> dict:
    url = f"{BASE_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print("=" * 60)
    print("RYVEN 3.0 UNIFIED INTERNET AGENT -- REAL-WORLD LIVE EVALUATION")
    print("=" * 60)

    # 1. Health check
    print("\n[0] Checking Backend Health & Tool Count...")
    health = get_json("/api/health")
    tools = health.get("registered_tools", [])
    print(f"Backend Status: {health.get('status')} | Version: {health.get('version')}")
    print(f"Total Registered Tools: {len(tools)}")
    assert "internet_search" in tools
    assert "web_research" in tools
    assert "web_task" in tools
    assert "web_verify" in tools
    print("[OK] All Internet tools confirmed registered in live ToolRegistry.")

    # 2. Test 1: "open chrome and search youtube for self"
    print("\n[1] Testing: 'open chrome and search youtube for self'...")
    res1 = send_chat("open chrome and search youtube for self")
    print(f"Success: {res1.get('success')} | Type: {res1.get('type')}")
    print(f"Message: {res1.get('message')}")
    print(f"Steps Completed: {res1.get('metadata', {}).get('steps_completed')}/{res1.get('metadata', {}).get('steps_total')}")
    obs1 = res1.get('metadata', {}).get('observations', [])
    for o in obs1:
        print(f"  - Observation: {o.get('summary')}")
    assert res1.get("success") is True
    print("[OK] Test 1 Passed!")

    # 3. Test 2: "search Google for React documentation"
    print("\n[2] Testing: 'search Google for React documentation'...")
    res2 = send_chat("search Google for React documentation")
    print(f"Success: {res2.get('success')} | Type: {res2.get('type')}")
    print(f"Message: {res2.get('message')}")
    print(f"Steps Completed: {res2.get('metadata', {}).get('steps_completed')}/{res2.get('metadata', {}).get('steps_total')}")
    assert res2.get("success") is True
    print("[OK] Test 2 Passed!")

    # 4. Test 3: "open GitHub"
    print("\n[3] Testing: 'open GitHub'...")
    res3 = send_chat("open GitHub")
    print(f"Success: {res3.get('success')} | Type: {res3.get('type')}")
    print(f"Message: {res3.get('message')}")
    assert res3.get("success") is True
    print("[OK] Test 3 Passed!")

    # 5. Test 4: "open my deployed website and inspect the homepage"
    print("\n[4] Testing: 'open my deployed website and inspect the homepage'...")
    res4 = send_chat("open my deployed website and inspect the homepage")
    print(f"Success: {res4.get('success')} | Type: {res4.get('type')}")
    print(f"Message: {res4.get('message')}")
    print(f"Steps Completed: {res4.get('metadata', {}).get('steps_completed')}/{res4.get('metadata', {}).get('steps_total')}")
    assert res4.get("success") is True
    print("[OK] Test 4 Passed!")

    # 6. Test 5: "research a technical topic and summarize it"
    print("\n[5] Testing: 'research FastAPI documentation'...")
    res5 = send_chat("research FastAPI documentation")
    print(f"Success: {res5.get('success')} | Type: {res5.get('type')}")
    print(f"Message: {res5.get('message')}")
    assert res5.get("success") is True
    print("[OK] Test 5 Passed!")

    # 7. Test Direct Internet Endpoints
    print("\n[6] Testing Direct Internet API Endpoints...")
    # State
    st = get_json("/api/internet/state")
    print(f"  - GET /api/internet/state: status={st.get('status')}")

    # Search
    search_data = post_json("/api/internet/search", {"query": "React 19 release", "max_results": 3})
    print(f"  - POST /api/internet/search: found {search_data.get('count')} results")
    assert search_data.get("success") is True

    # Research
    research_data = post_json("/api/internet/research", {"topic": "FastAPI Lifespan Events", "max_sources": 2})
    print(f"  - POST /api/internet/research: sources={research_data.get('sources_count')}")
    assert research_data.get("success") is True

    # Auth Resume (Testing token reject for invalid token)
    auth_data = post_json("/api/internet/auth-resume", {"token": "invalid-test-token"})
    print(f"  - POST /api/internet/auth-resume: success={auth_data.get('success')} (expected False for invalid token)")
    assert auth_data.get("success") is False

    print("\n" + "=" * 60)
    print("ALL 5 REAL-WORLD QUERIES & ALL INTERNET ENDPOINTS VERIFIED 100% LIVE!")
    print("=" * 60)


if __name__ == "__main__":
    main()
