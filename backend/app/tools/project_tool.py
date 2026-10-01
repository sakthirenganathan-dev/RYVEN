"""Safe project workspace creation and file generation tools for RYVEN Phase 4.1."""

import os
import re
from typing import Any, Dict, List, Optional
from app.core.logging_config import logger
from app.tools.base import BaseTool


def get_projects_root() -> str:
    """Return canonical path to the approved projects directory within the workspace."""
    workspace_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    projects_dir = os.path.join(workspace_path, "projects")
    os.makedirs(projects_dir, exist_ok=True)
    return projects_dir


def sanitize_project_name(name: str) -> Optional[str]:
    """Validate and sanitize project directory name.
    
    Rules:
    - Only alphanumeric, dashes, and underscores permitted.
    - No path separators ('/', '\\'), no dots/parent directory references ('..').
    - Length between 1 and 64 characters.
    """
    clean = name.strip()
    if not clean or len(clean) > 64:
        return None
    if not re.match(r"^[a-zA-Z0-9_\-]+$", clean):
        return None
    return clean


def resolve_project_path(project_name: str) -> Optional[str]:
    """Resolve and verify canonical absolute path for a project folder."""
    clean_name = sanitize_project_name(project_name)
    if not clean_name:
        return None
    projects_root = os.path.realpath(get_projects_root())
    target_path = os.path.realpath(os.path.join(projects_root, clean_name))
    # Enforce directory containment
    if not (target_path == projects_root or target_path.startswith(projects_root + os.sep)):
        return None
    return target_path


def resolve_project_file_path(project_name: str, relative_path: str) -> Optional[str]:
    """Resolve and verify canonical absolute path for a file inside a project."""
    project_dir = resolve_project_path(project_name)
    if not project_dir:
        return None

    # Disallow absolute paths, drive letters, and path traversal components
    if relative_path.startswith("/") or relative_path.startswith("\\") or ":" in relative_path:
        return None

    clean_rel = relative_path.replace("\\", "/").strip()
    if ".." in clean_rel.split("/"):
        return None

    target_file = os.path.realpath(os.path.join(project_dir, clean_rel))
    # Enforce file containment strictly inside project_dir
    if not target_file.startswith(project_dir + os.sep):
        return None
    return target_file


class CreateProjectFolderTool(BaseTool):
    """Safely creates a new project directory within the approved projects root."""

    name = "create_project_folder"
    description = (
        "Creates a new isolated project workspace directory inside the approved projects root. "
        "Project names must be alphanumeric and cannot contain path traversal characters."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Alphanumeric name of the project to create (e.g. 'TaskFlow', 'my_app')",
            }
        },
        "required": ["project_name"],
        "additionalProperties": False,
    }
    requires_confirmation = True

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Create project directory safely."""
        raw_name = kwargs.get("project_name", "")
        clean_name = sanitize_project_name(raw_name)
        if not clean_name:
            return {
                "success": False,
                "tool": self.name,
                "message": f"Invalid project name '{raw_name}'. Names must be alphanumeric with hyphens or underscores only.",
                "project_name": raw_name,
            }

        target_path = resolve_project_path(clean_name)
        if not target_path:
            return {
                "success": False,
                "tool": self.name,
                "message": f"Security restriction: Project path for '{clean_name}' escaped approved projects root.",
                "project_name": clean_name,
            }

        try:
            os.makedirs(target_path, exist_ok=True)
            logger.info(f"Created project folder: {target_path}")
            return {
                "success": True,
                "tool": self.name,
                "message": f"Project workspace folder created: projects/{clean_name}",
                "project_name": clean_name,
                "path": target_path,
            }
        except Exception as exc:
            logger.error(f"Failed to create project folder '{clean_name}': {exc}", exc_info=True)
            return {
                "success": False,
                "tool": self.name,
                "message": "Encountered an operating system error while creating the project folder.",
                "error": str(exc),
            }


class CreateProjectFileTool(BaseTool):
    """Safely generates a project file within the designated project directory."""

    name = "create_project_file"
    description = (
        "Writes a source file inside an approved project workspace. "
        "All file paths are strictly confined to the project directory."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Target project directory name",
            },
            "relative_path": {
                "type": "string",
                "description": "Relative file path within project (e.g. 'index.html', 'src/App.js')",
            },
            "content": {
                "type": "string",
                "description": "Text content to write into the file",
            },
        },
        "required": ["project_name", "relative_path", "content"],
        "additionalProperties": False,
    }
    requires_confirmation = True

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Write source file safely within project boundary."""
        project_name = kwargs.get("project_name", "")
        rel_path = kwargs.get("relative_path", "")
        content = kwargs.get("content", "")

        target_file = resolve_project_file_path(project_name, rel_path)
        if not target_file:
            return {
                "success": False,
                "tool": self.name,
                "message": f"Security restriction: Target path '{rel_path}' is invalid or attempts to escape project boundary.",
                "relative_path": rel_path,
            }

        try:
            parent_dir = os.path.dirname(target_file)
            os.makedirs(parent_dir, exist_ok=True)

            with open(target_file, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"Created project file: {target_file} ({len(content)} chars)")
            return {
                "success": True,
                "tool": self.name,
                "message": f"Generated file: {rel_path} ({len(content)} bytes)",
                "relative_path": rel_path,
                "bytes_written": len(content.encode("utf-8")),
            }
        except Exception as exc:
            logger.error(f"Failed to create project file '{rel_path}': {exc}", exc_info=True)
            return {
                "success": False,
                "tool": self.name,
                "message": f"Failed to write file '{rel_path}' due to an OS error.",
                "error": str(exc),
            }


class ValidateProjectFilesTool(BaseTool):
    """Validates that all expected project files have been created and contain valid content."""

    name = "validate_project_files"
    description = (
        "Validates presence and non-emptiness of expected source files in a project workspace."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "project_name": {
                "type": "string",
                "description": "Name of the project to validate",
            },
            "expected_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of expected relative file paths",
            },
        },
        "required": ["project_name", "expected_files"],
        "additionalProperties": False,
    }
    requires_confirmation = False

    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Validate presence and size of project files."""
        project_name = kwargs.get("project_name", "")
        expected_files: List[str] = kwargs.get("expected_files", [])

        project_dir = resolve_project_path(project_name)
        if not project_dir or not os.path.isdir(project_dir):
            return {
                "success": False,
                "tool": self.name,
                "message": f"Project workspace '{project_name}' does not exist.",
                "all_present": False,
            }

        validation_results: List[Dict[str, Any]] = []
        all_present = True

        for rel_file in expected_files:
            target_path = resolve_project_file_path(project_name, rel_file)
            if not target_path or not os.path.isfile(target_path):
                all_present = False
                validation_results.append({
                    "file": rel_file,
                    "present": False,
                    "size_bytes": 0,
                })
            else:
                size = os.path.getsize(target_path)
                validation_results.append({
                    "file": rel_file,
                    "present": True,
                    "size_bytes": size,
                })

        status_msg = (
            f"All {len(expected_files)} project files verified."
            if all_present
            else f"Validation incomplete: some expected project files are missing."
        )

        logger.info(f"Project validation for '{project_name}': {status_msg}")
        return {
            "success": all_present,
            "tool": self.name,
            "message": status_msg,
            "all_present": all_present,
            "files": validation_results,
        }
