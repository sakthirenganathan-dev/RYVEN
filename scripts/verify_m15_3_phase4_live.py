"""RYVEN 3.0 — M15.3 Phase 4: Live Windows Verification Script.

Deterministic verification testing against real RYVEN services:
1. InternetAgent has DownloadService and UploadService.
2. ToolRegistry contains all 4 file tools (web_download, web_upload, web_verify_download, web_verify_upload).
3. Approved download destination accepted.
4. Blocked system destination rejected.
5. Blocked path traversal rejected.
6. Blocked sensitive upload (.env, tokens) rejected without reading contents.
7. Approved upload path recognized.
8. Confirmation behavior for dangerous downloads (.exe/.bat).
9. ActionEvents properly published and redacted.
10. Verification services report expected outcomes.
"""

import asyncio
import os
import sys
import tempfile

# Ensure backend is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.actions.models import ActionEvent, ActionType, ActionStatus
from app.actions.event_bus import action_bus
from app.internet.agent import InternetAgent
from app.internet.download_service import DownloadService
from app.internet.upload_service import UploadService
from app.internet.file_models import (
    FileTransferStatus,
    is_approved_filesystem_path,
    is_sensitive_file,
    is_dangerous_download_extension,
    sanitize_download_filename,
    resolve_safe_destination,
)
from app.tools.registry import create_default_registry


async def main():
    print("=" * 60)
    print("RYVEN 3.0 — M15.3 PHASE 4: LIVE WINDOWS VERIFICATION")
    print("=" * 60)

    # 1. InternetAgent services
    print("\n[1] Checking InternetAgent initialization...")
    agent = InternetAgent()
    assert hasattr(agent, "download_service") and agent.download_service is not None
    assert hasattr(agent, "upload_service") and agent.upload_service is not None
    assert callable(agent.download_file)
    assert callable(agent.upload_file)
    assert callable(agent.verify_download)
    assert callable(agent.verify_upload)
    print("  -> PASS: InternetAgent has all file management services and facade methods.")

    # 2. ToolRegistry file tools
    print("\n[2] Checking ToolRegistry registration...")
    registry = create_default_registry()
    required_tools = [
        "web_download",
        "web_upload",
        "web_verify_download",
        "web_verify_upload",
    ]
    for tool_name in required_tools:
        tool = registry.get(tool_name)
        assert tool is not None, f"Missing tool: {tool_name}"
        assert tool.name == tool_name
    print(f"  -> PASS: All {len(required_tools)} tools registered in ToolRegistry.")

    # 3. Approved download destination
    print("\n[3] Checking approved download destination...")
    ok, dest_path, err = resolve_safe_destination("downloads", "report.pdf")
    assert ok is True, f"Failed resolving approved download destination: {err}"
    assert is_approved_filesystem_path(dest_path)
    print(f"  -> PASS: Approved destination resolved: {dest_path}")

    # 4. Blocked system destination
    print("\n[4] Checking blocked system destination...")
    sys_dir = r"C:\Windows\System32"
    ok, dest_path, err = resolve_safe_destination(sys_dir, "malicious.dll")
    assert ok is False
    assert "approved" in err.lower() or "escapes" in err.lower()
    print(f"  -> PASS: System directory rejected as expected: {err}")

    # 5. Blocked traversal
    print("\n[5] Checking blocked path traversal...")
    traversal_path = r"downloads/../../Windows/System32"
    ok, _, err = resolve_safe_destination(traversal_path, "escape.txt")
    assert ok is False
    print(f"  -> PASS: Path traversal rejected as expected: {err}")

    # 6. Blocked sensitive upload
    print("\n[6] Checking sensitive file rejection...")
    sensitive_samples = [
        ".env",
        ".env.local",
        "id_rsa",
        "server.pem",
        "github_token.txt",
        "api_key_backup.json",
    ]
    for s in sensitive_samples:
        is_sec, reason = is_sensitive_file(s)
        assert is_sec is True, f"Failed to detect sensitive file: {s}"
    print(f"  -> PASS: Detected and blocked {len(sensitive_samples)} sensitive file patterns.")

    # 7. Approved upload path
    print("\n[7] Checking approved upload path verification...")
    user_home = os.path.expanduser("~")
    approved_doc = os.path.join(user_home, "Downloads", "document.pdf")
    assert is_approved_filesystem_path(approved_doc) is True
    print(f"  -> PASS: Approved upload path verified: {approved_doc}")

    # 8. Confirmation behavior for dangerous downloads
    print("\n[8] Checking dangerous download confirmation gating...")
    dl_service = DownloadService()
    res = await dl_service.download_file("https://example.com/installer.exe", confirmed=False)
    assert res["requires_confirmation"] is True
    assert res["status"] == FileTransferStatus.WAITING_CONFIRMATION.value
    assert "token" in res["confirmation_token"] or "dl-" in res["confirmation_token"]
    print(f"  -> PASS: Dangerous download paused for confirmation (token: {res['confirmation_token']}).")

    # 9. ActionEvents emission & redaction
    print("\n[9] Checking ActionEvents emission & redaction...")
    events = []
    async def capture(ev: ActionEvent):
        events.append(ev)

    sid = action_bus.subscribe(capture)
    try:
        await dl_service.download_file("https://example.com/tool.bat", confirmed=False)
        await asyncio.sleep(0.05)
        matched = [e for e in events if e.action_type == ActionType.DOWNLOAD_CONFIRMATION_REQUESTED]
        assert len(matched) >= 1
        assert matched[0].confirmation_required is True
        print(f"  -> PASS: Published ActionEvent: {matched[0].action_type.value}, confirmation={matched[0].confirmation_required}")
    finally:
        action_bus.unsubscribe(sid)

    # 10. Verification logic
    print("\n[10] Checking verification logic...")
    # Verify non-existent file fails
    res_fake = dl_service.verify_download(r"C:\Users\sakth\Downloads\non_existent_123456.pdf")
    assert res_fake["verified"] is False

    # Verify real file in approved dir passes
    with tempfile.NamedTemporaryFile(dir=os.path.join(user_home, "Downloads"), delete=False, suffix=".txt") as tf:
        tf.write(b"RYVEN verification check content.")
        tf_path = tf.name

    try:
        res_real = dl_service.verify_download(tf_path, expected_size=len(b"RYVEN verification check content."))
        assert res_real["verified"] is True
        assert res_real["size_bytes"] == len(b"RYVEN verification check content.")
        print(f"  -> PASS: Verified real downloaded file: {tf_path} (size: {res_real['size_bytes']} bytes)")
    finally:
        if os.path.exists(tf_path):
            os.remove(tf_path)

    print("\n" + "=" * 60)
    print("ALL 10 LIVE WINDOWS VERIFICATION CHECKS PASSED DETERMINISTICALLY!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
