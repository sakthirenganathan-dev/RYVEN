"""RYVEN 3.0 — AI Model Types and Profiles.

Defines typed schemas for task types, model profiles, inference providers,
and routing decisions.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class TaskType(str, Enum):
    """Categorization of computational or cognitive tasks handled by AI models."""

    GENERAL_REASONING = "GENERAL_REASONING"
    PLANNING = "PLANNING"
    CODE = "CODE"
    VISION = "VISION"
    OCR = "OCR"
    EMBEDDING = "EMBEDDING"
    SPEECH_TO_TEXT = "SPEECH_TO_TEXT"
    TEXT_TO_SPEECH = "TEXT_TO_SPEECH"
    CLASSIFICATION = "CLASSIFICATION"


class ModelProvider(str, Enum):
    """Supported model inference providers."""

    OLLAMA = "OLLAMA"
    HUGGINGFACE_LOCAL = "HUGGINGFACE_LOCAL"
    HUGGINGFACE_REMOTE = "HUGGINGFACE_REMOTE"
    GROK = "GROK"


class ModelProfile(BaseModel):
    """Comprehensive metadata descriptor for an AI model."""

    id: str = Field(..., description="Unique model identifier within RYVEN")
    provider: ModelProvider = Field(..., description="Provider hosting this model")
    task_types: List[TaskType] = Field(default_factory=list, description="Supported tasks")
    local_or_remote: Literal["local", "remote"] = Field("local", description="Deployment boundary")
    model_name: str = Field(..., description="Provider-specific model name / tag / repo ID")
    capabilities: List[str] = Field(default_factory=list, description="Capabilities (e.g. streaming, tools)")
    memory_estimate_gb: float = Field(..., description="Estimated RAM / VRAM consumption in GB")
    context_length: int = Field(default=4096, description="Maximum context window tokens")
    enabled: bool = Field(default=True, description="Whether this model is activated for routing")
    installed: bool = Field(default=False, description="Whether weights are cached locally")
    sensitive_data_allowed: bool = Field(default=True, description="Allowed to ingest local sensitive data")
    priority: int = Field(default=100, description="Routing priority (lower = higher priority)")
    description: str = Field(default="", description="Human-readable model summary")


class RoutingDecision(BaseModel):
    """Explainable decision produced by the ModelRouter."""

    selected_model: str = Field(..., description="Chosen model identifier")
    provider: str = Field(..., description="Chosen model provider")
    task_type: TaskType = Field(..., description="Input task classification")
    reason: str = Field(..., description="Explainable deterministic routing rationale")
    fallback: Optional[str] = Field(default=None, description="Fallback model ID if primary unavailable")
    remote_allowed: bool = Field(default=False, description="Whether remote inference was authorized")
    local_or_remote: Literal["local", "remote"] = Field("local", description="Boundary of the chosen model")
    memory_estimate_gb: float = Field(default=0.0, description="Estimated memory impact")
