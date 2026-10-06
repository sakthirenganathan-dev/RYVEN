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


# --------------------------------------------------------------------------
# RYVEN 3.0 AGENT CONTROL PLANE ENDPOINTS
# --------------------------------------------------------------------------


def get_agent_engine(request: Request):
    """Return the agent engine owned by the running assistant instance."""
    return get_assistant(request).agent_engine


@router.get("/agent/tasks")
async def list_agent_tasks(request: Request) -> List[Dict[str, Any]]:
    """List tracked multi-step agent tasks."""
    return get_agent_engine(request).list_tasks()


@router.get("/agent/state")
async def get_agent_state(request: Request, task_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve full execution state for an agent task."""
    agent_engine = get_agent_engine(request)
    if task_id:
        st = agent_engine.get_task_state(task_id)
        if st:
            return st.model_dump()
    tasks = agent_engine.list_tasks()
    if tasks:
        latest = agent_engine.get_task_state(tasks[-1]["task_id"])
        if latest:
            return latest.model_dump()
    return {
        "status": "IDLE",
        "user_goal": "",
        "current_plan": None,
        "current_step_index": 0,
    }


@router.post("/agent/confirm")
async def confirm_agent_step(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Approve a confirmation-gated agent step."""
    token = payload.get("token", "")
    return await get_agent_engine(request).confirm_action(token)


@router.post("/agent/cancel")
async def cancel_agent_task(request: Request, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Cancel a running agent task."""
    task_id = payload.get("task_id", "")
    ok = get_agent_engine(request).cancel_task(task_id)
    return {"success": ok, "task_id": task_id}


# --------------------------------------------------------------------------
# RYVEN 3.0 UNIFIED INTERNET AGENT ENDPOINTS
# --------------------------------------------------------------------------


@router.get("/internet/state")
async def get_internet_state() -> Dict[str, Any]:
    """Retrieve observable live state of the Unified Internet Agent."""
    from app.internet.agent import internet_agent
    st = internet_agent.get_state()
    return st.model_dump()


@router.post("/internet/search")
async def internet_search_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Perform live web search and rank authoritative results."""
    from app.internet.agent import internet_agent
    query = payload.get("query", "")
    max_results = int(payload.get("max_results", 5))
    return await internet_agent.search(query=query, max_results=max_results)


@router.post("/internet/research")
async def internet_research_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute deep multi-source research on a technical topic with citations."""
    from app.internet.agent import internet_agent
    topic = payload.get("topic", "")
    max_sources = int(payload.get("max_sources", 3))
    return await internet_agent.research(topic=topic, max_sources=max_sources)


@router.post("/internet/task")
async def internet_task_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute an end-to-end multi-step web task."""
    from app.internet.agent import internet_agent
    goal = payload.get("goal", "")
    auto_confirm = bool(payload.get("auto_confirm", False))
    return await internet_agent.execute_task(goal=goal, auto_confirm=auto_confirm)


@router.post("/internet/auth-resume")
async def internet_auth_resume_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Resume paused internet task after user completed browser authentication."""
    from app.internet.agent import internet_agent
    token = payload.get("token", "") or payload.get("resume_token", "")
    return await internet_agent.resume_authentication(resume_token=token)


# --------------------------------------------------------------------------
# RYVEN 3.0 M15.1 MULTI-MODEL PROVIDER & ROUTING DECISION ENDPOINTS
# --------------------------------------------------------------------------


@router.get("/v1/models/hardware")
@router.get("/models/hardware")
async def get_models_hardware_endpoint() -> Dict[str, Any]:
    """Retrieve safe read-only hardware telemetry and local model execution tier."""
    from app.ai.hardware import HardwareDiagnostics
    profile = await HardwareDiagnostics.get_profile()
    return profile.model_dump()


@router.get("/v1/models/catalog")
@router.get("/models/catalog")
async def get_models_catalog_endpoint() -> Dict[str, Any]:
    """Retrieve catalog of supported local and remote models."""
    from app.ai.hf_manager import hf_model_manager
    from app.ai.registry import model_registry
    registered = model_registry.list_models()
    hf_candidates = hf_model_manager.get_catalog()
    return {
        "registered_models": [m.model_dump() for m in registered],
        "hf_catalog": [c.model_dump() for c in hf_candidates],
    }


@router.get("/v1/models/providers")
@router.get("/models/providers")
async def get_models_providers_endpoint() -> Dict[str, Any]:
    """Aggregate health and availability for all configured providers without exposing secrets."""
    from app.ai.unified import unified_ai_provider
    return await unified_ai_provider.check_health()


@router.get("/v1/models/route")
@router.get("/models/route")
async def get_models_route_endpoint(
    task_type: str = "GENERAL_REASONING",
    prompt: str = "",
    allow_remote: bool = False,
    preferred_model_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Deterministically explain and resolve model routing for a specific task."""
    from app.ai.models import TaskType
    from app.ai.router import model_router

    try:
        t_type = TaskType(task_type.upper())
    except ValueError:
        t_type = TaskType.GENERAL_REASONING

    decision = await model_router.route(
        task_type=t_type,
        prompt=prompt,
        allow_remote=allow_remote,
        preferred_model_id=preferred_model_id,
    )
    return {
        "task_type": decision.task_type.value,
        "selected_provider": decision.provider,
        "selected_model": decision.selected_model,
        "local": decision.local_or_remote == "local",
        "remote_allowed": decision.remote_allowed,
        "reason": decision.reason,
        "fallback": decision.fallback,
        "memory_estimate_gb": decision.memory_estimate_gb,
    }


# --------------------------------------------------------------------------
# M15.2 VISION & OCR API ENDPOINTS
# --------------------------------------------------------------------------


@router.post("/browser/screenshot")
@router.post("/v1/browser/screenshot")
async def capture_browser_screenshot_endpoint(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Capture in-memory visual screenshot of the active browser page."""
    from app.browser.engine import browser_engine

    data = payload or {}
    session_id = data.get("session_id")
    max_width = int(data.get("max_width", 800))
    max_height = int(data.get("max_height", 600))
    max_width = min(max(max_width, 100), 1920)
    max_height = min(max(max_height, 100), 1080)

    return await browser_engine.capture_screenshot(
        session_id=session_id,
        max_width=max_width,
        max_height=max_height,
    )


@router.post("/vision/analyze")
@router.post("/v1/vision/analyze")
async def analyze_vision_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze an image or active page snapshot using local vision models."""
    from app.ai.vision import perception_agent
    from app.browser.engine import browser_engine
    from fastapi import HTTPException

    if payload.get("allow_remote") is True:
        raise HTTPException(
            status_code=400,
            detail="Remote vision inference is strictly disabled in RYVEN. All vision processing must remain local.",
        )

    image_b64 = payload.get("image_b64")
    url = payload.get("url")
    session_id = payload.get("session_id")

    if not image_b64:
        capture_res = await browser_engine.capture_screenshot(session_id=session_id)
        if not capture_res.get("success"):
            return {
                "success": False,
                "error": f"No image provided and screenshot capture failed: {capture_res.get('error')}",
            }
        image_b64 = capture_res.get("screenshot_b64")
        url = capture_res.get("url")

    if image_b64 and len(image_b64) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image payload exceeds 10MB limit.")

    prompt = payload.get("prompt", "Analyze this webpage layout, interactive elements, and content.")
    result = await perception_agent.perceive(
        image_b64=image_b64,
        prompt=prompt,
        url=url,
        allow_remote=False,
    )
    return result.model_dump()


@router.post("/vision/ocr")
@router.post("/v1/vision/ocr")
async def extract_ocr_endpoint(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Extract visible text from image or active browser page using local OCR."""
    import base64
    import io
    from app.actions.event_bus import action_bus
    from app.actions.models import ActionEvent, ActionStatus, ActionType
    from app.ai.vision import perception_agent
    from app.browser.engine import browser_engine
    from app.browser.security import BrowserSecurityValidator
    from fastapi import HTTPException

    data = payload or {}
    image_b64 = data.get("image_b64")
    session_id = data.get("session_id")
    url = data.get("url", "")

    if not image_b64:
        capture_res = await browser_engine.capture_screenshot(session_id=session_id)
        if not capture_res.get("success"):
            return {
                "success": False,
                "error": f"No image provided and screenshot capture failed: {capture_res.get('error')}",
            }
        image_b64 = capture_res.get("screenshot_b64")
        url = capture_res.get("url", "")

    if not image_b64:
        return {"success": False, "error": "No image available for OCR."}

    if len(image_b64) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image payload exceeds 10MB limit.")

    await action_bus.publish(
        ActionEvent(
            action_type=ActionType.OCR_STARTED,
            status=ActionStatus.STARTED,
            title="Running OCR extraction",
            safe_metadata={"url": url, "has_image": True},
        )
    )

    ocr_text = ""
    engine_used = "none"
    text_regions = []

    # 1. pytesseract
    try:
        import pytesseract  # type: ignore
        from PIL import Image  # type: ignore

        img_bytes = base64.b64decode(image_b64)
        img = Image.open(io.BytesIO(img_bytes))
        raw_text = pytesseract.image_to_string(img)
        if raw_text and raw_text.strip():
            ocr_text = raw_text.strip()
            engine_used = "tesseract"
    except Exception:
        pass

    # 2. Local vision fallback
    if not ocr_text:
        try:
            result = await perception_agent.perceive(
                image_b64=image_b64,
                prompt="Extract all visible text and labels from this image.",
                url=url,
                allow_remote=False,
            )
            if result.ocr_text:
                ocr_text = result.ocr_text
                engine_used = "perception_agent"
            elif result.description:
                ocr_text = result.description
                engine_used = "perception_agent_desc"
            text_regions = [
                {"label": el.label, "text": el.text, "bbox": el.bbox, "confidence": el.confidence}
                for el in result.elements
                if el.text
            ]
        except Exception:
            pass

    sanitized = BrowserSecurityValidator.redact_credentials(ocr_text or "")
    if not sanitized:
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.OCR_FAILED,
                status=ActionStatus.FAILED,
                title="OCR failed to extract text",
                safe_metadata={"url": url, "error": "No text extracted"},
            )
        )
        return {"success": False, "error": "No text extracted from image."}

    await action_bus.publish(
        ActionEvent(
            action_type=ActionType.OCR_COMPLETED,
            status=ActionStatus.COMPLETED,
            title="OCR text extraction completed",
            safe_metadata={"url": url, "engine": engine_used, "text_length": len(sanitized)},
        )
    )

    return {
        "success": True,
        "engine": engine_used,
        "ocr_text": sanitized,
        "text_regions": text_regions,
        "length": len(sanitized),
    }


@router.get("/vision/status")
@router.get("/v1/vision/status")
async def get_vision_status_endpoint() -> Dict[str, Any]:
    """Retrieve availability and configuration status for Vision & OCR subsystem."""
    from app.ai.registry import model_registry
    from app.ai.models import TaskType

    pytesseract_available = False
    try:
        import pytesseract  # type: ignore
        pytesseract_available = True
    except ImportError:
        pass

    pil_available = False
    try:
        import PIL  # type: ignore
        pil_available = True
    except ImportError:
        pass

    cv2_available = False
    try:
        import cv2  # type: ignore
        cv2_available = True
    except ImportError:
        pass

    vision_models = model_registry.list_for_task(TaskType.VISION)
    installed_vision = [m.id for m in vision_models if m.installed]
    all_vision = [m.id for m in vision_models]

    return {
        "status": "ready" if (installed_vision or pytesseract_available) else "available",
        "local_only": True,
        "remote_vision_allowed": False,
        "default_model": "moondream",
        "vision_models": all_vision,
        "installed_vision_models": installed_vision,
        "ocr_engine": "tesseract" if pytesseract_available else "vision_model",
        "pytesseract_available": pytesseract_available,
        "pil_available": pil_available,
        "cv2_available": cv2_available,
    }


# ─────────────────────────────────────────────────────────────────────────────
# M15.3 Phase 4 — Browser File Management Endpoints
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/browser/download")
@router.post("/v1/browser/download")
async def browser_download_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Download a file from a URL to an approved local directory."""
    from app.internet.agent import internet_agent

    url = payload.get("url", "")
    destination_folder = payload.get("destination_folder", "downloads")
    custom_filename = payload.get("custom_filename")
    confirmed = bool(payload.get("confirmed", False))
    session_id = payload.get("session_id")

    return await internet_agent.download_file(
        url=url,
        destination_folder=destination_folder,
        custom_filename=custom_filename,
        confirmed=confirmed,
        session_id=session_id,
    )


@router.post("/browser/upload")
@router.post("/v1/browser/upload")
async def browser_upload_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Attach an approved local file to a web form file input element."""
    from app.internet.agent import internet_agent

    source_path = payload.get("source_path", "")
    target_field = payload.get("target_field", "")
    form_id = payload.get("form_id")
    confirmed = bool(payload.get("confirmed", False))
    session_id = payload.get("session_id")

    return await internet_agent.upload_file(
        source_path=source_path,
        target_field=target_field,
        form_id=form_id,
        confirmed=confirmed,
        session_id=session_id,
    )


@router.post("/browser/download/verify")
@router.post("/v1/browser/download/verify")
async def browser_download_verify_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Verify that a downloaded file exists and is within approved directories."""
    from app.internet.agent import internet_agent

    file_path = payload.get("file_path", "")
    expected_size = payload.get("expected_size")

    return internet_agent.verify_download(
        file_path=file_path,
        expected_size=expected_size,
    )


@router.post("/browser/upload/verify")
@router.post("/v1/browser/upload/verify")
async def browser_upload_verify_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Verify that a local file was attached to the target web field."""
    from app.internet.agent import internet_agent

    field_name = payload.get("field_name", "")
    filename = payload.get("filename", "")

    return internet_agent.verify_upload(
        field_name=field_name,
        filename=filename,
    )


# ===========================================================================
# Runtime & Reliability Endpoints (M15.3.9)
# ===========================================================================

@router.get("/runtime/status")
@router.get("/v1/runtime/status")
async def runtime_status_endpoint() -> Dict[str, Any]:
    """Return overall runtime status, host pressure, and performance overview."""
    from app.runtime.resource_manager import resource_manager
    from app.runtime.performance import runtime_performance_service
    from app.runtime.checkpoint_store import checkpoint_store
    from app.runtime.models import RuntimeState

    res_snapshot = resource_manager.get_resource_snapshot()
    perf = await runtime_performance_service.get_aggregate_metrics()
    incomplete = checkpoint_store.list_incomplete_tasks()

    state = RuntimeState.NORMAL.value
    if res_snapshot.get("status") == "CRITICAL":
        state = RuntimeState.RESOURCE_CRITICAL.value
    elif res_snapshot.get("status") == "WARNING":
        state = RuntimeState.RESOURCE_WARNING.value
    elif len(incomplete) > 0:
        state = RuntimeState.RECOVERING.value

    return {
        "runtime_state": state,
        "host_resources": res_snapshot,
        "performance": perf.model_dump(),
        "active_or_incomplete_tasks_count": len(incomplete),
        "status": "ok",
    }


@router.get("/runtime/resources")
@router.get("/v1/runtime/resources")
async def runtime_resources_endpoint() -> Dict[str, Any]:
    """Return host hardware resource utilization and pressure thresholds."""
    from app.runtime.resource_manager import resource_manager

    return resource_manager.get_resource_snapshot(force_refresh=True)


@router.get("/runtime/performance")
@router.get("/v1/runtime/performance")
async def runtime_performance_endpoint() -> Dict[str, Any]:
    """Return aggregate latency distributions and recent operation metrics."""
    from app.runtime.performance import runtime_performance_service

    agg = await runtime_performance_service.get_aggregate_metrics()
    recent = await runtime_performance_service.get_metrics(limit=25)

    return {
        "aggregate": agg.model_dump(),
        "recent_operations": [m.model_dump() for m in recent],
    }


@router.get("/runtime/tasks")
@router.get("/v1/runtime/tasks")
async def runtime_tasks_endpoint() -> Dict[str, Any]:
    """Return all persisted task checkpoints and current execution states."""
    from app.runtime.checkpoint_store import checkpoint_store

    checkpoints = checkpoint_store.list_checkpoints(limit=50)
    return {
        "total_persisted": len(checkpoints),
        "tasks": [c.model_dump() for c in checkpoints],
    }


@router.get("/runtime/recovery")
@router.get("/v1/runtime/recovery")
async def runtime_recovery_endpoint() -> Dict[str, Any]:
    """Scan and return recovery status for interrupted tasks."""
    from app.runtime.recovery import runtime_recovery_service

    decisions = runtime_recovery_service.scan_for_recoverable_tasks()
    return {
        "recoverable_count": len(decisions),
        "tasks": [d.model_dump() for d in decisions],
    }


@router.post("/runtime/recovery/resume")
@router.post("/v1/runtime/recovery/resume")
async def runtime_recovery_resume_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Resume an interrupted task following crash/restart, requiring confirmation if consequential."""
    from app.runtime.recovery import runtime_recovery_service

    task_id = payload.get("task_id", "")
    confirmed = bool(payload.get("confirmed", False))

    decision = runtime_recovery_service.resume_task(task_id, confirmed=confirmed)
    return decision.model_dump()


# ---------------------------------------------------------------------------
# M16.0 Multi-Agent Coordination Endpoints
# ---------------------------------------------------------------------------

@router.post("/agents/task")
@router.post("/v1/agents/task")
async def create_agent_task_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Submit a high-level goal to the central Agent Coordinator."""
    from app.agents import agent_coordinator

    goal = payload.get("goal", "")
    if not goal:
        return {"error": "Missing 'goal' parameter in request."}
    project_name = payload.get("project_name")
    auto_confirm = bool(payload.get("auto_confirm", False))

    graph = await agent_coordinator.coordinate(
        goal=goal,
        project_name=project_name,
        auto_confirm=auto_confirm,
    )
    return graph.to_dict()


@router.get("/agents")
@router.get("/v1/agents")
async def list_agents_endpoint() -> Dict[str, Any]:
    """Return all registered specialized agent roles and their capabilities."""
    from app.agents import agent_registry

    agents = agent_registry.list_agents()
    return {
        "total_agents": len(agents),
        "agents": [a.model_dump() for a in agents],
    }


@router.get("/agents/{agent_id}")
@router.get("/v1/agents/{agent_id}")
async def get_agent_endpoint(agent_id: str) -> Dict[str, Any]:
    """Return descriptor for a specific registered agent role."""
    from app.agents import agent_registry

    agent = agent_registry.get_agent_by_id(agent_id)
    if not agent:
        for a in agent_registry.list_agents():
            if a.role.value.lower() == agent_id.lower():
                return a.model_dump()
        return {"error": f"Agent '{agent_id}' not found."}
    return agent.model_dump()


@router.get("/agents/graph/{graph_id}")
@router.get("/v1/agents/graph/{graph_id}")
async def get_agent_graph_endpoint(graph_id: str) -> Dict[str, Any]:
    """Return full DAG state and progress for an active or completed task graph."""
    from app.agents import agent_coordinator

    graph = agent_coordinator.get_graph(graph_id)
    if not graph:
        return {"error": f"Graph '{graph_id}' not found."}
    return graph.to_dict()


@router.get("/agents/tasks/{task_id}")
@router.get("/v1/agents/tasks/{task_id}")
async def get_agent_task_endpoint(task_id: str) -> Dict[str, Any]:
    """Retrieve detailed state of a specific agent task."""
    from app.agents import agent_coordinator

    for graph in agent_coordinator._active_graphs.values():
        task = graph.get_task(task_id)
        if task:
            return task.model_dump()
    return {"error": f"Task '{task_id}' not found."}


@router.post("/agents/tasks/{task_id}/cancel")
@router.post("/v1/agents/tasks/{task_id}/cancel")
async def cancel_agent_task_endpoint(task_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Cancel execution of a task graph containing the specified task ID."""
    from app.agents import agent_coordinator

    reason = (payload or {}).get("reason", "User requested cancellation")
    target_graph_id = None
    if task_id in agent_coordinator._active_graphs:
        target_graph_id = task_id
    else:
        for g_id, g in agent_coordinator._active_graphs.items():
            if g.get_task(task_id):
                target_graph_id = g_id
                break

    if not target_graph_id:
        return {"error": f"Active graph for task '{task_id}' not found."}

    cancelled = await agent_coordinator.cancel_graph(target_graph_id, reason=reason)
    return cancelled.to_dict() if cancelled else {"error": "Failed to cancel graph."}


@router.post("/agents/tasks/{task_id}/confirm")
@router.post("/v1/agents/tasks/{task_id}/confirm")
async def confirm_agent_task_endpoint(task_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Explicitly confirm a gated action and resume graph execution."""
    from app.agents import agent_coordinator

    graph_id = (payload or {}).get("graph_id")
    if not graph_id:
        for g_id, g in agent_coordinator._active_graphs.items():
            if g.get_task(task_id):
                graph_id = g_id
                break

    if not graph_id:
        return {"error": f"Active graph for task '{task_id}' not found."}

    resumed = await agent_coordinator.confirm_task(graph_id, task_id)
    return resumed.to_dict() if resumed else {"error": "Failed to resume graph."}


@router.get("/agents/events")
@router.get("/v1/agents/events")
async def get_agent_events_endpoint(limit: int = 50) -> Dict[str, Any]:
    """Return recent agent coordination events from the ActionEventBus."""
    from app.actions.event_bus import action_bus

    all_events = action_bus.get_recent_events(limit=limit * 2)
    agent_events = [e for e in all_events if e.action_type.value.startswith("AGENT_")][:limit]
    return {
        "total_events": len(agent_events),
        "events": [e.model_dump() for e in agent_events],
    }


# ─────────────────────────────────────────────────────────────────────────────
# M16.1 — Intelligent Planning & Decomposition Endpoints
# ─────────────────────────────────────────────────────────────────────────────

_cached_plans: Dict[str, Any] = {}


@router.post("/planning/preview")
@router.post("/v1/planning/preview")
async def planning_preview_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Analyze a user goal and produce a preview of the task plan without executing."""
    from app.agents import PlanningEngine, PlanningRequest, planning_engine

    goal = payload.get("goal") or payload.get("user_goal", "")
    if not goal:
        return {"error": "Missing required field: 'goal'"}

    request = PlanningRequest(
        user_goal=goal,
        project_name=payload.get("project_name"),
        context=payload.get("context", {}),
        preferred_mode=payload.get("preferred_mode"),
        deadline_sec=payload.get("deadline_sec"),
    )

    plan_res = await planning_engine.plan(request)
    _cached_plans[plan_res.plan_id] = plan_res
    return plan_res.model_dump()


@router.post("/planning/validate")
@router.post("/v1/planning/validate")
async def planning_validate_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a draft list of planning tasks against all 15 safety & DAG rules."""
    from app.agents import PlanValidator, PlanningTaskDraft

    raw_tasks = payload.get("tasks", [])
    drafts: List[PlanningTaskDraft] = []
    for item in raw_tasks:
        try:
            drafts.append(PlanningTaskDraft(**item))
        except Exception as exc:
            return {"is_valid": False, "errors": [f"Malformed task draft: {exc}"]}

    validator = PlanValidator()
    result = validator.validate(drafts)
    return result.model_dump()


@router.get("/planning/{plan_id}")
@router.get("/v1/planning/{plan_id}")
async def get_plan_endpoint(plan_id: str) -> Dict[str, Any]:
    """Retrieve details and status for a previously planned execution."""
    from fastapi import HTTPException
    if plan_id in _cached_plans:
        plan = _cached_plans[plan_id]
        return plan.model_dump() if hasattr(plan, "model_dump") else plan
    raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found.")


@router.post("/planning/{plan_id}/execute")
@router.post("/v1/planning/{plan_id}/execute")
async def execute_plan_endpoint(plan_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Execute a previously validated plan through the central AgentCoordinator.

    Builds the AgentTaskGraph and starts execution as a background task,
    returning immediately with status EXECUTING so callers can poll for progress.
    """
    from fastapi import HTTPException
    from app.agents import agent_coordinator, planning_engine
    from app.agents.models import AgentStatus

    if plan_id not in _cached_plans:
        raise HTTPException(status_code=404, detail=f"Plan '{plan_id}' not found.")

    plan_res = _cached_plans[plan_id]
    auto_confirm = bool((payload or {}).get("auto_confirm", False))

    graph = planning_engine.to_agent_task_graph(plan_res)
    # Register graph as EXECUTING immediately and fire execution in the background
    agent_coordinator.start_graph(graph, auto_confirm=auto_confirm)
    return graph.to_dict()


@router.post("/planning/{plan_id}/cancel")
@router.post("/v1/planning/{plan_id}/cancel")
async def cancel_plan_endpoint(plan_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Cancel an active or queued plan."""
    from app.agents import agent_coordinator

    reason = (payload or {}).get("reason", "Cancelled by user")
    if plan_id in agent_coordinator._active_graphs:
        res = await agent_coordinator.cancel_graph(plan_id, reason=reason)
        return res.to_dict() if res else {"status": "CANCELLED"}

    return {"status": "CANCELLED", "plan_id": plan_id}


# ===========================================================================
# M17.0 Unified Personal Computer & Internet Control Endpoints
# ===========================================================================

@router.post("/control/execute")
@router.post("/v1/control/execute")
async def execute_control_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a natural-language goal across Windows desktop, browser, files, or projects."""
    from fastapi import HTTPException
    from app.control.engine import ryven_control_engine
    from app.control.models import ControlRequest

    goal = payload.get("goal") or payload.get("user_goal") or payload.get("message")
    if not goal or not isinstance(goal, str):
        raise HTTPException(status_code=400, detail="Missing required 'goal' parameter.")

    req = ControlRequest(
        goal=goal,
        project_name=payload.get("project_name"),
        auto_confirm=bool(payload.get("auto_confirm", False)),
        timeout=float(payload.get("timeout", 300.0)),
        session_id=payload.get("session_id", "default"),
        metadata=payload.get("metadata", {}),
    )

    result = await ryven_control_engine.execute_goal(req)
    return result.model_dump()


@router.get("/control/{control_id}")
@router.get("/v1/control/{control_id}")
async def get_control_status_endpoint(control_id: str) -> Dict[str, Any]:
    """Retrieve status, observations, and progress of a control execution."""
    from fastapi import HTTPException
    from app.control.engine import ryven_control_engine

    res = ryven_control_engine._active_executions.get(control_id)
    if not res:
        raise HTTPException(status_code=404, detail=f"Control execution '{control_id}' not found.")

    return res.model_dump()


@router.post("/control/{control_id}/confirm")
@router.post("/v1/control/{control_id}/confirm")
async def confirm_control_endpoint(control_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Provide user confirmation for a paused consequential action."""
    from fastapi import HTTPException
    from app.control.engine import ryven_control_engine

    token = (payload or {}).get("confirmation_token")
    auto_confirm_rest = bool((payload or {}).get("auto_confirm_rest", False))
    res = await ryven_control_engine.confirm_control(control_id, confirmation_token=token, auto_confirm_rest=auto_confirm_rest)
    if not res:
        raise HTTPException(status_code=404, detail=f"Control execution '{control_id}' not found.")

    return res.model_dump()


@router.post("/control/{control_id}/cancel")
@router.post("/v1/control/{control_id}/cancel")
async def cancel_control_endpoint(control_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Cancel an active control execution."""
    from app.control.engine import ryven_control_engine

    reason = (payload or {}).get("reason", "User requested cancellation")
    canceled = await ryven_control_engine.cancel_control(control_id, reason=reason)
    return {"control_id": control_id, "status": "CANCELLED", "success": canceled is not None}


@router.get("/control/applications/running")
@router.get("/v1/control/applications/running")
async def get_running_applications_endpoint() -> Dict[str, Any]:
    """Inspect active approved desktop applications."""
    from app.control.observer import observer_engine

    obs = await observer_engine.observe_applications()
    processes = obs.details.get("processes", [])
    count = obs.details.get("count", len(processes))
    return {
        "status": "ok",
        "count": count,
        "processes": processes,
        "summary": obs.summary,
        "observation": obs.model_dump(),
    }


@router.get("/control/permissions/catalog")
@router.get("/v1/control/permissions/catalog")
async def get_permissions_catalog_endpoint() -> Dict[str, Any]:
    """Return categorized tool permissions and consequential boundary rules."""
    from app.control.permissions import permission_manager

    return permission_manager.get_permission_catalog()


# ===========================================================================
# M17.6 Voice, Conversation & Multimodal Perception Endpoints
# ===========================================================================

@router.post("/voice/transcribe")
@router.post("/v1/voice/transcribe")
async def voice_transcribe_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Transcribe base64-encoded audio or mock audio into structured VoiceTranscript."""
    import base64
    from app.voice import voice_input_engine

    raw_b64 = payload.get("audio_base64", "")
    audio_format = payload.get("audio_format", "wav")
    language = payload.get("language", "en")

    try:
        audio_bytes = base64.b64decode(raw_b64) if raw_b64 else b""
    except Exception:
        audio_bytes = b""

    transcript = await voice_input_engine.process_audio(
        audio_bytes=audio_bytes,
        audio_format=audio_format,
        language=language,
    )
    return transcript.model_dump()


@router.post("/voice/speak")
@router.post("/v1/voice/speak")
async def voice_speak_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Synthesize sanitized assistant text into speech with barge-in support."""
    import base64
    from app.voice import voice_output_engine

    text = payload.get("text", "")
    voice = payload.get("voice", "default")
    speed = float(payload.get("speed", 1.0))

    result = await voice_output_engine.speak(raw_text=text, voice=voice, speed=speed)
    dumped = result.model_dump()
    if dumped.get("audio_bytes"):
        dumped["audio_base64"] = base64.b64encode(dumped.pop("audio_bytes")).decode("ascii")
    else:
        dumped.pop("audio_bytes", None)
        dumped["audio_base64"] = ""
    return dumped


@router.post("/voice/interrupt")
@router.post("/v1/voice/interrupt")
async def voice_interrupt_endpoint(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Immediately stop voice output (barge-in) and cancel active listening."""
    from app.control.engine import ryven_control_engine

    ryven_control_engine.interrupt_voice()
    return {"status": "INTERRUPTED", "message": "Voice synthesis and reception interrupted."}


@router.post("/conversation/message")
@router.post("/v1/conversation/message")
async def conversation_message_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process a natural conversational message or voice instruction with full context tracking."""
    from fastapi import HTTPException
    from app.control.conversation import conversation_manager

    message = payload.get("message") or payload.get("text")
    if not message or not isinstance(message, str):
        raise HTTPException(status_code=400, detail="Missing required 'message' string.")

    confidence = float(payload.get("confidence", 1.0))
    auto_confirm = bool(payload.get("auto_confirm", False))
    session_id = payload.get("session_id", "default")

    response = await conversation_manager.process_user_message(
        message=message,
        voice_confidence=confidence,
        auto_confirm=auto_confirm,
        session_id=session_id,
    )
    return response.model_dump()


@router.post("/conversation/cancel")
@router.post("/v1/conversation/cancel")
async def conversation_cancel_endpoint(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Cancel the active task associated with the conversational session."""
    from app.control.conversation import conversation_manager

    session_id = (payload or {}).get("session_id", "default")
    ctx = conversation_manager.get_context(session_id)
    if ctx.active_task_id:
        from app.control.task import unified_task_orchestrator
        await unified_task_orchestrator.cancel_task(ctx.active_task_id, reason="User cancelled conversation.")
        ctx.clear()
        return {"status": "CANCELLED", "task_id": ctx.active_task_id, "session_id": session_id}

    return {"status": "IDLE", "message": "No active conversational task to cancel.", "session_id": session_id}


@router.get("/conversation/context")
@router.get("/v1/conversation/context")
async def get_conversation_context_endpoint(session_id: str = "default") -> Dict[str, Any]:
    """Retrieve safe, bounded conversational context and active task pointers."""
    from app.control.conversation import conversation_manager

    ctx = conversation_manager.get_context(session_id)
    return {
        "session_id": session_id,
        "turns_count": len(ctx.turns),
        "active_task_id": ctx.active_task_id,
        "active_task_status": ctx.active_task_status.value if ctx.active_task_status else None,
        "last_mentioned_app": ctx.last_mentioned_app,
        "last_mentioned_url": ctx.last_mentioned_url,
        "pending_confirmation": ctx.pending_confirmation_token is not None,
        "recent_turns": [t.model_dump() for t in ctx.get_recent_turns(5)],
    }


@router.get("/multimodal/context")
@router.get("/v1/multimodal/context")
async def get_multimodal_context_endpoint(target_app: Optional[str] = None, session_id: str = "default") -> Dict[str, Any]:
    """Inspect safe, aggregated desktop, browser, and visual context."""
    from app.control.multimodal import multimodal_context_engine

    ctx = await multimodal_context_engine.gather_context(target_app=target_app, session_id=session_id)
    return ctx.model_dump()


@router.post("/multimodal/query")
@router.post("/v1/multimodal/query")
async def multimodal_query_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Answer natural-language visual questions descriptively without action execution."""
    from fastapi import HTTPException
    from app.control.multimodal import multimodal_context_engine

    query = payload.get("query") or payload.get("question")
    if not query or not isinstance(query, str):
        raise HTTPException(status_code=400, detail="Missing required 'query' parameter.")

    session_id = payload.get("session_id", "default")
    answer = await multimodal_context_engine.answer_visual_query(query=query, session_id=session_id)
    return {"query": query, "answer": answer}


# ─────────────────────────────────────────────────────────────────────────────
# M17.7 Long-Horizon Task Persistence & Autonomous Execution Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/tasks")
@router.get("/v1/tasks")
async def list_tasks_endpoint(
    limit: int = 50,
    state: Optional[str] = None,
) -> Dict[str, Any]:
    """List persistent multi-step long-horizon tasks with optional state filter."""
    from app.control.long_horizon import long_horizon_task_manager, LongHorizonTaskState

    state_filter = None
    if state:
        try:
            state_filter = LongHorizonTaskState(state.upper())
        except ValueError:
            pass

    tasks = long_horizon_task_manager.list_tasks(limit=limit, state=state_filter)
    return {
        "tasks": [t.model_dump() for t in tasks],
        "count": len(tasks),
        "limit": limit,
    }


@router.post("/tasks")
@router.post("/v1/tasks")
async def create_task_endpoint(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new persistent long-horizon task, optionally executing immediately."""
    from fastapi import HTTPException
    from app.control.long_horizon import long_horizon_task_manager

    goal = payload.get("goal")
    if not goal or not isinstance(goal, str):
        raise HTTPException(status_code=400, detail="Missing required 'goal' parameter.")

    execute_now = bool(payload.get("execute", False))
    auto_confirm = bool(payload.get("auto_confirm", False))
    session_id = payload.get("session_id", "default")

    task = await long_horizon_task_manager.create_long_horizon_task(goal=goal)

    if execute_now:
        progress = await long_horizon_task_manager.execute_task(
            task.task_id,
            auto_confirm=auto_confirm,
            session_id=session_id,
        )
        return {
            "task": task.model_dump(),
            "progress": progress.model_dump(),
        }

    return {"task": task.model_dump()}


@router.get("/tasks/{task_id}")
@router.get("/v1/tasks/{task_id}")
async def get_task_endpoint(task_id: str) -> Dict[str, Any]:
    """Retrieve full persistent task graph, steps, and audit journal."""
    from fastapi import HTTPException
    from app.control.long_horizon import long_horizon_task_manager

    task = long_horizon_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")

    return task.model_dump()


@router.get("/tasks/{task_id}/progress")
@router.get("/v1/tasks/{task_id}/progress")
async def get_task_progress_endpoint(task_id: str) -> Dict[str, Any]:
    """Retrieve lightweight, real-time progress snapshot for task UI timeline."""
    from fastapi import HTTPException
    from app.control.long_horizon import long_horizon_task_manager

    progress = long_horizon_task_manager.get_progress(task_id)
    if not progress:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")

    return progress.model_dump()


@router.post("/tasks/{task_id}/pause")
@router.post("/v1/tasks/{task_id}/pause")
async def pause_task_endpoint(task_id: str) -> Dict[str, Any]:
    """Pause an active long-horizon task gracefully at the current step boundary."""
    from fastapi import HTTPException
    from app.control.long_horizon import long_horizon_task_manager

    task = long_horizon_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")

    progress = await long_horizon_task_manager.pause_task(task_id)
    return {"status": "PAUSED", "progress": progress.model_dump()}


@router.post("/tasks/{task_id}/resume")
@router.post("/v1/tasks/{task_id}/resume")
async def resume_task_endpoint(task_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Safely resume a paused, interrupted, or blocked task after re-validating state."""
    from fastapi import HTTPException
    from app.control.long_horizon import long_horizon_task_manager

    task = long_horizon_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")

    auto_confirm = bool((payload or {}).get("auto_confirm", False))
    session_id = (payload or {}).get("session_id", "default")

    progress = await long_horizon_task_manager.resume_task(
        task_id=task_id,
        auto_confirm=auto_confirm,
        session_id=session_id,
    )
    return {"status": "RESUMED", "progress": progress.model_dump()}


@router.post("/tasks/{task_id}/cancel")
@router.post("/v1/tasks/{task_id}/cancel")
async def cancel_task_endpoint(task_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Immediately cancel an in-flight or pending long-horizon task."""
    from fastapi import HTTPException
    from app.control.long_horizon import long_horizon_task_manager

    task = long_horizon_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")

    reason = (payload or {}).get("reason", "Cancelled via API")
    progress = await long_horizon_task_manager.cancel_task(task_id, reason=reason)
    return {"status": "CANCELLED", "progress": progress.model_dump()}


@router.post("/tasks/{task_id}/retry")
@router.post("/v1/tasks/{task_id}/retry")
async def retry_task_endpoint(task_id: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Reset failed steps and restart long-horizon execution."""
    from fastapi import HTTPException
    from app.control.long_horizon import long_horizon_task_manager

    task = long_horizon_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found.")

    auto_confirm = bool((payload or {}).get("auto_confirm", False))
    session_id = (payload or {}).get("session_id", "default")

    progress = await long_horizon_task_manager.retry_task(
        task_id=task_id,
        auto_confirm=auto_confirm,
        session_id=session_id,
    )
    return {"status": "RETRYING", "progress": progress.model_dump()}


@router.post("/tasks/{task_id}/confirm")
@router.post("/v1/tasks/{task_id}/confirm")
async def confirm_task_endpoint(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Approve or reject a consequential action paused for user confirmation."""
    from fastapi import HTTPException
    from app.control.long_horizon import long_horizon_task_manager

    token = payload.get("token") or payload.get("confirmation_token")
    approved = bool(payload.get("approved", True))
    session_id = payload.get("session_id", "default")

    if not token and approved:
        raise HTTPException(status_code=400, detail="Missing required 'token' parameter.")

    try:
        progress = await long_horizon_task_manager.confirm_task_step(
            task_id=task_id,
            confirmation_token=token or "",
            approved=approved,
            session_id=session_id,
        )
        return {"status": "CONFIRMED" if approved else "REJECTED", "progress": progress.model_dump()}
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
