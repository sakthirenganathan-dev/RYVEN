"""RYVEN DEV ENGINE — M8: Existing Project Modification Engine Models.

Defines all data structures for:
  - ExistingProjectModel (scanned project state)
  - ModificationRequest (what the user wants changed)
  - ModificationPlan (what files will change and how)
  - PatchOperation (single-file change unit)
  - ModificationResult (final outcome)
  - RollbackRecord (pre-modification snapshot for restoration)
"""

from __future__ import annotations

import hashlib
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Project scan models
# ─────────────────────────────────────────────────────────────────────────────

class DetectedProjectType(str, Enum):
    REACT_TS = "react_ts"
    REACT_JS = "react_js"
    PYTHON_APP = "python_app"
    PYTHON_API = "python_api"
    VANILLA_WEB = "vanilla_web"
    UNKNOWN = "unknown"


class ScannedFileInfo(BaseModel):
    """Metadata for a single scanned file (no raw content by default)."""
    relative_path: str
    size_bytes: int = 0
    is_protected: bool = False
    language: str = ""
    purpose: str = ""   # "entry", "component", "config", "test", "style", etc.


class ExistingProjectModel(BaseModel):
    """Structured model of an existing project after scanning."""

    project_name: str
    root_path: str
    framework: str = ""
    language: str = ""
    project_type: DetectedProjectType = DetectedProjectType.UNKNOWN
    package_manager: str = ""   # "npm" | "pip" | ""
    entry_points: List[str] = Field(default_factory=list)
    source_files: List[ScannedFileInfo] = Field(default_factory=list)
    config_files: List[str] = Field(default_factory=list)
    test_files: List[str] = Field(default_factory=list)
    dependency_manifest: str = ""   # "package.json" | "requirements.txt" | ""
    architecture_summary: str = ""  # Short text summary for LLM context
    relevant_files: List[str] = Field(default_factory=list)  # files likely relevant to modification
    protected_files: List[str] = Field(default_factory=list)  # files that may not be modified
    total_files_scanned: int = 0
    scan_truncated: bool = False    # True if file limit was hit

    @property
    def source_file_paths(self) -> List[str]:
        return [f.relative_path for f in self.source_files]


# ─────────────────────────────────────────────────────────────────────────────
# Modification request
# ─────────────────────────────────────────────────────────────────────────────

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ModificationRequest(BaseModel):
    """Structured representation of what the user wants changed in an existing project."""

    request_id: str = Field(default_factory=lambda: f"req-{uuid.uuid4().hex[:8]}")
    project_name: str
    project_path: str
    user_request: str           # original natural-language request
    requested_changes: List[str] = Field(default_factory=list)   # derived action list
    relevant_files: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# Patch / modification plan
# ─────────────────────────────────────────────────────────────────────────────

class PatchOperation(str, Enum):
    MODIFY = "MODIFY"
    CREATE = "CREATE"
    # DELETE is intentionally NOT in the enum — file deletion is blocked by policy.


class FilePatch(BaseModel):
    """A single controlled file modification or creation."""

    patch_id: str = Field(default_factory=lambda: f"patch-{uuid.uuid4().hex[:8]}")
    relative_path: str
    operation: PatchOperation
    original_hash: str = ""         # SHA-256 of original content (empty for CREATE)
    proposed_content: str = ""      # New full content
    reason: str = ""
    validation_status: str = "PENDING"   # PENDING | VALID | INVALID
    size_bytes: int = 0

    def compute_content_hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def model_post_init(self, __context: Any) -> None:
        if self.proposed_content:
            self.size_bytes = len(self.proposed_content.encode("utf-8"))


class ModificationPlan(BaseModel):
    """Complete, structured plan for modifying an existing project."""

    plan_id: str = Field(default_factory=lambda: f"plan-{uuid.uuid4().hex[:8]}")
    project_name: str
    objective: str
    patches: List[FilePatch] = Field(default_factory=list)
    dependency_changes: List[str] = Field(default_factory=list)
    config_changes: List[str] = Field(default_factory=list)
    test_changes: List[str] = Field(default_factory=list)
    validation_strategy: str = ""
    build_strategy: str = ""
    risk_level: RiskLevel = RiskLevel.LOW
    requires_confirmation: bool = True
    summary: str = ""

    @property
    def files_to_modify(self) -> List[str]:
        return [p.relative_path for p in self.patches if p.operation == PatchOperation.MODIFY]

    @property
    def files_to_create(self) -> List[str]:
        return [p.relative_path for p in self.patches if p.operation == PatchOperation.CREATE]

    @property
    def total_changes(self) -> int:
        return len(self.patches)


# ─────────────────────────────────────────────────────────────────────────────
# Rollback
# ─────────────────────────────────────────────────────────────────────────────

class RollbackEntry(BaseModel):
    """Snapshot of a file before modification — used for rollback."""

    relative_path: str
    original_content: str
    original_hash: str
    absolute_path: str
    existed_before: bool = True


class RollbackRecord(BaseModel):
    """Complete rollback snapshot for an entire modification operation."""

    record_id: str = Field(default_factory=lambda: f"rb-{uuid.uuid4().hex[:8]}")
    project_name: str
    plan_id: str
    entries: List[RollbackEntry] = Field(default_factory=list)
    committed: bool = False     # True = successfully written; False = nothing applied yet
    rolled_back: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Modification result
# ─────────────────────────────────────────────────────────────────────────────

class ModificationStatus(str, Enum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"
    BLOCKED = "BLOCKED"
    WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
    CANCELLED = "CANCELLED"


class AppliedChange(BaseModel):
    """Record of a single applied file change."""
    relative_path: str
    operation: str
    bytes_written: int = 0
    success: bool = False
    error: str = ""


class ModificationResult(BaseModel):
    """Final structured outcome of an existing project modification operation."""

    modification_id: str = Field(default_factory=lambda: f"mod-{uuid.uuid4().hex[:8]}")
    project_name: str
    status: ModificationStatus
    success: bool
    message: str
    applied_changes: List[AppliedChange] = Field(default_factory=list)
    build_success: Optional[bool] = None
    test_success: Optional[bool] = None
    quality_gate_passed: Optional[bool] = None
    rollback_available: bool = False
    was_rolled_back: bool = False
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    git_summary: str = ""
    duration_ms: float = 0.0
