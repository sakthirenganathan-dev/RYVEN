"""Safe tool registry for RYVEN."""

from typing import Any, Dict, List, Optional
from app.core.logging_config import logger
from app.tools.base import BaseTool


class ToolRegistry:
    """Registry maintaining safe, explicitly authorized tools."""

    def __init__(self) -> None:
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool, override: bool = False) -> None:
        """Register a new tool instance in the registry."""
        if not isinstance(tool, BaseTool):
            raise TypeError(f"Tool {tool} must inherit from BaseTool")

        if not tool.name or not isinstance(tool.name, str):
            raise ValueError("Tool must define a non-empty string 'name'")

        name_lower = tool.name.lower()
        if name_lower in self._tools and not override:
            raise ValueError(f"Tool '{tool.name}' is already registered in ToolRegistry")

        self._tools[name_lower] = tool
        logger.info(f"Registered tool: {tool.name} ({tool.description})")

    def get(self, name: str) -> Optional[BaseTool]:
        """Retrieve a registered tool by case-insensitive name."""
        return self._tools.get(name.lower())

    def has_tool(self, name: str) -> bool:
        """Check if a tool exists in the registry."""
        return name.lower() in self._tools

    def has(self, name: str) -> bool:
        """Alias for has_tool."""
        return self.has_tool(name)

    def list_tools(self) -> List[str]:
        """Return names of all currently registered tools."""
        return [tool.name for tool in self._tools.values()]

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return metadata for all registered tools."""
        return [tool.get_info() for tool in self._tools.values()]


def create_default_registry() -> ToolRegistry:
    """Instantiate and populate a ToolRegistry with all standard Phase 3 safe tools."""
    from app.tools.time_tool import TimeTool
    from app.tools.system_tool import SystemStatusTool
    from app.tools.system_info_tool import SystemInfoTool
    from app.tools.app_tool import OpenApplicationTool
    from app.tools.website_tool import OpenWebsiteTool
    from app.tools.folder_tool import OpenFolderTool
    from app.tools.search_tool import SearchFilesTool
    from app.tools.file_tool import OpenFileTool
    from app.tools.clipboard_tool import GetClipboardTool, SetClipboardTool
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

    registry = ToolRegistry()
    registry.register(TimeTool())
    registry.register(SystemStatusTool())
    registry.register(SystemInfoTool())
    registry.register(OpenApplicationTool())
    registry.register(OpenWebsiteTool())
    registry.register(OpenFolderTool())
    registry.register(SearchFilesTool())
    registry.register(OpenFileTool())
    registry.register(GetClipboardTool())
    registry.register(SetClipboardTool())
    registry.register(CreateProjectFolderTool())
    registry.register(CreateProjectFileTool())
    registry.register(ValidateProjectFilesTool())
    registry.register(ApplyProjectModificationTool())
    registry.register(ResolveExistingProjectTool())
    registry.register(ScanExistingProjectTool())
    registry.register(PlanProjectModificationsTool())
    registry.register(ValidateModificationPlanTool())
    registry.register(BuildProjectTool())
    registry.register(TestProjectTool())
    registry.register(QualityGateTool())
    # M11 Git Tools
    registry.register(GitStatusTool())
    registry.register(GitDiffTool())
    registry.register(GitBranchTool())
    registry.register(GitRemoteTool())
    registry.register(GitLogTool())
    registry.register(GitStageTool())
    registry.register(GitUnstageTool())
    registry.register(GitCommitTool())
    registry.register(GitPushTool())
    # M12 Deployment Tools
    from app.tools.deployment_tool import (
        DeploymentDeployTool,
        DeploymentDetectTool,
        DeploymentPreflightTool,
        DeploymentPreviewTool,
        DeploymentStatusTool,
        DeploymentVerifyTool,
    )
    registry.register(DeploymentDetectTool())
    registry.register(DeploymentPreflightTool())
    registry.register(DeploymentPreviewTool())
    registry.register(DeploymentDeployTool())
    registry.register(DeploymentStatusTool())
    registry.register(DeploymentVerifyTool())
    # M11.5 Knowledge Graph Tools
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
    registry.register(GraphStatusTool())
    registry.register(GraphBuildTool())
    registry.register(GraphUpdateTool())
    registry.register(GraphQueryTool())
    registry.register(GraphFindSymbolTool())
    registry.register(GraphFindDependenciesTool())
    registry.register(GraphFindDependentsTool())
    registry.register(GraphFindCallersTool())
    registry.register(GraphExplainTool())
    registry.register(GraphPathTool())
    # M13 Health & Verification Tools
    from app.health.health_tool import (
        HealthCheckTool,
        HealthStatusTool,
        HealthHistoryTool,
        HealthMonitorStartTool,
        HealthMonitorStopTool,
    )
    registry.register(HealthCheckTool())
    registry.register(HealthStatusTool())
    registry.register(HealthHistoryTool())
    registry.register(HealthMonitorStartTool())
    registry.register(HealthMonitorStopTool())
    return registry



