"""Unit tests for ToolRegistry, TimeTool, and SystemStatusTool."""

import pytest
from app.tools.base import BaseTool
from app.tools.registry import ToolRegistry
from app.tools.system_tool import SystemStatusTool
from app.tools.time_tool import TimeTool


class DummyTool(BaseTool):
    name = "dummy"
    description = "A dummy testing tool"

    async def execute(self, **kwargs):
        return {"result": "ok"}


@pytest.mark.asyncio
async def test_tool_registry():
    """Verify tool registration, retrieval, and collision handling."""
    registry = ToolRegistry()
    assert len(registry.list_tools()) == 0

    tool = DummyTool()
    registry.register(tool)

    assert registry.has_tool("dummy")
    assert registry.has_tool("DUMMY")  # Case-insensitivity
    assert registry.get("dummy") == tool
    assert registry.list_tools() == ["dummy"]

    # Prevent accidental duplicates
    with pytest.raises(ValueError, match="already registered"):
        registry.register(tool)

    # Allow explicit override
    registry.register(tool, override=True)
    assert len(registry.list_tools()) == 1


@pytest.mark.asyncio
async def test_time_tool():
    """Verify TimeTool execution returns formatted time and date."""
    time_tool = TimeTool()
    output = await time_tool.execute()

    assert "time" in output
    assert "date" in output
    assert "day_of_week" in output
    assert "iso" in output
    assert "message" in output
    assert "It is " in output["message"]


@pytest.mark.asyncio
async def test_system_status_tool():
    """Verify SystemStatusTool returns CPU, RAM, disk, and battery info."""
    sys_tool = SystemStatusTool()
    output = await sys_tool.execute()

    assert "cpu_percent" in output
    assert isinstance(output["cpu_percent"], (int, float))

    assert "ram_total_gb" in output
    assert "ram_used_gb" in output
    assert "ram_percent" in output
    assert output["ram_total_gb"] > 0

    assert "disk" in output
    assert "total_gb" in output["disk"]
    assert "used_gb" in output["disk"]
    assert output["disk"]["total_gb"] > 0

    assert "battery" in output
    assert "available" in output["battery"]
    assert isinstance(output["battery"]["available"], bool)

    assert "message" in output
    assert "System Diagnostics:" in output["message"]
