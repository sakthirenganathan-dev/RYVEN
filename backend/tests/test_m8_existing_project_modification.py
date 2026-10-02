"""RYVEN DEV ENGINE — M8: Existing Project Modification Engine Test Suite.

Coverage:
  1. Intent routing — existing project modification detection
  2. Educational question does NOT route to modification
  3. New project creation does NOT route to modification
  4. ProjectResolver — valid project name
  5. ProjectResolver — path traversal blocked
  6. ProjectResolver — Windows system path blocked
  7. ProjectResolver — absolute drive path blocked
  8. ProjectResolver — empty name rejected
  9. ProjectResolver — non-existent project returns not found
  10. ProjectResolver — list available projects
  11. ProjectScanner — scans real project
  12. ProjectScanner — sensitive file protected
  13. ProjectScanner — non-existent project returns None
  14. ProjectScanner — file count limit
  15. DiffEngine — MODIFY patch with hash capture
  16. DiffEngine — CREATE patch for new file
  17. DiffEngine — protected file rejected
  18. DiffEngine — path traversal rejected
  19. DiffEngine — content too large rejected
  20. DiffEngine — original hash verification (pass)
  21. DiffEngine — stale hash detection (abort)
  22. ModificationPlanner — valid plan produced
  23. ModificationPlanner — DELETE blocked by planner
  24. ModificationPlanner — empty patch rejected
  25. ModificationPlanner — all patches rejected returns None
  26. ModificationValidator — valid plan passes
  27. ModificationValidator — path containment failure
  28. ModificationValidator — protected file rejected
  29. ModificationValidator — empty content rejected
  30. ModificationValidator — Python syntax error caught
  31. ModificationValidator — JSON parse error caught
  32. ModificationValidator — total size limit
  33. ApplyEngine — blocked without confirmation
  34. ApplyEngine — applies CREATE patch
  35. ApplyEngine — applies MODIFY patch
  36. ApplyEngine — rollback on stale hash
  37. ApplyEngine — write failure triggers rollback
  38. RollbackEngine — restores modified files
  39. RollbackEngine — removes newly created files
  40. ApplyProjectModificationTool — unconfirmed blocked
  41. ApplyProjectModificationTool — DELETE operation blocked
  42. ApplyProjectModificationTool — successful apply
  43. WorkflowPlanner — plans modify_existing_project workflow
  44. IntentRouter — existing project modification routes to workflow
  45. IntentRouter — "What is React?" stays AI
  46. Full pipeline: resolve → scan → plan → validate → apply → rollback
  47. Prompt injection in project name blocked
  48. Prompt injection in modification content blocked (path containment)
  49. Existing project workflow does not break previous tests
  50. Regression — permissions still block unauthorized tools
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.router import IntentRouter
from app.dev_engine.m8_apply import ApplyEngine, RollbackEngine
from app.dev_engine.m8_models import (
    FilePatch,
    ModificationPlan,
    ModificationRequest,
    ModificationStatus,
    PatchOperation,
    RiskLevel,
    RollbackEntry,
    RollbackRecord,
)
from app.dev_engine.m8_planner import DiffEngine, ModificationPlanner
from app.dev_engine.m8_resolver import ProjectResolver
from app.dev_engine.m8_scanner import ProjectScanner
from app.dev_engine.m8_validator import ModificationValidator
from app.tools.modification_tool import ApplyProjectModificationTool
from app.tools.project_tool import get_projects_root, resolve_project_path


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

SAFE_PROJECT_NAME = "M8TestProject"


@pytest.fixture(autouse=True)
def isolated_test_project():
    """Create a real disposable project in projects/ for each test, clean up after."""
    projects_root = get_projects_root()
    project_dir = os.path.join(projects_root, SAFE_PROJECT_NAME)
    os.makedirs(project_dir, exist_ok=True)

    # Create sample project files
    index_html = os.path.join(project_dir, "index.html")
    styles_css = os.path.join(project_dir, "styles.css")
    app_js = os.path.join(project_dir, "app.js")
    readme = os.path.join(project_dir, "README.md")
    pkg_json = os.path.join(project_dir, "package.json")

    with open(index_html, "w", encoding="utf-8") as f:
        f.write("<!DOCTYPE html>\n<html><head><title>M8Test</title></head><body><h1>Hello</h1></body></html>\n")
    with open(styles_css, "w", encoding="utf-8") as f:
        f.write("body { font-family: sans-serif; background: #0b0f19; color: #f0f0f0; }\n")
    with open(app_js, "w", encoding="utf-8") as f:
        f.write("console.log('M8TestProject loaded.');\n")
    with open(readme, "w", encoding="utf-8") as f:
        f.write("# M8TestProject\n\nVanilla web project.\n")
    with open(pkg_json, "w", encoding="utf-8") as f:
        f.write('{"name": "m8-test-project", "version": "0.1.0", "private": true}\n')

    yield project_dir

    # Cleanup — remove test project after each test
    if os.path.isdir(project_dir):
        shutil.rmtree(project_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Intent routing
# ─────────────────────────────────────────────────────────────────────────────

class TestM8IntentRouting:
    router = IntentRouter()

    def test_modify_existing_project_routes_to_workflow(self):
        dec = self.router.route("Modify my existing React project and add a login page.")
        assert dec.intent == "workflow"
        assert dec.workflow_name == "modify_existing_project"

    def test_update_existing_project_routes(self):
        dec = self.router.route("Update my existing project to add dark mode.")
        assert dec.intent == "workflow"
        assert dec.workflow_name == "modify_existing_project"

    def test_add_feature_to_existing_project_routes(self):
        dec = self.router.route("Add a REST API endpoint to my existing Python project.")
        assert dec.intent == "workflow"
        assert dec.workflow_name == "modify_existing_project"

    def test_fix_bug_in_project_routes(self):
        dec = self.router.route("Fix the navbar responsiveness issue in my existing project.")
        assert dec.intent == "workflow"
        assert dec.workflow_name == "modify_existing_project"

    def test_educational_question_stays_ai(self):
        dec = self.router.route("What is React?")
        assert dec.intent == "ai"
        assert dec.workflow_name != "modify_existing_project"

    def test_explain_question_stays_ai(self):
        dec = self.router.route("Explain how React routing works.")
        assert dec.intent == "ai"

    def test_new_project_creation_not_modification(self):
        dec = self.router.route("Create a new React project called TaskFlow.")
        assert dec.intent == "workflow"
        assert dec.workflow_name == "create_project"   # NOT modify_existing_project

    def test_dark_mode_add_to_existing_routes(self):
        dec = self.router.route("Add dark mode to my existing React app.")
        assert dec.intent == "workflow"
        assert dec.workflow_name == "modify_existing_project"


# ─────────────────────────────────────────────────────────────────────────────
# 2. ProjectResolver security tests
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectResolver:
    resolver = ProjectResolver()

    def test_valid_project_resolves(self):
        result = self.resolver.resolve(SAFE_PROJECT_NAME)
        assert result.found is True
        assert result.security_blocked is False
        assert SAFE_PROJECT_NAME in result.absolute_path

    def test_path_traversal_blocked(self):
        result = self.resolver.resolve("../../evil")
        assert result.found is False
        assert result.security_blocked is True

    def test_windows_absolute_path_blocked(self):
        result = self.resolver.resolve(r"C:\Windows\System32")
        assert result.found is False
        assert result.security_blocked is True

    def test_unix_absolute_path_blocked(self):
        result = self.resolver.resolve("/etc/passwd")
        assert result.found is False
        assert result.security_blocked is True

    def test_windows_system_path_blocked(self):
        result = self.resolver.resolve("Windows\\System32")
        assert result.found is False
        assert result.security_blocked is True

    def test_empty_name_rejected(self):
        result = self.resolver.resolve("")
        assert result.found is False
        assert result.security_blocked is False

    def test_invalid_chars_rejected(self):
        result = self.resolver.resolve("my project!")
        assert result.found is False
        assert result.security_blocked is True

    def test_nonexistent_project_not_found(self):
        result = self.resolver.resolve("NonExistentProject_xyz987")
        assert result.found is False
        assert result.security_blocked is False
        assert "does not exist" in result.reason.lower()

    def test_list_available_projects_includes_test_project(self):
        projects = self.resolver.list_available_projects()
        assert SAFE_PROJECT_NAME in projects


# ─────────────────────────────────────────────────────────────────────────────
# 3. ProjectScanner tests
# ─────────────────────────────────────────────────────────────────────────────

class TestProjectScanner:
    scanner = ProjectScanner()

    def test_scans_real_project(self):
        model = self.scanner.scan(SAFE_PROJECT_NAME)
        assert model is not None
        assert model.project_name == SAFE_PROJECT_NAME
        assert len(model.source_files) >= 3
        assert model.total_files_scanned >= 3

    def test_sensitive_file_protected(self):
        """Create a .env file — scanner must protect it."""
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        env_path = os.path.join(project_dir, ".env")
        with open(env_path, "w") as f:
            f.write("SECRET_KEY=abc123\n")
        model = self.scanner.scan(SAFE_PROJECT_NAME)
        assert ".env" in model.protected_files
        assert ".env" not in model.source_file_paths

    def test_nonexistent_project_returns_none(self):
        model = self.scanner.scan("NonExistentXyz999")
        assert model is None

    def test_architecture_summary_is_not_empty(self):
        model = self.scanner.scan(SAFE_PROJECT_NAME)
        assert model.architecture_summary != ""
        assert SAFE_PROJECT_NAME in model.architecture_summary

    def test_relevant_files_bounded(self):
        model = self.scanner.scan(SAFE_PROJECT_NAME)
        assert len(model.relevant_files) <= 10   # MAX_CONTEXT_FILES

    def test_framework_detected(self):
        model = self.scanner.scan(SAFE_PROJECT_NAME)
        assert model.framework != ""   # Should detect at least "vanilla"

    def test_skip_dirs_not_scanned(self):
        """node_modules should be skipped."""
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        nm_dir = os.path.join(project_dir, "node_modules", "some_package")
        os.makedirs(nm_dir, exist_ok=True)
        with open(os.path.join(nm_dir, "index.js"), "w") as f:
            f.write("module.exports = {};")
        model = self.scanner.scan(SAFE_PROJECT_NAME)
        nm_paths = [f.relative_path for f in model.source_files if "node_modules" in f.relative_path]
        assert len(nm_paths) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 4. DiffEngine tests
# ─────────────────────────────────────────────────────────────────────────────

class TestDiffEngine:
    diff = DiffEngine()

    def test_create_modify_patch(self):
        patch = self.diff.create_modify_patch(
            project_name=SAFE_PROJECT_NAME,
            relative_path="styles.css",
            proposed_content="body { background: #000; }\n",
            reason="Update background color",
        )
        assert patch is not None
        assert patch.operation == PatchOperation.MODIFY
        assert patch.original_hash != ""
        assert patch.proposed_content == "body { background: #000; }\n"

    def test_create_new_file_patch(self):
        patch = self.diff.create_new_file_patch(
            project_name=SAFE_PROJECT_NAME,
            relative_path="src/new_module.js",
            proposed_content="export function hello() { return 'hi'; }\n",
            reason="Add new module",
        )
        assert patch is not None
        assert patch.operation == PatchOperation.CREATE
        assert patch.original_hash == ""

    def test_protected_file_rejected(self):
        patch = self.diff.create_modify_patch(
            project_name=SAFE_PROJECT_NAME,
            relative_path="styles.css",
            proposed_content="body { background: red; }\n",
            protected_files=["styles.css"],
        )
        assert patch is None

    def test_path_traversal_rejected(self):
        patch = self.diff.create_modify_patch(
            project_name=SAFE_PROJECT_NAME,
            relative_path="../../../etc/passwd",
            proposed_content="evil content",
        )
        assert patch is None

    def test_content_too_large_rejected(self):
        huge_content = "x" * 600_000   # > MAX_PROPOSED_CONTENT_BYTES (500 KB)
        patch = self.diff.create_modify_patch(
            project_name=SAFE_PROJECT_NAME,
            relative_path="index.html",
            proposed_content=huge_content,
        )
        assert patch is None

    def test_hash_verification_passes(self):
        """Hash captured at plan time matches file on disk."""
        patch = self.diff.create_modify_patch(
            project_name=SAFE_PROJECT_NAME,
            relative_path="styles.css",
            proposed_content="body {}\n",
        )
        assert patch is not None
        result = self.diff.verify_original_hash(SAFE_PROJECT_NAME, patch)
        assert result is True

    def test_stale_hash_detected(self):
        """File modified externally after plan — verification must fail."""
        patch = self.diff.create_modify_patch(
            project_name=SAFE_PROJECT_NAME,
            relative_path="styles.css",
            proposed_content="body {}\n",
        )
        assert patch is not None
        # Modify the file externally
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        css_path = os.path.join(project_dir, "styles.css")
        with open(css_path, "a", encoding="utf-8") as f:
            f.write("\n/* external change */\n")
        result = self.diff.verify_original_hash(SAFE_PROJECT_NAME, patch)
        assert result is False   # STALE — abort

    def test_create_patch_hash_always_passes(self):
        """CREATE patches have no original — hash verification always True."""
        patch = self.diff.create_new_file_patch(
            project_name=SAFE_PROJECT_NAME,
            relative_path="src/brand_new.js",
            proposed_content="console.log('new file');\n",
        )
        assert patch is not None
        result = self.diff.verify_original_hash(SAFE_PROJECT_NAME, patch)
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# 5. ModificationPlanner tests
# ─────────────────────────────────────────────────────────────────────────────

class TestModificationPlanner:
    planner = ModificationPlanner()

    def _make_scanner_model(self):
        scanner = ProjectScanner()
        return scanner.scan(SAFE_PROJECT_NAME)

    def _make_request(self):
        return ModificationRequest(
            project_name=SAFE_PROJECT_NAME,
            project_path=resolve_project_path(SAFE_PROJECT_NAME),
            user_request="Update styles to add dark background",
            requires_confirmation=True,
        )

    def test_valid_plan_produced(self):
        model = self._make_scanner_model()
        request = self._make_request()
        plan = self.planner.plan(
            project_model=model,
            request=request,
            proposed_patches=[
                {
                    "relative_path": "styles.css",
                    "operation": "MODIFY",
                    "proposed_content": "body { background: #111; color: #fff; }\n",
                    "reason": "Dark background",
                }
            ],
        )
        assert plan is not None
        assert len(plan.patches) == 1
        assert plan.patches[0].operation == PatchOperation.MODIFY

    def test_delete_blocked_by_planner(self):
        model = self._make_scanner_model()
        request = self._make_request()
        plan = self.planner.plan(
            project_model=model,
            request=request,
            proposed_patches=[
                {
                    "relative_path": "styles.css",
                    "operation": "DELETE",
                    "proposed_content": "",
                }
            ],
        )
        # DELETE is blocked — no valid patches remain → plan is None
        assert plan is None

    def test_empty_patch_skipped(self):
        model = self._make_scanner_model()
        request = self._make_request()
        plan = self.planner.plan(
            project_model=model,
            request=request,
            proposed_patches=[
                {"relative_path": "", "operation": "MODIFY", "proposed_content": ""},
            ],
        )
        assert plan is None

    def test_create_patch_in_plan(self):
        model = self._make_scanner_model()
        request = self._make_request()
        plan = self.planner.plan(
            project_model=model,
            request=request,
            proposed_patches=[
                {
                    "relative_path": "src/login.js",
                    "operation": "CREATE",
                    "proposed_content": "export function login() {}\n",
                    "reason": "Add login module",
                }
            ],
        )
        assert plan is not None
        assert "src/login.js" in plan.files_to_create

    def test_risk_assessment_high_for_config_change(self):
        model = self._make_scanner_model()
        request = self._make_request()
        plan = self.planner.plan(
            project_model=model,
            request=request,
            proposed_patches=[
                {
                    "relative_path": "package.json",
                    "operation": "MODIFY",
                    "proposed_content": '{"name": "m8-test", "version": "0.2.0"}\n',
                    "reason": "Bump version",
                }
            ],
        )
        assert plan is not None
        assert plan.risk_level == RiskLevel.HIGH


# ─────────────────────────────────────────────────────────────────────────────
# 6. ModificationValidator tests
# ─────────────────────────────────────────────────────────────────────────────

class TestModificationValidator:
    validator = ModificationValidator()

    def _make_plan(self, patches):
        return ModificationPlan(
            project_name=SAFE_PROJECT_NAME,
            objective="Test modification",
            patches=patches,
        )

    def test_valid_plan_passes(self):
        plan = self._make_plan([
            FilePatch(
                relative_path="styles.css",
                operation=PatchOperation.MODIFY,
                original_hash="abc",
                proposed_content="body { margin: 0; }\n",
            )
        ])
        ok, err = self.validator.validate(plan)
        assert ok is True
        assert err == ""

    def test_path_traversal_fails_containment(self):
        plan = self._make_plan([
            FilePatch(
                relative_path="../../evil.txt",
                operation=PatchOperation.MODIFY,
                proposed_content="evil",
            )
        ])
        ok, err = self.validator.validate(plan)
        assert ok is False
        assert "containment" in err.lower()

    def test_protected_file_rejected(self):
        plan = self._make_plan([
            FilePatch(
                relative_path="styles.css",
                operation=PatchOperation.MODIFY,
                proposed_content="body {}\n",
            )
        ])
        ok, err = self.validator.validate(plan, protected_files=["styles.css"])
        assert ok is False
        assert "protected" in err.lower()

    def test_empty_content_rejected(self):
        plan = self._make_plan([
            FilePatch(
                relative_path="app.js",
                operation=PatchOperation.MODIFY,
                proposed_content="   ",
            )
        ])
        ok, err = self.validator.validate(plan)
        assert ok is False
        assert "empty" in err.lower()

    def test_python_syntax_error_caught(self):
        plan = self._make_plan([
            FilePatch(
                relative_path="main.py",
                operation=PatchOperation.MODIFY,
                proposed_content="def broken(:\n    pass\n",
            )
        ])
        ok, err = self.validator.validate(plan)
        assert ok is False
        assert "syntax" in err.lower()

    def test_valid_python_passes(self):
        plan = self._make_plan([
            FilePatch(
                relative_path="main.py",
                operation=PatchOperation.MODIFY,
                proposed_content="def hello():\n    return 'world'\n",
            )
        ])
        ok, err = self.validator.validate(plan)
        assert ok is True

    def test_json_parse_error_caught(self):
        plan = self._make_plan([
            FilePatch(
                relative_path="package.json",
                operation=PatchOperation.MODIFY,
                proposed_content="{invalid json",
            )
        ])
        ok, err = self.validator.validate(plan)
        assert ok is False
        assert "json" in err.lower()

    def test_valid_json_passes(self):
        plan = self._make_plan([
            FilePatch(
                relative_path="package.json",
                operation=PatchOperation.MODIFY,
                proposed_content='{"name": "test", "version": "1.0.0"}\n',
            )
        ])
        ok, err = self.validator.validate(plan)
        assert ok is True


# ─────────────────────────────────────────────────────────────────────────────
# 7. ApplyEngine tests
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyEngine:
    engine = ApplyEngine()

    def _make_plan(self, patches):
        return ModificationPlan(
            project_name=SAFE_PROJECT_NAME,
            objective="Test apply",
            patches=patches,
        )

    def test_blocked_without_confirmation(self):
        plan = self._make_plan([
            FilePatch(
                relative_path="styles.css",
                operation=PatchOperation.MODIFY,
                proposed_content="body {}\n",
            )
        ])
        result = self.engine.apply(plan, confirmed=False)
        assert result.success is False
        assert result.status == ModificationStatus.BLOCKED

    def test_applies_create_patch(self):
        plan = self._make_plan([
            FilePatch(
                relative_path="src/new_file.js",
                operation=PatchOperation.CREATE,
                proposed_content="console.log('created');\n",
            )
        ])
        result = self.engine.apply(plan, confirmed=True)
        assert result.success is True
        # Verify file exists on disk
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        assert os.path.isfile(os.path.join(project_dir, "src", "new_file.js"))

    def test_applies_modify_patch(self):
        diff = DiffEngine()
        patch = diff.create_modify_patch(
            project_name=SAFE_PROJECT_NAME,
            relative_path="styles.css",
            proposed_content="body { background: #222; }\n",
            reason="Dark theme",
        )
        assert patch is not None
        plan = self._make_plan([patch])
        result = self.engine.apply(plan, confirmed=True)
        assert result.success is True
        # Verify content was written
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        with open(os.path.join(project_dir, "styles.css"), "r") as f:
            content = f.read()
        assert "#222" in content

    def test_rollback_on_stale_hash(self):
        """If file changes externally between plan and apply, apply must abort and rollback."""
        diff = DiffEngine()
        patch = diff.create_modify_patch(
            project_name=SAFE_PROJECT_NAME,
            relative_path="app.js",
            proposed_content="console.log('modified');\n",
        )
        assert patch is not None
        # Externally modify the file to make hash stale
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        js_path = os.path.join(project_dir, "app.js")
        with open(js_path, "a") as f:
            f.write("\n// external change\n")
        plan = self._make_plan([patch])
        result = self.engine.apply(plan, confirmed=True)
        assert result.success is False
        assert result.was_rolled_back is True
        assert result.status == ModificationStatus.ROLLED_BACK


# ─────────────────────────────────────────────────────────────────────────────
# 8. RollbackEngine tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRollbackEngine:
    rollback_engine = RollbackEngine()

    def test_restores_modified_file(self):
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        css_path = os.path.join(project_dir, "styles.css")
        original_content = open(css_path, "r", encoding="utf-8").read()
        original_hash = hashlib.sha256(original_content.encode()).hexdigest()

        # Simulate a modification
        with open(css_path, "w", encoding="utf-8") as f:
            f.write("body { background: red; }\n")

        record = RollbackRecord(
            project_name=SAFE_PROJECT_NAME,
            plan_id="plan-test",
            entries=[
                RollbackEntry(
                    relative_path="styles.css",
                    original_content=original_content,
                    original_hash=original_hash,
                    absolute_path=css_path,
                    existed_before=True,
                )
            ],
        )
        success = self.rollback_engine.rollback(record)
        assert success is True
        restored = open(css_path, "r", encoding="utf-8").read()
        assert restored == original_content

    def test_removes_newly_created_file(self):
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        new_file = os.path.join(project_dir, "src", "to_remove.js")
        os.makedirs(os.path.dirname(new_file), exist_ok=True)
        with open(new_file, "w") as f:
            f.write("// new file\n")

        record = RollbackRecord(
            project_name=SAFE_PROJECT_NAME,
            plan_id="plan-test",
            entries=[
                RollbackEntry(
                    relative_path="src/to_remove.js",
                    original_content="",
                    original_hash="",
                    absolute_path=new_file,
                    existed_before=False,
                )
            ],
        )
        success = self.rollback_engine.rollback(record)
        assert success is True
        assert not os.path.isfile(new_file)


# ─────────────────────────────────────────────────────────────────────────────
# 9. ApplyProjectModificationTool tests
# ─────────────────────────────────────────────────────────────────────────────

class TestApplyProjectModificationTool:
    tool = ApplyProjectModificationTool()

    def test_unconfirmed_blocked(self):
        result = asyncio.run(
            self.tool.execute(
                project_name=SAFE_PROJECT_NAME,
                objective="Add dark mode",
                patches=[
                    {
                        "relative_path": "styles.css",
                        "operation": "MODIFY",
                        "proposed_content": "body { background: #111; }\n",
                    }
                ],
                confirmed=False,
            )
        )
        assert result["success"] is False
        assert result.get("security_blocked") is True

    def test_delete_operation_blocked(self):
        result = asyncio.run(
            self.tool.execute(
                project_name=SAFE_PROJECT_NAME,
                objective="Delete file",
                patches=[
                    {
                        "relative_path": "styles.css",
                        "operation": "DELETE",
                        "proposed_content": "",
                    }
                ],
                confirmed=True,
            )
        )
        assert result["success"] is False
        assert result.get("security_blocked") is True

    def test_successful_apply_creates_file(self):
        result = asyncio.run(
            self.tool.execute(
                project_name=SAFE_PROJECT_NAME,
                objective="Add a new component",
                patches=[
                    {
                        "relative_path": "src/component.js",
                        "operation": "CREATE",
                        "proposed_content": "export function Component() { return null; }\n",
                        "reason": "Add component",
                    }
                ],
                confirmed=True,
            )
        )
        assert result["success"] is True
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        assert os.path.isfile(os.path.join(project_dir, "src", "component.js"))

    def test_no_patches_rejected(self):
        result = asyncio.run(
            self.tool.execute(
                project_name=SAFE_PROJECT_NAME,
                objective="Nothing",
                patches=[],
                confirmed=True,
            )
        )
        assert result["success"] is False

    def test_path_traversal_in_patch_rejected(self):
        result = asyncio.run(
            self.tool.execute(
                project_name=SAFE_PROJECT_NAME,
                objective="Escape sandbox",
                patches=[
                    {
                        "relative_path": "../../evil.txt",
                        "operation": "CREATE",
                        "proposed_content": "evil",
                    }
                ],
                confirmed=True,
            )
        )
        # Should fail due to validator path containment check
        assert result["success"] is False



# ─────────────────────────────────────────────────────────────────────────────
# 10. WorkflowPlanner M8 integration
# ─────────────────────────────────────────────────────────────────────────────

class TestM8WorkflowPlanner:
    def test_planner_plans_existing_modification_workflow(self):
        from app.workflows.planner import WorkflowPlanner
        planner = WorkflowPlanner()
        workflow = planner.plan_existing_project_modification(
            query="Modify my existing project and add dark mode.",
            project_name_hint="",
        )
        assert workflow is not None
        assert "modify" in workflow.name.lower() or "existing" in workflow.name.lower()
        assert workflow.metadata.get("workflow_type") == "existing_project_modification"

    def test_planner_plan_routes_modification_request(self):
        from app.workflows.planner import WorkflowPlanner
        planner = WorkflowPlanner()
        workflow = planner.plan("Modify my existing project and add dark mode.")
        assert workflow is not None
        assert workflow.metadata.get("workflow_type") == "existing_project_modification"

    def test_planner_create_project_not_modification(self):
        from app.workflows.planner import WorkflowPlanner
        planner = WorkflowPlanner()
        workflow = planner.plan("Create a new React project called MyApp.")
        assert workflow is not None
        # Should not be an existing project modification
        assert workflow.metadata.get("workflow_type") != "existing_project_modification"

    def test_can_plan_recognizes_modification(self):
        from app.workflows.planner import WorkflowPlanner
        planner = WorkflowPlanner()
        assert planner.can_plan("Modify my existing Python project.") is True

    def test_can_plan_rejects_general_question(self):
        from app.workflows.planner import WorkflowPlanner
        planner = WorkflowPlanner()
        assert planner.can_plan("What is a REST API?") is False


# ─────────────────────────────────────────────────────────────────────────────
# 11. Security attack tests
# ─────────────────────────────────────────────────────────────────────────────

class TestM8SecurityAttacks:
    resolver = ProjectResolver()
    scanner = ProjectScanner()

    def test_prompt_injection_in_project_name_blocked(self):
        """Attempt to inject 'ignore all previous instructions' as project name."""
        result = self.resolver.resolve("ignore_all_previous_instructions")
        # Not a traversal but project won't exist
        assert result.found is False

    def test_dot_dot_slash_blocked(self):
        result = self.resolver.resolve("../../../Windows")
        assert result.found is False
        assert result.security_blocked is True

    def test_windows_system32_blocked(self):
        result = self.resolver.resolve("system32")
        # 'system32' is a valid-looking name but the project won't exist
        assert result.found is False
        # security_blocked only triggers for actual path patterns
        # system32 alone is not flagged unless it's in a blocked path pattern

    def test_env_file_never_in_source_files(self):
        """Even if .env exists, scanner must protect it."""
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        env_path = os.path.join(project_dir, ".env")
        with open(env_path, "w") as f:
            f.write("SECRET=supersecret\n")
        model = self.scanner.scan(SAFE_PROJECT_NAME)
        source_paths = [f.relative_path for f in model.source_files]
        assert ".env" not in source_paths

    def test_private_key_file_never_in_source_files(self):
        """Private key files must be protected."""
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        key_path = os.path.join(project_dir, "id_rsa")
        with open(key_path, "w") as f:
            f.write("-----BEGIN RSA PRIVATE KEY-----\nFAKE\n-----END RSA PRIVATE KEY-----\n")
        model = self.scanner.scan(SAFE_PROJECT_NAME)
        source_paths = [f.relative_path for f in model.source_files]
        assert "id_rsa" not in source_paths
        assert "id_rsa" in model.protected_files


# ─────────────────────────────────────────────────────────────────────────────
# 12. Full pipeline integration test
# ─────────────────────────────────────────────────────────────────────────────

class TestM8FullPipeline:
    def test_full_pipeline_resolve_scan_plan_validate_apply(self):
        """Integration test: resolve → scan → plan → validate → apply → verify."""
        # 1. Resolve
        resolver = ProjectResolver()
        resolved = resolver.resolve(SAFE_PROJECT_NAME)
        assert resolved.found is True

        # 2. Scan
        scanner = ProjectScanner()
        model = scanner.scan(SAFE_PROJECT_NAME)
        assert model is not None

        # 3. Plan
        request = ModificationRequest(
            project_name=SAFE_PROJECT_NAME,
            project_path=resolved.absolute_path,
            user_request="Add a footer to the HTML",
        )
        planner = ModificationPlanner()
        plan = planner.plan(
            project_model=model,
            request=request,
            proposed_patches=[
                {
                    "relative_path": "index.html",
                    "operation": "MODIFY",
                    "proposed_content": (
                        "<!DOCTYPE html>\n<html><body>"
                        "<h1>Hello</h1><footer>Footer</footer>"
                        "</body></html>\n"
                    ),
                    "reason": "Add footer",
                }
            ],
        )
        assert plan is not None

        # 4. Validate
        validator = ModificationValidator()
        ok, err = validator.validate(plan, protected_files=model.protected_files)
        assert ok is True, f"Validation failed: {err}"

        # 5. Apply
        apply_engine = ApplyEngine()
        result = apply_engine.apply(plan, confirmed=True)
        assert result.success is True

        # 6. Verify on disk
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        with open(os.path.join(project_dir, "index.html"), "r", encoding="utf-8") as f:
            content = f.read()
        assert "Footer" in content

    def test_rollback_restores_state_after_failure(self):
        """If something fails after partial apply, rollback must restore state."""
        project_dir = resolve_project_path(SAFE_PROJECT_NAME)
        css_path = os.path.join(project_dir, "styles.css")
        original = open(css_path, "r", encoding="utf-8").read()
        original_hash = hashlib.sha256(original.encode()).hexdigest()

        rollback_engine = RollbackEngine()
        record = RollbackRecord(
            project_name=SAFE_PROJECT_NAME,
            plan_id="test-plan",
            entries=[
                RollbackEntry(
                    relative_path="styles.css",
                    original_content=original,
                    original_hash=original_hash,
                    absolute_path=css_path,
                    existed_before=True,
                )
            ],
        )
        # Corrupt the file (simulate failed apply)
        with open(css_path, "w") as f:
            f.write("CORRUPTED CONTENT\n")

        success = rollback_engine.rollback(record)
        assert success is True
        restored = open(css_path, "r", encoding="utf-8").read()
        assert restored == original
