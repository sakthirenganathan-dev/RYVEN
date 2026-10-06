"""OpenApplicationTool for launching an allowlist of desktop applications."""

import os
import shutil
from typing import Any, Dict, Optional
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
            }
        },
        "required": ["application"],
        "additionalProperties": False,
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

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Execute the application opening action safely."""
        app_input = (kwargs.get("application") or kwargs.get("app_name") or kwargs.get("app") or "").strip()
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
        exe_path = self._locate_executable(app_key)

        if not exe_path:
            return {
                "success": False,
                "tool": self.name,
                "message": f"{display_name} was not found on this system.",
                "application": display_name,
            }

        try:
            # Safe Windows process launch via os.startfile without shell invocation
            logger.info(f"Opening approved application {display_name} via {exe_path}")
            os.startfile(exe_path)
            return {
                "success": True,
                "tool": self.name,
                "application": display_name,
                "path": exe_path,
                "message": f"{display_name} is now open.",
            }
        except Exception as exc:
            logger.error(f"Failed to open application {display_name}: {exc}")
            return {
                "success": False,
                "tool": self.name,
                "application": display_name,
                "message": f"Unable to open {display_name}: {str(exc)}",
            }
