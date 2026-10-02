"""Deployment preview generator formatting structured human-in-the-loop confirmation summaries."""

from __future__ import annotations

from typing import Optional
from app.deployment.deployment_detector import DeploymentDetector
from app.deployment.deployment_models import (
    DeploymentFramework,
    DeploymentPreview,
    DeploymentProvider,
    DeploymentRequest,
)
from app.dev_engine.quality_gate import QualityGate
from app.git.git_engine import GitEngine


class DeploymentPreviewGenerator:
    """Generates user-facing structured deployment previews prior to execution."""

    def __init__(
        self,
        git_engine: Optional[GitEngine] = None,
        quality_gate: Optional[QualityGate] = None,
        detector: Optional[DeploymentDetector] = None,
    ) -> None:
        self.git_engine = git_engine or GitEngine()
        self.quality_gate = quality_gate or QualityGate()
        self.detector = detector or DeploymentDetector()

    async def generate_preview(
        self,
        request: DeploymentRequest,
    ) -> DeploymentPreview:
        """Construct full deployment preview."""
        project_name = request.project_name
        detection = self.detector.detect(project_name)

        # Inspect Git
        branch = "main"
        git_status_str = "Clean"
        uncommitted = 0
        try:
            status_res = await self.git_engine.status(project_name=project_name)
            if status_res.success:
                branch = status_res.data.get("branch", "main")
                clean = status_res.data.get("clean", True)
                staged = len(status_res.data.get("staged_files", []))
                unstaged = len(status_res.data.get("unstaged_files", []))
                untracked = len(status_res.data.get("untracked_files", []))
                uncommitted = staged + unstaged + untracked
                git_status_str = "Clean" if clean else f"{uncommitted} uncommitted changes ({staged} staged, {unstaged} unstaged, {untracked} untracked)"
            else:
                git_status_str = "Non-Git project (direct file deployment)"
        except Exception:
            git_status_str = "Git status unavailable"

        # Quality Gate
        expected = ["package.json"] if detection.package_manager == "npm" else []
        qg = self.quality_gate.evaluate(project_name=project_name, expected_files=expected)
        qg_status = qg.state

        # Formatted banner text
        preview_text = (
            "------------------------------------\n"
            "🚀 RYVEN DEPLOYMENT PREVIEW\n\n"
            f"Project:     {project_name}\n"
            f"Provider:    {request.provider.value}\n"
            f"Framework:   {detection.framework.value}\n"
            f"Branch:      {branch}\n"
            f"Build:       {'READY' if detection.build_command else 'STATIC'}\n"
            f"Quality:     {qg_status}\n"
            f"Git Status:  {git_status_str}\n"
            f"Target:      {request.target_environment}\n"
            f"Action:      Deploy project to {request.provider.value}\n\n"
            "Confirmation required: YES\n"
            "------------------------------------"
        )

        return DeploymentPreview(
            project_name=project_name,
            provider=request.provider,
            framework=detection.framework,
            branch=branch,
            build_status="READY" if detection.build_command else "STATIC",
            test_status="READY" if detection.test_command else "NONE",
            quality_gate_status=qg_status,
            git_status=git_status_str,
            target_environment=request.target_environment,
            uncommitted_changes=uncommitted,
            required_env_vars=detection.detected_env_vars,
            action_description=f"Deploy '{project_name}' to {request.provider.value} ({request.target_environment})",
            confirmation_required=True,
            preview_message=preview_text,
        )
