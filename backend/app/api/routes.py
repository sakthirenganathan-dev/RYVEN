"""FastAPI endpoints for RYVEN."""

from typing import Any, Dict
from fastapi import APIRouter, Depends, Request
from app.core.assistant import Assistant
from app.core.config import settings
from app.core.logging_config import logger
from app.schemas.messages import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    SystemStatusData,
    SystemStatusResponse,
)
from app.tools.system_tool import SystemStatusTool

router = APIRouter(prefix="/api", tags=["ryven"])


def get_assistant(request: Request) -> Assistant:
    """Dependency provider for the Assistant instance."""
    if hasattr(request.app.state, "assistant"):
        return request.app.state.assistant
    return Assistant()


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(
    payload: ChatRequest,
    assistant: Assistant = Depends(get_assistant),
) -> ChatResponse:
    """Process a user message or voice instruction with session context."""
    session_id = payload.session_id or "default"
    logger.info(
        f"API request: POST /api/chat (session='{session_id}', query='{payload.message[:30]}...')"
    )
    response = await assistant.process(payload.message, session_id=session_id)
    return response


@router.get("/health", response_model=HealthResponse)
async def health_endpoint(
    assistant: Assistant = Depends(get_assistant),
) -> HealthResponse:
    """Return backend health, Ollama daemon status, and model availability."""
    ollama_health = await assistant.ai_provider.check_health()
    is_online = ollama_health.get("online", False)
    model_available = ollama_health.get("model_available", False)

    return HealthResponse(
        status="ok",
        app=settings.app_name,
        version=settings.version,
        environment=settings.environment,
        backend="online",
        ollama="online" if is_online else "offline",
        model=settings.ollama_model,
        model_available=model_available,
        registered_tools=assistant.registry.list_tools(),
        ai_provider=assistant.ai_provider.provider_name,
    )


@router.get("/system/status", response_model=SystemStatusResponse)
async def system_status_endpoint() -> SystemStatusResponse:
    """Return current hardware diagnostics (CPU, RAM, storage, battery)."""
    tool = SystemStatusTool()
    metrics: Dict[str, Any] = await tool.execute()

    return SystemStatusResponse(
        success=True,
        data=SystemStatusData(
            cpu_percent=metrics["cpu_percent"],
            ram_total_gb=metrics["ram_total_gb"],
            ram_used_gb=metrics["ram_used_gb"],
            ram_percent=metrics["ram_percent"],
            disk=metrics["disk"],
            battery=metrics["battery"],
        ),
        message=metrics.get("message", "Telemetry retrieved"),
    )
