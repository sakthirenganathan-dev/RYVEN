"""Lightweight derived project state tracking for RYVEN Orchestrator."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ProjectState(BaseModel):
    """Derived snapshot of project status across the development lifecycle."""
    project_name: str
    project_path: str = ""
    framework: str = "unknown"
    current_branch: str = "main"
    git_clean: bool = True
    modified_files: List[str] = Field(default_factory=list)
    build_status: str = "NOT_CHECKED"      # "NOT_CHECKED", "PASSED", "FAILED"
    test_status: str = "NOT_CHECKED"       # "NOT_CHECKED", "PASSED", "FAILED", "TESTS_NOT_AVAILABLE"
    quality_gate_status: str = "NOT_CHECKED" # "NOT_CHECKED", "PASSED", "FAILED"
    deployment_status: str = "NOT_DEPLOYED"# "NOT_DEPLOYED", "DEPLOYING", "SUCCESS", "FAILED"
    deployment_url: Optional[str] = None
    health_status: str = "UNKNOWN"         # "UNKNOWN", "HEALTHY", "DEGRADED", "UNHEALTHY"
    health_latency_ms: Optional[float] = None
    last_commit_hash: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        """Return clean dictionary snapshot."""
        return self.model_dump()


class ProjectStateTracker:
    """Manages derived project state updates during orchestration."""

    def __init__(self, project_name: str = "", project_path: str = "") -> None:
        norm_path = project_path.replace("\\", "/") if project_path else ""
        self.state = ProjectState(project_name=project_name, project_path=norm_path)

    def get_or_create(self, project_path: str, project_name: str) -> ProjectState:
        norm_path = project_path.replace("\\", "/") if project_path else ""
        self.state.project_path = norm_path
        self.state.project_name = project_name
        return self.state

    def update_from_scan(self, project_path: str, framework: str) -> None:
        self.state.project_path = project_path.replace("\\", "/")
        self.state.framework = framework

    def update_from_modification(self, modified_files: List[str]) -> None:
        self.state.modified_files = list(set(self.state.modified_files + modified_files))
        self.state.git_clean = False
        self.state.build_status = "NOT_CHECKED"
        self.state.test_status = "NOT_CHECKED"
        self.state.quality_gate_status = "NOT_CHECKED"

    def update_from_build(self, success: bool) -> None:
        self.state.build_status = "PASSED" if success else "FAILED"

    def update_build(self, project_path: str, success: bool) -> None:
        self.update_from_build(success)

    def update_from_test(self, success: bool, no_tests: bool = False) -> None:
        if no_tests:
            self.state.test_status = "TESTS_NOT_AVAILABLE"
        else:
            self.state.test_status = "PASSED" if success else "FAILED"

    def update_test(self, project_path: str, success: bool, no_tests: bool = False) -> None:
        self.update_from_test(success, no_tests=no_tests)

    def update_from_quality_gate(self, passed: bool) -> None:
        self.state.quality_gate_status = "PASSED" if passed else "FAILED"

    def update_quality_gate(self, project_path: str, status: str) -> None:
        self.state.quality_gate_status = status

    def update_from_git(self, branch: Optional[str] = None, commit_hash: Optional[str] = None, is_clean: bool = True) -> None:
        if branch:
            self.state.current_branch = branch
        if commit_hash:
            self.state.last_commit_hash = commit_hash
        self.state.git_clean = is_clean

    def update_from_deployment(self, status: str, url: Optional[str] = None) -> None:
        self.state.deployment_status = status
        if url:
            self.state.deployment_url = url

    def update_deployment(self, project_path: str, url: str, health: str = "UNKNOWN", latency_ms: Optional[float] = None) -> None:
        self.update_from_deployment("SUCCESS", url)
        self.update_from_health(health, latency_ms)

    def update_from_health(self, status: str, latency_ms: Optional[float] = None) -> None:
        self.state.health_status = status
        if latency_ms is not None:
            self.state.health_latency_ms = latency_ms

    def get_state(self) -> ProjectState:
        return self.state
