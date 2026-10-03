"""RYVEN 3.0 — Resource Lifecycle & Cleanup Service (M15.3.9).

Coordinates cleanup hooks for temporary files, idle HTTP clients, active browser sessions,
and lingering background tasks. Emits RUNTIME_CLEANUP_COMPLETED upon execution.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.core.logging_config import logger


class ResourceLifecycleManager:
    """Central registry and executor for graceful resource cleanup."""

    def __init__(self) -> None:
        self._temp_files: Set[str] = set()
        self._cleanup_callbacks: List[Callable[[], Any]] = []
        self._async_cleanup_callbacks: List[Callable[[], Any]] = []

    def register_temp_file(self, file_path: str) -> None:
        """Register a temporary file to be purged upon task or runtime cleanup."""
        self._temp_files.add(str(file_path))

    def unregister_temp_file(self, file_path: str) -> None:
        """Unregister a temporary file (e.g. if already removed)."""
        self._temp_files.discard(str(file_path))

    def register_cleanup_hook(self, callback: Callable[[], Any], is_async: bool = False) -> None:
        """Register a callback to invoke during system cleanup."""
        if is_async:
            self._async_cleanup_callbacks.append(callback)
        else:
            self._cleanup_callbacks.append(callback)

    async def cleanup_all(self) -> Dict[str, Any]:
        """Perform comprehensive resource cleanup."""
        purged_files = 0
        failed_files = 0
        callbacks_run = 0

        # 1. Clean registered temporary files
        for fpath in list(self._temp_files):
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
                    purged_files += 1
                self._temp_files.discard(fpath)
            except Exception as exc:
                failed_files += 1
                logger.debug(f"[LIFECYCLE] Failed removing temp file '{fpath}': {exc}")

        # 2. Run sync callbacks
        for cb in self._cleanup_callbacks:
            try:
                cb()
                callbacks_run += 1
            except Exception as exc:
                logger.warning(f"[LIFECYCLE] Error in sync cleanup callback: {exc}")

        # 3. Run async callbacks
        for acb in self._async_cleanup_callbacks:
            try:
                res = acb()
                if asyncio.iscoroutine(res):
                    await res
                callbacks_run += 1
            except Exception as exc:
                logger.warning(f"[LIFECYCLE] Error in async cleanup callback: {exc}")

        summary = {
            "purged_temp_files": purged_files,
            "failed_temp_files": failed_files,
            "callbacks_executed": callbacks_run,
        }

        try:
            action_bus.emit(
                ActionEvent(
                    action_type=ActionType.RUNTIME_CLEANUP_COMPLETED,
                    status=ActionStatus.COMPLETED,
                    title="Resource Cleanup Completed",
                    description=f"Purged {purged_files} temp files, executed {callbacks_run} hooks.",
                    safe_metadata=summary,
                )
            )
        except Exception:
            pass

        return summary


# Global default singleton
resource_lifecycle = ResourceLifecycleManager()
lifecycle_manager = resource_lifecycle
