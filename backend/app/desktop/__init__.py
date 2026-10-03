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
]
