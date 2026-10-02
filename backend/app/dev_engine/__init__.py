"""RYVEN DEV ENGINE v2 — Project Intelligence, Code Generation, and Execution Engine Package."""

# ── v1 exports (preserved for 100% backward compatibility) ──────────────────
from app.dev_engine.models import (
    ProjectType,
    ProjectSpecification,
    FilePlanItem,
    FilePlan,
    GeneratedFile,
    BuildTestRequest,
    BuildTestResult,
    BuildTestPolicy,
    BuildError as LegacyBuildError,
    FixProposal,
    FixResult,
    ProjectDevelopmentResult,
)
from app.dev_engine.specification import ProjectSpecificationEngine
from app.dev_engine.file_plan import FilePlanEngine
from app.dev_engine.generator import CodeGenerationEngine
from app.dev_engine.validator import ProjectQualityValidator
from app.dev_engine.build_test import SafeBuildExecutor
from app.dev_engine.fix_loop import FixLoopManager

# ── v2 exports ───────────────────────────────────────────────────────────────
from app.dev_engine.error_analyzer import BuildError, ErrorAnalyzer
from app.dev_engine.sandbox import SandboxPolicy, SandboxProcessRequest, SandboxProcessResult, SandboxProcessRunner
from app.dev_engine.dependency import DependencyManager, DependencyRequest, DependencyResult
from app.dev_engine.build_engine import BuildEngine, BuildRequest, BuildResult
from app.dev_engine.test_engine import TestEngine, TestRequest, TestResult
from app.dev_engine.dev_loop import DevelopmentLoopManager, DevelopmentLoopResult
from app.dev_engine.quality_gate import QualityGate, QualityGateResult
from app.dev_engine.git_engine import GitEngine, GitResult

# ── M8 / M8.5 exports (Existing Project Modification Integration) ─────────────
from app.dev_engine.m8_models import (
    ExistingProjectModel,
    ExistingProjectModificationResult,
    ModificationRequest,
    ModificationPlan,
    FilePatch,
    PatchOperation,
    RollbackRecord,
    RollbackEntry,
    ModificationResult,
    ModificationStatus,
    RiskLevel as M8RiskLevel,
)
from app.dev_engine.m8_resolver import ProjectResolver, ResolvedProject
from app.dev_engine.m8_scanner import ProjectScanner
from app.dev_engine.m8_planner import DiffEngine, ModificationPlanner
from app.dev_engine.m8_validator import ModificationValidator
from app.dev_engine.m8_apply import ApplyEngine, RollbackEngine
from app.dev_engine.m8_orchestrator import ExistingProjectOrchestrator

__all__ = [
    # v1
    "ProjectType", "ProjectSpecification", "FilePlanItem", "FilePlan",
    "GeneratedFile", "BuildTestRequest", "BuildTestResult", "BuildTestPolicy",
    "LegacyBuildError", "FixProposal", "FixResult", "ProjectDevelopmentResult",
    "ProjectSpecificationEngine", "FilePlanEngine", "CodeGenerationEngine",
    "ProjectQualityValidator", "SafeBuildExecutor", "FixLoopManager",
    # v2
    "BuildError", "ErrorAnalyzer",
    "SandboxPolicy", "SandboxProcessRequest", "SandboxProcessResult", "SandboxProcessRunner",
    "DependencyManager", "DependencyRequest", "DependencyResult",
    "BuildEngine", "BuildRequest", "BuildResult",
    "TestEngine", "TestRequest", "TestResult",
    "DevelopmentLoopManager", "DevelopmentLoopResult",
    "QualityGate", "QualityGateResult",
    "GitEngine", "GitResult",
    # M8 & M8.5
    "ExistingProjectModel", "ExistingProjectModificationResult", "ModificationRequest", "ModificationPlan",
    "FilePatch", "PatchOperation", "RollbackRecord", "RollbackEntry",
    "ModificationResult", "ModificationStatus", "M8RiskLevel",
    "ProjectResolver", "ResolvedProject",
    "ProjectScanner",
    "DiffEngine", "ModificationPlanner",
    "ModificationValidator",
    "ApplyEngine", "RollbackEngine",
    "ExistingProjectOrchestrator",
]
