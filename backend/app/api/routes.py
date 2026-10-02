"""FastAPI endpoints for RYVEN."""

from typing import Any, Dict, List
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


# ─────────────────────────────────────────────────────────────────────────────
# M12 Deployment Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/deployment/detect")
async def deployment_detect_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect and detect project framework and deployment readiness."""
    from app.deployment.deployment_engine import DeploymentEngine
    engine = DeploymentEngine()
    project_name = payload.get("project_name", "")
    res = engine.detect(project_name)
    return res.model_dump()


@router.post("/deployment/preflight")
async def deployment_preflight_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute 17-point preflight validation before deployment."""
    from app.deployment.deployment_engine import DeploymentEngine
    from app.deployment.deployment_models import DeploymentProvider
    engine = DeploymentEngine()
    project_name = payload.get("project_name", "")
    prov_str = payload.get("provider", "VERCEL").upper()
    try:
        provider = DeploymentProvider(prov_str)
    except ValueError:
        provider = DeploymentProvider.VERCEL
    res = await engine.run_preflight(project_name=project_name, provider=provider)
    return res.model_dump()


@router.post("/deployment/preview")
async def deployment_preview_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Generate structured two-stage deployment preview."""
    from app.deployment.deployment_engine import DeploymentEngine
    from app.deployment.deployment_models import DeploymentProvider, DeploymentRequest
    engine = DeploymentEngine()
    project_name = payload.get("project_name", "")
    prov_str = payload.get("provider", "VERCEL").upper()
    target_env = payload.get("target_environment", "production")
    try:
        provider = DeploymentProvider(prov_str)
    except ValueError:
        provider = DeploymentProvider.VERCEL
    req = DeploymentRequest(project_name=project_name, provider=provider, target_environment=target_env)
    res = await engine.preview(req)
    return res.model_dump()


@router.post("/deployment/deploy")
async def deployment_deploy_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute project deployment with mandatory confirmation."""
    from app.deployment.deployment_engine import DeploymentEngine
    from app.deployment.deployment_models import DeploymentProvider, DeploymentRequest
    engine = DeploymentEngine()
    project_name = payload.get("project_name", "")
    prov_str = payload.get("provider", "VERCEL").upper()
    target_env = payload.get("target_environment", "production")
    confirmed = bool(payload.get("confirmed", False))
    try:
        provider = DeploymentProvider(prov_str)
    except ValueError:
        provider = DeploymentProvider.VERCEL
    req = DeploymentRequest(
        project_name=project_name,
        provider=provider,
        target_environment=target_env,
        confirmed=confirmed,
    )
    res = await engine.deploy(req)
    return res.model_dump()


@router.post("/deployment/verify")
async def deployment_verify_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Verify live HTTP reachability of a deployed URL."""
    from app.deployment.deployment_verifier import DeploymentVerifier
    verifier = DeploymentVerifier()
    url = payload.get("url", "")
    res = await verifier.verify_endpoint(url)
    return res.model_dump()


# ─────────────────────────────────────────────────────────────────────────────
# M13 Health & Monitoring Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/health/check")
async def health_check_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Perform on-demand HTTP health probe against an endpoint."""
    from app.health.health_service import health_service
    url = payload.get("url", "")
    timeout = float(payload.get("timeout_seconds", 10.0))
    res = await health_service.check(url=url, timeout_seconds=timeout)
    return res.model_dump()


@router.get("/health/status")
async def health_status_endpoint(url: str) -> Dict[str, Any]:
    """Get the latest health summary and uptime for a target URL."""
    from app.health.health_service import health_service
    summary = health_service.get_summary(url)
    return summary.model_dump()


@router.get("/health/history")
async def health_history_endpoint(url: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Retrieve historical health check records for a target URL."""
    from app.health.health_service import health_service
    records = health_service.get_history(url, limit=limit)
    return [r.model_dump() for r in records]


@router.post("/health/monitor/start")
async def health_monitor_start_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Start background health monitoring for a target URL."""
    from app.health.health_service import health_service
    url = payload.get("url", "")
    interval = float(payload.get("interval_seconds", 30.0))
    status = health_service.start_monitoring(url, interval_seconds=interval)
    return status.model_dump()


@router.post("/health/monitor/stop")
async def health_monitor_stop_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Stop active health monitoring for a target URL."""
    from app.health.health_service import health_service
    url = payload.get("url", "")
    stopped = health_service.stop_monitoring(url)
    return {"url": url, "stopped": stopped}

