"""RYVEN 3.0 — AI Privacy Modes and Boundary Policies.

Defines authoritative privacy modes governing local vs. remote inference,
credential scrubbing, and interactive authorization gates (M17.10 Phase 4).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional, Union


class PrivacyMode(str, Enum):
    """Authoritative privacy boundaries for model execution."""

    LOCAL_ONLY = "LOCAL_ONLY"
    """Permit local providers only. Remote execution is strictly prohibited."""

    PRIVACY_FIRST = "PRIVACY_FIRST"
    """Secure default. Local is preferred; remote inference is denied unless explicitly
    authorized by user confirmation and free of sensitive data.
    """

    BALANCED = "BALANCED"
    """Local is preferred. Remote inference is permitted when explicitly enabled,
    eligible, and sensitive content has been safely redacted.
    """

    MAX_REASONING = "MAX_REASONING"
    """Permits remote reasoning when explicitly selected and authorized.
    Secret inspection and security denials cannot be overridden.
    """


DEFAULT_PRIVACY_MODE = PrivacyMode.PRIVACY_FIRST


def parse_privacy_mode(val: Optional[Union[PrivacyMode, str]]) -> PrivacyMode:
    """Parse string or enum into PrivacyMode, defaulting to PRIVACY_FIRST if None."""
    if val is None:
        return DEFAULT_PRIVACY_MODE
    if isinstance(val, PrivacyMode):
        return val
    try:
        return PrivacyMode(str(val).strip().upper())
    except (ValueError, KeyError, AttributeError):
        raise ValueError(
            f"Unsupported privacy mode '{val}'. Valid modes: "
            f"{[m.value for m in PrivacyMode]}"
        )
