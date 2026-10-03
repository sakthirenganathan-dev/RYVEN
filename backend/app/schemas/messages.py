"""Request and response message schemas for RYVEN."""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Incoming user chat or command request."""

    message: str = Field(..., min_length=1, description="The user message or voice instruction to process")
    session_id: Optional[str] = Field(
        default="default",
        description="Session identifier for multi-turn short-term conversation context",
    )


class ChatResponse(BaseModel):
    """Structured response returned by the Assistant."""

    success: bool = Field(default=True, description="Indicates if the request was successfully processed")
    type: Literal["tool", "ai", "workflow", "error", "agent"] = Field(..., description="The response generator type")
    message: str = Field(..., description="Human-readable response message")
    tool: Optional[str] = Field(default=None, description="Name of the tool executed, if applicable")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Additional contextual metadata")


class HealthResponse(BaseModel):
    """Health check status response."""

    status: str = Field(default="ok", description="Overall system operational status")
    app: str = Field(default="RYVEN Backend", description="Application name")
    version: str = Field(..., description="Application version")
    environment: str = Field(..., description="Execution environment")
    backend: str = Field(default="online", description="Backend service status")
    ollama: str = Field(..., description="Ollama daemon connectivity status ('online' | 'offline')")
    model: str = Field(..., description="Configured LLM model name")
    model_available: bool = Field(
        default=False,
        description="Whether the configured model is installed and ready on Ollama",
    )
    registered_tools: List[str] = Field(default_factory=list, description="Currently active tools")
    ai_provider: str = Field(..., description="Current AI provider name")


class DiskMetric(BaseModel):
    """Storage usage metrics."""

    total_gb: float
    used_gb: float
    free_gb: float
    percent: float


class BatteryMetric(BaseModel):
    """Battery metrics if hardware battery is present."""

    available: bool = False
    percent: Optional[float] = None
    power_plugged: Optional[bool] = None
    time_left_seconds: Optional[int] = None


class SystemStatusData(BaseModel):
    """Hardware telemetry metrics."""

    cpu_percent: float = Field(..., description="CPU utilization percentage")
    ram_total_gb: float = Field(..., description="Total system RAM in GB")
    ram_used_gb: float = Field(..., description="Used system RAM in GB")
    ram_percent: float = Field(..., description="RAM utilization percentage")
    disk: DiskMetric = Field(..., description="Primary disk usage metrics")
    battery: BatteryMetric = Field(..., description="Battery state metrics")


class SystemStatusResponse(BaseModel):
    """Response returned by /api/system/status."""

    success: bool = True
    data: SystemStatusData
    message: str = "System telemetry retrieved successfully"
