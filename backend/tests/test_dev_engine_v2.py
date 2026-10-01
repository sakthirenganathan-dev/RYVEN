"""Automated tests for RYVEN DEV ENGINE v2.

Milestones covered:
  1. Sandbox (SandboxPolicy, SandboxProcessRunner)
  2. Dependency Manager
  3. Build Engine
  4. Test Engine
  5. Error Analyzer
  6. Fix Engine (integration with existing FixLoopManager)
  7. Development Loop Manager
  9. Quality Gate
  10. Git Engine

All tests run without real network calls. Subprocess calls that would
require external tools are mocked or tested at the policy/validation layer.
"""

import asyncio
import os
import shutil

import pytest

from app.dev_engine.dependency import (
    DependencyManager,
    DependencyRequest,
    validate_npm_package,
    validate_pypi_package,
)
from app.dev_engine.error_analyzer import BuildError, ErrorAnalyzer
from app.dev_engine.quality_gate import QualityGate, QualityGateResult
from app.dev_engine.sandbox import (
    SandboxPolicy,
    SandboxProcessRequest,
    SandboxProcessRunner,
)
from app.dev_engine.build_engine import BuildEngine, BuildRequest
from app.dev_engine.test_engine import TestEngine, TestRequest
from app.dev_engine.git_engine import (
    GitEngine,
    _validate_commit_message,
    _validate_branch,
)
from app.dev_engine.dev_loop import DevelopmentLoopManager
from app.tools.project_tool import (
    CreateProjectFolderTool,
    CreateProjectFileTool,
    get_projects_root,
    resolve_project_path,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup_test_dirs():
    yield
    for name in ["SandboxTest", "QualityTest", "GitTest"]:
        p = resolve_project_path(name)
        if p and os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONE 1 — Sandbox Policy
# ─────────────────────────────────────────────────────────────────────────────

class TestSandboxPolicy:

    def test_approved_executables_pass(self):
        policy = SandboxPolicy()
        for exe in ["npm", "node", "python", "python3", "git", "npx"]:
            assert policy.is_executable_approved(exe), f"'{exe}' should be approved"

    def test_bare_exe_name_normalized(self):
        policy = SandboxPolicy()
        # Windows .exe suffix
        assert policy.is_executable_approved("npm.exe")
        assert policy.is_executable_approved("Python.EXE")

    def test_blocked_executables_rejected(self):
        policy = SandboxPolicy()
        for exe in ["cmd", "cmd.exe", "powershell", "powershell.exe", "bash", "sh", "calc", "notepad"]:
            assert not policy.is_executable_approved(exe), f"'{exe}' should be rejected"

    def test_injection_patterns_detected(self):
        policy = SandboxPolicy()
        injection_args = [
            ["install", ";", "rm -rf /"],
            ["install", "pkg&&evil"],
            ["run", "$(whoami)"],
            ["install", "`id`"],
        ]
        for args in injection_args:
            err = policy.validate_arguments(args)
            assert err is not None, f"Injection not detected in {args}"

    def test_blocked_argument_fragments(self):
        policy = SandboxPolicy()
        blocked = [
            ["install", "--unsafe-perm"],
            ["install", "../../evil"],
        ]
        for args in blocked:
            err = policy.validate_arguments(args)
            assert err is not None, f"Blocked fragment not caught in {args}"

    def test_clean_arguments_pass(self):
        policy = SandboxPolicy()
        clean_args = [
            ["install", "--no-audit", "--no-fund"],
            ["run", "build"],
            ["-m", "pytest", "-v"],
        ]
        for args in clean_args:
            err = policy.validate_arguments(args)
            assert err is None, f"Clean args incorrectly rejected: {args}"


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONE 1 — SandboxProcessRunner (unit, no real subprocess)
# ─────────────────────────────────────────────────────────────────────────────

class TestSandboxProcessRunner:

    @pytest.mark.asyncio
    async def test_rejects_unapproved_executable(self):
        runner = SandboxProcessRunner()
        req = SandboxProcessRequest(
            project_name="SandboxTest",
            executable="cmd",
            arguments=["/c", "dir"],
        )
        result = await runner.run(req)
        assert result.success is False
        assert result.security_blocked is True
        assert "not on the approved list" in result.block_reason

    @pytest.mark.asyncio
    async def test_rejects_injection_in_arguments(self):
        runner = SandboxProcessRunner()
        req = SandboxProcessRequest(
            project_name="SandboxTest",
            executable="npm",
            arguments=["install", ";rm -rf /"],
        )
        result = await runner.run(req)
        assert result.success is False
        assert result.security_blocked is True

    @pytest.mark.asyncio
    async def test_rejects_invalid_project_name(self):
        runner = SandboxProcessRunner()
        req = SandboxProcessRequest(
            project_name="../../evil",
            executable="npm",
            arguments=["install"],
        )
        result = await runner.run(req)
        assert result.success is False
        assert result.security_blocked is True

    @pytest.mark.asyncio
    async def test_rejects_nonexistent_project_directory(self):
        """Runner should fail cleanly when project directory doesn't exist."""
        runner = SandboxProcessRunner()
        req = SandboxProcessRequest(
            project_name="DoesNotExist99",
            executable="npm",
            arguments=["install"],
        )
        result = await runner.run(req)
        assert result.success is False
        assert not result.security_blocked   # not a security block, just missing dir

    @pytest.mark.asyncio
    async def test_runs_safe_subprocess_in_project_dir(self):
        """Verify runner actually executes a real approved process (python --version)."""
        # Create the project directory first
        folder_tool = CreateProjectFolderTool()
        await folder_tool.execute(project_name="SandboxTest")

        runner = SandboxProcessRunner()
        req = SandboxProcessRequest(
            project_name="SandboxTest",
            executable="python",
            arguments=["--version"],
            timeout_seconds=10.0,
            operation_label="python version check",
        )
        result = await runner.run(req)
        # python --version exits 0
        assert result.exit_code == 0
        assert result.success is True
        assert "python" in (result.stdout + result.stderr).lower()
        assert result.duration_ms > 0


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONE 2 — Dependency Manager (validation layer; no real install)
# ─────────────────────────────────────────────────────────────────────────────

class TestDependencyManager:

    def test_npm_package_validation_valid(self):
        valid_pkgs = [
            "react", "react-dom", "@types/react", "vite@^5.3.4", "lucide-react",
        ]
        for pkg in valid_pkgs:
            assert validate_npm_package(pkg) is None, f"Valid npm pkg rejected: {pkg}"

    def test_npm_package_validation_invalid(self):
        invalid_pkgs = [
            "pkg;rm -rf /", "../../evil", "pkg | cat /etc/passwd", "pkg`id`",
        ]
        for pkg in invalid_pkgs:
            assert validate_npm_package(pkg) is not None, f"Invalid npm pkg passed: {pkg}"

    def test_pypi_package_validation_valid(self):
        valid_pkgs = ["fastapi", "uvicorn", "pydantic", "requests>=2.28.0", "pytest"]
        for pkg in valid_pkgs:
            assert validate_pypi_package(pkg) is None, f"Valid pypi pkg rejected: {pkg}"

    def test_pypi_package_validation_invalid(self):
        invalid_pkgs = [
            "pkg;rm -rf /", "../../evil", "pkg && echo hacked",
        ]
        for pkg in invalid_pkgs:
            assert validate_pypi_package(pkg) is not None, f"Invalid pypi pkg passed: {pkg}"

    @pytest.mark.asyncio
    async def test_install_blocked_without_confirmation(self):
        dm = DependencyManager()
        req = DependencyRequest(
            project_name="SandboxTest",
            project_type="react_ts",
            packages=["react"],
            confirmed=False,   # NOT confirmed
        )
        result = await dm.install(req)
        assert result.success is False
        assert result.security_blocked is True
        assert "confirmation" in result.block_reason.lower()

    @pytest.mark.asyncio
    async def test_install_blocked_for_unknown_project_type(self):
        dm = DependencyManager()
        req = DependencyRequest(
            project_name="SandboxTest",
            project_type="cobol",  # unsupported
            packages=["somelib"],
            confirmed=True,
        )
        result = await dm.install(req)
        assert result.success is False
        assert result.security_blocked is True

    @pytest.mark.asyncio
    async def test_all_invalid_packages_rejected(self):
        dm = DependencyManager()
        req = DependencyRequest(
            project_name="SandboxTest",
            project_type="react_ts",
            packages=["../../evil", "pkg;rm -rf /"],
            confirmed=True,
        )
        result = await dm.install(req)
        assert result.success is False
        assert len(result.packages_rejected) == 2
        assert len(result.packages_validated) == 0


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONE 3 — Build Engine (confirmation gate + missing package.json)
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildEngine:

    @pytest.mark.asyncio
    async def test_build_blocked_without_confirmation(self):
        engine = BuildEngine()
        req = BuildRequest(
            project_name="SandboxTest",
            project_type="react_ts",
            confirmed=False,
        )
        result = await engine.build(req)
        assert result.success is False
        assert result.security_blocked is True
        assert "confirmation" in result.block_reason.lower()

    @pytest.mark.asyncio
    async def test_build_fails_gracefully_if_no_package_json(self):
        """React build should fail gracefully if package.json is missing."""
        folder_tool = CreateProjectFolderTool()
        await folder_tool.execute(project_name="SandboxTest")

        engine = BuildEngine()
        req = BuildRequest(
            project_name="SandboxTest",
            project_type="react_ts",
            confirmed=True,
        )
        result = await engine.build(req)
        # Should fail but NOT be a security block
        assert result.success is False
        assert not result.security_blocked
        assert "package.json" in result.message.lower()

    @pytest.mark.asyncio
    async def test_python_build_missing_entry(self):
        folder_tool = CreateProjectFolderTool()
        await folder_tool.execute(project_name="SandboxTest")

        engine = BuildEngine()
        req = BuildRequest(
            project_name="SandboxTest",
            project_type="python_app",
            entry_file="main.py",
            confirmed=True,
        )
        result = await engine.build(req)
        assert result.success is False
        assert "main.py" in result.message or "entry file" in result.message.lower()

    @pytest.mark.asyncio
    async def test_python_build_valid_file(self):
        """Real python py_compile check on valid Python source."""
        folder_tool = CreateProjectFolderTool()
        file_tool = CreateProjectFileTool()
        await folder_tool.execute(project_name="SandboxTest")
        await file_tool.execute(
            project_name="SandboxTest",
            relative_path="main.py",
            content="def main():\n    print('Hello from RYVEN')\n\nif __name__ == '__main__':\n    main()\n",
        )

        engine = BuildEngine()
        req = BuildRequest(
            project_name="SandboxTest",
            project_type="python_app",
            entry_file="main.py",
            confirmed=True,
        )
        result = await engine.build(req)
        assert result.success is True
        assert result.exit_code == 0

    @pytest.mark.asyncio
    async def test_python_build_syntax_error(self):
        """Real python py_compile check on broken Python source — must report failure."""
        folder_tool = CreateProjectFolderTool()
        file_tool = CreateProjectFileTool()
        await folder_tool.execute(project_name="SandboxTest")
        await file_tool.execute(
            project_name="SandboxTest",
            relative_path="main.py",
            content="def broken(:\n    pass\n",  # syntax error
        )

        engine = BuildEngine()
        req = BuildRequest(
            project_name="SandboxTest",
            project_type="python_app",
            entry_file="main.py",
            confirmed=True,
        )
        result = await engine.build(req)
        assert result.success is False
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_unsupported_project_type_blocked(self):
        engine = BuildEngine()
        req = BuildRequest(
            project_name="SandboxTest",
            project_type="cobol",
            confirmed=True,
        )
        result = await engine.build(req)
        assert result.success is False
        assert result.security_blocked is True


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONE 4 — Test Engine (confirmation gate + path traversal)
# ─────────────────────────────────────────────────────────────────────────────

class TestTestEngine:

    @pytest.mark.asyncio
    async def test_blocked_without_confirmation(self):
        engine = TestEngine()
        req = TestRequest(
            project_name="SandboxTest",
            project_type="python_app",
            confirmed=False,
        )
        result = await engine.run_tests(req)
        assert result.success is False
        assert result.security_blocked is True

    @pytest.mark.asyncio
    async def test_path_traversal_in_test_path_rejected(self):
        engine = TestEngine()
        req = TestRequest(
            project_name="SandboxTest",
            project_type="python_app",
            test_path="../../etc",
            confirmed=True,
        )
        result = await engine.run_tests(req)
        assert result.success is False
        assert result.security_blocked is True

    @pytest.mark.asyncio
    async def test_unsupported_project_type_blocked(self):
        engine = TestEngine()
        req = TestRequest(
            project_name="SandboxTest",
            project_type="cobol",
            confirmed=True,
        )
        result = await engine.run_tests(req)
        assert result.success is False
        assert result.security_blocked is True


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONE 5 — Error Analyzer
# ─────────────────────────────────────────────────────────────────────────────

class TestErrorAnalyzer:

    def _make_result(self, stdout="", stderr="", success=False, exit_code=1):
        from app.dev_engine.sandbox import SandboxProcessResult
        return SandboxProcessResult(
            success=success, executable="npm", exit_code=exit_code,
            stdout=stdout, stderr=stderr, project_name="Test", operation_label="test",
        )

    def test_parse_typescript_error(self):
        analyzer = ErrorAnalyzer()
        stderr = "src/App.tsx(12,5): error TS2345: Argument of type 'string' is not assignable to 'number'."
        result = self._make_result(stderr=stderr)
        errors = analyzer.analyze_npm_build(result)
        assert len(errors) >= 1
        assert errors[0].file_path == "src/App.tsx"
        assert errors[0].line_number == 12
        assert errors[0].error_code == "TS2345"
        assert errors[0].severity == "ERROR"

    def test_parse_python_syntax_error(self):
        analyzer = ErrorAnalyzer()
        stderr = '  File "main.py", line 3\n    def broken(:\n             ^\nSyntaxError: invalid syntax\n'
        result = self._make_result(stderr=stderr)
        errors = analyzer.analyze_python_build(result)
        assert len(errors) >= 1
        assert errors[0].file_path == "main.py"
        assert errors[0].line_number == 3

    def test_sanitize_absolute_path(self):
        analyzer = ErrorAnalyzer()
        raw = r"C:\Users\user\projects\MyApp\src\App.tsx"
        sanitized = analyzer._sanitize_path(raw)
        assert "C:\\" not in sanitized
        assert "src/App.tsx" in sanitized or "App.tsx" in sanitized

    def test_sanitize_message_removes_tokens(self):
        analyzer = ErrorAnalyzer()
        msg = "Error with token=ghp_abcdefghijklmnopqrstuvwxyz12345678901234"
        sanitized = analyzer._sanitize_message(msg)
        assert "ghp_" not in sanitized
        assert "[REDACTED]" in sanitized

    def test_fallback_error_on_generic_failure(self):
        analyzer = ErrorAnalyzer()
        result = self._make_result(stderr="Something went horribly wrong")
        errors = analyzer.analyze_npm_build(result)
        assert len(errors) == 1
        assert "Something went horribly wrong" in errors[0].message

    def test_parse_pytest_failures(self):
        analyzer = ErrorAnalyzer()
        stdout = "FAILED tests/test_main.py::test_addition\nFAILED tests/test_main.py::test_subtraction"
        result = self._make_result(stdout=stdout)
        errors = analyzer.analyze_pytest(result)
        assert len(errors) == 2
        assert all(e.category == "TEST_FAILURE" for e in errors)


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONE 9 — Quality Gate
# ─────────────────────────────────────────────────────────────────────────────

class TestQualityGate:

    @pytest.mark.asyncio
    async def test_blocked_if_project_dir_missing(self):
        gate = QualityGate()
        result = gate.evaluate(
            project_name="DoesNotExist99",
            expected_files=["index.html"],
        )
        assert result.state == "BLOCKED"

    @pytest.mark.asyncio
    async def test_fail_if_files_missing(self):
        folder_tool = CreateProjectFolderTool()
        await folder_tool.execute(project_name="QualityTest")

        gate = QualityGate()
        result = gate.evaluate(
            project_name="QualityTest",
            expected_files=["index.html", "styles.css"],
        )
        assert result.state == "FAIL"
        assert any("missing" in c.message.lower() for c in result.checks if not c.passed)

    @pytest.mark.asyncio
    async def test_fail_if_no_build_result(self):
        folder_tool = CreateProjectFolderTool()
        file_tool = CreateProjectFileTool()
        await folder_tool.execute(project_name="QualityTest")
        await file_tool.execute(project_name="QualityTest", relative_path="index.html", content="<html><body>ok</body></html>")
        await file_tool.execute(project_name="QualityTest", relative_path="requirements.txt", content="pytest\n")

        gate = QualityGate()
        result = gate.evaluate(
            project_name="QualityTest",
            expected_files=["index.html"],
            build_result=None,
        )
        # No build result → build check fails → overall FAIL
        assert result.state == "FAIL"

    @pytest.mark.asyncio
    async def test_pass_when_all_checks_ok(self):
        from app.dev_engine.build_engine import BuildResult as BR
        from app.dev_engine.test_engine import TestResult as TR

        folder_tool = CreateProjectFolderTool()
        file_tool = CreateProjectFileTool()
        await folder_tool.execute(project_name="QualityTest")
        await file_tool.execute(project_name="QualityTest", relative_path="index.html", content="<html><body>ok</body></html>")
        await file_tool.execute(project_name="QualityTest", relative_path="requirements.txt", content="pytest\n")

        fake_build = BR(
            success=True, project_name="QualityTest", project_type="vanilla_web",
            exit_code=0, message="OK"
        )
        fake_tests = TR(
            success=True, project_name="QualityTest", project_type="vanilla_web",
            tests_total=5, tests_passed=5
        )

        gate = QualityGate()
        result = gate.evaluate(
            project_name="QualityTest",
            expected_files=["index.html"],
            build_result=fake_build,
            test_result=fake_tests,
        )
        assert result.state == "PASS"
        assert result.passed is True


# ─────────────────────────────────────────────────────────────────────────────
# MILESTONE 10 — Git Engine (validation; no real git needed for policy tests)
# ─────────────────────────────────────────────────────────────────────────────

class TestGitEngine:

    def test_commit_message_valid(self):
        assert _validate_commit_message("feat: add dark futuristic UI") is None
        assert _validate_commit_message("fix(auth): resolve token expiry bug") is None

    def test_commit_message_rejects_injection(self):
        assert _validate_commit_message("msg; rm -rf /") is not None
        assert _validate_commit_message("msg && evil") is not None
        assert _validate_commit_message("") is not None
        assert _validate_commit_message("x" * 201) is not None

    def test_branch_name_validation(self):
        assert _validate_branch("main") is None
        assert _validate_branch("feature/auth-login") is None
        assert _validate_branch("fix-123") is None
        assert _validate_branch("") is not None
        assert _validate_branch("branch;evil") is not None

    @pytest.mark.asyncio
    async def test_git_add_blocked_without_confirmation(self):
        engine = GitEngine()
        result = await engine.add(project_name="GitTest", confirmed=False)
        assert result.success is False
        assert result.security_blocked is True

    @pytest.mark.asyncio
    async def test_git_commit_blocked_without_confirmation(self):
        engine = GitEngine()
        result = await engine.commit(project_name="GitTest", message="test", confirmed=False)
        assert result.success is False
        assert result.security_blocked is True

    @pytest.mark.asyncio
    async def test_git_commit_blocked_with_injected_message(self):
        engine = GitEngine()
        result = await engine.commit(
            project_name="GitTest",
            message="commit; rm -rf /",
            confirmed=True,
        )
        assert result.success is False
        assert result.security_blocked is True

    @pytest.mark.asyncio
    async def test_git_add_rejects_path_traversal(self):
        engine = GitEngine()
        result = await engine.add(
            project_name="GitTest",
            paths=["../../secret.txt"],
            confirmed=True,
        )
        assert result.success is False
        assert result.security_blocked is True

    @pytest.mark.asyncio
    async def test_git_status_read_only_safe(self):
        """git status is read-only and doesn't need confirmation — should attempt."""
        # Create project dir so runner doesn't fail on missing dir
        folder_tool = CreateProjectFolderTool()
        await folder_tool.execute(project_name="GitTest")

        engine = GitEngine()
        result = await engine.status(project_name="GitTest")
        # May fail if git not initialized, but should NOT be a security block
        assert not result.security_blocked


# ─────────────────────────────────────────────────────────────────────────────
# Security attack regression — cross-milestone
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurityAttacks:

    def test_command_injection_blocked_sandbox(self):
        policy = SandboxPolicy()
        injections = [
            [";", "rm -rf /"],
            ["&&", "whoami"],
            ["|", "cat /etc/passwd"],
            ["`", "id", "`"],
            ["$(whoami)"],
        ]
        for args in injections:
            err = policy.validate_arguments(args)
            assert err is not None, f"Injection not blocked: {args}"

    def test_path_traversal_blocked_sandbox(self):
        policy = SandboxPolicy()
        traversal = [["../../etc/passwd"], ["..\\..\\"]]
        for args in traversal:
            err = policy.validate_arguments(args)
            assert err is not None, f"Traversal not blocked: {args}"

    def test_arbitrary_executable_rejected(self):
        policy = SandboxPolicy()
        arbitrary = ["calc.exe", "explorer", "regedit", "format", "del", "rm"]
        for exe in arbitrary:
            assert not policy.is_executable_approved(exe), f"'{exe}' should be rejected"

    @pytest.mark.asyncio
    async def test_environment_injection_not_forwarded(self):
        """Verify malicious env key is silently dropped."""
        runner = SandboxProcessRunner()
        # Build safe env manually to test the filter
        safe_env = runner._build_safe_environment(
            {"SAFE_KEY": "value", "invalid-key!": "evil", "123BAD": "x"}
        )
        assert "invalid-key!" not in safe_env
        assert "123BAD" not in safe_env

    @pytest.mark.asyncio
    async def test_oversized_output_capped(self):
        """Verify output exceeding MAX_OUTPUT_BYTES is truncated safely."""
        from app.dev_engine.sandbox import MAX_OUTPUT_BYTES
        oversized = b"X" * (MAX_OUTPUT_BYTES + 10_000)
        capped = SandboxProcessRunner._cap(oversized)
        assert "[OUTPUT TRUNCATED" in capped

    def test_malicious_npm_packages_rejected(self):
        attacks = [
            "pkg;shutdown -h now",
            "pkg`rm -rf /`",
            "../../evil",
            "pkg && whoami",
        ]
        for pkg in attacks:
            assert validate_npm_package(pkg) is not None, f"Malicious npm pkg passed: {pkg}"

    def test_malicious_pypi_packages_rejected(self):
        attacks = ["pkg;rm -rf /", "../../evil", "pkg && echo bad"]
        for pkg in attacks:
            assert validate_pypi_package(pkg) is not None, f"Malicious pypi pkg passed: {pkg}"

    def test_git_commit_message_injection(self):
        injections = [
            "feat: ok; rm -rf /",
            "msg && calc",
            "feat: test | dir",
        ]
        for msg in injections:
            assert _validate_commit_message(msg) is not None, f"Injection passed: {msg}"

    @pytest.mark.asyncio
    async def test_dev_loop_respects_cancellation(self):
        loop = DevelopmentLoopManager(max_iterations=5)
        loop.cancel()   # cancel before even starting
        result = await loop.run(
            project_name="SandboxTest",
            project_type="python_app",
        )
        assert result.cancelled is True
        assert result.success is False
