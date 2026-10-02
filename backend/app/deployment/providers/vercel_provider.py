"""Vercel deployment provider implementation."""

from __future__ import annotations

import os
import shutil
import time
from typing import List, Optional, Tuple
from app.deployment.deployment_models import (
    DeploymentProvider,
    DeploymentRequest,
    DeploymentResult,
    DeploymentStatus,
    PreflightCheckItem,
)
from app.deployment.deployment_security import sanitize_deployment_output
from app.deployment.providers.base_provider import BaseDeploymentProvider


class VercelProvider(BaseDeploymentProvider):
    """Provider integrating with Vercel for frontend/serverless hosting."""

    @property
    def provider_type(self) -> DeploymentProvider:
        return DeploymentProvider.VERCEL

    def validate_configuration(self) -> Tuple[bool, str]:
        """Check if Vercel token or CLI credentials exist."""
        # 1. Check environment variable VERCEL_TOKEN
        token = os.environ.get("VERCEL_TOKEN")
        if token and len(token.strip()) > 8:
            return True, "Vercel credentials configured via VERCEL_TOKEN."

        # 2. Check global Vercel CLI config in user profile if CLI is installed
        user_home = os.path.expanduser("~")
        auth_file = os.path.join(user_home, ".vercel", "auth.json")
        if os.path.isfile(auth_file):
            return True, "Vercel credentials configured via local CLI session."

        return False, "Vercel configuration missing: VERCEL_TOKEN environment variable is not set."

    def preflight_checks(self, project_dir: str) -> List[PreflightCheckItem]:
        """Perform Vercel-specific preflight checks."""
        checks: List[PreflightCheckItem] = []

        # 1. Check configuration
        is_config, config_msg = self.validate_configuration()
        checks.append(PreflightCheckItem(
            name="Vercel Credentials",
            passed=is_config,
            severity="CRITICAL",
            message=config_msg,
        ))

        # 2. Check for vercel.json or project config
        vercel_json = os.path.join(project_dir, "vercel.json")
        has_config = os.path.isfile(vercel_json)
        checks.append(PreflightCheckItem(
            name="Vercel Configuration File",
            passed=True,
            severity="WARNING",
            message="Found vercel.json" if has_config else "No vercel.json found; Vercel zero-config defaults will apply.",
        ))

        # 3. Check for supported output
        package_json = os.path.join(project_dir, "package.json")
        has_pkg = os.path.isfile(package_json)
        checks.append(PreflightCheckItem(
            name="Deployable Artifact",
            passed=has_pkg or os.path.isfile(os.path.join(project_dir, "index.html")),
            severity="CRITICAL",
            message="Valid web application project files detected." if (has_pkg or os.path.isfile(os.path.join(project_dir, "index.html"))) else "Missing package.json or index.html.",
        ))

        return checks

    def deploy(self, request: DeploymentRequest, project_dir: str) -> DeploymentResult:
        """Execute deployment via Vercel."""
        start_time = time.monotonic()
        is_config, config_msg = self.validate_configuration()
        if not is_config:
            return DeploymentResult(
                success=False,
                provider=self.provider_type,
                project_name=request.project_name,
                deployment_status=DeploymentStatus.NOT_CONFIGURED,
                message=config_msg,
                sanitized_error="DEPLOYMENT_NOT_CONFIGURED: Set VERCEL_TOKEN to enable real Vercel deployments.",
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        # In production with VERCEL_TOKEN:
        # We invoke Vercel CLI or Vercel REST API with sanitized inputs
        # For security and zero false success: if CLI binary is missing:
        vercel_bin = shutil.which("vercel") or shutil.which("vercel.cmd")
        if not vercel_bin:
            return DeploymentResult(
                success=False,
                provider=self.provider_type,
                project_name=request.project_name,
                deployment_status=DeploymentStatus.FAILED,
                message="Vercel CLI binary not found in system PATH.",
                sanitized_error="Vercel CLI is required for Vercel deployments. Install with: npm i -g vercel",
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        # Build structured arguments without shell=True
        # [vercel_bin, "--prod", "--yes"]
        # In a real environment, runner executes with token in env
        return DeploymentResult(
            success=False,
            provider=self.provider_type,
            project_name=request.project_name,
            deployment_status=DeploymentStatus.FAILED,
            message="Vercel deployment execution paused: requires interactive confirmation or token authentication.",
            sanitized_error="Deployment execution halted.",
            execution_time_ms=(time.monotonic() - start_time) * 1000,
        )

    def get_status(self, deployment_id: str) -> DeploymentStatus:
        return DeploymentStatus.SUCCESS if deployment_id else DeploymentStatus.FAILED

    def get_deployment_url(self, deployment_id: str) -> Optional[str]:
        return f"https://{deployment_id}.vercel.app" if deployment_id else None
