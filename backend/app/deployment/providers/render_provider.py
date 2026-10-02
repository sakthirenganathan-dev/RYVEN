"""Render deployment provider implementation."""

from __future__ import annotations

import os
import time
from typing import List, Optional, Tuple
from app.deployment.deployment_models import (
    DeploymentProvider,
    DeploymentRequest,
    DeploymentResult,
    DeploymentStatus,
    PreflightCheckItem,
)
from app.deployment.providers.base_provider import BaseDeploymentProvider


class RenderProvider(BaseDeploymentProvider):
    """Provider integrating with Render for web services and static sites."""

    @property
    def provider_type(self) -> DeploymentProvider:
        return DeploymentProvider.RENDER

    def validate_configuration(self) -> Tuple[bool, str]:
        """Check if RENDER_API_KEY environment variable exists."""
        token = os.environ.get("RENDER_API_KEY")
        if token and len(token.strip()) > 8:
            return True, "Render credentials configured via RENDER_API_KEY."
        return False, "Render configuration missing: RENDER_API_KEY environment variable is not set."

    def preflight_checks(self, project_dir: str) -> List[PreflightCheckItem]:
        """Perform Render-specific preflight checks."""
        checks: List[PreflightCheckItem] = []
        is_config, config_msg = self.validate_configuration()
        checks.append(PreflightCheckItem(
            name="Render Credentials",
            passed=is_config,
            severity="CRITICAL",
            message=config_msg,
        ))

        # Check render.yaml
        render_yaml = os.path.join(project_dir, "render.yaml")
        checks.append(PreflightCheckItem(
            name="Render Blueprint",
            passed=True,
            severity="WARNING",
            message="Found render.yaml blueprint." if os.path.isfile(render_yaml) else "No render.yaml blueprint; standard service defaults will apply.",
        ))
        return checks

    def deploy(self, request: DeploymentRequest, project_dir: str) -> DeploymentResult:
        """Execute deployment via Render."""
        start_time = time.monotonic()
        is_config, config_msg = self.validate_configuration()
        if not is_config:
            return DeploymentResult(
                success=False,
                provider=self.provider_type,
                project_name=request.project_name,
                deployment_status=DeploymentStatus.NOT_CONFIGURED,
                message=config_msg,
                sanitized_error="DEPLOYMENT_NOT_CONFIGURED: Set RENDER_API_KEY to enable Render deployments.",
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        return DeploymentResult(
            success=False,
            provider=self.provider_type,
            project_name=request.project_name,
            deployment_status=DeploymentStatus.FAILED,
            message="Render deployment execution paused: requires connected service ID or webhooks.",
            sanitized_error="Render service not specified.",
            execution_time_ms=(time.monotonic() - start_time) * 1000,
        )

    def get_status(self, deployment_id: str) -> DeploymentStatus:
        return DeploymentStatus.SUCCESS if deployment_id else DeploymentStatus.FAILED

    def get_deployment_url(self, deployment_id: str) -> Optional[str]:
        return f"https://{deployment_id}.onrender.com" if deployment_id else None
