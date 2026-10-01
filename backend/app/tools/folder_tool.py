"""OpenFolderTool for opening safe, predefined Windows directories in Explorer."""

import os
from typing import Any, Dict, List, Optional
from app.core.logging_config import logger
from app.tools.base import BaseTool


def get_approved_directories() -> Dict[str, str]:
    """Dynamically resolve canonical paths for approved directories on Windows."""
    home = os.path.expanduser("~")
    userprofile = os.environ.get("USERPROFILE", home)
    onedrive = os.path.join(userprofile, "OneDrive")

    # Desktop resolution
    desktop_candidates = [
        os.path.join(userprofile, "Desktop"),
        os.path.join(onedrive, "Desktop"),
    ]
    desktop_path = next((p for p in desktop_candidates if os.path.isdir(p)), desktop_candidates[0])

    # Documents resolution
    docs_candidates = [
        os.path.join(userprofile, "Documents"),
        os.path.join(onedrive, "Documents"),
    ]
    docs_path = next((p for p in docs_candidates if os.path.isdir(p)), docs_candidates[0])

    # Downloads resolution
    downloads_path = os.path.join(userprofile, "Downloads")

    # Workspace directory (parent or project root)
    workspace_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

    return {
        "desktop": desktop_path,
        "documents": docs_path,
        "downloads": downloads_path,
        "workspace": workspace_path,
        "project": workspace_path,
    }


class OpenFolderTool(BaseTool):
    """Safely opens approved local folders in Windows File Explorer."""

    name = "open_folder"
    description = (
        "Opens an approved local folder (Desktop, Documents, Downloads, or Project Workspace) "
        "in Windows File Explorer. Arbitrary or unapproved directory paths are strictly rejected."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "folder": {
                "type": "string",
                "description": "Name of the approved folder to open ('desktop', 'documents', 'downloads', 'workspace')",
            }
        },
        "required": ["folder"],
        "additionalProperties": False,
    }

    def _resolve_folder_path(self, folder_name: str) -> Optional[str]:
        """Resolve folder name to an approved directory path."""
        clean = folder_name.strip().lower()
        # Reject path separators and traversal attempts immediately
        if "/" in clean or "\\" in clean or ".." in clean:
            return None

        approved = get_approved_directories()
        clean_alias = clean.replace("folder", "").replace("directory", "").strip()

        alias_map = {
            "desktop": "desktop",
            "my desktop": "desktop",
            "documents": "documents",
            "my documents": "documents",
            "downloads": "downloads",
            "my downloads": "downloads",
            "workspace": "workspace",
            "my workspace": "workspace",
            "project": "workspace",
            "project workspace": "workspace",
        }

        target_key = alias_map.get(clean_alias) or alias_map.get(clean)
        if target_key:
            target_path = approved.get(target_key)
            if target_path and os.path.isdir(target_path):
                return target_path

        return None

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Execute directory opening safely."""
        folder_arg = kwargs.get("folder", "").strip()
        if not folder_arg:
            return {
                "success": False,
                "tool": self.name,
                "message": "No folder specified to open.",
                "folder": None,
            }

        resolved_path = self._resolve_folder_path(folder_arg)
        if not resolved_path:
            return {
                "success": False,
                "tool": self.name,
                "message": (
                    f"Security restriction: '{folder_arg}' is not in the approved folders allowlist "
                    "(Desktop, Documents, Downloads, Workspace)."
                ),
                "folder": folder_arg,
            }

        try:
            logger.info(f"Opening approved folder: {resolved_path}")
            os.startfile(resolved_path)
            folder_display = os.path.basename(resolved_path) or folder_arg
            return {
                "success": True,
                "tool": self.name,
                "folder": folder_display,
                "path": resolved_path,
                "message": f"Opening {folder_display} folder in Windows Explorer.",
            }
        except Exception as exc:
            logger.error(f"Failed to open folder {resolved_path}: {exc}")
            return {
                "success": False,
                "tool": self.name,
                "folder": folder_arg,
                "message": f"Unable to open folder: {str(exc)}",
            }
