"""OpenFileTool for safely opening files from approved locations using default applications."""

import os
from typing import Any, Dict, Optional
from app.core.logging_config import logger
from app.tools.base import BaseTool
from app.tools.folder_tool import get_approved_directories
from app.tools.search_tool import get_last_search_results


class OpenFileTool(BaseTool):
    """Safely opens a file located within approved directories using the system default application."""

    name = "open_file"
    description = (
        "Opens an approved file using the system default application. Only opens files "
        "inside approved directories (Desktop, Documents, Downloads, or Workspace) or "
        "files recently discovered by search."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path or filename of the file to open",
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def _is_path_approved(self, target_path: str) -> bool:
        """Verify that target_path resides within one of the approved directory roots."""
        real_target = os.path.realpath(target_path)
        approved = get_approved_directories()

        for approved_root in approved.values():
            if os.path.isdir(approved_root):
                real_root = os.path.realpath(approved_root)
                # Check if real_target starts with real_root + separator
                if real_target.lower() == real_root.lower() or real_target.lower().startswith(
                    real_root.lower() + os.path.sep
                ):
                    return True

        return False

    def _resolve_file(self, query: str) -> Optional[str]:
        """Resolve file from explicit path or recent search results."""
        clean = query.strip().strip('"').strip("'")

        # 1. If it's an existing path and approved
        if os.path.isfile(clean) and self._is_path_approved(clean):
            return os.path.realpath(clean)

        # 2. Check recent search results
        recent = get_last_search_results()
        query_lower = clean.lower()

        # Exact filename match in recent search
        for item in recent:
            if item["filename"].lower() == query_lower:
                if os.path.isfile(item["path"]) and self._is_path_approved(item["path"]):
                    return os.path.realpath(item["path"])

        # Partial match in recent search
        for item in recent:
            if query_lower in item["filename"].lower() or query_lower in item["extension"].lower():
                if os.path.isfile(item["path"]) and self._is_path_approved(item["path"]):
                    return os.path.realpath(item["path"])

        # 3. Check direct filename in approved directories
        approved = get_approved_directories()
        for root in approved.values():
            direct_candidate = os.path.join(root, clean)
            if os.path.isfile(direct_candidate) and self._is_path_approved(direct_candidate):
                return os.path.realpath(direct_candidate)

        return None

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Execute file opening safely."""
        file_query = kwargs.get("path", "").strip()
        if not file_query:
            return {
                "success": False,
                "tool": self.name,
                "message": "No file path or filename provided to open.",
                "file": None,
            }

        resolved_file = self._resolve_file(file_query)
        if not resolved_file:
            return {
                "success": False,
                "tool": self.name,
                "message": (
                    f"Security restriction or file not found: '{file_query}' could not be safely resolved "
                    "within approved directories."
                ),
                "file": file_query,
            }

        # Double check safety before launch
        if not self._is_path_approved(resolved_file):
            return {
                "success": False,
                "tool": self.name,
                "message": f"Access denied: '{resolved_file}' resides outside approved directories.",
                "file": resolved_file,
            }

        try:
            logger.info(f"Opening approved file: {resolved_file}")
            os.startfile(resolved_file)
            filename = os.path.basename(resolved_file)
            return {
                "success": True,
                "tool": self.name,
                "file": filename,
                "path": resolved_file,
                "message": f"Opening '{filename}' with your default application.",
            }
        except Exception as exc:
            logger.error(f"Failed to open file {resolved_file}: {exc}")
            return {
                "success": False,
                "tool": self.name,
                "file": resolved_file,
                "message": f"Unable to open file: {str(exc)}",
            }
