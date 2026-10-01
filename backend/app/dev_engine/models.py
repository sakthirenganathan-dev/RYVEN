"""Data models and schemas for RYVEN DEV ENGINE v1."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import hashlib


class ProjectType(str, Enum):
    """Supported project templates in DEV ENGINE v1."""
    REACT_TS = "react_ts"
    VANILLA_WEB = "vanilla_web"
    PYTHON_APP = "python_app"
    PYTHON_API = "python_api"


class ProjectSpecification(BaseModel):
    """Structured architectural specification for a requested development project."""

    project_name: str
    project_type: str = "react_ts"
    language: str = "typescript"
    framework: str = "react"
    description: str = ""
    features: List[str] = Field(default_factory=list)
    pages: List[str] = Field(default_factory=list)
    components: List[str] = Field(default_factory=list)
    data_models: List[str] = Field(default_factory=list)
    api_requirements: List[str] = Field(default_factory=list)
    authentication: Optional[str] = None
    styling: str = "dark futuristic"
    dependencies: List[str] = Field(default_factory=list)
    entry_points: List[str] = Field(default_factory=list)
    expected_files: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    generation_strategy: str = "template_customized"


class FilePlanItem(BaseModel):
    """Individual file planned for generation."""

    relative_path: str
    action: str = "CREATE"  # Allowed: CREATE. Disallowed: DELETE, RENAME, MOVE
    content_source: str = "generator"
    required: bool = True
    overwrite_allowed: bool = False
    validation_rules: List[str] = Field(default_factory=list)


class FilePlan(BaseModel):
    """Complete file creation plan for an entire project."""

    project_name: str
    project_type: str
    items: List[FilePlanItem] = Field(default_factory=list)
    total_files: int = 0
    estimated_size_bytes: int = 0
    overwrite_allowed: bool = False

    def model_post_init(self, __context: Any) -> None:
        self.total_files = len(self.items)


class GeneratedFile(BaseModel):
    """In-memory generated file representation.
    
    Security: The AI/Engine creates GeneratedFile objects in memory only.
    Writing to disk must strictly occur through ToolRegistry / CreateProjectFileTool.
    """

    relative_path: str
    content: str
    language: str = "text"
    size_bytes: int = 0
    checksum_sha256: str = ""
    validation_metadata: Dict[str, Any] = Field(default_factory=dict)

    def model_post_init(self, __context: Any) -> None:
        raw_bytes = self.content.encode("utf-8")
        self.size_bytes = len(raw_bytes)
        self.checksum_sha256 = hashlib.sha256(raw_bytes).hexdigest()


class BuildTestPolicy(BaseModel):
    """Security policy governing build and test execution."""

    allowed_commands: List[str] = Field(default_factory=lambda: ["build", "lint", "test", "check"])
    disallowed_executables: List[str] = Field(
        default_factory=lambda: [
            "cmd.exe",
            "cmd",
            "powershell.exe",
            "powershell",
            "bash",
            "sh",
            "eval",
            "sudo",
            "npm",
            "npx",
            "pip",
            "pip3",
            "yarn",
            "pnpm",
        ]
    )
    allow_arbitrary_shell: bool = False
    max_timeout_seconds: float = 60.0


class BuildTestRequest(BaseModel):
    """Controlled specification for a build or test run."""

    project_name: str
    command_type: str  # e.g., "syntax_check", "dry_run_build"
    working_directory: str = ""
    timeout_seconds: float = 30.0


class BuildTestResult(BaseModel):
    """Execution telemetry for a safe build/test operation."""

    success: bool
    command: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    message: str = ""


class BuildError(BaseModel):
    """Structured representation of an error encountered during validation/build."""

    error_type: str  # e.g., "SYNTAX_ERROR", "MISSING_FILE", "SCHEMA_ERROR"
    message: str
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    raw_log: str = ""


class FixProposal(BaseModel):
    """AI-proposed remedial fix for an identified error."""

    fix_id: str
    target_file: str
    description: str
    proposed_content: str
    requires_confirmation: bool = True


class FixResult(BaseModel):
    """Outcome of attempting to apply a fix proposal."""

    success: bool
    fix_id: str
    applied: bool = False
    message: str


class ProjectDevelopmentResult(BaseModel):
    """Final structured outcome returned by DEV ENGINE v1."""

    workflow_id: str
    project_name: str
    project_type: str
    project_path: str
    status: str  # COMPLETED, WAITING_FOR_CONFIRMATION, FAILED, BLOCKED
    files_created: int = 0
    files_failed: int = 0
    validation_passed: bool = False
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    duration_ms: float = 0.0
    next_action: str = ""
