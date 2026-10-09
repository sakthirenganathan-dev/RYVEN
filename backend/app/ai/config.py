"""RYVEN 3.0 — AI Router & Multi-Model Intelligence Configuration.

Defines validated configuration settings for model routing, provider health tracking,
circuit breakers, and controlled fallbacks (M17.10 Phase 3).
"""

from __future__ import annotations

from typing import Any, Dict
from pydantic import BaseModel, Field, field_validator

from app.ai.privacy import PrivacyMode, parse_privacy_mode


class RouterConfig(BaseModel):
    """Validated configuration for the authoritative ModelRouter."""

    default_local_model: str = Field(
        default="qwen2.5:7b",
        description="Default local model identifier",
    )
    privacy_mode: PrivacyMode = Field(
        default=PrivacyMode.PRIVACY_FIRST,
        description="Active privacy mode: LOCAL_ONLY, PRIVACY_FIRST, BALANCED, MAX_REASONING",
    )
    allow_remote_inference: bool = Field(
        default=False,
        description="Global opt-in flag for remote inference (disabled by default)",
    )
    fallback_enabled: bool = Field(
        default=True,
        description="Whether automated provider fallback is active",
    )
    max_fallback_depth: int = Field(
        default=1,
        description="Maximum fallback attempts (strictly capped at 1)",
    )
    circuit_breaker_threshold: int = Field(
        default=3,
        description="Consecutive failure threshold to trip circuit breaker open",
    )
    circuit_breaker_cooldown_seconds: float = Field(
        default=60.0,
        description="Cooldown duration before attempting half-open recovery probe",
    )
    provider_timeouts: Dict[str, float] = Field(
        default_factory=lambda: {
            "ollama": 120.0,
            "grok": 60.0,
            "huggingface": 60.0,
        },
        description="Provider-specific timeout budgets in seconds",
    )
    history_buffer_size: int = Field(
        default=50,
        description="Bounded size of in-memory latency and telemetry records",
    )

    @field_validator("max_fallback_depth")
    @classmethod
    def validate_fallback_depth(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Fallback depth cannot be negative")
        if v > 1:
            raise ValueError("Maximum fallback depth is strictly hard-capped at 1")
        return v

    @field_validator("circuit_breaker_threshold")
    @classmethod
    def validate_threshold(cls, v: int) -> int:
        if v < 1:
            raise ValueError("Circuit breaker failure threshold must be at least 1")
        return v

    @field_validator("circuit_breaker_cooldown_seconds")
    @classmethod
    def validate_cooldown(cls, v: float) -> float:
        if v <= 0.0:
            raise ValueError("Circuit breaker cooldown must be greater than 0 seconds")
        return v

    @field_validator("provider_timeouts")
    @classmethod
    def validate_timeouts(cls, v: Dict[str, float]) -> Dict[str, float]:
        for k, t in v.items():
            if t <= 0.0:
                raise ValueError(f"Timeout for provider '{k}' must be greater than 0 seconds")
        return v

    @field_validator("history_buffer_size")
    @classmethod
    def validate_history_size(cls, v: int) -> int:
        if v < 5 or v > 500:
            raise ValueError("History buffer size must be between 5 and 500")
        return v


# Global default configuration instance
default_router_config = RouterConfig()
