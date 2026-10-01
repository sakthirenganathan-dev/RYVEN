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

    def list_tools(self) -> List[str]:
        """Return names of all currently registered tools."""
        return [tool.name for tool in self._tools.values()]

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Return metadata for all registered tools."""
        return [tool.get_info() for tool in self._tools.values()]
