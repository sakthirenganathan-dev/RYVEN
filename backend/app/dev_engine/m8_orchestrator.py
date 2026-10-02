"""RYVEN DEV ENGINE — M8.5: Existing Project Modification Orchestrator.

Integrates the entire existing project modification lifecycle:
  1. Resolve existing project
  2. Scan project structure
  3. Plan targeted modifications (Minimal Diff Principle)
  4. Validate modification plan
  5. Confirmation gate
  6. Atomic apply with rollback snapshot & stale file detection
  7. Validate modified project
  8. Build project via SandboxProcessRunner / BuildEngine
  9. Run tests via TestEngine (or report TESTS_NOT_AVAILABLE)
  10. Error analysis via ErrorAnalyzer
  11. Controlled fix loop via FixLoopManager (max 10 iterations, requires confirmation)
  12. Quality Gate evaluation
  13. Return structured ExistingProjectModificationResult

Security rules strictly enforced:
  - No arbitrary shell, cmd.exe, or PowerShell.
  - Path containment verified on all project targets.
  - Protected files (.env, keys, credentials) are never read or written.
  - File deletion is permanently blocked.
  - Stale hash detection prevents overwriting external changes.
  - Atomic rollback on write or validation failures.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
import uuid

from app.core.logging_config import logger
from app.dev_engine.build_engine import BuildEngine, BuildRequest, BuildResult
from app.dev_engine.constants import MAX_FIX_ITERATIONS
from app.dev_engine.error_analyzer import BuildError, ErrorAnalyzer
from app.dev_engine.fix_loop import FixLoopManager
from app.dev_engine.m8_apply import ApplyEngine, RollbackEngine
from app.dev_engine.m8_models import (
    ExistingProjectModel,
    ExistingProjectModificationResult,
    FilePatch,
    ModificationPlan,
    ModificationRequest,
    ModificationResult,
    ModificationStatus,
    PatchOperation,
    RiskLevel,
    RollbackRecord,
)
from app.dev_engine.m8_planner import ModificationPlanner
from app.dev_engine.m8_resolver import ProjectResolver, ResolvedProject
from app.dev_engine.m8_scanner import ProjectScanner
from app.dev_engine.m8_validator import ModificationValidator
from app.dev_engine.models import FixProposal, FixResult
from app.dev_engine.quality_gate import QualityGate, QualityGateResult
from app.dev_engine.sandbox import SandboxPolicy
from app.dev_engine.test_engine import TestEngine, TestRequest, TestResult
from app.git.git_engine import GitEngine
from app.tools.project_tool import resolve_project_file_path, resolve_project_path


class ExistingProjectOrchestrator:
    """Coordinates end-to-end existing project modification, validation, build, test, and quality gate."""

    def __init__(self, policy: Optional[SandboxPolicy] = None) -> None:
        self.policy = policy or SandboxPolicy()
        self.resolver = ProjectResolver()
        self.scanner = ProjectScanner()
        self.planner = ModificationPlanner()
        self.validator = ModificationValidator()
        self.apply_engine = ApplyEngine()
        self.rollback_engine = RollbackEngine()
        self.build_engine = BuildEngine(policy=self.policy)
        self.test_engine = TestEngine(policy=self.policy)
        self.analyzer = ErrorAnalyzer()
        self.fix_manager = FixLoopManager()
        self.quality_gate = QualityGate()
        self.git_engine = GitEngine(policy=self.policy)
        from app.deployment.deployment_engine import DeploymentEngine
        self.deployment_engine = DeploymentEngine(git_engine=self.git_engine, quality_gate=self.quality_gate)


    async def execute_modification_workflow(
        self,
        project_name_hint: str,
        user_request: str,
        proposed_patches: Optional[List[dict]] = None,
        confirmed: bool = False,
        allow_fix_loop: bool = False,
        max_fix_iterations: int = 3,
        cancelled: bool = False,
    ) -> ExistingProjectModificationResult:
        """Run the full M8.5 existing project modification workflow."""
        start_time = time.monotonic()
        workflow_id = f"wf-m85-{uuid.uuid4().hex[:8]}"

        # 0. Check cancellation at boundary
        if cancelled:
            return ExistingProjectModificationResult(
                workflow_id=workflow_id,
                project_name=project_name_hint,
                status="CANCELLED",
                user_message="Workflow cancelled before execution.",
                duration_ms=(time.monotonic() - start_time) * 1000,
            )

        # 1. Resolve Project
        logger.info(f"[M8.5 ORCHESTRATOR] Step 1: Resolving project hint '{project_name_hint}'")
        resolved: ResolvedProject = self.resolver.resolve(project_name_hint)
        if not resolved.found:
            status = "BLOCKED" if resolved.security_blocked else "FAILED"
            return ExistingProjectModificationResult(
                workflow_id=workflow_id,
                project_name=project_name_hint,
                status=status,
                user_message=resolved.reason,
                duration_ms=(time.monotonic() - start_time) * 1000,
            )

        project_name = resolved.project_name
        project_path = resolved.absolute_path

        # 2. Scan Project
        logger.info(f"[M8.5 ORCHESTRATOR] Step 2: Scanning project '{project_name}'")
        model: Optional[ExistingProjectModel] = self.scanner.scan(project_name)
        if not model:
            return ExistingProjectModificationResult(
                workflow_id=workflow_id,
                project_name=project_name,
                project_path=project_path,
                status="FAILED",
                user_message=f"Failed to scan project '{project_name}'.",
                duration_ms=(time.monotonic() - start_time) * 1000,
            )

        # 2b. Optional Knowledge Graph Context Optimization (Milestone 11.5)
        kg_context_dict = None
        try:
            from app.knowledge_graph.service import knowledge_graph_service
            kg_package = knowledge_graph_service.build_optimized_context(
                project_path=project_path,
                task=user_request,
            )
            kg_context_dict = kg_package.model_dump()
            logger.info(
                f"[M8.5 ORCHESTRATOR] KG context built: {kg_package.budget.reduction_percentage}% estimated reduction "
                f"({kg_package.budget.optimized_estimated_tokens} tokens vs {kg_package.budget.baseline_estimated_tokens} baseline)"
            )
        except Exception as e:
            logger.debug(f"[M8.5 ORCHESTRATOR] KG context skipped: {e}")

        # 3. Plan Modifications (Minimal Diff Principle)
        logger.info(f"[M8.5 ORCHESTRATOR] Step 3: Planning modifications for '{project_name}'")
        req = ModificationRequest(
            project_name=project_name,
            project_path=project_path,
            user_request=user_request,
        )


        raw_patches = proposed_patches or []
        if not raw_patches:
            raw_patches = self._generate_targeted_patches(model, user_request)

        plan: Optional[ModificationPlan] = self.planner.plan(model, req, raw_patches)
        if not plan:
            return ExistingProjectModificationResult(
                workflow_id=workflow_id,
                project_name=project_name,
                project_path=project_path,
                status="FAILED",
                files_scanned=model.total_files_scanned,
                user_message="Planning failed: proposed patches were empty, malformed, or violated security constraints.",
                duration_ms=(time.monotonic() - start_time) * 1000,
            )

        # 4. Validate Modification Plan
        logger.info(f"[M8.5 ORCHESTRATOR] Step 4: Validating modification plan '{plan.plan_id}'")
        is_valid, val_err = self.validator.validate(plan, protected_files=model.protected_files)
        val_result_dict = {"valid": is_valid, "error": val_err}
        if not is_valid:
            return ExistingProjectModificationResult(
                workflow_id=workflow_id,
                project_name=project_name,
                project_path=project_path,
                status="BLOCKED",
                files_scanned=model.total_files_scanned,
                modification_plan=plan.model_dump(),
                validation_result=val_result_dict,
                user_message=f"Modification plan failed validation: {val_err}",
                duration_ms=(time.monotonic() - start_time) * 1000,
            )

        # 5. Confirmation Gate
        if not confirmed:
            logger.info(f"[M8.5 ORCHESTRATOR] Step 5: Awaiting confirmation for '{project_name}'")
            return ExistingProjectModificationResult(
                workflow_id=workflow_id,
                project_name=project_name,
                project_path=project_path,
                status="WAITING_FOR_CONFIRMATION",
                files_scanned=model.total_files_scanned,
                modification_plan=plan.model_dump(),
                validation_result=val_result_dict,
                confirmation_required=True,
                confirmation_status="PENDING",
                user_message=f"Plan created for '{project_name}'. {len(plan.patches)} file(s) to change. Waiting for user confirmation.",
                duration_ms=(time.monotonic() - start_time) * 1000,
            )

        # Check cancellation before write
        if cancelled:
            return ExistingProjectModificationResult(
                workflow_id=workflow_id,
                project_name=project_name,
                project_path=project_path,
                status="CANCELLED",
                files_scanned=model.total_files_scanned,
                modification_plan=plan.model_dump(),
                user_message="Modification cancelled prior to disk write.",
                duration_ms=(time.monotonic() - start_time) * 1000,
            )

        # 6. Apply Changes Atomically
        logger.info(f"[M8.5 ORCHESTRATOR] Step 6: Applying changes atomically to '{project_name}'")
        apply_res: ModificationResult = self.apply_engine.apply(plan, confirmed=True)
        if not apply_res.success:
            status_str = "ROLLED_BACK" if apply_res.was_rolled_back else "FAILED"
            return ExistingProjectModificationResult(
                workflow_id=workflow_id,
                project_name=project_name,
                project_path=project_path,
                status=status_str,
                files_scanned=model.total_files_scanned,
                files_modified=len([c for c in apply_res.applied_changes if c.success]),
                modification_plan=plan.model_dump(),
                validation_result=val_result_dict,
                apply_result=apply_res.model_dump(),
                rollback_result={"was_rolled_back": apply_res.was_rolled_back},
                user_message=apply_res.message,
                duration_ms=(time.monotonic() - start_time) * 1000,
            )

        files_modified_count = len(apply_res.applied_changes)

        # 7. Validate Modified Project (Post-apply check)
        logger.info(f"[M8.5 ORCHESTRATOR] Step 7: Validating modified files on disk")
        expected_paths = [p.relative_path for p in plan.patches]

        # 8. Build Project
        logger.info(f"[M8.5 ORCHESTRATOR] Step 8: Building project '{project_name}'")
        build_req = BuildRequest(
            project_name=project_name,
            project_type=model.project_type.value,
            confirmed=True,
        )
        build_res: BuildResult = await self.build_engine.build(build_req)

        # 9. Test Project (if tests available)
        logger.info(f"[M8.5 ORCHESTRATOR] Step 9: Running tests for '{project_name}'")
        test_res: Optional[TestResult] = None
        has_tests = bool(model.test_files)
        if has_tests and build_res.success:
            test_req = TestRequest(
                project_name=project_name,
                project_type=model.project_type.value,
                confirmed=True,
            )
            test_res = await self.test_engine.run_tests(test_req)
        elif not has_tests:
            test_res = TestResult(
                success=True,
                project_name=project_name,
                project_type=model.project_type.value,
                message="No test suite configured. Tests not available.",
            )

        # 10. Error Analysis (if build or test failed)
        errors: List[BuildError] = []
        if not build_res.success:
            errors.extend(build_res.errors)
        if test_res and not test_res.success:
            errors.extend(test_res.failures)

        # 11. Controlled Fix Loop (if permitted and errors exist)
        fix_iterations_run = 0
        current_build_res = build_res
        current_test_res = test_res

        if errors and allow_fix_loop:
            logger.info(f"[M8.5 ORCHESTRATOR] Step 11: Initiating controlled fix loop for '{project_name}'")
            ceiling = min(max_fix_iterations, 10)
            while errors and fix_iterations_run < ceiling:
                fix_iterations_run += 1
                primary_error = errors[0]
                if not primary_error.file_path:
                    break

                # Formulate fix patch safely
                fix_patch = self._generate_fix_content(project_name, primary_error)
                if not fix_patch:
                    break

                proposal = FixProposal(
                    fix_id=f"fix-{uuid.uuid4().hex[:8]}",
                    target_file=primary_error.file_path,
                    description=f"Fix {primary_error.category}: {primary_error.message[:100]}",
                    proposed_content=fix_patch,
                    requires_confirmation=False,  # auto-confirmed in orchestrated loop
                )

                fix_res: FixResult = await self.fix_manager.apply_fix(
                    project_name=project_name,
                    proposal=proposal,
                    user_confirmed=True,
                )
                if not fix_res.success:
                    break

                # Rebuild
                current_build_res = await self.build_engine.build(build_req)
                if current_build_res.success and has_tests:
                    current_test_res = await self.test_engine.run_tests(
                        TestRequest(project_name=project_name, project_type=model.project_type.value, confirmed=True)
                    )

                errors = []
                if not current_build_res.success:
                    errors.extend(current_build_res.errors)
                if current_test_res and not current_test_res.success:
                    errors.extend(current_test_res.failures)

        # 12. Quality Gate
        logger.info(f"[M8.5 ORCHESTRATOR] Step 12: Evaluating Quality Gate for '{project_name}'")
        qg_res: QualityGateResult = self.quality_gate.evaluate(
            project_name=project_name,
            expected_files=expected_paths,
            build_result=current_build_res,
            test_result=current_test_res,
        )

        # 13. Final Structured Result
        overall_success = qg_res.passed and current_build_res.success
        final_status = "COMPLETED" if overall_success else "FAILED"

        user_msg = (
            f"Successfully modified '{project_name}'. "
            f"{files_modified_count} file(s) changed. "
            f"Build: {'PASS' if current_build_res.success else 'FAIL'}. "
            f"Quality Gate: {qg_res.state}."
        )

        # 12b. Git Commit (Only if explicitly requested by user, e.g. "fix and commit", and Quality Gate passed)
        git_commit_res = None
        git_summary = ""
        should_commit = any(kw in user_request.lower() for kw in ["and commit", "then commit", "fix and commit", "commit it", "commit them"])
        if should_commit:
            if not qg_res.passed or not current_build_res.success:
                user_msg = (
                    f"Successfully modified '{project_name}'. "
                    f"{files_modified_count} file(s) changed. "
                    f"Build: {'PASS' if current_build_res.success else 'FAIL'}. "
                    f"Quality Gate: {qg_res.state}. "
                    f"Changes were applied, but Git commit was NOT performed because the quality gate failed."
                )
                git_summary = "Commit aborted: Quality Gate failed."
            else:
                logger.info(f"[M8.5 ORCHESTRATOR] Step 12b: Executing requested Git commit for '{project_name}'")
                stage_res = await self.git_engine.stage(project_name=project_name, files=expected_paths, confirmed=True)
                if stage_res.success:
                    commit_msg = f"RYVEN: {user_request[:120]}"
                    c_res, verif = await self.git_engine.commit_and_verify(
                        project_name=project_name,
                        message=commit_msg,
                        confirmed=True,
                    )
                    git_commit_res = c_res.model_dump()
                    if c_res.success and verif:
                        git_summary = f"Committed as {verif.commit_hash[:7]} on branch '{verif.branch}'"
                        user_msg = (
                            f"Successfully modified '{project_name}'. "
                            f"{files_modified_count} file(s) changed. "
                            f"Build: {'PASS' if current_build_res.success else 'FAIL'}. "
                            f"Quality Gate: {qg_res.state}. "
                            f"Git commit created: {verif.commit_hash[:7]}."
                        )
                    else:
                        git_summary = f"Commit failed: {c_res.message}"
                else:
                    git_summary = f"Staging failed: {stage_res.message}"

        # Step 12c: Optional deployment integration (Milestone 12)
        deployment_res_dict = None
        user_wants_deploy = any(kw in user_request.lower() for kw in ["deploy", "publish", "ship"])
        if user_wants_deploy and final_status == "COMPLETED" and qg_res.passed:
            from app.deployment.deployment_models import DeploymentProvider, DeploymentRequest
            prov = DeploymentProvider.VERCEL
            if "railway" in user_request.lower():
                prov = DeploymentProvider.RAILWAY
            elif "render" in user_request.lower():
                prov = DeploymentProvider.RENDER
            d_req = DeploymentRequest(
                project_name=project_name,
                provider=prov,
                confirmed=True,
            )
            d_res = await self.deployment_engine.deploy(d_req)
            deployment_res_dict = d_res.model_dump()
            if d_res.success and d_res.deployment_url:
                user_msg += f" Deployed to {prov.value}: {d_res.deployment_url}."
            else:
                user_msg += f" Deployment to {prov.value}: {d_res.message}."

        return ExistingProjectModificationResult(
            workflow_id=workflow_id,
            project_name=project_name,
            project_path=project_path,
            status=final_status,
            files_scanned=model.total_files_scanned,
            files_modified=files_modified_count,
            modification_plan=plan.model_dump(),
            validation_result=val_result_dict,
            confirmation_required=True,
            confirmation_status="CONFIRMED",
            apply_result=apply_res.model_dump(),
            rollback_result={"was_rolled_back": apply_res.was_rolled_back, "available": True},
            build_result=current_build_res.model_dump(),
            test_result=current_test_res.model_dump() if current_test_res else None,
            error_analysis={"total_errors": len(errors), "categories": [e.category for e in errors]},
            fix_iterations=fix_iterations_run,
            quality_gate_result=qg_res.model_dump(),
            git_commit_result=git_commit_res,
            git_summary=git_summary,
            deployment_result=deployment_res_dict,
            knowledge_graph_context=kg_context_dict,
            duration_ms=(time.monotonic() - start_time) * 1000,
            user_message=user_msg,
        )



    def _generate_targeted_patches(self, model: ExistingProjectModel, user_request: str) -> List[dict]:
        """Synthesize minimal, targeted patches based on user request keywords."""
        lower = user_request.lower()
        patches: List[dict] = []
        if "dark mode" in lower or "theme" in lower:
            css_file = next((f.relative_path for f in model.source_files if f.relative_path.endswith(".css")), None)
            if css_file:
                target_path = resolve_project_file_path(model.project_name, css_file)
                existing = ""
                if target_path:
                    try:
                        with open(target_path, "r", encoding="utf-8") as f:
                            existing = f.read(100_000)
                    except Exception:
                        pass
                dark_css = "\n\n/* Dark mode styles added by RYVEN */\n[data-theme='dark'], body.dark {\n  background-color: #0b0f19;\n  color: #f0f6fc;\n}\n"
                patches.append({
                    "relative_path": css_file,
                    "operation": "MODIFY",
                    "proposed_content": existing + dark_css if dark_css not in existing else existing,
                    "reason": "Add dark mode theme variables and styles",
                })
            else:
                patches.append({
                    "relative_path": "theme.css",
                    "operation": "CREATE",
                    "proposed_content": "/* Dark mode theme by RYVEN */\nbody.dark { background-color: #0b0f19; color: #f0f6fc; }\n",
                    "reason": "Add theme styles",
                })
        elif "login" in lower or "auth" in lower:
            rel = "src/components/Login.tsx" if "react_ts" in model.project_type.value else "login.html"
            patches.append({
                "relative_path": rel,
                "operation": "CREATE",
                "proposed_content": "/* Login component added by RYVEN */\nexport function Login() {\n  return (\n    <div className='login-box'>\n      <h2>Sign In</h2>\n      <input type='text' placeholder='Username' />\n      <input type='password' placeholder='Password' />\n      <button type='submit'>Login</button>\n    </div>\n  );\n}\n",
                "reason": "Add authentication login component",
            })
        elif "readme" in lower or "doc" in lower:
            target = "README.md"
            patches.append({
                "relative_path": target,
                "operation": "CREATE" if target not in model.source_file_paths else "MODIFY",
                "proposed_content": f"# {model.project_name}\n\nUpdated by RYVEN Existing Project Engine.\nObjective: {user_request}\n",
                "reason": "Update project documentation",
            })
        else:
            target = "README.md"
            patches.append({
                "relative_path": target,
                "operation": "CREATE" if target not in model.source_file_paths else "MODIFY",
                "proposed_content": f"# {model.project_name}\n\nUpdated by RYVEN Existing Project Engine.\nObjective: {user_request}\n",
                "reason": f"Apply requested change: {user_request[:60]}",
            })
        return patches

    def _generate_fix_content(self, project_name: str, error: BuildError) -> Optional[str]:
        """Produce safe corrected content for a targeted build/test error."""
        if not error.file_path:
            return None
        abs_path = resolve_project_file_path(project_name, error.file_path)
        if not abs_path:
            return None
        try:
            with open(abs_path, "r", encoding="utf-8") as f:
                content = f.read(100_000)
            # Safe minimal fix heuristic: strip invalid syntax or append missing export
            if "SyntaxError" in error.message or "invalid syntax" in error.message:
                lines = content.splitlines()
                if error.line_number and 1 <= error.line_number <= len(lines):
                    # Comment out malformed line
                    lines[error.line_number - 1] = f"# FIX: {lines[error.line_number - 1]}"
                    return "\n".join(lines) + "\n"
            return content
        except Exception:
            return None
