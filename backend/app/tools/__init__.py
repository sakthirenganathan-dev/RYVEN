"""Tool system package for safe registered operations."""

from app.tools.base import BaseTool
from app.tools.registry import ToolRegistry
from app.tools.time_tool import TimeTool
from app.tools.system_tool import SystemStatusTool
from app.tools.app_tool import OpenApplicationTool
from app.tools.website_tool import OpenWebsiteTool
from app.tools.folder_tool import OpenFolderTool
from app.tools.search_tool import SearchFilesTool
from app.tools.file_tool import OpenFileTool
from app.tools.clipboard_tool import GetClipboardTool, SetClipboardTool
from app.tools.system_info_tool import SystemInfoTool
from app.tools.project_tool import (
    CreateProjectFolderTool,
    CreateProjectFileTool,
    ValidateProjectFilesTool,
)
from app.tools.modification_tool import (
    ApplyProjectModificationTool,
    BuildProjectTool,
    PlanProjectModificationsTool,
    QualityGateTool,
    ResolveExistingProjectTool,
    ScanExistingProjectTool,
    TestProjectTool,
    ValidateModificationPlanTool,
)
from app.tools.git_tool import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitPushTool,
    GitRemoteTool,
    GitStageTool,
    GitStatusTool,
    GitUnstageTool,
)
from app.tools.deployment_tool import (
    DeploymentDeployTool,
    DeploymentDetectTool,
    DeploymentPreflightTool,
    DeploymentPreviewTool,
    DeploymentStatusTool,
    DeploymentVerifyTool,
)
from app.tools.knowledge_graph_tool import (
    GraphBuildTool,
    GraphExplainTool,
    GraphFindCallersTool,
    GraphFindDependenciesTool,
    GraphFindDependentsTool,
    GraphFindSymbolTool,
    GraphPathTool,
    GraphQueryTool,
    GraphStatusTool,
    GraphUpdateTool,
)


__all__ = [
    "BaseTool",
    "ToolRegistry",
    "TimeTool",
    "SystemStatusTool",
    "OpenApplicationTool",
    "OpenWebsiteTool",
    "OpenFolderTool",
    "SearchFilesTool",
    "OpenFileTool",
    "GetClipboardTool",
    "SetClipboardTool",
    "SystemInfoTool",
    "CreateProjectFolderTool",
    "CreateProjectFileTool",
    "ValidateProjectFilesTool",
    "ApplyProjectModificationTool",
    "ResolveExistingProjectTool",
    "ScanExistingProjectTool",
    "PlanProjectModificationsTool",
    "ValidateModificationPlanTool",
    "BuildProjectTool",
    "TestProjectTool",
    "QualityGateTool",
    "GitStatusTool",
    "GitDiffTool",
    "GitBranchTool",
    "GitRemoteTool",
    "GitLogTool",
    "GitStageTool",
    "GitUnstageTool",
    "GitCommitTool",
    "GitPushTool",
    "DeploymentDetectTool",
    "DeploymentPreflightTool",
    "DeploymentPreviewTool",
    "DeploymentDeployTool",
    "DeploymentStatusTool",
    "DeploymentVerifyTool",
    "GraphBuildTool",
    "GraphUpdateTool",
    "GraphQueryTool",
    "GraphFindSymbolTool",
    "GraphFindDependenciesTool",
    "GraphFindDependentsTool",
    "GraphFindCallersTool",
    "GraphExplainTool",
    "GraphPathTool",
    "GraphStatusTool",
    "HealthCheckTool",
    "HealthStatusTool",
    "HealthHistoryTool",
    "HealthMonitorStartTool",
    "HealthMonitorStopTool",
    "OrchestrateTaskTool",
    "GetOrchestrationStatusTool",
    "ConfirmOrchestrationTool",
    "PauseOrchestrationTool",
    "ResumeOrchestrationTool",
    "CancelOrchestrationTool",
]


def __getattr__(name: str):
    if name in (
        "HealthCheckTool",
        "HealthStatusTool",
        "HealthHistoryTool",
        "HealthMonitorStartTool",
        "HealthMonitorStopTool",
    ):
        import app.health.health_tool as ht
        return getattr(ht, name)
    if name in (
        "OrchestrateTaskTool",
        "GetOrchestrationStatusTool",
        "ConfirmOrchestrationTool",
        "PauseOrchestrationTool",
        "ResumeOrchestrationTool",
        "CancelOrchestrationTool",
    ):
        import app.orchestrator.tools as ot
        return getattr(ot, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")



