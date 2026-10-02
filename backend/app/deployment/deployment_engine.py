"""Master unified Deployment Engine for RYVEN 2.0 (Milestone 12).

Orchestrates framework detection, 17-point preflight validation, structured
confirmation previews, provider execution, verification, and sanitized telemetry.
"""

from __future__ import annotations

import time
from typing import Dict, List, Optional
from app.core.logging_config import logger
from app.deployment.deployment_detector import DeploymentDetector
from app.deployment.deployment_models import (
    DeploymentFramework,
    DeploymentPreview,
    DeploymentProvider,
    DeploymentRequest,
    DeploymentResult,
    DeploymentStatus,
    PreflightResult,
    ProjectDetectionResult,
    VerificationResult,
)
from app.deployment.deployment_preflight import DeploymentPreflight
from app.deployment.deployment_preview import DeploymentPreviewGenerator
from app.deployment.deployment_security import sanitize_deployment_output
from app.deployment.deployment_telemetry import deployment_telemetry
from app.deployment.deployment_validator import (
    validate_deployment_request,
    validate_project_for_deployment,
)
from app.deployment.deployment_verifier import DeploymentVerifier
from app.deployment.providers.base_provider import BaseDeploymentProvider
from app.deployment.providers.mock_provider import MockDeploymentProvider
from app.deployment.providers.railway_provider import RailwayProvider
from app.deployment.providers.render_provider import RenderProvider
from app.deployment.providers.vercel_provider import VercelProvider
from app.dev_engine.quality_gate import QualityGate
from app.git.git_engine import GitEngine
from app.tools.project_tool import resolve_project_path


class DeploymentEngine:
    """Core orchestrator for secure, permission-governed project deployments."""

    def __init__(
        self,
        git_engine: Optional[GitEngine] = None,
        quality_gate: Optional[QualityGate] = None,
        detector: Optional[DeploymentDetector] = None,
        preflight: Optional[DeploymentPreflight] = None,
        preview_gen: Optional[DeploymentPreviewGenerator] = None,
        verifier: Optional[DeploymentVerifier] = None,
    ) -> None:
        self.git_engine = git_engine or GitEngine()
        self.quality_gate = quality_gate or QualityGate()
        self.detector = detector or DeploymentDetector()
        self.preflight = preflight or DeploymentPreflight(
            git_engine=self.git_engine,
            quality_gate=self.quality_gate,
            detector=self.detector,
        )
        self.preview_gen = preview_gen or DeploymentPreviewGenerator(
            git_engine=self.git_engine,
            quality_gate=self.quality_gate,
            detector=self.detector,
        )
        self.verifier = verifier or DeploymentVerifier()

        # Registered providers cache
        self._providers: Dict[DeploymentProvider, BaseDeploymentProvider] = {
            DeploymentProvider.VERCEL: VercelProvider(),
            DeploymentProvider.RENDER: RenderProvider(),
            DeploymentProvider.RAILWAY: RailwayProvider(),
            DeploymentProvider.MOCK: MockDeploymentProvider(),
        }

    def register_provider(self, provider: BaseDeploymentProvider) -> None:
        """Register or override a provider implementation (e.g. for testing)."""
        self._providers[provider.provider_type] = provider

    def get_provider(self, provider_type: DeploymentProvider) -> BaseDeploymentProvider:
        """Retrieve provider instance."""
        return self._providers.get(provider_type, self._providers[DeploymentProvider.VERCEL])

    def detect(self, project_name: str) -> ProjectDetectionResult:
        """Detect project framework, build system, and required variables."""
        deployment_telemetry.record_event("deployment.detected", {"project_name": project_name})
        return self.detector.detect(project_name)

    async def run_preflight(
        self,
        project_name: str,
        provider: DeploymentProvider = DeploymentProvider.VERCEL,
        provider_override: Optional[BaseDeploymentProvider] = None,
    ) -> PreflightResult:
        """Execute 17-point preflight validation."""
        deployment_telemetry.record_event("deployment.preflight_started", {"project_name": project_name, "provider": provider.value})
        prov = provider_override or self.get_provider(provider)
        res = await self.preflight.run_preflight(
            project_name=project_name,
            provider=provider,
            provider_override=prov,
        )
        if res.success:
            deployment_telemetry.record_event("deployment.preflight_passed", {"project_name": project_name})
        else:
            deployment_telemetry.record_event("deployment.preflight_failed", {"project_name": project_name, "failures": res.critical_failures})
        return res

    async def preview(self, request: DeploymentRequest) -> DeploymentPreview:
        """Generate structured preview before confirmation."""
        deployment_telemetry.record_event("deployment.preview_created", {"project_name": request.project_name, "provider": request.provider.value})
        deployment_telemetry.record_event("deployment.confirmation_requested", {"project_name": request.project_name})
        return await self.preview_gen.generate_preview(request)

    async def deploy(
        self,
        request: DeploymentRequest,
        provider_override: Optional[BaseDeploymentProvider] = None,
    ) -> DeploymentResult:
        """Execute deployment workflow with confirmation enforcement, preflight, and verification."""
        start_time = time.monotonic()
        prov_type = request.provider
        project_name = request.project_name

        # 1. Validate request structure
        is_req_valid, req_err = validate_deployment_request(request)
        if not is_req_valid:
            deployment_telemetry.record_event("deployment.failed", {"project_name": project_name, "reason": req_err})
            return DeploymentResult(
                success=False,
                provider=prov_type,
                project_name=project_name,
                deployment_status=DeploymentStatus.FAILED,
                message=f"Invalid deployment request: {req_err}",
                sanitized_error=sanitize_deployment_output(req_err),
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        # 2. Confirmation gate (MANDATORY)
        if not request.confirmed:
            deployment_telemetry.record_event("deployment.confirmation_requested", {"project_name": project_name})
            return DeploymentResult(
                success=False,
                provider=prov_type,
                project_name=project_name,
                deployment_status=DeploymentStatus.WAITING_CONFIRMATION,
                message="Deployment halted: explicit user confirmation required before proceeding to cloud deployment.",
                sanitized_error="Confirmation required (confirmed=False)",
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        deployment_telemetry.record_event("deployment.confirmed", {"project_name": project_name})
        deployment_telemetry.record_event("deployment.started", {"project_name": project_name, "provider": prov_type.value})

        # 3. Resolve project path
        valid_dir, dir_err, proj_dir = validate_project_for_deployment(project_name)
        if not valid_dir or not proj_dir:
            deployment_telemetry.record_event("deployment.failed", {"project_name": project_name, "reason": dir_err})
            return DeploymentResult(
                success=False,
                provider=prov_type,
                project_name=project_name,
                deployment_status=DeploymentStatus.FAILED,
                message=dir_err,
                sanitized_error=dir_err,
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        # 4. Run Preflight
        prov_instance = provider_override or self.get_provider(prov_type)
        preflight_res = await self.preflight.run_preflight(
            project_name=project_name,
            provider=prov_type,
            provider_override=prov_instance,
        )
        if not preflight_res.success:
            err_msg = f"Preflight failed: {'; '.join(preflight_res.critical_failures)}"
            deployment_telemetry.record_event("deployment.failed", {"project_name": project_name, "reason": err_msg})
            return DeploymentResult(
                success=False,
                provider=prov_type,
                project_name=project_name,
                deployment_status=DeploymentStatus.FAILED,
                message=err_msg,
                sanitized_error=sanitize_deployment_output(err_msg),
                execution_time_ms=(time.monotonic() - start_time) * 1000,
            )

        # 5. Execute Provider Deployment
        deploy_res = prov_instance.deploy(request, proj_dir)

        # 6. Post-deployment verification if success and URL available
        if deploy_res.success and deploy_res.deployment_url:
            verif_res = await self.verifier.verify_endpoint(deploy_res.deployment_url)
            deploy_res.verification_result = verif_res
            from app.health.health_service import health_service
            hist = health_service.get_history(deploy_res.deployment_url, limit=1)
            if hist:
                deploy_res.health_result = hist[0]
            if verif_res.reachable:
                deployment_telemetry.record_event("deployment.verification_passed", {"url": deploy_res.deployment_url})
            else:
                deployment_telemetry.record_event("deployment.verification_failed", {"url": deploy_res.deployment_url, "reason": verif_res.message})

        # Record final telemetry
        if deploy_res.success:
            deployment_telemetry.record_event("deployment.succeeded", {"project_name": project_name, "url": deploy_res.deployment_url})
        else:
            deployment_telemetry.record_event("deployment.failed", {"project_name": project_name, "reason": deploy_res.sanitized_error or deploy_res.message})

        deploy_res.execution_time_ms = (time.monotonic() - start_time) * 1000
        return deploy_res

    def get_status(self, provider: DeploymentProvider, deployment_id: str) -> DeploymentStatus:
        """Query status of active deployment from provider."""
        prov = self.get_provider(provider)
        return prov.get_status(deployment_id)
