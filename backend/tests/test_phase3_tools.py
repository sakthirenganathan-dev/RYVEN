"""Unit tests for Phase 3 controlled laptop intelligence tools."""

import os
import pytest
from unittest.mock import MagicMock, patch
from app.tools.app_tool import OpenApplicationTool
from app.tools.clipboard_tool import GetClipboardTool, SetClipboardTool
from app.tools.file_tool import OpenFileTool
from app.tools.folder_tool import OpenFolderTool, get_approved_directories
from app.tools.search_tool import SearchFilesTool, _LAST_SEARCH_RESULTS
from app.tools.system_info_tool import SystemInfoTool
from app.tools.website_tool import OpenWebsiteTool


# --- 1. OpenApplicationTool Tests ---

@pytest.mark.asyncio
async def test_app_tool_unknown_application():
    """Verify that unapproved applications are rejected."""
    tool = OpenApplicationTool()
    result = await tool.execute(application="unapproved_random_app.exe")
    assert result["success"] is False
    assert "approved applications allowlist" in result["message"]


@pytest.mark.asyncio
async def test_app_tool_empty_input():
    """Verify that empty application parameter returns an error."""
    tool = OpenApplicationTool()
    result = await tool.execute(application="")
    assert result["success"] is False
    assert "No application specified" in result["message"]


@pytest.mark.asyncio
async def test_app_tool_approved_app_launch():
    """Verify approved app resolves and invokes native os.startfile safely."""
    tool = OpenApplicationTool()
    with patch.object(tool, "_locate_executable", return_value="C:\\Windows\\notepad.exe"):
        with patch("os.startfile", create=True) as mock_startfile:
            result = await tool.execute(application="notepad")
            assert result["success"] is True
            assert "Notepad" in result["application"]
            mock_startfile.assert_called_once_with("C:\\Windows\\notepad.exe")


@pytest.mark.asyncio
async def test_app_tool_missing_executable():
    """Verify clean message when approved app executable is not found on machine."""
    tool = OpenApplicationTool()
    with patch.object(tool, "_locate_executable", return_value=None):
        result = await tool.execute(application="vscode")
        assert result["success"] is False
        assert "not found" in result["message"]


# --- 2. OpenWebsiteTool Tests ---

@pytest.mark.asyncio
async def test_website_tool_known_shortcut():
    """Verify known site shortcuts resolve to official HTTPS URLs."""
    tool = OpenWebsiteTool()
    with patch("webbrowser.open", return_value=True) as mock_wb:
        result = await tool.execute(url="github")
        assert result["success"] is True
        assert result["url"] == "https://github.com"
        mock_wb.assert_called_once_with("https://github.com")


@pytest.mark.asyncio
async def test_website_tool_valid_https():
    """Verify valid external HTTPS URLs are permitted."""
    tool = OpenWebsiteTool()
    with patch("webbrowser.open", return_value=True):
        result = await tool.execute(url="https://python.org")
        assert result["success"] is True
        assert result["url"] == "https://python.org"


@pytest.mark.asyncio
async def test_website_tool_unsafe_schemes_rejected():
    """Verify unsafe protocols like file:// or javascript: are blocked."""
    tool = OpenWebsiteTool()
    unsafe_urls = [
        "file:///C:/Windows/System32/calc.exe",
        "javascript:alert(document.cookie)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "vbscript:msgbox(1)",
    ]
    for target in unsafe_urls:
        result = await tool.execute(url=target)
        assert result["success"] is False
        assert "not a valid or safe http/https URL" in result["message"] or "Security restriction" in result["message"]


@pytest.mark.asyncio
async def test_website_tool_empty():
    """Verify empty URL input returns an error."""
    tool = OpenWebsiteTool()
    result = await tool.execute(url="")
    assert result["success"] is False


# --- 3. OpenFolderTool Tests ---

@pytest.mark.asyncio
async def test_folder_tool_approved_folders():
    """Verify approved folder identifiers resolve and open."""
    tool = OpenFolderTool()
    with patch("os.startfile", create=True) as mock_startfile:
        for folder_name in ["downloads", "desktop", "documents", "workspace"]:
            result = await tool.execute(folder=folder_name)
            assert result["success"] is True
            assert "folder" in result


@pytest.mark.asyncio
async def test_folder_tool_unapproved_folder():
    """Verify arbitrary filesystem directories are rejected."""
    tool = OpenFolderTool()
    result = await tool.execute(folder="C:\\Windows\\System32")
    assert result["success"] is False
    assert "approved folders allowlist" in result["message"]


# --- 4. SearchFilesTool Tests ---

@pytest.mark.asyncio
async def test_search_files_approved_scope(tmp_path):
    """Verify file search discovers files within an approved directory."""
    test_file = tmp_path / "hello_ryven.py"
    test_file.write_text("print('test')")

    tool = SearchFilesTool()
    with patch("app.tools.search_tool.get_approved_directories", return_value={"test": str(tmp_path)}):
        result = await tool.execute(query="hello", scope="test")
        assert result["success"] is True
        assert result["count"] >= 1
        assert any("hello_ryven.py" in f["filename"] for f in result["files"])


@pytest.mark.asyncio
async def test_search_files_unapproved_scope():
    """Verify searching outside approved directories is rejected."""
    tool = SearchFilesTool()
    result = await tool.execute(query="java", scope="system32")
    assert result["success"] is False
    assert "not in approved directories allowlist" in result["message"]


@pytest.mark.asyncio
async def test_search_files_no_results(tmp_path):
    """Verify clean response when no files match query."""
    tool = SearchFilesTool()
    with patch("app.tools.search_tool.get_approved_directories", return_value={"test": str(tmp_path)}):
        result = await tool.execute(query="nonexistent_xyz_file")
        assert result["success"] is True
        assert result["count"] == 0


# --- 5. OpenFileTool Tests ---

@pytest.mark.asyncio
async def test_open_file_approved_path(tmp_path):
    """Verify opening an existing file in an approved directory succeeds."""
    target_file = tmp_path / "document.txt"
    target_file.write_text("content")

    tool = OpenFileTool()
    with patch("app.tools.file_tool.get_approved_directories", return_value={"test": str(tmp_path)}):
        with patch("os.startfile", create=True) as mock_startfile:
            result = await tool.execute(path=str(target_file))
            assert result["success"] is True
            assert "document.txt" in result["message"]


@pytest.mark.asyncio
async def test_open_file_unapproved_path():
    """Verify opening files outside approved directories is rejected."""
    tool = OpenFileTool()
    with patch("app.tools.file_tool.get_approved_directories", return_value={"test": "E:\\SafeDir"}):
        result = await tool.execute(path="C:\\Windows\\System32\\cmd.exe")
        assert result["success"] is False
        assert "could not be safely resolved within approved directories" in result["message"]


@pytest.mark.asyncio
async def test_open_file_from_recent_search(tmp_path):
    """Verify opening a file found in recent search results."""
    target_file = tmp_path / "found_report.pdf"
    target_file.write_text("pdf content")

    tool = OpenFileTool()
    with patch("app.tools.file_tool.get_approved_directories", return_value={"test": str(tmp_path)}):
        with patch("app.tools.file_tool.get_last_search_results", return_value=[{"filename": "found_report.pdf", "path": str(target_file)}]):
            with patch("os.startfile", create=True):
                result = await tool.execute(path="found_report.pdf")
                assert result["success"] is True


# --- 6. Clipboard Tools Tests ---

@pytest.mark.asyncio
async def test_clipboard_get_and_set():
    """Verify writing to and reading from the clipboard via mocked native calls."""
    get_tool = GetClipboardTool()
    set_tool = SetClipboardTool()

    with patch("app.tools.clipboard_tool._write_win32_clipboard", return_value=True):
        res_set = await set_tool.execute(text="Hello from RYVEN test")
        assert res_set["success"] is True
        assert "Copied to clipboard" in res_set["message"]

    with patch("app.tools.clipboard_tool._read_win32_clipboard", return_value="Hello from RYVEN test"):
        res_get = await get_tool.execute()
        assert res_get["success"] is True
        assert res_get["text"] == "Hello from RYVEN test"


@pytest.mark.asyncio
async def test_clipboard_set_length_limit():
    """Verify clipboard tool rejects excessively large text payloads."""
    set_tool = SetClipboardTool()
    giant_text = "A" * (set_tool.MAX_LENGTH + 50)
    result = await set_tool.execute(text=giant_text)
    assert result["success"] is False
    assert "exceeds maximum length" in result["message"]


# --- 7. SystemInfoTool Tests ---

@pytest.mark.asyncio
async def test_system_info_tool():
    """Verify SystemInfoTool retrieves non-destructive platform metadata."""
    tool = SystemInfoTool()
    result = await tool.execute()
    assert result["success"] is True
    assert "os" in result
    assert "hostname" in result
