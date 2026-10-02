"""Live Windows validation script for RYVEN Milestone 12 Deployment Engine."""

import asyncio
import json
import os
import shutil
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from app.core.assistant import Assistant
from app.core.router import IntentRouter
from app.deployment.deployment_detector import DeploymentDetector
from app.deployment.deployment_engine import DeploymentEngine
from app.deployment.deployment_models import DeploymentProvider, DeploymentRequest, DeploymentStatus
from app.deployment.deployment_security import sanitize_deployment_output
from app.deployment.providers.mock_provider import MockDeploymentProvider
from app.tools.project_tool import get_projects_root


async def main():
    print("=" * 60)
    print("RYVEN 2.0 MILESTONE 12 — LIVE WINDOWS VERIFICATION")
    print("=" * 60)

    # Setup a sample project
    root = get_projects_root()
    sample_name = "live_m12_validation_app"
    sample_dir = os.path.join(root, sample_name)
    os.makedirs(sample_dir, exist_ok=True)

    with open(os.path.join(sample_dir, "package.json"), "w", encoding="utf-8") as f:
        f.write('{"name": "live-app", "scripts": {"build": "vite build"}, "dependencies": {"react": "^18.0.0", "vite": "^5.0.0"}}')
    with open(os.path.join(sample_dir, ".env.example"), "w", encoding="utf-8") as f:
        f.write("VITE_API_URL=https://api.example.com\n")

    try:
        # 1. Project Detection
        print("\n--- 1. PROJECT & FRAMEWORK DETECTION ---")
        detector = DeploymentDetector()
        detect_res = detector.detect(sample_name)
        print(f"Project: {detect_res.project_name}")
        print(f"Framework: {detect_res.framework.value}")
        print(f"Package Manager: {detect_res.package_manager}")
        print(f"Build Command: {detect_res.build_command}")
        print(f"Deployable: {detect_res.is_deployable}")
        print(f"Detected Env Vars: {detect_res.detected_env_vars}")
        assert detect_res.is_deployable is True
        assert detect_res.framework.value == "REACT_VITE"
        print(">>> CHECK 1 PASSED: Project framework safely detected from metadata.")

        # 2. 17-Point Preflight Checks
        print("\n--- 2. 17-POINT PREFLIGHT VALIDATION ---")
        engine = DeploymentEngine()
        mock_prov = MockDeploymentProvider(mode="SUCCESS")
        engine.register_provider(mock_prov)
        preflight_res = await engine.run_preflight(
            project_name=sample_name,
            provider=DeploymentProvider.MOCK,
            provider_override=mock_prov,
        )
        print(f"Preflight Success: {preflight_res.success}")
        print(f"Checks Count: {len(preflight_res.checks)}")
        print(f"Critical Failures: {preflight_res.critical_failures}")
        assert preflight_res.success is True
        assert len(preflight_res.checks) >= 17
        print(">>> CHECK 2 PASSED: 17-point preflight validation verified.")

        # 3. Two-Stage Preview Generation
        print("\n--- 3. DEPLOYMENT PREVIEW GENERATION ---")
        preview = await engine.preview(DeploymentRequest(
            project_name=sample_name,
            provider=DeploymentProvider.MOCK,
        ))
        print("Preview Output:")
        print(preview.preview_message)
        assert preview.confirmation_required is True
        assert "RYVEN DEPLOYMENT PREVIEW" in preview.preview_message
        print(">>> CHECK 3 PASSED: Two-stage preview generated with confirmation required.")

        # 4. Confirmation Gate Enforcement
        print("\n--- 4. CONFIRMATION GATE ENFORCEMENT ---")
        unconfirmed_req = DeploymentRequest(
            project_name=sample_name,
            provider=DeploymentProvider.MOCK,
            confirmed=False,
        )
        halted_res = await engine.deploy(unconfirmed_req, provider_override=mock_prov)
        print(f"Halted Status: {halted_res.deployment_status.value}")
        print(f"Halted Message: {halted_res.message}")
        assert halted_res.deployment_status == DeploymentStatus.WAITING_CONFIRMATION
        assert halted_res.success is False
        print(">>> CHECK 4 PASSED: Deployment halted when user confirmation is not provided.")

        # 5. Confirmed Deployment & Verification
        print("\n--- 5. CONFIRMED DEPLOYMENT EXECUTION ---")
        confirmed_req = DeploymentRequest(
            project_name=sample_name,
            provider=DeploymentProvider.MOCK,
            confirmed=True,
        )
        deploy_res = await engine.deploy(confirmed_req, provider_override=mock_prov)
        print(f"Deployment Success: {deploy_res.success}")
        print(f"Deployment URL: {deploy_res.deployment_url}")
        print(f"Deployment ID: {deploy_res.deployment_id}")
        assert deploy_res.success is True
        assert deploy_res.deployment_status == DeploymentStatus.SUCCESS
        assert deploy_res.deployment_url is not None
        print(">>> CHECK 5 PASSED: Confirmed deployment executed and URL returned.")

        # 6. Secret Redaction
        print("\n--- 6. SECRET REDACTION IN LOGS & OUTPUT ---")
        raw_msg = "Error deploying: VERCEL_TOKEN=secret_token_12345678 and RENDER_API_KEY=rnd_key1234567890abcdef"
        sanitized = sanitize_deployment_output(raw_msg)
        print(f"Raw:       {raw_msg}")
        print(f"Sanitized: {sanitized}")
        assert "secret_token_12345678" not in sanitized
        assert "rnd_key1234567890abcdef" not in sanitized
        print(">>> CHECK 6 PASSED: Secrets reliably redacted from all output.")

        # 7. Router Intent Separation
        print("\n--- 7. INTENT ROUTER SEPARATION ---")
        router = IntentRouter()
        deploy_intent = router.route("Deploy this project to Vercel")
        print(f"Query: 'Deploy this project to Vercel' -> Intent: {deploy_intent.intent} ({deploy_intent.workflow_name})")
        assert deploy_intent.intent == "workflow"
        assert deploy_intent.workflow_name == "deployment"

        edu_intent = router.route("What is Vercel?")
        print(f"Query: 'What is Vercel?' -> Intent: {edu_intent.intent} ({edu_intent.reason})")
        assert edu_intent.intent == "ai"
        print(">>> CHECK 7 PASSED: Operational queries route to workflow, educational queries route to AI.")

        print("\n" + "=" * 60)
        print("ALL 7 LIVE WINDOWS VALIDATION CHECKS PASSED (100%)!")
        print("=" * 60)

    finally:
        if os.path.exists(sample_dir):
            shutil.rmtree(sample_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
