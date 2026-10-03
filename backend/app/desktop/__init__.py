"""
RYVEN 3.0 — Controlled Desktop Interaction Package (Milestone 17.1).
Provides native Windows desktop observation, semantic target resolution,
and controlled action execution.
"""

from app.desktop.interaction import WindowsDesktopDriver, desktop_driver
from app.desktop.resolver import DesktopTargetResolver, desktop_target_resolver

__all__ = [
    "WindowsDesktopDriver",
    "desktop_driver",
    "DesktopTargetResolver",
    "desktop_target_resolver",
    "DesktopActionEngine",
    "desktop_action_engine",
    "DesktopInspectTool",
    "DesktopFocusTool",
    "DesktopClickTool",
    "DesktopDoubleClickTool",
    "DesktopRightClickTool",
    "DesktopTypeTool",
    "DesktopKeyTool",
    "DesktopHotkeyTool",
    "DesktopScrollTool",
]


def __getattr__(name: str):
    if name in ("DesktopActionEngine", "desktop_action_engine"):
        import app.desktop.action_engine as ae
        return getattr(ae, name)
    if name in (
        "DesktopInspectTool",
        "DesktopFocusTool",
        "DesktopClickTool",
        "DesktopDoubleClickTool",
        "DesktopRightClickTool",
        "DesktopTypeTool",
        "DesktopKeyTool",
        "DesktopHotkeyTool",
        "DesktopScrollTool",
    ):
        import app.desktop.tools as tools
        return getattr(tools, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
