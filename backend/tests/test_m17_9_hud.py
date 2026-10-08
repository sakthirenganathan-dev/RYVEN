"""Tests for RYVEN 3.0 M17.9 Phase 6 — Memory HUD & User Memory Management UI."""

import os
import re
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND_DIR = REPO_ROOT / "RAVAN"
MEMORY_HUD_PATH = FRONTEND_DIR / "src" / "components" / "MemoryHUD.tsx"
DEVELOPER_HUD_PATH = FRONTEND_DIR / "src" / "components" / "developer" / "DeveloperHUD.tsx"
AGENTS_SERVICE_PATH = FRONTEND_DIR / "src" / "services" / "agents.ts"


def test_01_memory_hud_file_exists():
    """Verify RAVAN/src/components/MemoryHUD.tsx exists and is non-empty."""
    assert MEMORY_HUD_PATH.exists(), f"MemoryHUD.tsx missing at {MEMORY_HUD_PATH}"
    assert MEMORY_HUD_PATH.stat().st_size > 1000, "MemoryHUD.tsx is too small"


def test_02_memory_hud_uses_agents_service():
    """Verify MemoryHUD imports and uses agents.ts memory methods."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    expected_imports = [
        "searchMemories",
        "getMemory",
        "getPreferences",
        "savePreference",
        "deletePreference",
        "deleteMemory",
        "deleteTaskMemories",
        "deleteProjectMemories",
        "exportMemories",
        "getMemoryStats",
    ]
    for imp in expected_imports:
        assert imp in content, f"MemoryHUD.tsx must import and use '{imp}'"


def test_03_developer_hud_integrates_memory_tab():
    """Verify DeveloperHUD imports MemoryHUD, defines 'memory' view, and provides MEMORY tab button."""
    content = DEVELOPER_HUD_PATH.read_text(encoding="utf-8")
    assert "MemoryHUD" in content, "DeveloperHUD.tsx must import MemoryHUD"
    assert '"memory"' in content, "DeveloperHUD.tsx DeveloperView must include 'memory'"
    assert "MEMORY" in content, "DeveloperHUD.tsx must provide a MEMORY tab button"
    assert "<MemoryHUD" in content, "DeveloperHUD.tsx must render <MemoryHUD />"


def test_04_agents_service_contracts():
    """Verify agents.ts exports all required memory functions and interfaces."""
    content = AGENTS_SERVICE_PATH.read_text(encoding="utf-8")
    expected_exports = [
        "export interface MemoryItem",
        "export interface MemorySearchResult",
        "export interface UserPreference",
        "export interface UserPreferencesList",
        "export interface MemoryDeleteResult",
        "export interface MemoryExportResult",
        "export interface MemoryStats",
        "export async function searchMemories",
        "export async function getMemory",
        "export async function getPreferences",
        "export async function savePreference",
        "export async function deletePreference",
        "export async function deleteMemory",
        "export async function deleteTaskMemories",
        "export async function deleteProjectMemories",
        "export async function exportMemories",
        "export async function getMemoryStats",
    ]
    for exp in expected_exports:
        assert exp in content, f"agents.ts must export '{exp}'"


def test_05_no_mock_data_in_hud():
    """Verify MemoryHUD does not contain hardcoded mock memory data or fake statistics."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    mock_patterns = [
        r"mockMemories\s*=",
        r"fakeStats\s*=",
        r"dummyMemories\s*=",
        r"total_memories:\s*42",
        r"semantic_count:\s*100",
    ]
    for pat in mock_patterns:
        assert not re.search(pat, content, re.IGNORECASE), f"Found mock data pattern '{pat}' in MemoryHUD.tsx"


def test_06_no_direct_sqlite_in_frontend():
    """Verify frontend components and services never access SQLite directly."""
    for path in [MEMORY_HUD_PATH, AGENTS_SERVICE_PATH]:
        content = path.read_text(encoding="utf-8")
        assert "sqlite3" not in content.lower(), f"Direct sqlite found in {path.name}"
        assert "select * from" not in content.lower(), f"Raw SQL found in {path.name}"


def test_07_no_execution_bypass_in_frontend():
    """Verify frontend code contains zero execution primitives or process spawners."""
    forbidden = [
        "subprocess",
        "os.system",
        "shell=True",
        "cmd.exe",
        "powershell",
        "pwsh",
        "pyautogui",
        "pynput",
        "ctypes",
        "win32api",
    ]
    for path in [MEMORY_HUD_PATH, AGENTS_SERVICE_PATH, DEVELOPER_HUD_PATH]:
        content = path.read_text(encoding="utf-8")
        for f in forbidden:
            assert f not in content, f"Forbidden execution term '{f}' found in {path.name}"


def test_08_confirmation_token_handling():
    """Verify deleteProjectMemories handles confirmation tokens and 409 status cleanly."""
    content_hud = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    content_agents = AGENTS_SERVICE_PATH.read_text(encoding="utf-8")

    assert "X-Confirmation-Token" in content_agents, "agents.ts must pass X-Confirmation-Token header"
    assert "409" in content_hud, "MemoryHUD must handle HTTP 409 status code"
    assert "CONF-" in content_hud or "confirmation" in content_hud.lower(), "MemoryHUD must support confirmation token workflow"


def test_09_trust_badge_mapping():
    """Verify trust badges match authoritative backend trust levels."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    expected_trust_levels = [
        "USER_CONFIRMED",
        "SYSTEM_DERIVED",
        "TASK_DERIVED",
        "DOCUMENT_DERIVED",
        "WEB_DERIVED",
    ]
    for tl in expected_trust_levels:
        assert tl in content, f"MemoryHUD must handle trust level '{tl}'"


def test_10_taint_status_visualization():
    """Verify taint status (CLEAN / TAINTED) is visualized in MemoryHUD."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    assert "taint_status" in content, "MemoryHUD must display taint_status"
    assert "CLEAN" in content, "MemoryHUD must handle CLEAN taint status"


def test_11_bounded_search_limit():
    """Verify searchMemories enforces a hard limit of 5 results for context safety."""
    content = AGENTS_SERVICE_PATH.read_text(encoding="utf-8")
    assert "Math.min(limit, 5)" in content, "searchMemories must bound limit to maximum 5"


def test_12_sanitized_export_handling():
    """Verify exportMemories calls backend export endpoint and bounds export count."""
    content_agents = AGENTS_SERVICE_PATH.read_text(encoding="utf-8")
    content_hud = MEMORY_HUD_PATH.read_text(encoding="utf-8")

    assert "/api/memory/export" in content_agents, "agents.ts must call /api/memory/export"
    assert "exportMemories" in content_hud, "MemoryHUD must call exportMemories"
    assert "Math.min(limit, 100)" in content_agents, "exportMemories must bound export limit to 100"


def test_13_forbidden_preference_warning():
    """Verify preference management includes guidance against sensitive/secret keys."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    assert "Forbidden keys" in content or "passwords" in content.lower(), (
        "MemoryHUD must include warning on forbidden preference keys"
    )


def test_14_task_memory_purge_isolation():
    """Verify task memory purge notes that TaskPersistenceRepository is preserved."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    assert "TaskPersistenceRepository" in content or "authoritative" in content.lower(), (
        "MemoryHUD must clarify that authoritative task history is preserved during memory purge"
    )


def test_15_advisory_context_disclaimer():
    """Verify HUD clearly informs user that memory is advisory context, not execution authority."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    assert "Advisory" in content or "advisory" in content, (
        "MemoryHUD must present memory as advisory context"
    )


def test_16_empty_states_present():
    """Verify user-friendly empty states are implemented for search and preferences."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    assert "No memories found" in content or "No preferences stored" in content, (
        "MemoryHUD must provide informative empty state messages"
    )


def test_17_copy_memory_id_feature():
    """Verify copy to clipboard functionality exists for inspected memory ID."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    assert "navigator.clipboard.writeText" in content or "copyToClipboard" in content, (
        "MemoryHUD must support copying memory ID to clipboard"
    )


def test_18_json_download_helper():
    """Verify browser download helper exists for exported memories."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    assert "Blob" in content, "MemoryHUD must create a Blob for JSON export"
    assert "download" in content, "MemoryHUD must trigger a download"


def test_19_error_and_alert_banner():
    """Verify friendly error and success alert banners are present."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    assert "alert" in content.lower(), "MemoryHUD must manage alert state"
    assert "isError" in content, "MemoryHUD must distinguish error vs success alerts"


def test_20_standalone_and_embedded_mode():
    """Verify MemoryHUD supports both standalone and embedded HUDPanel rendering."""
    content = MEMORY_HUD_PATH.read_text(encoding="utf-8")
    assert "standalone" in content, "MemoryHUD must accept optional standalone prop"
    assert "HUDPanel" in content, "MemoryHUD must render HUDPanel in standalone mode"
