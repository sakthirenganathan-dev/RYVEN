"""Base abstract class for all deployment providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple
from app.deployment.deployment_models import (
    DeploymentPreview,
    DeploymentProvider,
    DeploymentRequest,
    DeploymentResult,
    DeploymentStatus,
    PreflightCheckItem,
    VerificationResult,
)


class BaseDeploymentProvider(ABC):
    """Abstract base provider for cloud hosting platforms."""

    @property
    @abstractmethod
    def provider_type(self) -> DeploymentProvider:
        """The specific deployment provider enum."""
        ...

    @abstractmethod
    def validate_configuration(self) -> Tuple[bool, str]:
        """Verify that necessary credentials/environment configuration are present."""
        ...

    @abstractmethod
    def preflight_checks(self, project_dir: str) -> List[PreflightCheckItem]:
        """Perform provider-specific preflight validation checks."""
        ...

    @abstractmethod
    def deploy(self, request: DeploymentRequest, project_dir: str) -> DeploymentResult:
        """Execute the deployment according to provider specification."""
        ...

    @abstractmethod
    def get_status(self, deployment_id: str) -> DeploymentStatus:
        """Query the runtime status of an active or completed deployment."""
        ...

    @abstractmethod
    def get_deployment_url(self, deployment_id: str) -> Optional[str]:
        """Retrieve the live public URL for a deployment."""
        ...
