"""Strongly typed models and enums for RYVEN 2.0 Milestone 12 Deployment Engine."""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DeploymentProvider(str, Enum):
    """Supported deployment target providers."""
    VERCEL = "VERCEL"
    RENDER = "RENDER"
    RAILWAY = "RAILWAY"
    MOCK = "MOCK"


class DeploymentFramework(str, Enum):
    """Detected project framework types."""
    REACT_VITE = "REACT_VITE"
    NEXTJS = "NEXTJS"
    FASTAPI = "FASTAPI"
    NODE = "NODE"
    PYTHON = "PYTHON"
    UNKNOWN = "UNKNOWN"


class DeploymentStatus(str, Enum):
    """Lifecycle status of a deployment operation."""
    DETECTING = "DETECTING"
    PREFLIGHT = "PREFLIGHT"
    PREVIEW = "PREVIEW"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    DEPLOYING = "DEPLOYING"
    VERIFYING = "VERIFYING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CANCELLED = "CANCELLED"


class ProjectDetectionResult(BaseModel):
    """Structured result of project framework and configuration detection."""
    project_name: str
    project_path: str
    framework: DeploymentFramework = DeploymentFramework.UNKNOWN
    project_type: str = "unknown"
    package_manager: str = "unknown"
    build_command: str = ""
    test_command: str = ""
    output_dir: str = ""
    detected_env_vars: List[str] = Field(default_factory=list)
    is_deployable: bool = False
    notes: List[str] = Field(default_factory=list)


class PreflightCheckItem(BaseModel):
    """A single evaluation check within the deployment preflight."""
    name: str
    passed: bool
    severity: str = "CRITICAL"  # "CRITICAL" or "WARNING"
    message: str = ""


class PreflightResult(BaseModel):
    """Outcome of the deployment preflight validation."""
    success: bool
    project_name: str
    provider: DeploymentProvider
    checks: List[PreflightCheckItem] = Field(default_factory=list)
    critical_failures: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    recommendations: List[str] = Field(default_factory=list)


class DeploymentPreview(BaseModel):
    """Two-stage deployment preview shown to the user prior to execution."""
    project_name: str
    provider: DeploymentProvider
    framework: DeploymentFramework
    branch: str = "main"
    build_status: str = "NOT_CHECKED"
    test_status: str = "NOT_CHECKED"
    quality_gate_status: str = "NOT_CHECKED"
    git_status: str = "UNKNOWN"
    target_environment: str = "production"
    uncommitted_changes: int = 0
    required_env_vars: List[str] = Field(default_factory=list)
    action_description: str = "Deploy project to remote infrastructure"
    confirmation_required: bool = True
    preview_message: str = ""


class DeploymentRequest(BaseModel):
    """User or system request to trigger a deployment."""
    project_name: str
    provider: DeploymentProvider = DeploymentProvider.VERCEL
    target_environment: str = "production"
    branch: str = "main"
    confirmed: bool = False
    skip_tests: bool = False
    custom_build_command: Optional[str] = None


class VerificationResult(BaseModel):
    """Result of HTTP health check and endpoint verification."""
    reachable: bool
    status_code: Optional[int] = None
    response_time_ms: float = 0.0
    url: str = ""
    message: str = ""
    headers_checked: Dict[str, str] = Field(default_factory=dict)


class DeploymentResult(BaseModel):
    """Final sanitized result of a deployment operation."""
    success: bool
    provider: DeploymentProvider
    project_name: str
    deployment_status: DeploymentStatus
    deployment_url: Optional[str] = None
    deployment_id: Optional[str] = None
    message: str = ""
    verification_result: Optional[VerificationResult] = None
    health_result: Optional[Any] = None
    sanitized_error: Optional[str] = None
    execution_time_ms: float = 0.0
