"""Registered Deployment tools for RYVEN 2.0 (Milestone 12).

Exposes detect, preflight, preview, deploy, status, and verify operations to ToolRegistry.
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from app.core.logging_config import logger
from app.deployment.deployment_models import (
    DeploymentProvider,
    DeploymentRequest,
)
from app.deployment.deployment_security import sanitize_deployment_output
from app.tools.base import BaseTool


class BaseDeploymentTool(BaseTool):
    """Base class for all deployment tools with shared engine instance."""

    def __init__(self, engine: Optional[Any] = None) -> None:
        self._engine = engine

    @property
    def engine(self):
        if self._engine is None:
            from app.deployment.deployment_engine import DeploymentEngine
            self._engine = DeploymentEngine()
        return self._engine

    @property
    def input_schema(self) -> Dict[str, Any]:
        return getattr(self, "parameters_schema", {})




class DeploymentDetectTool(BaseDeploymentTool):
    """Inspects a project directory and detects framework, build commands, and variables."""

    @property
    def name(self) -> str:
        return "deployment_detect"

    @property
    def description(self) -> str:
        return "Safely inspects project files to detect framework, package manager, and build configuration."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Name of the existing project directory to inspect.",
                }
            },
            "required": ["project_name"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        project_name = arguments.get("project_name", "")
        if not project_name:
            return {"success": False, "error": "Argument 'project_name' is required."}

        res = self.engine.detect(project_name)
        return {
            "success": res.is_deployable,
            "project_name": res.project_name,
            "framework": res.framework.value,
            "project_type": res.project_type,
            "package_manager": res.package_manager,
            "build_command": res.build_command,
            "test_command": res.test_command,
            "output_dir": res.output_dir,
            "detected_env_vars": res.detected_env_vars,
            "notes": res.notes,
        }


class DeploymentPreflightTool(BaseDeploymentTool):
    """Executes 17-point preflight validation before any deployment is attempted."""

    @property
    def name(self) -> str:
        return "deployment_preflight"

    @property
    def description(self) -> str:
        return "Runs mandatory 17-point preflight checks covering path safety, Git state, Quality Gate, and provider credentials."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Name of the project to validate.",
                },
                "provider": {
                    "type": "string",
                    "enum": ["VERCEL", "RENDER", "RAILWAY", "MOCK"],
                    "description": "Target cloud deployment provider. Defaults to VERCEL.",
                },
            },
            "required": ["project_name"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        project_name = arguments.get("project_name", "")
        prov_str = arguments.get("provider", "VERCEL").upper()
        try:
            provider = DeploymentProvider(prov_str)
        except ValueError:
            provider = DeploymentProvider.VERCEL

        res = await self.engine.run_preflight(project_name=project_name, provider=provider)
        return {
            "success": res.success,
            "project_name": res.project_name,
            "provider": res.provider.value,
            "critical_failures": [sanitize_deployment_output(f) for f in res.critical_failures],
            "warnings": [sanitize_deployment_output(w) for w in res.warnings],
            "recommendations": res.recommendations,
            "checks_evaluated": len(res.checks),
        }


class DeploymentPreviewTool(BaseDeploymentTool):
    """Generates structured two-stage deployment preview for human review."""

    @property
    def name(self) -> str:
        return "deployment_preview"

    @property
    def description(self) -> str:
        return "Generates a complete deployment preview showing branch, Quality Gate status, and target platform before user confirmation."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Name of the project to preview for deployment.",
                },
                "provider": {
                    "type": "string",
                    "enum": ["VERCEL", "RENDER", "RAILWAY", "MOCK"],
                    "description": "Target cloud deployment provider.",
                },
                "target_environment": {
                    "type": "string",
                    "description": "Deployment environment (production or preview).",
                },
            },
            "required": ["project_name"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        project_name = arguments.get("project_name", "")
        prov_str = arguments.get("provider", "VERCEL").upper()
        target_env = arguments.get("target_environment", "production")
        try:
            provider = DeploymentProvider(prov_str)
        except ValueError:
            provider = DeploymentProvider.VERCEL

        req = DeploymentRequest(
            project_name=project_name,
            provider=provider,
            target_environment=target_env,
        )
        preview = await self.engine.preview(req)
        return {
            "success": True,
            "project_name": preview.project_name,
            "provider": preview.provider.value,
            "framework": preview.framework.value,
            "branch": preview.branch,
            "quality_gate_status": preview.quality_gate_status,
            "git_status": preview.git_status,
            "target_environment": preview.target_environment,
            "uncommitted_changes": preview.uncommitted_changes,
            "confirmation_required": preview.confirmation_required,
            "preview_message": preview.preview_message,
        }


class DeploymentDeployTool(BaseDeploymentTool):
    """Executes project deployment (requires explicit human confirmation)."""

    @property
    def name(self) -> str:
        return "deployment_deploy"

    @property
    def description(self) -> str:
        return "Executes project deployment to the specified provider. Requires confirmed=True."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "project_name": {
                    "type": "string",
                    "description": "Name of the project to deploy.",
                },
                "provider": {
                    "type": "string",
                    "enum": ["VERCEL", "RENDER", "RAILWAY", "MOCK"],
                    "description": "Target provider.",
                },
                "target_environment": {
                    "type": "string",
                    "description": "Deployment environment (production, preview).",
                },
                "confirmed": {
                    "type": "boolean",
                    "description": "Mandatory user approval confirmation flag.",
                },
            },
            "required": ["project_name", "confirmed"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        project_name = arguments.get("project_name", "")
        prov_str = arguments.get("provider", "VERCEL").upper()
        confirmed = bool(arguments.get("confirmed", False))
        target_env = arguments.get("target_environment", "production")

        try:
            provider = DeploymentProvider(prov_str)
        except ValueError:
            provider = DeploymentProvider.VERCEL

        req = DeploymentRequest(
            project_name=project_name,
            provider=provider,
            target_environment=target_env,
            confirmed=confirmed,
        )

        res = await self.engine.deploy(req)
        return {
            "success": res.success,
            "project_name": res.project_name,
            "provider": res.provider.value,
            "deployment_status": res.deployment_status.value,
            "deployment_url": res.deployment_url,
            "deployment_id": res.deployment_id,
            "message": sanitize_deployment_output(res.message),
            "sanitized_error": res.sanitized_error,
            "execution_time_ms": res.execution_time_ms,
            "verification": res.verification_result.model_dump() if res.verification_result else None,
        }


class DeploymentStatusTool(BaseDeploymentTool):
    """Queries the status of an ongoing or completed deployment."""

    @property
    def name(self) -> str:
        return "deployment_status"

    @property
    def description(self) -> str:
        return "Queries the status of a deployment from the provider."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "enum": ["VERCEL", "RENDER", "RAILWAY", "MOCK"],
                },
                "deployment_id": {
                    "type": "string",
                    "description": "Deployment ID returned from a previous deployment request.",
                },
            },
            "required": ["deployment_id"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        prov_str = arguments.get("provider", "VERCEL").upper()
        d_id = arguments.get("deployment_id", "")
        try:
            provider = DeploymentProvider(prov_str)
        except ValueError:
            provider = DeploymentProvider.VERCEL

        st = self.engine.get_status(provider=provider, deployment_id=d_id)
        return {
            "success": True,
            "deployment_id": d_id,
            "status": st.value,
        }


class DeploymentVerifyTool(BaseDeploymentTool):
    """Verifies that a deployment URL is reachable and serving valid HTTP responses."""

    @property
    def name(self) -> str:
        return "deployment_verify"

    @property
    def description(self) -> str:
        return "Performs safe HTTP health check against a deployed URL to confirm reachability."

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Public URL of the deployed application to probe.",
                }
            },
            "required": ["url"],
        }

    async def execute(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        url = arguments.get("url", "")
        if not url:
            return {"success": False, "error": "Argument 'url' is required."}

        verif_res = await self.engine.verifier.verify_endpoint(url)
        return {
            "success": verif_res.reachable,
            "url": verif_res.url,
            "status_code": verif_res.status_code,
            "response_time_ms": verif_res.response_time_ms,
            "message": verif_res.message,
        }
