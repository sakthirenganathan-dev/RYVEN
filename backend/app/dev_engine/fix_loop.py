"""Error and Fix Loop Architecture for RYVEN DEV ENGINE v1."""

from typing import Optional
import uuid
from app.core.logging_config import logger
from app.dev_engine.models import (
    BuildError,
    FixProposal,
    FixResult,
)
from app.tools.project_tool import resolve_project_file_path


class FixLoopManager:
    """Coordinates error analysis, fix proposal generation, and confirmed fix application.
    
    Security Mandate:
    - Never silently modify files without explicit confirmation.
    - Path containment is strictly enforced before proposing or applying fixes.
    """

    def analyze_error_and_propose_fix(
        self,
        project_name: str,
        error: BuildError,
        corrected_content: str,
    ) -> Optional[FixProposal]:
        """Synthesize a structured FixProposal from an analyzed build error."""
        if not error.file_path:
            logger.warning("Cannot propose fix: BuildError has no associated file path.")
            return None

        # Verify target file path containment
        resolved_path = resolve_project_file_path(project_name, error.file_path)
        if not resolved_path:
            logger.warning(f"Security Alert: Target fix path '{error.file_path}' escapes project '{project_name}'.")
            return None

        etype = getattr(error, "error_type", getattr(error, "category", "BUILD_ERROR"))
        proposal = FixProposal(
            fix_id=f"fix-{uuid.uuid4().hex[:8]}",
            target_file=error.file_path,
            description=f"Resolve {etype}: {error.message}",
            proposed_content=corrected_content,
            requires_confirmation=True,
        )

        logger.info(f"Generated FixProposal {proposal.fix_id} for '{error.file_path}'.")
        return proposal

    async def apply_fix(
        self,
        project_name: str,
        proposal: FixProposal,
        user_confirmed: bool = False,
    ) -> FixResult:
        """Apply a validated fix proposal only when explicit user confirmation is provided."""
        if proposal.requires_confirmation and not user_confirmed:
            logger.warning(f"Fix application paused: Proposal {proposal.fix_id} requires explicit confirmation.")
            return FixResult(
                success=False,
                fix_id=proposal.fix_id,
                applied=False,
                message="Fix requires user confirmation before filesystem modification.",
            )

        resolved_path = resolve_project_file_path(project_name, proposal.target_file)
        if not resolved_path:
            return FixResult(
                success=False,
                fix_id=proposal.fix_id,
                applied=False,
                message=f"Security violation: Target file '{proposal.target_file}' escaped project boundary.",
            )

        try:
            with open(resolved_path, "w", encoding="utf-8") as f:
                f.write(proposal.proposed_content)

            logger.info(f"Successfully applied fix {proposal.fix_id} to '{proposal.target_file}'.")
            return FixResult(
                success=True,
                fix_id=proposal.fix_id,
                applied=True,
                message=f"Applied fix to {proposal.target_file} successfully.",
            )
        except Exception as exc:
            logger.error(f"Failed to write fix to '{proposal.target_file}': {exc}", exc_info=True)
            return FixResult(
                success=False,
                fix_id=proposal.fix_id,
                applied=False,
                message=f"Filesystem error while applying fix: {str(exc)}",
            )
