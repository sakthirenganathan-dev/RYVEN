"""Validation logic for project names, directories, providers, and deployment parameters."""

from __future__ import annotations

import os
from typing import Optional, Tuple
from app.deployment.deployment_models import DeploymentProvider, DeploymentRequest
from app.deployment.deployment_security import is_safe_deployment_path
from app.tools.project_tool import resolve_project_path


def validate_project_for_deployment(project_name: str) -> Tuple[bool, str, Optional[str]]:
    """Validate that the project exists, has a safe path, and is accessible."""
    if not project_name or not isinstance(project_name, str):
        return False, "Project name must be a non-empty string.", None

    project_name = project_name.strip()
    if not project_name or any(c in project_name for c in '<>:"/\\|?*'):
        return False, f"Invalid project name format: '{project_name}'", None

    # Resolve project path
    proj_dir = resolve_project_path(project_name)
    if not proj_dir:
        return False, f"Could not resolve project path for '{project_name}'.", None

    if not os.path.exists(proj_dir):
        return False, f"Project directory '{project_name}' does not exist.", None

    if not os.path.isdir(proj_dir):
        return False, f"Project path '{proj_dir}' is not a directory.", None

    if not is_safe_deployment_path(proj_dir):
        return False, f"Project path '{proj_dir}' violates containment policy.", None

    return True, "Project directory is valid and contained.", proj_dir


def validate_provider(provider_input: str) -> Tuple[bool, Optional[DeploymentProvider], str]:
    """Validate and normalize deployment provider."""
    if not provider_input or not isinstance(provider_input, str):
        return False, None, "Provider must be specified."

    clean = provider_input.strip().upper()
    try:
        provider = DeploymentProvider(clean)
        return True, provider, f"Valid provider: {provider.value}"
    except ValueError:
        valid_options = ", ".join([p.value for p in DeploymentProvider])
        return False, None, f"Unsupported deployment provider '{provider_input}'. Supported: {valid_options}"


def validate_environment(env_str: str) -> Tuple[bool, str]:
    """Validate target environment (production, staging, preview)."""
    clean = env_str.strip().lower()
    allowed = {"production", "staging", "preview", "development"}
    if clean in allowed:
        return True, clean
    return False, f"Unsupported environment '{env_str}'. Allowed: {', '.join(sorted(allowed))}"


def validate_deployment_request(request: DeploymentRequest) -> Tuple[bool, str]:
    """Validate all fields of a DeploymentRequest."""
    valid_proj, proj_msg, _ = validate_project_for_deployment(request.project_name)
    if not valid_proj:
        return False, proj_msg

    valid_env, _ = validate_environment(request.target_environment)
    if not valid_env:
        return False, f"Invalid target environment '{request.target_environment}'"

    return True, "Deployment request is valid."
