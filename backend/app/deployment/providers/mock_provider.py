"""Mock deployment provider for comprehensive testing without external network calls."""

from __future__ import annotations

import time
from typing import List, Optional, Tuple
from app.deployment.deployment_models import (
    DeploymentProvider,
    DeploymentRequest,
    DeploymentResult,
    DeploymentStatus,
    PreflightCheckItem,
    VerificationResult,
)
from app.deployment.providers.base_provider import BaseDeploymentProvider


class MockDeploymentProvider(BaseDeploymentProvider):
    """Configurable mock provider supporting test fixtures for all outcomes."""

    def __init__(
        self,
        mode: str = "SUCCESS",
        configured: bool = True,
        deployment_url: str = "https://mock-app.vercel.app",
        deployment_id: str = "dpl_mock123456",
        simulated_delay_ms: float = 10.0,
    ) -> None:
        self.mode = mode.upper()
        self.configured = configured
        self._deployment_url = deployment_url
        self._deployment_id = deployment_id
        self.simulated_delay_ms = simulated_delay_ms

    @property
    def provider_type(self) -> DeploymentProvider:
        return DeploymentProvider.MOCK

    def validate_configuration(self) -> Tuple[bool, str]:
        if not self.configured or self.mode == "AUTH_FAILURE":
            return False, "Mock provider credentials missing or invalid."
        if self.mode == "INVALID_CONFIGURATION":
            return False, "Mock provider configuration is invalid."
        return True, "Mock provider is properly configured."

    def preflight_checks(self, project_dir: str) -> List[PreflightCheckItem]:
        is_config, config_msg = self.validate_configuration()
        return [
            PreflightCheckItem(
                name="Mock Credentials",
                passed=is_config,
                severity="CRITICAL",
                message=config_msg,
            ),
            PreflightCheckItem(
                name="Mock Environment",
                passed=True,
                severity="WARNING",
                message="Mock environment active for test runner.",
            ),
        ]

    def deploy(self, request: DeploymentRequest, project_dir: str) -> DeploymentResult:
        start_time = time.monotonic()
        is_config, config_msg = self.validate_configuration()
        if not is_config:
            return DeploymentResult(
                success=False,
                provider=self.provider_type,
                project_name=request.project_name,
                deployment_status=DeploymentStatus.NOT_CONFIGURED,
                message=config_msg,
                sanitized_error="DEPLOYMENT_NOT_CONFIGURED: Mock credentials missing.",
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        if self.mode == "FAILURE":
            return DeploymentResult(
                success=False,
                provider=self.provider_type,
                project_name=request.project_name,
                deployment_status=DeploymentStatus.FAILED,
                message="Mock deployment simulation failed during build.",
                sanitized_error="Build failed: exit status 1 in mock container",
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        if self.mode == "TIMEOUT":
            return DeploymentResult(
                success=False,
                provider=self.provider_type,
                project_name=request.project_name,
                deployment_status=DeploymentStatus.FAILED,
                message="Mock deployment timed out waiting for container startup.",
                sanitized_error="Timeout: execution exceeded deadline",
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        # SUCCESS mode
        return DeploymentResult(
            success=True,
            provider=self.provider_type,
            project_name=request.project_name,
            deployment_status=DeploymentStatus.SUCCESS,
            deployment_url=self._deployment_url,
            deployment_id=self._deployment_id,
            message="Mock deployment succeeded.",
            verification_result=VerificationResult(
                reachable=True,
                status_code=200,
                response_time_ms=45.0,
                url=self._deployment_url,
                message="Endpoint responded with HTTP 200 OK.",
            ),
            execution_time_ms=(time.monotonic() - start_time) * 1000,
        )

    def get_status(self, deployment_id: str) -> DeploymentStatus:
        if self.mode == "SUCCESS":
            return DeploymentStatus.SUCCESS
        elif self.mode in ("FAILURE", "TIMEOUT"):
            return DeploymentStatus.FAILED
        return DeploymentStatus.NOT_CONFIGURED

    def get_deployment_url(self, deployment_id: str) -> Optional[str]:
        return self._deployment_url if self.mode == "SUCCESS" else None
