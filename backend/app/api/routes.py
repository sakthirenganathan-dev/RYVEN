"""FastAPI endpoints for RYVEN."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
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


# ─────────────────────────────────────────────────────────────────────────────
# M14 Autonomous Development Orchestrator Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/orchestrate")
async def orchestrate_create_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new orchestration plan and optionally begin execution."""
    from app.orchestrator.engine import orchestrator_engine
    goal = payload.get("goal", "")
    project_path = payload.get("project_path", "")
    project_name = payload.get("project_name", "")
    auto_start = payload.get("auto_start", True)

    task = orchestrator_engine.create_and_plan(
        goal=goal,
        project_path=project_path,
        project_name=project_name,
    )

    if auto_start and task.plan and task.plan.valid:
        res = await orchestrator_engine.execute_task(task.task_id)
        return res.model_dump()
    return task.model_dump()


@router.get("/orchestrate/{task_id}")
async def orchestrate_get_endpoint(task_id: str) -> Dict[str, Any]:
    """Retrieve the current state, plan, and checkpoints of an orchestrated task."""
    from app.orchestrator.engine import orchestrator_engine
    task = orchestrator_engine.get_task(task_id)
    if not task:
        return {"error": f"Task '{task_id}' not found", "task_id": task_id}
    return task.model_dump()


@router.post("/orchestrate/{task_id}/confirm")
async def orchestrate_confirm_endpoint(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Confirm or reject a protected step and resume orchestration execution."""
    from app.orchestrator.engine import orchestrator_engine
    from app.orchestrator.models import TaskState
    step_id = payload.get("step_id", "")
    confirmed = bool(payload.get("confirmed", True))
    override = bool(payload.get("override", False))

    res = orchestrator_engine.confirm_step(
        task_id=task_id,
        step_id=step_id,
        confirmed=confirmed,
        override=override,
    )
    if confirmed and res.status != TaskState.CANCELLED:
        res = await orchestrator_engine.execute_task(task_id)
    return res.model_dump()


@router.post("/orchestrate/{task_id}/pause")
async def orchestrate_pause_endpoint(task_id: str) -> Dict[str, Any]:
    """Pause an active orchestration task."""
    from app.orchestrator.engine import orchestrator_engine
    task = orchestrator_engine.pause_task(task_id)
    if not task:
        return {"error": f"Task '{task_id}' not found", "task_id": task_id}
    return task.model_dump()


@router.post("/orchestrate/{task_id}/resume")
async def orchestrate_resume_endpoint(task_id: str) -> Dict[str, Any]:
    """Resume execution of a paused orchestration task."""
    from app.orchestrator.engine import orchestrator_engine
    orchestrator_engine.resume_task(task_id)
    res = await orchestrator_engine.execute_task(task_id)
    return res.model_dump()


@router.post("/orchestrate/{task_id}/cancel")
async def orchestrate_cancel_endpoint(task_id: str) -> Dict[str, Any]:
    """Cancel an active or paused orchestration task safely."""
    from app.orchestrator.engine import orchestrator_engine
    task = orchestrator_engine.cancel_task(task_id)
    if not task:
        return {"error": f"Task '{task_id}' not found", "task_id": task_id}
    return task.model_dump()


# ─────────────────────────────────────────────────────────────────────────────
# M14.2 Action Engine / Live Activity Endpoints
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import json


@router.get("/actions/stream")
async def actions_sse_stream(request: Request):
    """Server-Sent Events (SSE) stream of live RYVEN action events.

    Reconnect-safe, disconnect-safe, bounded delivery.
    No credentials are ever included in events.
    """
    from app.actions.event_bus import action_bus

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)

        async def on_event(event):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Drop overflow; never block tool execution

        sid = action_bus.subscribe(on_event)
        try:
            yield 'data: {"type": "connected", "message": "RYVEN Action Stream connected"}\n\n'
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    payload = json.dumps(event.model_dump())
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except Exception:
            pass
        finally:
            action_bus.unsubscribe(sid)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/actions/recent")
async def actions_recent(limit: int = 50) -> List[Dict[str, Any]]:
    """Return the most recent N action events from the bounded ring buffer."""
    from app.actions.service import action_service
    events = action_service.get_recent(limit=min(limit, 200))
    return [e.model_dump() for e in events]


@router.get("/actions/task/{task_id}")
async def actions_by_task(task_id: str) -> List[Dict[str, Any]]:
    """Return all stored action events for a specific task."""
    from app.actions.service import action_service
    events = action_service.get_task_events(task_id)
    return [e.model_dump() for e in events]


@router.get("/actions/task/{task_id}/summary")
async def actions_task_summary(task_id: str, goal: str = "") -> Dict[str, Any]:
    """Return structured TaskSummary for a completed task.
    All values come from actual stored events — nothing is fabricated.
    """
    from app.actions.service import action_service
    summary = action_service.get_task_summary(task_id, goal=goal)
    return summary.model_dump()


@router.post("/actions/task/{task_id}/replay")
async def actions_create_replay(task_id: str) -> Dict[str, Any]:
    """Create a READ-ONLY replay session from stored task events.

    SAFETY: Replay NEVER calls any tool, shell command, Git operation,
    file modification, or deployment step.
    Replay only reads historical ActionEvent objects.
    """
    from app.actions.service import action_service
    session = action_service.create_replay(task_id)
    return session.to_dict()


@router.post("/actions/task/{task_id}/replay/next")
async def actions_replay_next(task_id: str) -> Dict[str, Any]:
    """Advance replay one frame forward (read-only)."""
    from app.actions.service import action_service
    frame = action_service.replay_next(task_id)
    if frame is None:
        return {"error": f"No replay session for task '{task_id}' or end of replay."}
    return frame


@router.post("/actions/task/{task_id}/replay/previous")
async def actions_replay_previous(task_id: str) -> Dict[str, Any]:
    """Move replay one frame backward (read-only)."""
    from app.actions.service import action_service
    frame = action_service.replay_previous(task_id)
    if frame is None:
        return {"error": f"No replay session for task '{task_id}'."}
    return frame


@router.post("/actions/task/{task_id}/replay/restart")
async def actions_replay_restart(task_id: str) -> Dict[str, Any]:
    """Restart replay from the beginning (read-only)."""
    from app.actions.service import action_service
    result = action_service.replay_restart(task_id)
    if result is None:
        return {"error": f"No replay session for task '{task_id}'."}
    return result


@router.get("/actions/stats")
async def actions_stats() -> Dict[str, Any]:
    """Return event bus statistics."""
    from app.actions.service import action_service
    return action_service.get_stats()


# --------------------------------------------------------------------------
# M14.3 CONTROLLED BROWSER API ENDPOINTS
# --------------------------------------------------------------------------


@router.get("/browser/state")
async def get_browser_state(session_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve active or specified controlled browser state."""
    from app.browser.engine import browser_engine
    state = browser_engine.get_session(session_id)
    if not state:
        return {
            "session_id": session_id or "",
            "browser_status": "IDLE",
            "current_url": "about:blank",
            "page_title": "",
            "tabs": [],
            "history": [],
            "last_action": None,
        }
    return state.model_dump()


@router.get("/browser/sessions")
async def list_browser_sessions() -> List[Dict[str, Any]]:
    """List all tracked browser sessions."""
    from app.browser.engine import browser_engine
    return browser_engine.list_sessions()


@router.post("/browser/confirm")
async def confirm_browser_action(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Approve and proceed with a pending browser action requiring human confirmation."""
    token = payload.get("token", "")
    from app.browser.engine import browser_engine
    pending = browser_engine._pending_confirmations.pop(token, None)
    if not pending:
        return {"success": False, "message": f"Confirmation token '{token}' not found or already consumed."}

    action = pending.get("action", "")
    if action == "click_element":
        return await browser_engine.click_element(
            selector=pending.get("selector", ""),
            session_id=pending.get("session_id"),
            confirmed=True,
        )
    return {"success": True, "message": f"Action '{action}' confirmed."}

