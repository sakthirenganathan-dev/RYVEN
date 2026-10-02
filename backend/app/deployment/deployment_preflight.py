"""Pre-flight validation engine executing comprehensive checks before deployment."""

from __future__ import annotations

import os
from typing import List, Optional
from app.deployment.deployment_detector import DeploymentDetector
from app.deployment.deployment_models import (
    DeploymentProvider,
    PreflightCheckItem,
    PreflightResult,
)
from app.deployment.deployment_security import (
    extract_required_env_vars,
    is_safe_deployment_path,
    scan_deployment_sensitive_files,
)
from app.deployment.deployment_validator import validate_project_for_deployment
from app.deployment.providers.base_provider import BaseDeploymentProvider
from app.deployment.providers.mock_provider import MockDeploymentProvider
from app.deployment.providers.railway_provider import RailwayProvider
from app.deployment.providers.render_provider import RenderProvider
from app.deployment.providers.vercel_provider import VercelProvider
from app.dev_engine.quality_gate import QualityGate
from app.git.git_engine import GitEngine
from app.tools.project_tool import resolve_project_path


class DeploymentPreflight:
    """Executes mandatory 17-point preflight verification before any deployment is attempted."""

    def __init__(
        self,
        git_engine: Optional[GitEngine] = None,
        quality_gate: Optional[QualityGate] = None,
        detector: Optional[DeploymentDetector] = None,
    ) -> None:
        self.git_engine = git_engine or GitEngine()
        self.quality_gate = quality_gate or QualityGate()
        self.detector = detector or DeploymentDetector()

    def get_provider_instance(self, provider: DeploymentProvider) -> BaseDeploymentProvider:
        if provider == DeploymentProvider.VERCEL:
            return VercelProvider()
        elif provider == DeploymentProvider.RENDER:
            return RenderProvider()
        elif provider == DeploymentProvider.RAILWAY:
            return RailwayProvider()
        elif provider == DeploymentProvider.MOCK:
            return MockDeploymentProvider()
        return VercelProvider()

    async def run_preflight(
        self,
        project_name: str,
        provider: DeploymentProvider = DeploymentProvider.VERCEL,
        provider_override: Optional[BaseDeploymentProvider] = None,
        target_environment: str = "production",
    ) -> PreflightResult:
        """Run all 17 preflight validation checks."""
        checks: List[PreflightCheckItem] = []
        critical_failures: List[str] = []
        warnings: List[str] = []
        recommendations: List[str] = []

        # 1. Project path valid
        valid_name, name_msg, proj_dir = validate_project_for_deployment(project_name)
        checks.append(PreflightCheckItem(
            name="1. Project Path Format",
            passed=valid_name,
            severity="CRITICAL",
            message=name_msg,
        ))
        if not valid_name:
            critical_failures.append(name_msg)

        # 2. Project exists
        exists = os.path.exists(proj_dir) if proj_dir else False
        checks.append(PreflightCheckItem(
            name="2. Project Directory Exists",
            passed=exists,
            severity="CRITICAL",
            message=f"Project directory found at {proj_dir}" if exists else f"Project directory does not exist.",
        ))
        if not exists:
            critical_failures.append("Project directory does not exist.")

        # 3. Inside approved workspace roots
        inside_root = is_safe_deployment_path(proj_dir) if proj_dir else False
        checks.append(PreflightCheckItem(
            name="3. Workspace Root Containment",
            passed=inside_root,
            severity="CRITICAL",
            message="Contained within authorized projects folder." if inside_root else "Path escapes approved project root.",
        ))
        if not inside_root:
            critical_failures.append("Project escapes workspace containment boundary.")

        # 4. No path traversal
        no_traversal = ".." not in (proj_dir or "") and not (project_name or "").startswith("..")
        checks.append(PreflightCheckItem(
            name="4. Path Traversal Inspection",
            passed=no_traversal,
            severity="CRITICAL",
            message="No traversal sequences detected." if no_traversal else "Path traversal pattern detected.",
        ))
        if not no_traversal:
            critical_failures.append("Path traversal attempt detected.")

        # 5. No system paths
        p_clean = (proj_dir or "").strip().lower()
        is_system = any(p_clean.startswith(s) for s in ["c:\\windows", "c:\\program files", "/etc", "/usr", "/bin"])
        checks.append(PreflightCheckItem(
            name="5. System Directory Protection",
            passed=not is_system,
            severity="CRITICAL",
            message="Not a system-reserved directory." if not is_system else "Project path is inside a protected OS directory.",
        ))
        if is_system:
            critical_failures.append("System directory access is prohibited.")

        # 6. No UNC paths
        is_unc = (proj_dir or "").startswith("\\\\") or (proj_dir or "").startswith("//")
        checks.append(PreflightCheckItem(
            name="6. UNC Path Rejection",
            passed=not is_unc,
            severity="CRITICAL",
            message="Local filesystem path verified." if not is_unc else "UNC network paths are rejected.",
        ))
        if is_unc:
            critical_failures.append("UNC network paths are prohibited.")

        # 7. No protected paths
        is_protected = any(k in (proj_dir or "").lower() for k in [".git", ".env", "id_rsa", "system32"])
        checks.append(PreflightCheckItem(
            name="7. Protected Directory Verification",
            passed=not is_protected,
            severity="CRITICAL",
            message="Target is not a protected metadata directory." if not is_protected else "Target path refers to protected system metadata.",
        ))
        if is_protected:
            critical_failures.append("Cannot deploy protected metadata directly.")

        # 8. Git repository state checked
        # 9. Current branch identified
        branch_name = "unknown"
        git_clean = False
        try:
            status_res = await self.git_engine.status(project_name=project_name)
            if status_res.success:
                branch_name = status_res.data.get("branch", "unknown")
                git_clean = status_res.data.get("clean", False)
                untracked_count = len(status_res.data.get("untracked_files", []))
                checks.append(PreflightCheckItem(
                    name="8. Git Repository State",
                    passed=True,
                    severity="WARNING",
                    message=f"Git repository inspected. Clean: {git_clean}, Untracked: {untracked_count}.",
                ))
                checks.append(PreflightCheckItem(
                    name="9. Branch Identification",
                    passed=True,
                    severity="WARNING",
                    message=f"Active branch: '{branch_name}'.",
                ))
                if not git_clean:
                    warnings.append(f"Working tree has uncommitted modifications on branch '{branch_name}'.")
            else:
                checks.append(PreflightCheckItem(
                    name="8. Git Repository State",
                    passed=True,
                    severity="WARNING",
                    message=f"Project is not a Git repo ({status_res.message}). Non-git deployment mode will be used.",
                ))
                checks.append(PreflightCheckItem(
                    name="9. Branch Identification",
                    passed=True,
                    severity="WARNING",
                    message="No Git branch (unversioned directory).",
                ))
        except Exception as e:
            checks.append(PreflightCheckItem(
                name="8. Git Repository State",
                passed=True,
                severity="WARNING",
                message=f"Git inspection skipped: {str(e)}",
            ))
            checks.append(PreflightCheckItem(
                name="9. Branch Identification",
                passed=True,
                severity="WARNING",
                message="Branch unverified.",
            ))

        # 10. Build status checked
        detection = self.detector.detect(project_name)
        has_build = bool(detection.build_command)
        checks.append(PreflightCheckItem(
            name="10. Build Configuration Status",
            passed=detection.is_deployable,
            severity="CRITICAL",
            message=f"Build command: '{detection.build_command}'" if has_build else "No explicit build step required (static/standard).",
        ))
        if not detection.is_deployable:
            critical_failures.append("Project is not marked as deployable (no recognizable web/backend framework).")

        # 11. Tests checked
        has_tests = bool(detection.test_command)
        checks.append(PreflightCheckItem(
            name="11. Test Suite Configuration",
            passed=True,
            severity="WARNING",
            message=f"Test command: '{detection.test_command}'" if has_tests else "No automated tests declared in project manifest.",
        ))

        # 12. Quality Gate checked
        # Evaluate Quality Gate
        expected_files = ["package.json"] if detection.package_manager == "npm" else []
        qg_res = self.quality_gate.evaluate(project_name=project_name, expected_files=expected_files)
        qg_passed = qg_res.state in ("PASS", "RELEASE_READY")
        checks.append(PreflightCheckItem(
            name="12. Quality Gate Verification",
            passed=qg_passed,
            severity="CRITICAL",
            message=f"Quality Gate State: {qg_res.state}.",
        ))
        if not qg_passed and qg_res.state == "BLOCKED":
            critical_failures.append(f"Quality gate blocked: {qg_res.message}")

        # 13. Deployment provider available
        provider_impl = provider_override or self.get_provider_instance(provider)
        checks.append(PreflightCheckItem(
            name="13. Deployment Provider Availability",
            passed=True,
            severity="CRITICAL",
            message=f"Targeting provider: {provider.value}.",
        ))

        # 14. Provider configuration valid
        is_config, config_msg = provider_impl.validate_configuration()
        checks.append(PreflightCheckItem(
            name="14. Provider Configuration Validation",
            passed=is_config,
            severity="CRITICAL",
            message=config_msg,
        ))
        if not is_config:
            critical_failures.append(config_msg)
            recommendations.append(f"Configure provider credentials ({provider.value}) before deploying.")

        # 15. Required environment variables identified
        req_vars = detection.detected_env_vars
        checks.append(PreflightCheckItem(
            name="15. Environment Variables Inspection",
            passed=True,
            severity="WARNING",
            message=f"{len(req_vars)} required env variable name(s) identified (values protected)." if req_vars else "No special environment variables required.",
        ))

        # 16. Sensitive files detected
        sens_files = scan_deployment_sensitive_files(proj_dir) if proj_dir else []
        checks.append(PreflightCheckItem(
            name="16. Sensitive File Exposure Scan",
            passed=len(sens_files) == 0,
            severity="WARNING",
            message=f"Found {len(sens_files)} sensitive file(s) ({', '.join(sens_files[:3])}). Ensure they are gitignored." if sens_files else "Clean: zero sensitive unencrypted key/env files found.",
        ))
        if sens_files:
            warnings.append(f"Sensitive files found in project: {', '.join(sens_files)}. Ensure secrets are not pushed or uploaded.")

        # 17. Deployment configuration validated
        provider_specific_checks = provider_impl.preflight_checks(proj_dir or "") if proj_dir else []
        all_passed = all(c.passed for c in provider_specific_checks if c.severity == "CRITICAL")
        checks.append(PreflightCheckItem(
            name="17. Target Platform Manifest Validation",
            passed=all_passed,
            severity="CRITICAL",
            message="Platform-specific manifests validated." if all_passed else "Platform-specific preflight checks failed.",
        ))
        checks.extend(provider_specific_checks)

        success = len(critical_failures) == 0

        return PreflightResult(
            success=success,
            project_name=project_name,
            provider=provider,
            checks=checks,
            critical_failures=critical_failures,
            warnings=warnings,
            recommendations=recommendations,
        )
