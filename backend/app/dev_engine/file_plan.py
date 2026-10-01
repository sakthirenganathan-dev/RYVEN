"""File Plan Engine managing structured file manifests and overwrite security."""

import os
from typing import Optional, Tuple
from app.core.logging_config import logger
from app.dev_engine.constants import (
    MAX_FILES_PER_PROJECT,
    MAX_TOTAL_PROJECT_SIZE_BYTES,
)
from app.dev_engine.models import FilePlan, FilePlanItem, ProjectSpecification
from app.tools.project_tool import resolve_project_path, resolve_project_file_path


class FilePlanEngine:
    """Creates and validates controlled FilePlan manifests before code generation or execution."""

    def create_file_plan(
        self,
        spec: ProjectSpecification,
        overwrite_allowed: bool = False,
    ) -> FilePlan:
        """Formulate a structured FilePlan from a ProjectSpecification."""
        items = [
            FilePlanItem(
                relative_path=rel_path,
                action="CREATE",
                content_source=f"template:{spec.project_type}",
                required=True,
                overwrite_allowed=overwrite_allowed,
                validation_rules=["non_empty", "path_containment"],
            )
            for rel_path in spec.expected_files
        ]

        # Estimated size: ~1.5 KB per file average
        estimated_size = len(items) * 1500

        plan = FilePlan(
            project_name=spec.project_name,
            project_type=spec.project_type,
            items=items,
            total_files=len(items),
            estimated_size_bytes=estimated_size,
            overwrite_allowed=overwrite_allowed,
        )

        logger.info(f"FilePlan created for '{plan.project_name}': {plan.total_files} files planned.")
        return plan

    def validate_plan(self, plan: FilePlan) -> Tuple[bool, Optional[str]]:
        """Validate FilePlan against overwrite protection, file limits, and boundary constraints.
        
        Returns:
            (is_valid, rejection_reason)
        """
        # 1. Total file count check
        if len(plan.items) > MAX_FILES_PER_PROJECT:
            reason = f"FilePlan rejected: file count ({len(plan.items)}) exceeds limit of {MAX_FILES_PER_PROJECT}."
            logger.warning(reason)
            return False, reason

        # 2. Total estimated size check
        if plan.estimated_size_bytes > MAX_TOTAL_PROJECT_SIZE_BYTES:
            reason = f"FilePlan rejected: estimated size ({plan.estimated_size_bytes}B) exceeds max {MAX_TOTAL_PROJECT_SIZE_BYTES}B."
            logger.warning(reason)
            return False, reason

        # 3. Overwrite protection: check if project folder already exists on disk
        target_project_dir = resolve_project_path(plan.project_name)
        if not target_project_dir:
            return False, f"FilePlan rejected: invalid project path for '{plan.project_name}'."

        if os.path.exists(target_project_dir) and not plan.overwrite_allowed:
            reason = (
                f"Project '{plan.project_name}' already exists at {target_project_dir}. "
                "Overwriting existing projects is blocked by default policy."
            )
            logger.warning(f"Overwrite Protection BLOCKED: {reason}")
            return False, reason

        # 4. Individual file path safety check
        for item in plan.items:
            if item.action != "CREATE":
                return False, f"Action '{item.action}' is disallowed. Only 'CREATE' actions are permitted."

            resolved_file = resolve_project_file_path(plan.project_name, item.relative_path)
            if not resolved_file:
                return False, f"FilePlan rejected: file '{item.relative_path}' violates project boundary containment."

            if os.path.exists(resolved_file) and not item.overwrite_allowed:
                return False, f"File '{item.relative_path}' already exists and overwrite is not allowed."

        logger.info(f"FilePlan for '{plan.project_name}' successfully validated.")
        return True, None
