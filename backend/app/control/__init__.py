"""RYVEN 3.0 M17.0 — Unified Computer & Internet Control Plane Package."""

from app.control.models import (
    ControlRequest,
    ControlResult,
    ControlStatus,
    DiscoveredScopeItem,
    FailureClass,
    ObservationRecord,
    PermissionCategory,
    ScopeBoundary,
)
from app.control.permissions import (
    CapabilityPermissionManager,
    PermissionCheckResult,
    permission_manager,
)
def __getattr__(name: str):
    if name in ("ObserverEngine", "observer_engine"):
        import app.control.observer as obs
        return getattr(obs, name)
    if name in ("RyvenControlEngine", "ryven_control_engine"):
        import app.control.engine as eng
        return getattr(eng, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "ControlRequest",
    "ControlResult",
    "ControlStatus",
    "DiscoveredScopeItem",
    "FailureClass",
    "ObservationRecord",
    "PermissionCategory",
    "ScopeBoundary",
    "CapabilityPermissionManager",
    "PermissionCheckResult",
    "permission_manager",
    "ObserverEngine",
    "observer_engine",
    "RyvenControlEngine",
    "ryven_control_engine",
]
