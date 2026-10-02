"""Railway deployment provider implementation."""

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


class RailwayProvider(BaseDeploymentProvider):
    """Provider integrating with Railway for containerized and full-stack hosting."""

    @property
    def provider_type(self) -> DeploymentProvider:
        return DeploymentProvider.RAILWAY

    def validate_configuration(self) -> Tuple[bool, str]:
        """Check if RAILWAY_TOKEN environment variable exists."""
        token = os.environ.get("RAILWAY_TOKEN")
        if token and len(token.strip()) > 8:
            return True, "Railway credentials configured via RAILWAY_TOKEN."
        return False, "Railway configuration missing: RAILWAY_TOKEN environment variable is not set."

    def preflight_checks(self, project_dir: str) -> List[PreflightCheckItem]:
        """Perform Railway-specific preflight checks."""
        checks: List[PreflightCheckItem] = []
        is_config, config_msg = self.validate_configuration()
        checks.append(PreflightCheckItem(
            name="Railway Credentials",
            passed=is_config,
            severity="CRITICAL",
            message=config_msg,
        ))

        # Check railway.json or Dockerfile or Procfile
        has_config = (
            os.path.isfile(os.path.join(project_dir, "railway.json"))
            or os.path.isfile(os.path.join(project_dir, "Dockerfile"))
            or os.path.isfile(os.path.join(project_dir, "Procfile"))
        )
        checks.append(PreflightCheckItem(
            name="Railway Configuration File",
            passed=True,
            severity="WARNING",
            message="Found container or Procfile configuration." if has_config else "No explicit railway config; Nixpacks auto-detection will apply.",
        ))
        return checks

    def deploy(self, request: DeploymentRequest, project_dir: str) -> DeploymentResult:
        """Execute deployment via Railway."""
        start_time = time.monotonic()
        is_config, config_msg = self.validate_configuration()
        if not is_config:
            return DeploymentResult(
                success=False,
                provider=self.provider_type,
                project_name=request.project_name,
                deployment_status=DeploymentStatus.NOT_CONFIGURED,
                message=config_msg,
                sanitized_error="DEPLOYMENT_NOT_CONFIGURED: Set RAILWAY_TOKEN to enable Railway deployments.",
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        return DeploymentResult(
            success=False,
            provider=self.provider_type,
            project_name=request.project_name,
            deployment_status=DeploymentStatus.FAILED,
            message="Railway deployment execution paused: requires connected project or Railway CLI.",
            sanitized_error="Railway service not specified.",
            execution_time_ms=(time.monotonic() - start_time) * 1000,
        )

    def get_status(self, deployment_id: str) -> DeploymentStatus:
        return DeploymentStatus.SUCCESS if deployment_id else DeploymentStatus.FAILED

    def get_deployment_url(self, deployment_id: str) -> Optional[str]:
        return f"https://{deployment_id}.up.railway.app" if deployment_id else None
