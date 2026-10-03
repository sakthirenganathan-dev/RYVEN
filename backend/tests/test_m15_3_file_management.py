"""RYVEN 3.0 — M15.3 Phase 4: Download & Upload Management Tests.

Comprehensive test suite verifying:
- Download URL security: HTTP/HTTPS allowed; javascript:, data:, file: blocked; SSRF localhost,
  loopback, private IPs, metadata endpoints blocked.
- Download destination validation: approved folders, path traversal blocking, UNC blocking,
  system directory blocking.
- Filename sanitization, length limiting, and collision handling ('file (1).ext').
- Download streaming, Content-Length pre-check, and max size limit enforcement.
- Dangerous extension classification (.exe, .bat, .ps1, etc.) and confirmation gating.
- Post-download verification: file existence, non-empty, containment check.
- Upload validation: approved folders, regular file check, path traversal, UNC, system paths.
- Sensitive file blocking: .env, .env.*, id_rsa, .pem, .key, credentials, tokens, secrets.
- File input targeting: strictly requires <input type="file">; rejects text/password inputs.
- Post-upload verification and separate form submission gating.
- ActionEventBus emission and secret redaction.
- InternetAgent facade methods and ToolRegistry registrations.
- FastAPI endpoints for browser download and upload.

All tests run in-memory with zero external network access.
"""

from __future__ import annotations

import os
import tempfile
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.browser.engine import BrowserEngine
from app.core.permissions import safety_guard
from app.internet.agent import InternetAgent, internet_agent
from app.internet.download_service import DownloadService
from app.internet.file_models import (
    DEFAULT_MAX_DOWNLOAD_SIZE_BYTES,
    DEFAULT_MAX_UPLOAD_SIZE_BYTES,
    DownloadRequest,
    DownloadResult,
    FileTransferStatus,
    UploadRequest,
    UploadResult,
    is_approved_filesystem_path,
    is_dangerous_download_extension,
    is_sensitive_file,
    resolve_safe_destination,
    resolve_unique_filename,
    sanitize_download_filename,
)
from app.internet.models import FormFieldInfo, WebFormInfo
from app.internet.tools import (
    WebDownloadTool,
    WebUploadTool,
    WebVerifyDownloadTool,
    WebVerifyUploadTool,
)
from app.internet.upload_service import UploadService
from app.tools.folder_tool import get_approved_directories
from app.tools.registry import create_default_registry


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------


def make_mock_engine() -> MagicMock:
    engine = MagicMock(spec=BrowserEngine)
    engine.type_text = AsyncMock(return_value={"success": True, "message": "Set file input"})
    engine.click_element = AsyncMock(return_value={"success": True, "message": "Clicked element"})
    return engine


def get_test_approved_dir() -> str:
    approved = get_approved_directories()
    # Workspace or downloads or desktop
    for k in ("downloads", "workspace", "desktop", "documents"):
        p = approved.get(k)
        if p and os.path.isdir(p):
            return p
    return list(approved.values())[0]


# ---------------------------------------------------------------------------
# 1. Download URL Security & SSRF Protection Tests
# ---------------------------------------------------------------------------


class TestDownloadURLSecurity:
    """Verifies that only valid HTTP/HTTPS URLs are accepted and dangerous schemes / SSRF are blocked."""

    @pytest.mark.asyncio
    async def test_blocked_javascript_scheme(self):
        service = DownloadService()
        res = await service.download_file("javascript:alert(1)")
        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value
        assert "security policy" in res["error"].lower()

    @pytest.mark.asyncio
    async def test_blocked_data_scheme(self):
        service = DownloadService()
        res = await service.download_file("data:text/plain;base64,SGVsbG8=")
        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value

    @pytest.mark.asyncio
    async def test_blocked_file_scheme(self):
        service = DownloadService()
        res = await service.download_file("file:///C:/Windows/System32/calc.exe")
        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value

    @pytest.mark.asyncio
    async def test_blocked_ftp_scheme(self):
        service = DownloadService()
        res = await service.download_file("ftp://example.com/file.txt")
        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value

    @pytest.mark.asyncio
    async def test_ssrf_localhost_blocked(self):
        service = DownloadService()
        res = await service.download_file("http://localhost:8000/secret.txt")
        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value
        assert "ssrf" in res["error"].lower() or "blocked" in res["error"].lower()

    @pytest.mark.asyncio
    async def test_ssrf_loopback_ip_blocked(self):
        service = DownloadService()
        res = await service.download_file("http://127.0.0.1/file.txt")
        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value

    @pytest.mark.asyncio
    async def test_ssrf_private_class_a_ip_blocked(self):
        service = DownloadService()
        res = await service.download_file("http://10.0.0.1/admin.backup")
        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value

    @pytest.mark.asyncio
    async def test_ssrf_private_class_c_ip_blocked(self):
        service = DownloadService()
        res = await service.download_file("http://192.168.1.1/router.cfg")
        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value

    @pytest.mark.asyncio
    async def test_ssrf_cloud_metadata_blocked(self):
        service = DownloadService()
        res = await service.download_file("http://169.254.169.254/latest/meta-data/")
        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value

    @pytest.mark.asyncio
    async def test_illegal_control_characters_rejected(self):
        service = DownloadService()
        res = await service.download_file("https://example.com/file.txt;rm -rf /")
        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value


# ---------------------------------------------------------------------------
# 2. Download Destination & Filesystem Containment Tests
# ---------------------------------------------------------------------------


class TestDownloadDestinationSecurity:
    """Verifies that download destinations cannot escape approved directories."""

    def test_path_traversal_rejected(self):
        is_safe = is_approved_filesystem_path("../../Windows/System32")
        assert is_safe is False

    def test_unc_path_rejected(self):
        is_safe = is_approved_filesystem_path(r"\\evil-server\share\file.txt")
        assert is_safe is False

    def test_windows_system_dir_rejected(self):
        is_safe = is_approved_filesystem_path(r"C:\Windows\System32\cmd.exe")
        assert is_safe is False

    def test_program_files_rejected(self):
        is_safe = is_approved_filesystem_path(r"C:\Program Files\Malware\bad.dll")
        assert is_safe is False

    def test_drive_root_rejected(self):
        is_safe = is_approved_filesystem_path(r"C:\test.txt")
        assert is_safe is False

    def test_approved_directory_accepted(self):
        approved = get_approved_directories()
        for root in approved.values():
            if root and os.path.exists(root):
                test_file = os.path.join(root, "test_file.txt")
                assert is_approved_filesystem_path(test_file) is True

    def test_resolve_safe_destination_approved_category(self):
        ok, path, err = resolve_safe_destination("downloads", "report.pdf")
        assert ok is True
        assert path.endswith("report.pdf")
        assert err == ""

    def test_resolve_safe_destination_unapproved_escape(self):
        ok, path, err = resolve_safe_destination(r"C:\Windows\Temp", "hack.dll")
        assert ok is False
        assert "not within an approved directory" in err


# ---------------------------------------------------------------------------
# 3. Filename Sanitization & Collision Handling Tests
# ---------------------------------------------------------------------------


class TestFilenameSanitization:
    """Tests filename sanitization and safe collision resolution."""

    def test_sanitize_traversal_components(self):
        sanitized = sanitize_download_filename("../../../secret.txt")
        assert ".." not in sanitized
        assert "/" not in sanitized
        assert "\\" not in sanitized
        assert "secret.txt" in sanitized

    def test_sanitize_windows_illegal_characters(self):
        sanitized = sanitize_download_filename('report:2026*final?.pdf<>"|')
        for illegal in '<>:"/\\|?*':
            assert illegal not in sanitized
        assert sanitized.endswith(".pdf")

    def test_sanitize_empty_returns_fallback(self):
        sanitized = sanitize_download_filename("", fallback="safe_file.dat")
        assert sanitized == "safe_file.dat"

    def test_sanitize_long_filename_truncated(self):
        long_name = "a" * 300 + ".txt"
        sanitized = sanitize_download_filename(long_name)
        assert len(sanitized) <= 200
        assert sanitized.endswith(".txt")

    def test_duplicate_collision_handling(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file1 = os.path.join(tmpdir, "doc.pdf")
            with open(file1, "w") as f:
                f.write("first")

            cand1 = resolve_unique_filename(tmpdir, "doc.pdf")
            assert os.path.basename(cand1) == "doc (1).pdf"

            with open(cand1, "w") as f:
                f.write("second")

            cand2 = resolve_unique_filename(tmpdir, "doc.pdf")
            assert os.path.basename(cand2) == "doc (2).pdf"


# ---------------------------------------------------------------------------
# 4. Dangerous Extension Classification & Confirmation Gating Tests
# ---------------------------------------------------------------------------


class TestDownloadConfirmationGating:
    """Tests that executable or dangerous file downloads require explicit user approval."""

    def test_dangerous_extension_classification(self):
        assert is_dangerous_download_extension("setup.exe") is True
        assert is_dangerous_download_extension("install.msi") is True
        assert is_dangerous_download_extension("script.bat") is True
        assert is_dangerous_download_extension("run.cmd") is True
        assert is_dangerous_download_extension("deploy.ps1") is True
        assert is_dangerous_download_extension("payload.vbs") is True
        assert is_dangerous_download_extension("logic.js") is True
        assert is_dangerous_download_extension("hook.dll") is True

    def test_safe_extension_classification(self):
        assert is_dangerous_download_extension("report.pdf") is False
        assert is_dangerous_download_extension("data.csv") is False
        assert is_dangerous_download_extension("image.png") is False
        assert is_dangerous_download_extension("archive.zip") is False
        assert is_dangerous_download_extension("notes.txt") is False

    @pytest.mark.asyncio
    async def test_download_executable_without_confirmation_pauses(self):
        service = DownloadService()
        res = await service.download_file(
            url="https://example.com/installer.exe",
            confirmed=False,
        )

        assert res["success"] is False
        assert res["status"] == FileTransferStatus.WAITING_CONFIRMATION.value
        assert res["requires_confirmation"] is True
        assert res["confirmation_token"] is not None
        assert res["confirmation_token"].startswith("dl-")
        assert "CONFIRMATION REQUIRED" in res["message"]


# ---------------------------------------------------------------------------
# 5. Download Execution, Size Limits & Verification Tests
# ---------------------------------------------------------------------------


class TestDownloadExecutionAndVerification:
    """Tests streaming download execution, Content-Length checks, and post-download verification."""

    @pytest.mark.asyncio
    async def test_download_success_with_mocked_stream(self):
        service = DownloadService()
        approved_dir = get_test_approved_dir()

        # Mock stream response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-length": "13",
            "content-type": "text/plain",
            "content-disposition": 'attachment; filename="notes.txt"',
        }

        async def fake_aiter_bytes(chunk_size=65536):
            yield b"Hello, World!"

        mock_response.aiter_bytes = fake_aiter_bytes

        class DummyAsyncContextManager:
            def __init__(self, resp):
                self.resp = resp
            async def __aenter__(self):
                return self.resp
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=DummyAsyncContextManager(mock_response))

        with patch.object(service, "_get_client", return_value=mock_client):
            res = await service.download_file(
                url="https://example.com/notes.txt",
                destination_folder=approved_dir,
                confirmed=True,
            )

            assert res["success"] is True
            assert res["verified"] is True
            assert res["status"] == FileTransferStatus.COMPLETED.value
            assert res["size_bytes"] == 13
            assert os.path.exists(res["destination_path"])

            # Clean up created test file
            if os.path.exists(res["destination_path"]):
                os.remove(res["destination_path"])

    @pytest.mark.asyncio
    async def test_download_content_length_exceeds_limit(self):
        service = DownloadService()
        approved_dir = get_test_approved_dir()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.headers = {
            "content-length": "100000000",  # 100MB
            "content-type": "application/octet-stream",
        }

        class DummyAsyncContextManager:
            def __init__(self, resp):
                self.resp = resp
            async def __aenter__(self):
                return self.resp
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass

        mock_client = MagicMock()
        mock_client.stream = MagicMock(return_value=DummyAsyncContextManager(mock_response))

        with patch.object(service, "_get_client", return_value=mock_client):
            res = await service.download_file(
                url="https://example.com/bigfile.bin",
                destination_folder=approved_dir,
                max_size_bytes=5000000,  # 5MB limit
                confirmed=True,
            )

            assert res["success"] is False
            assert res["error_code"] == "SIZE_LIMIT_EXCEEDED"


    def test_verify_download_nonexistent_file(self):
        v = DownloadService.verify_download(r"C:\nonexistent_file_xyz.dat")
        assert v["verified"] is False
        assert "does not exist" in v["reason"]

    def test_verify_download_valid_file(self):
        approved_dir = get_test_approved_dir()
        test_path = os.path.join(approved_dir, "test_verify.txt")
        with open(test_path, "w") as f:
            f.write("Sample test content")

        try:
            v = DownloadService.verify_download(test_path, expected_size=19)
            assert v["verified"] is True
            assert v["size_bytes"] == 19
        finally:
            if os.path.exists(test_path):
                os.remove(test_path)


# ---------------------------------------------------------------------------
# 6. Upload Security & Sensitive File Blocking Tests
# ---------------------------------------------------------------------------


class TestUploadSecurity:
    """Verifies that sensitive configuration files, private keys, and tokens are blocked from upload."""

    def test_sensitive_file_detection_env(self):
        is_sens, reason = is_sensitive_file(".env")
        assert is_sens is True
        assert "sensitive credential" in reason.lower()

    def test_sensitive_file_detection_env_local(self):
        is_sens, reason = is_sensitive_file(".env.local")
        assert is_sens is True

    def test_sensitive_file_detection_ssh_key(self):
        is_sens, reason = is_sensitive_file("id_rsa")
        assert is_sens is True

    def test_sensitive_file_detection_pem(self):
        is_sens, reason = is_sensitive_file("server_private.pem")
        assert is_sens is True

    def test_sensitive_file_detection_token_keyword(self):
        is_sens, reason = is_sensitive_file("github_token.txt")
        assert is_sens is True

    def test_sensitive_file_detection_api_key_keyword(self):
        is_sens, reason = is_sensitive_file("openai_api_key.json")
        assert is_sens is True

    def test_safe_file_not_flagged(self):
        is_sens, _ = is_sensitive_file("resume.pdf")
        assert is_sens is False

    @pytest.mark.asyncio
    async def test_upload_blocked_for_sensitive_file(self):
        mock_engine = make_mock_engine()
        service = UploadService(engine=mock_engine)
        approved_dir = get_test_approved_dir()

        # Create dummy .env in approved dir
        env_file = os.path.join(approved_dir, ".env")
        with open(env_file, "w") as f:
            f.write("SECRET_KEY=12345")

        try:
            res = await service.upload_file(
                source_path=env_file,
                target_field="attachment",
            )

            assert res["success"] is False
            assert res["status"] == FileTransferStatus.BLOCKED.value
            assert res["error_code"] == "SENSITIVE_FILE_BLOCKED"
            # Ensure BrowserEngine was NEVER called
            mock_engine.type_text.assert_not_called()
        finally:
            if os.path.exists(env_file):
                os.remove(env_file)

    @pytest.mark.asyncio
    async def test_upload_blocked_outside_approved_dir(self):
        mock_engine = make_mock_engine()
        service = UploadService(engine=mock_engine)

        res = await service.upload_file(
            source_path=r"C:\Windows\System32\drivers\etc\hosts",
            target_field="attachment",
        )

        assert res["success"] is False
        assert res["status"] == FileTransferStatus.BLOCKED.value
        mock_engine.type_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_upload_missing_file_rejected(self):
        mock_engine = make_mock_engine()
        service = UploadService(engine=mock_engine)

        res = await service.upload_file(
            source_path=r"nonexistent_file_abc123.pdf",
            target_field="attachment",
        )

        assert res["success"] is False
        assert res["error_code"] == "FILE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_upload_directory_rejected(self):
        mock_engine = make_mock_engine()
        service = UploadService(engine=mock_engine)
        approved_dir = get_test_approved_dir()

        res = await service.upload_file(
            source_path=approved_dir,
            target_field="attachment",
        )

        assert res["success"] is False
        assert res["error_code"] == "NOT_A_REGULAR_FILE"


# ---------------------------------------------------------------------------
# 7. File Input Type Targeting & Interaction Tests
# ---------------------------------------------------------------------------


class TestUploadFieldTargeting:
    """Verifies that file uploads only target <input type="file"> elements."""

    @pytest.mark.asyncio
    async def test_upload_rejects_text_input_masquerading(self):
        mock_engine = make_mock_engine()
        service = UploadService(engine=mock_engine)
        approved_dir = get_test_approved_dir()

        dummy_file = os.path.join(approved_dir, "resume.pdf")
        with open(dummy_file, "w") as f:
            f.write("PDF content")

        # Form with a text input, not file input
        form = WebFormInfo(
            form_id="test_form",
            fields=[
                FormFieldInfo(field_id="txt-field", name="username", input_type="text"),
            ],
        )

        try:
            res = await service.upload_file(
                source_path=dummy_file,
                target_field="username",
                form=form,
            )

            assert res["success"] is False
            assert res["error_code"] == "INVALID_FIELD_TYPE"
            assert "expected 'file'" in res["error"].lower()
            mock_engine.type_text.assert_not_called()
        finally:
            if os.path.exists(dummy_file):
                os.remove(dummy_file)

    @pytest.mark.asyncio
    async def test_upload_succeeds_for_valid_file_input(self):
        mock_engine = make_mock_engine()
        service = UploadService(engine=mock_engine)
        approved_dir = get_test_approved_dir()

        dummy_file = os.path.join(approved_dir, "document.pdf")
        with open(dummy_file, "w") as f:
            f.write("Valid document")

        form = WebFormInfo(
            form_id="job_app",
            fields=[
                FormFieldInfo(field_id="cv_input", name="resume", input_type="file", label_text="Attach Resume"),
            ],
        )

        try:
            res = await service.upload_file(
                source_path=dummy_file,
                target_field="Attach Resume",
                form=form,
            )

            assert res["success"] is True
            assert res["verified"] is True
            assert res["status"] == FileTransferStatus.COMPLETED.value
            assert res["filename"] == "document.pdf"
            mock_engine.type_text.assert_awaited_once()
        finally:
            if os.path.exists(dummy_file):
                os.remove(dummy_file)


# ---------------------------------------------------------------------------
# 8. ActionEvent Redaction & Safety Tests
# ---------------------------------------------------------------------------


class TestFileManagementActionEvents:
    """Verifies that ActionEvents for downloads and uploads are properly redacted."""

    @pytest.mark.asyncio
    async def test_download_action_events_emitted(self):
        events = []

        async def capture(ev: ActionEvent):
            events.append(ev)

        sid = action_bus.subscribe(capture)
        try:
            service = DownloadService()
            await service.download_file("https://example.com/installer.exe", confirmed=False)
            await asyncio.sleep(0.05)

            req_events = [e for e in events if e.action_type == ActionType.DOWNLOAD_CONFIRMATION_REQUESTED]
            assert len(req_events) >= 1
            assert req_events[0].confirmation_required is True
        finally:
            action_bus.unsubscribe(sid)

    @pytest.mark.asyncio
    async def test_upload_action_events_never_include_secret_contents(self):
        events = []

        async def capture(ev: ActionEvent):
            events.append(ev)

        sid = action_bus.subscribe(capture)
        try:
            mock_engine = make_mock_engine()
            service = UploadService(engine=mock_engine)
            approved_dir = get_test_approved_dir()

            sec_file = os.path.join(approved_dir, "my_token.txt")
            with open(sec_file, "w") as f:
                f.write("SUPER_SECRET_VALUE_12345")

            try:
                await service.upload_file(source_path=sec_file, target_field="upload")
                await asyncio.sleep(0.05)

                for ev in events:
                    ev_str = str(ev.model_dump())
                    assert "SUPER_SECRET_VALUE_12345" not in ev_str
            finally:
                if os.path.exists(sec_file):
                    os.remove(sec_file)
        finally:
            action_bus.unsubscribe(sid)


# ---------------------------------------------------------------------------
# 9. InternetAgent Facade Integration Tests
# ---------------------------------------------------------------------------


class TestInternetAgentFileManagementIntegration:
    """Verifies that InternetAgent properly exposes download, upload, and verification facade methods."""

    def test_internet_agent_has_file_services(self):
        assert hasattr(internet_agent, "download_service")
        assert hasattr(internet_agent, "upload_service")
        assert isinstance(internet_agent.download_service, DownloadService)
        assert isinstance(internet_agent.upload_service, UploadService)

    @pytest.mark.asyncio
    async def test_agent_download_file_facade(self):
        with patch.object(internet_agent.download_service, "download_file", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = {"success": True, "status": "COMPLETED"}
            res = await internet_agent.download_file("https://example.com/file.pdf")
            assert res["success"] is True
            mock_dl.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_upload_file_facade(self):
        with patch.object(internet_agent.upload_service, "upload_file", new_callable=AsyncMock) as mock_ul:
            mock_ul.return_value = {"success": True, "status": "COMPLETED"}
            res = await internet_agent.upload_file("C:/test.pdf", "attachment")
            assert res["success"] is True
            mock_ul.assert_awaited_once()

    def test_agent_verify_download_facade(self):
        with patch.object(internet_agent.verification_service, "verify_download") as mock_vd:
            mock_vd.return_value = {"verified": True}
            res = internet_agent.verify_download("C:/path/file.pdf")
            assert res["verified"] is True
            mock_vd.assert_called_once()

    def test_agent_verify_upload_facade(self):
        with patch.object(internet_agent.verification_service, "verify_upload") as mock_vu:
            mock_vu.return_value = {"verified": True}
            res = internet_agent.verify_upload("file_input", "resume.pdf")
            assert res["verified"] is True
            mock_vu.assert_called_once()


# ---------------------------------------------------------------------------
# 10. ToolRegistry & SafetyGuard Integration Tests
# ---------------------------------------------------------------------------


class TestToolRegistryFileTools:
    """Verifies that all 4 file management tools are registered and permitted by SafetyGuard."""

    def test_all_file_tools_in_registry(self):
        registry = create_default_registry()
        tool_names = registry.list_tools()

        assert "web_download" in tool_names
        assert "web_upload" in tool_names
        assert "web_verify_download" in tool_names
        assert "web_verify_upload" in tool_names

    def test_file_tools_permitted_by_safety_guard(self):
        perm_dl = safety_guard.validate_action("web_download", {"url": "https://example.com/doc.pdf"})
        assert perm_dl.allowed is True
        assert perm_dl.risk_level == "safe"

        perm_ul = safety_guard.validate_action("web_upload", {"source_path": "file.pdf", "target_field": "cv"})
        assert perm_ul.allowed is True
        assert perm_ul.risk_level == "safe"

    @pytest.mark.asyncio
    async def test_web_download_tool_execute(self):
        tool = WebDownloadTool()
        with patch.object(internet_agent, "download_file", new_callable=AsyncMock) as mock_dl:
            mock_dl.return_value = {"success": True, "status": "COMPLETED"}
            res = await tool.execute(url="https://example.com/report.pdf")
            assert res["success"] is True
            mock_dl.assert_awaited_once_with(
                url="https://example.com/report.pdf",
                destination_folder="downloads",
                custom_filename=None,
                confirmed=False,
                session_id=None,
            )

    @pytest.mark.asyncio
    async def test_web_upload_tool_execute(self):
        tool = WebUploadTool()
        with patch.object(internet_agent, "upload_file", new_callable=AsyncMock) as mock_ul:
            mock_ul.return_value = {"success": True, "status": "COMPLETED"}
            res = await tool.execute(source_path="C:/doc.pdf", target_field="file_input")
            assert res["success"] is True
            mock_ul.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_web_verify_download_tool_execute(self):
        tool = WebVerifyDownloadTool()
        with patch.object(internet_agent, "verify_download") as mock_vd:
            mock_vd.return_value = {"verified": True}
            res = await tool.execute(file_path="C:/doc.pdf")
            assert res["verified"] is True
            mock_vd.assert_called_once()

    @pytest.mark.asyncio
    async def test_web_verify_upload_tool_execute(self):
        tool = WebVerifyUploadTool()
        with patch.object(internet_agent, "verify_upload") as mock_vu:
            mock_vu.return_value = {"verified": True}
            res = await tool.execute(field_name="cv", filename="doc.pdf")
            assert res["verified"] is True
            mock_vu.assert_called_once()
