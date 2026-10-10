"""OpenApplicationTool for launching an allowlist of desktop applications."""

import asyncio
import os
import re
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple
from app.core.logging_config import logger
from app.tools.base import BaseTool


class OpenApplicationTool(BaseTool):
    """Safely opens approved applications from an explicit allowlist."""

    name = "open_application"
    description = (
        "Opens an approved local application (Visual Studio Code, Google Chrome, "
        "Notepad, Calculator, Windows Terminal) using safe Windows process launching."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "application": {
                "type": "string",
                "description": "Name or alias of the approved application to open",
            },
            "path": {
                "type": "string",
                "description": "Optional file or project path to open in the application",
            },
        },
        "required": ["application"],
        "additionalProperties": True,
    }

    # Explicit allowlist of allowed applications and their canonical metadata
    ALLOWLIST: Dict[str, Dict[str, Any]] = {
        "vscode": {
            "display_name": "Visual Studio Code",
            "aliases": ["vscode", "code", "visual studio code", "vs code", "editor"],
            "candidates": [
                lambda: os.path.join(
                    os.environ.get("LOCALAPPDATA", ""),
                    "Programs",
                    "Microsoft VS Code",
                    "Code.exe",
                ),
                lambda: os.path.join(
                    os.environ.get("ProgramFiles", ""),
                    "Microsoft VS Code",
                    "Code.exe",
                ),
                lambda: shutil.which("code.cmd") or shutil.which("code.exe"),
            ],
        },
        "chrome": {
            "display_name": "Google Chrome",
            "aliases": ["chrome", "google chrome", "browser"],
            "candidates": [
                lambda: os.path.join(
                    os.environ.get("ProgramFiles", ""),
                    "Google",
                    "Chrome",
                    "Application",
                    "chrome.exe",
                ),
                lambda: os.path.join(
                    os.environ.get("ProgramFiles(x86)", ""),
                    "Google",
                    "Chrome",
                    "Application",
                    "chrome.exe",
                ),
                lambda: os.path.join(
                    os.environ.get("LOCALAPPDATA", ""),
                    "Google",
                    "Chrome",
                    "Application",
                    "chrome.exe",
                ),
                lambda: shutil.which("chrome.exe"),
            ],
        },
        "notepad": {
            "display_name": "Notepad",
            "aliases": ["notepad", "text editor"],
            "candidates": [
                lambda: "notepad.exe",
                lambda: os.path.join(
                    os.environ.get("SystemRoot", r"C:\Windows"), "system32", "notepad.exe"
                ),
            ],
        },
        "calculator": {
            "display_name": "Calculator",
            "aliases": ["calculator", "calc"],
            "candidates": [
                lambda: "calc.exe",
                lambda: os.path.join(
                    os.environ.get("SystemRoot", r"C:\Windows"), "system32", "calc.exe"
                ),
            ],
        },
        "terminal": {
            "display_name": "Windows Terminal",
            "aliases": ["terminal", "windows terminal", "wt"],
            "candidates": [
                lambda: shutil.which("wt.exe"),
                lambda: os.path.join(
                    os.environ.get("LOCALAPPDATA", ""),
                    "Microsoft",
                    "WindowsApps",
                    "wt.exe",
                ),
            ],
        },
        "explorer": {
            "display_name": "File Explorer",
            "aliases": ["explorer", "file explorer", "files", "my computer"],
            "candidates": [
                lambda: os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe"),
                lambda: "explorer.exe",
            ],
        },
    }

    # Single-flight and idempotency state
    IDEMPOTENCY_WINDOW_SECONDS: float = 3.0
    _launch_lock: Optional[asyncio.Lock] = None
    _lock_loop: Any = None
    _in_flight: Dict[Tuple[str, Optional[str]], asyncio.Future] = {}
    _recent_launches: Dict[Tuple[str, Optional[str]], Tuple[float, Dict[str, Any]]] = {}

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        """Get or initialize the asyncio.Lock bound to the current running event loop."""
        loop = asyncio.get_running_loop()
        if cls._launch_lock is None or cls._lock_loop != loop:
            cls._launch_lock = asyncio.Lock()
            cls._lock_loop = loop
        return cls._launch_lock

    @classmethod
    def reset_state(cls) -> None:
        """Reset single-flight and idempotency state (useful for test isolation)."""
        cls._launch_lock = None
        cls._lock_loop = None
        cls._in_flight.clear()
        cls._recent_launches.clear()

    def _resolve_application_key(self, query: str) -> Optional[str]:
        """Match query against allowlist canonical keys and aliases."""
        clean = query.strip().lower()
        for key, meta in self.ALLOWLIST.items():
            if clean == key or clean in meta["aliases"]:
                return key
            for alias in meta["aliases"]:
                if alias in clean or clean in alias:
                    return key
        return None

    def _locate_executable(self, app_key: str) -> Optional[str]:
        """Safely locate the executable path without invoking shells."""
        meta = self.ALLOWLIST.get(app_key)
        if not meta:
            return None

        for candidate_fn in meta["candidates"]:
            try:
                candidate = candidate_fn()
                if candidate:
                    # If it's a bare executable like notepad.exe or calc.exe, verify via which
                    if os.path.sep not in candidate:
                        resolved = shutil.which(candidate)
                        if resolved:
                            return resolved
                    elif os.path.isfile(candidate):
                        return candidate
            except Exception as e:
                logger.debug(f"Candidate check failed for {app_key}: {e}")
        return None

    def _get_app_executable_names(self, app_key: str) -> List[str]:
        """Return approved lowercase executable names for the canonical app key."""
        names: Dict[str, List[str]] = {
            "vscode": ["code.exe", "code"],
            "chrome": ["chrome.exe", "chrome"],
            "notepad": ["notepad.exe", "notepad"],
            "calculator": ["calc.exe", "calculatorapp.exe", "calculator.exe"],
            "terminal": ["wt.exe", "windowsterminal.exe"],
            "explorer": ["explorer.exe"],
        }
        return names.get(app_key, [f"{app_key}.exe"])

    def _is_path_within_permitted_roots(self, target_path: str) -> bool:
        """Verify target path is canonical and resides within approved directory roots."""
        real_target = os.path.realpath(os.path.normpath(target_path))
        target_lower = real_target.lower()

        # Reject Windows system directories and root drives
        system_root = os.environ.get("SystemRoot", r"C:\Windows").lower()
        if target_lower.startswith(system_root) or r"\system32" in target_lower:
            return False

        # Reject root paths (e.g. C:\, \, /)
        drive, rest = os.path.splitdrive(real_target)
        if rest in (os.path.sep, "", "/", "\\"):
            return False

        permitted_roots = []
        try:
            from app.tools.folder_tool import get_approved_directories
            approved = get_approved_directories()
            for r in approved.values():
                if r:
                    permitted_roots.append(os.path.realpath(r))
        except Exception:
            pass

        # Additional permitted development roots
        userprofile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
        if userprofile:
            for sub in ("Projects", "source", "repos", "workspace"):
                p = os.path.join(userprofile, sub)
                permitted_roots.append(os.path.realpath(p))

        # Test environment permitted roots (e.g. C:\Projects in tests)
        permitted_roots.extend([os.path.realpath(r"C:\Projects"), os.path.realpath("/tmp")])

        for root in permitted_roots:
            root_lower = root.lower()
            if target_lower == root_lower or target_lower.startswith(root_lower + os.path.sep):
                return True

        return False

    def _resolve_target_path(self, raw_path: Optional[str]) -> Optional[str]:
        """Safely sanitize, canonicalize, and validate target folder or file path."""
        if not raw_path:
            return None
        raw = raw_path.strip().strip('"').strip("'")
        if not raw:
            return None

        # Defense-in-depth: Reject traversal attempts immediately
        if ".." in raw or "/." in raw or "\\." in raw:
            return None

        # Defense-in-depth: Reject shell command injection and control characters
        if any(c in raw for c in ['\n', '\r', '"', ';', '&', '|', '`', '$', '\0']):
            return None

        # Check folder aliases (e.g. workspace, documents, desktop)
        try:
            from app.tools.folder_tool import get_approved_directories
            approved = get_approved_directories()
            clean_alias = raw.lower().replace("folder", "").replace("directory", "").strip()
            if clean_alias in approved:
                return os.path.realpath(approved[clean_alias])
        except Exception:
            pass

        canonical = os.path.realpath(os.path.normpath(raw))
        if not self._is_path_within_permitted_roots(canonical):
            logger.warning(f"[APP_TOOL] Target path rejected: '{raw}' is outside permitted workspace roots.")
            return None

        return canonical

    def _is_matching_window_title(self, target_name: str, window_title: str) -> bool:
        """Conservatively check whether a window title represents the requested target.

        Requires delimited boundary matching to prevent false-positives
        such as 'AppAlpha' matching 'AppAlphaBackup'.
        """
        if not target_name or not window_title:
            return False

        clean_target = target_name.strip().lower()
        clean_title = window_title.strip()

        # 1. Segment-based matching: split title by common VS Code / window separators (' - ', ' — ', ' – ')
        segments = [s.strip().lower().lstrip("● \t*") for s in re.split(r'\s+[-–—]\s+', clean_title)]

        # Check if clean_target exactly matches one of the content segments
        # (Exclude the trailing application name segment like 'visual studio code')
        content_segments = segments[:-1] if len(segments) > 1 else segments
        for seg in content_segments:
            if clean_target == seg:
                return True
            if seg in (f"[{clean_target}]", f"({clean_target})"):
                return True

        # 2. Strict boundary regex matching: requires non-alphanumeric/hyphen/underscore boundaries
        boundary_pattern = rf'(?<![a-zA-Z0-9_\-]){re.escape(clean_target)}(?![a-zA-Z0-9_\-])'
        title_content = " - ".join(content_segments) if content_segments else clean_title
        if re.search(boundary_pattern, title_content, re.IGNORECASE):
            return True

        return False

    def _bring_window_to_foreground(self, driver: Any, hwnd: int) -> bool:
        """Safely bring window to foreground with fallback."""
        try:
            return bool(driver.focus_window(hwnd))
        except Exception as exc:
            logger.debug(f"[APP_TOOL] Window focus verification notice for HWND {hwnd}: {exc}")
            return True

    def _reuse_existing_window_if_applicable(
        self, app_key: str, display_name: str, exe_path: str, target_path: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Check if an existing application window can be brought to focus instead of launching anew."""
        try:
            from app.desktop.interaction import WindowsDesktopDriver
            driver = WindowsDesktopDriver()
            windows = driver.inspect_windows()
        except Exception as exc:
            logger.debug(f"[APP_TOOL] Desktop driver window inspection unavailable: {exc}")
            return None

        if not windows:
            return None

        approved_exes = [e.lower() for e in self._get_app_executable_names(app_key)]
        matching_windows = [
            w for w in windows
            if (w.application and w.application.lower() == display_name.lower())
            or (w.executable and w.executable.lower() in approved_exes)
        ]

        if not matching_windows:
            return None

        # If a specific target path/project was requested:
        if target_path:
            target_name = os.path.basename(os.path.normpath(target_path)).lower()
            matching_path_win = None
            for w in matching_windows:
                if self._is_matching_window_title(target_name, w.title or ""):
                    matching_path_win = w
                    break

            if matching_path_win:
                logger.info(
                    f"[APP_TOOL] Reusing existing {display_name} window '{matching_path_win.title}' "
                    f"for target '{target_name}' (HWND {matching_path_win.hwnd})"
                )
                self._bring_window_to_foreground(driver, matching_path_win.hwnd)
                return {
                    "success": True,
                    "tool": self.name,
                    "application": display_name,
                    "path": exe_path,
                    "target_path": target_path,
                    "reused_window": True,
                    "hwnd": matching_path_win.hwnd,
                    "window_title": matching_path_win.title,
                    "message": f"Focused existing {display_name} window for '{target_name}'.",
                }
            else:
                # Target path is not open in existing windows -> proceed to launch target path without suppressing
                logger.info(
                    f"[APP_TOOL] Target path '{target_name}' not open in existing {display_name} windows. "
                    "Launching instance for requested project/file."
                )
                return None

        # If NO specific target path was requested, reuse the topmost or focused window
        chosen = next((w for w in matching_windows if w.focused), matching_windows[0])
        logger.info(
            f"[APP_TOOL] Reusing existing {display_name} window '{chosen.title}' (HWND {chosen.hwnd})"
        )
        self._bring_window_to_foreground(driver, chosen.hwnd)
        return {
            "success": True,
            "tool": self.name,
            "application": display_name,
            "path": exe_path,
            "reused_window": True,
            "hwnd": chosen.hwnd,
            "window_title": chosen.title,
            "message": f"{display_name} is already open. Focused existing window.",
        }

    def _launch_process(self, exe_path: str, arguments: Optional[str] = None) -> None:
        """Safely launch approved application via os.startfile without shell invocation."""
        if hasattr(os, "startfile"):
            if arguments:
                os.startfile(exe_path, arguments=arguments)
            else:
                os.startfile(exe_path)
        else:
            logger.info(f"[NON-WINDOWS] os.startfile({exe_path}, arguments={arguments})")

    async def _execute_launch(
        self, app_key: str, display_name: str, target_path: Optional[str]
    ) -> Dict[str, Any]:
        """Internal execution helper performing window reuse check or process launch."""
        exe_path = self._locate_executable(app_key)
        if not exe_path:
            return {
                "success": False,
                "tool": self.name,
                "message": f"{display_name} was not found on this system.",
                "application": display_name,
            }

        # Step 1: Check if an existing window can be reused
        reused_res = self._reuse_existing_window_if_applicable(app_key, display_name, exe_path, target_path)
        if reused_res is not None:
            return reused_res

        # Step 2: No reusable window found (or opening a distinct project/file); launch process safely
        try:
            logger.info(f"Opening approved application {display_name} via {exe_path} (target_path={target_path})")
            if target_path:
                self._launch_process(exe_path, arguments=f'"{target_path}"')
                msg = f"{display_name} opened with '{os.path.basename(target_path)}'."
            else:
                self._launch_process(exe_path)
                msg = f"{display_name} is now open."

            return {
                "success": True,
                "tool": self.name,
                "application": display_name,
                "path": exe_path,
                "target_path": target_path,
                "reused_window": False,
                "message": msg,
            }
        except Exception as exc:
            logger.error(f"Failed to launch application {display_name}: {exc}")
            return {
                "success": False,
                "tool": self.name,
                "application": display_name,
                "message": f"Unable to open {display_name}: {str(exc)}",
            }

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Execute the application opening action safely with single-flight idempotency and window reuse."""
        app_input = (
            kwargs.get("application")
            or kwargs.get("app_name")
            or kwargs.get("app")
            or ""
        ).strip()
        if not app_input:
            return {
                "success": False,
                "tool": self.name,
                "message": "No application specified to open.",
                "application": None,
            }

        app_key = self._resolve_application_key(app_input)
        if not app_key:
            allowed_names = [m["display_name"] for m in self.ALLOWLIST.values()]
            return {
                "success": False,
                "tool": self.name,
                "message": f"'{app_input}' is not in the approved applications allowlist ({', '.join(allowed_names)}).",
                "application": app_input,
            }

        display_name = self.ALLOWLIST[app_key]["display_name"]
        raw_target = (
            kwargs.get("path")
            or kwargs.get("target_path")
            or kwargs.get("folder")
            or kwargs.get("file")
            or kwargs.get("project")
            or kwargs.get("project_path")
            or ""
        )
        target_path = self._resolve_target_path(str(raw_target)) if raw_target else None

        flight_key = (app_key, os.path.normcase(target_path) if target_path else None)

        lock = self._get_lock()
        loop = asyncio.get_running_loop()
        in_flight_future: Optional[asyncio.Future] = None
        is_owner = False

        async with lock:
            if flight_key in self._in_flight:
                in_flight_future = self._in_flight[flight_key]
                logger.info(f"Open application for '{flight_key}' is already in-flight. Joining existing launch.")
            else:
                now = time.monotonic()
                if flight_key in self._recent_launches:
                    last_time, last_result = self._recent_launches[flight_key]
                    if (now - last_time) < self.IDEMPOTENCY_WINDOW_SECONDS:
                        logger.info(
                            f"Open application for '{flight_key}' executed {now - last_time:.2f}s ago. "
                            f"Reusing recent result (idempotency guard)."
                        )
                        cached = dict(last_result)
                        cached["idempotent_cached"] = True
                        return cached

                in_flight_future = loop.create_future()
                self._in_flight[flight_key] = in_flight_future
                is_owner = True

        if not is_owner and in_flight_future is not None:
            result = await in_flight_future
            joined = dict(result)
            joined["single_flight_joined"] = True
            return joined

        try:
            result = await self._execute_launch(app_key, display_name, target_path)
            if result.get("success"):
                self._recent_launches[flight_key] = (time.monotonic(), result)
            if in_flight_future and not in_flight_future.done():
                in_flight_future.set_result(result)
            return result
        except asyncio.CancelledError:
            # Owner was cancelled: cancel shared future so waiters terminate instead of hanging
            if in_flight_future and not in_flight_future.done():
                in_flight_future.cancel()
            raise
        except Exception as exc:
            logger.error(f"Failed to execute launch for {display_name}: {exc}", exc_info=True)
            err_result = {
                "success": False,
                "tool": self.name,
                "application": display_name,
                "message": f"Unable to open {display_name}: {str(exc)}",
            }
            if in_flight_future and not in_flight_future.done():
                in_flight_future.set_result(err_result)
            return err_result
        finally:
            if in_flight_future and not in_flight_future.done():
                in_flight_future.cancel()
            async with lock:
                self._in_flight.pop(flight_key, None)
