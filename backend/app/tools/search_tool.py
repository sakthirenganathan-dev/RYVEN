"""SearchFilesTool for safely discovering files within approved directories."""

import datetime
import os
from typing import Any, Dict, List, Optional
from app.core.logging_config import logger
from app.tools.base import BaseTool
from app.tools.folder_tool import get_approved_directories

# Shared in-memory cache of most recent search results for OpenFileTool
_LAST_SEARCH_RESULTS: List[Dict[str, Any]] = []


def get_last_search_results() -> List[Dict[str, Any]]:
    """Retrieve recent search results."""
    return _LAST_SEARCH_RESULTS


class SearchFilesTool(BaseTool):
    """Safely searches for files inside approved user directories."""

    name = "search_files"
    description = (
        "Searches for files matching a query or extension within approved directories "
        "(Desktop, Documents, Downloads, or Project Workspace). Never accesses system roots."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Filename or keyword to search for (e.g. 'Java', 'notes', 'budget')",
            },
            "extension": {
                "type": "string",
                "description": "Optional file extension to filter by (e.g. '.java', '.txt', '.pdf')",
            },
            "scope": {
                "type": "string",
                "description": "Optional scope restrictor ('desktop', 'documents', 'downloads', 'workspace')",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    # Folders to skip during search traversal
    SKIP_DIRS = {
        ".git",
        "node_modules",
        ".venv",
        "__pycache__",
        ".output",
        "appdata",
        "$recycle.bin",
        "system volume information",
    }

    MAX_RESULTS = 20
    MAX_DEPTH = 4

    def _resolve_search_roots(self, scope: Optional[str]) -> List[str]:
        """Determine approved search root directories."""
        approved = get_approved_directories()
        if scope and scope.strip().lower() in approved:
            target = approved[scope.strip().lower()]
            return [target] if os.path.isdir(target) else []

        # Default: all existing approved locations
        roots = []
        for path in approved.values():
            if os.path.isdir(path) and path not in roots:
                roots.append(path)
        return roots

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Execute safe search across approved directories."""
        global _LAST_SEARCH_RESULTS

        query = kwargs.get("query", "").strip()
        extension = kwargs.get("extension", "").strip().lower()
        if extension and not extension.startswith("."):
            extension = f".{extension}"
        scope = kwargs.get("scope", "")
        if scope and scope.strip().lower() not in get_approved_directories():
            return {
                "success": False,
                "tool": self.name,
                "message": f"Search scope '{scope}' is not in approved directories allowlist.",
                "files": [],
            }

        search_roots = self._resolve_search_roots(scope)
        if not search_roots:
            return {
                "success": False,
                "tool": self.name,
                "message": "No valid approved directories found for search scope.",
                "files": [],
            }

        matches: List[Dict[str, Any]] = []
        query_lower = query.lower()

        for root_dir in search_roots:
            if len(matches) >= self.MAX_RESULTS:
                break

            base_depth = root_dir.rstrip(os.path.sep).count(os.path.sep)

            for current_root, dirs, files in os.walk(root_dir):
                # Enforce max depth
                current_depth = current_root.rstrip(os.path.sep).count(os.path.sep) - base_depth
                if current_depth > self.MAX_DEPTH:
                    dirs.clear()
                    continue

                # Prune excluded directories in-place
                dirs[:] = [d for d in dirs if d.lower() not in self.SKIP_DIRS and not d.startswith(".")]

                for file in files:
                    file_lower = file.lower()
                    _, ext = os.path.splitext(file_lower)

                    # Check extension match if specified
                    if extension and ext != extension:
                        continue

                    # Check query string match
                    if query_lower and query_lower not in file_lower:
                        continue

                    full_path = os.path.join(current_root, file)
                    try:
                        stat = os.stat(full_path)
                        modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                        size_kb = round(stat.st_size / 1024, 1)

                        matches.append({
                            "filename": file,
                            "path": full_path,
                            "extension": ext,
                            "size_kb": size_kb,
                            "modified": modified,
                        })

                        if len(matches) >= self.MAX_RESULTS:
                            break
                    except (PermissionError, FileNotFoundError, OSError):
                        continue

                if len(matches) >= self.MAX_RESULTS:
                    break

        _LAST_SEARCH_RESULTS = matches

        if matches:
            summary = f"Discovered {len(matches)} matching file(s) in approved directories:\n"
            file_summaries = [f"• {m['filename']} ({m['size_kb']} KB, modified {m['modified']})" for m in matches[:5]]
            summary += "\n".join(file_summaries)
            if len(matches) > 5:
                summary += f"\n...and {len(matches) - 5} more file(s)."
        else:
            criteria = f"'{query}'" if query else ""
            if extension:
                criteria += f" with extension {extension}"
            summary = f"No files matching {criteria} were found in approved directories."

        return {
            "success": True,
            "tool": self.name,
            "query": query,
            "extension": extension,
            "count": len(matches),
            "files": matches,
            "message": summary,
        }
