"""Project Quality Validator performing structural, syntax, and security checks on generated files."""

import ast
import json
from typing import List, Tuple
from app.core.logging_config import logger
from app.dev_engine.constants import (
    MAX_FILE_SIZE_BYTES,
    MAX_FILES_PER_PROJECT,
    MAX_TOTAL_PROJECT_SIZE_BYTES,
)
from app.dev_engine.models import GeneratedFile


class ProjectQualityValidator:
    """Validates structural integrity, syntax correctness, and security boundaries of generated files."""

    def validate_generated_files(
        self, files: List[GeneratedFile]
    ) -> Tuple[bool, List[str], List[str]]:
        """Perform comprehensive pre-write checks on in-memory generated files.
        
        Returns:
            (is_valid, errors, warnings)
        """
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Total file count check
        if len(files) > MAX_FILES_PER_PROJECT:
            errors.append(f"Total file count ({len(files)}) exceeds maximum limit of {MAX_FILES_PER_PROJECT}.")

        total_bytes = sum(f.size_bytes for f in files)
        if total_bytes > MAX_TOTAL_PROJECT_SIZE_BYTES:
            errors.append(f"Total project size ({total_bytes}B) exceeds limit of {MAX_TOTAL_PROJECT_SIZE_BYTES}B.")

        for f in files:
            # 2. File size boundary
            if f.size_bytes > MAX_FILE_SIZE_BYTES:
                errors.append(f"File '{f.relative_path}' size ({f.size_bytes}B) exceeds limit of {MAX_FILE_SIZE_BYTES}B.")

            # 3. Path safety check
            rel = f.relative_path.replace("\\", "/")
            if ".." in rel.split("/") or rel.startswith("/") or ":" in rel:
                errors.append(f"Path traversal or invalid path detected in '{f.relative_path}'.")

            # 4. Non-empty check
            if not f.content.strip():
                errors.append(f"File '{f.relative_path}' is empty.")

            # 5. Language-specific structural syntax validation (No execution!)
            if f.relative_path.endswith(".json"):
                try:
                    json.loads(f.content)
                except Exception as exc:
                    errors.append(f"JSON syntax error in '{f.relative_path}': {str(exc)}")

            elif f.relative_path.endswith(".py"):
                try:
                    ast.parse(f.content)
                except SyntaxError as syn_err:
                    errors.append(f"Python syntax error in '{f.relative_path}' line {syn_err.lineno}: {syn_err.msg}")

            elif f.relative_path.endswith(".html"):
                lower = f.content.lower()
                if "<html" not in lower or "<body" not in lower:
                    warnings.append(f"HTML file '{f.relative_path}' missing standard <html> or <body> tags.")

        is_valid = len(errors) == 0
        if is_valid:
            logger.info(f"ProjectQualityValidator PASSED: {len(files)} files verified cleanly.")
        else:
            logger.warning(f"ProjectQualityValidator FAILED with {len(errors)} error(s): {errors}")

        return is_valid, errors, warnings
