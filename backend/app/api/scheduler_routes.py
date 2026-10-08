"""
RYVEN 3.0 — Milestone 17.8 Multi-Task Scheduling REST API.
Phase 5: Authoritative Multi-Task REST API & Control Endpoints (/api/scheduler).

Authoritative Control Hierarchy:
User / Conversation
        ↓
REST / Frontend Control Surface
        ↓
MultiTaskScheduler
        ↓
SafeConcurrencyController
        ↓
TaskResourceManager
        ↓
LongHorizonTaskManager
        ↓
UnifiedTaskOrchestrator
        ↓
Existing Execution Engines

CRITICAL ARCHITECTURAL INVARIANTS:
1. Control surface ONLY. Zero direct Win32, subprocess, shell, cmd, PowerShell,
   pyautogui, pynput, or tool execution.
2. Strict delegation: Never fake state mutations in the database directly.
3. Secrets and confirmation tokens are strictly scrubbed; never leaked through GET responses.
4. Consequential action confirmation rules and SafetyGuard remain strictly enforced.
5. Parameterized, safe, robust error handling with appropriate HTTP status codes (400, 404, 409, 422, 500).
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Union
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.actions.event_bus import action_bus
from app.actions.models import ActionType, ActionStatus
from app.control.concurrency import (
    ConcurrencyState,
    SafeConcurrencyController,
    safe_concurrency_controller,
)
from app.control.long_horizon import (
    LongHorizonTask,
    LongHorizonTaskManager,
    LongHorizonTaskState,
    TaskPersistenceRepository,
    _scrub_secrets_recursive,
    long_horizon_task_manager,
    task_persistence_repo,
)
from app.control.recovery import (
    MultiTaskRecoveryManager,
    RecoveryState,
    multi_task_recovery_manager,
)
from app.control.resources import (
    CANONICAL_RESOURCE_ORDER,
    DEFAULT_RESOURCE_CAPACITIES,
    ResourceType,
    TaskResourceManager,
    task_resource_manager,
)
from app.control.scheduler import (
    MultiTaskScheduler,
    ScheduledTask,
    SchedulerState,
    TaskPriority,
    calculate_effective_priority,
    multi_task_scheduler,
)
from app.core.logging_config import logger


scheduler_router = APIRouter(prefix="/scheduler", tags=["scheduler"])


# ---------------------------------------------------------------------------
# Dynamic Repository Resolution
# ---------------------------------------------------------------------------

def _get_repo() -> TaskPersistenceRepository:
    """Return the authoritative repository bound to multi_task_scheduler."""
    return multi_task_scheduler.repo or task_persistence_repo


# ---------------------------------------------------------------------------
# Request & Response Data Models
# ---------------------------------------------------------------------------

class PriorityUpdateRequest(BaseModel):
    """Payload to update priority of a task."""
    priority: Union[int, str] = Field(
        ...,
        description="Target priority: integer between 1-100 or standard tier (CRITICAL, HIGH, NORMAL, LOW, BACKGROUND)",
    )


class TaskControlRequest(BaseModel):
    """Payload for pause, resume, cancel, or retry."""
    reason: Optional[str] = Field(default=None, description="Optional explanation for the action")
    auto_confirm: Optional[bool] = Field(default=False, description="Whether to auto-confirm if allowed")


class TaskSubmitRequest(BaseModel):
    """Payload to admit a new goal/task into the scheduler."""
    goal: str = Field(..., description="High-level task goal")
    priority: Optional[Union[int, str]] = Field(default=TaskPriority.NORMAL.value, description="Initial priority")
    dependencies: Optional[List[str]] = Field(default_factory=list, description="List of prerequisite task IDs")
    resources: Optional[List[str]] = Field(default_factory=list, description="Requested logical resources")


# ---------------------------------------------------------------------------
# Helper: Secret Pattern & Scrubbing
# ---------------------------------------------------------------------------

_SECRET_PATTERN = re.compile(
    r'(?:sk-[a-zA-Z0-9_\-]{8,}|bearer\s+[a-zA-Z0-9_\-\.]+|api[_-]?key["\']?\s*[:=]\s*["\']?[a-zA-Z0-9_\-]+|password["\']?\s*[:=]\s*["\']?[^\s"\'&]+)',
    re.IGNORECASE,
)

def _sanitize_text(text: Optional[str]) -> str:
    """Scrub sensitive credentials, tokens, and secret substrings from text."""
    if not text:
        return ""
    cleaned = re.sub(r'sk-[a-zA-Z0-9_\-]+', '[REDACTED]', text)
    cleaned = _SECRET_PATTERN.sub('[REDACTED]', cleaned)
    return cleaned


# ---------------------------------------------------------------------------
# Helper: Parse and Validate Priority
# ---------------------------------------------------------------------------

def _parse_priority_tier(raw_priority: Union[int, str]) -> int:
    """Validate and parse priority into canonical integer tier."""
    if isinstance(raw_priority, str):
        cleaned = raw_priority.strip().upper()
        tier_map = {
            "CRITICAL": TaskPriority.CRITICAL.value,
            "HIGH": TaskPriority.HIGH.value,
            "NORMAL": TaskPriority.NORMAL.value,
            "LOW": TaskPriority.LOW.value,
            "BACKGROUND": TaskPriority.BACKGROUND.value,
        }
        if cleaned in tier_map:
            return tier_map[cleaned]
        try:
            val = int(cleaned)
            if 1 <= val <= 100:
                return val
        except ValueError:
            pass
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid priority '{raw_priority}'. Must be an integer between 1 and 100 or one of: CRITICAL, HIGH, NORMAL, LOW, BACKGROUND.",
        )

    if isinstance(raw_priority, int):
        if 1 <= raw_priority <= 100:
            return raw_priority
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Priority integer must be between 1 and 100 (received {raw_priority}).",
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Priority must be an integer or recognized priority string.",
    )


# ---------------------------------------------------------------------------
# GET /api/scheduler/status
# ---------------------------------------------------------------------------

@scheduler_router.get("/status")
async def get_scheduler_status_endpoint() -> Dict[str, Any]:
    """Return safe, aggregated scheduler status and execution plane telemetry.
    
    Invariants: Zero secrets or confirmation tokens exposed.
    """
    try:
        raw_status = multi_task_scheduler.scheduler_status()
        queued_tasks = multi_task_scheduler.get_queue()
        active_tasks = multi_task_scheduler.get_active_tasks()
        active_slots_count = safe_concurrency_controller.active_count
        max_concurrency = safe_concurrency_controller.max_concurrency

        # Calculate blocked tasks in queue
        blocked_count = 0
        for item in queued_tasks:
            # Check dependency blockage
            if not multi_task_scheduler._is_task_ready(item):
                blocked_count += 1
                continue
            # Check resource blockage
            req_res = task_resource_manager.infer_required_resources(item)
            if not task_resource_manager.can_acquire(item.task_id, req_res):
                blocked_count += 1

        # Check paused count
        paused_count = len(multi_task_scheduler._paused_tasks)

        # Check recovery required count
        recovery_required_count = multi_task_recovery_manager.metrics.get("recovery_required", 0)

        # Resource summary
        res_snapshot = task_resource_manager.snapshot()
        res_summary = {
            "occupied_resources": res_snapshot.occupied_resources_count,
            "active_leases": res_snapshot.active_leases_count,
            "waiting_tasks": res_snapshot.waiting_tasks_count,
        }

        # Concurrency preemption count
        slot_snapshot = safe_concurrency_controller.snapshot()
        preemption_count = sum(s.preemption_count for s in slot_snapshot.active_slots.values())

        return {
            "scheduler_state": raw_status.scheduler_state.value,
            "running_task_count": len(active_tasks),
            "queued_task_count": len(queued_tasks),
            "paused_task_count": paused_count,
            "blocked_task_count": blocked_count,
            "recovery_required_count": recovery_required_count,
            "active_execution_slots": active_slots_count,
            "max_concurrency": max_concurrency,
            "resource_usage_summary": res_summary,
            "preemption_count": preemption_count,
            "uptime_seconds": raw_status.scheduler_uptime_seconds,
        }
    except Exception as exc:
        logger.error(f"[API/SCHEDULER] Failed to get status: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve scheduler status.",
        )


# ---------------------------------------------------------------------------
# GET /api/scheduler/queue
# ---------------------------------------------------------------------------

@scheduler_router.get("/queue")
async def get_scheduler_queue_endpoint() -> Dict[str, Any]:
    """Return safe, deterministic priority-ordered task queue metadata.
    
    Includes dependency states, resource conflicts, and starvation bonuses.
    Never exposes passwords, tokens, cookies, credentials, or prompts.
    """
    try:
        queued_items = multi_task_scheduler.get_queue()
        queue_data: List[Dict[str, Any]] = []

        for item in queued_items:
            # 1. Dependency evaluation
            is_dep_ready = multi_task_scheduler._is_task_ready(item)
            dep_blocked_reason = None
            if not is_dep_ready and item.dependencies:
                unsatisfied = [
                    d for d in item.dependencies
                    if d not in multi_task_scheduler._completed_task_ids
                ]
                if unsatisfied:
                    dep_blocked_reason = f"Waiting for dependencies: {', '.join(unsatisfied)}"

            # 2. Resource conflict evaluation
            req_resources = task_resource_manager.infer_required_resources(item)
            conflicts = task_resource_manager.get_conflicts(item.task_id, req_resources)
            res_blocked_reason = None
            blocked_resources: List[str] = []
            if conflicts:
                # Include both resource base name and canonical string for flexible inspection
                blocked_resources = [c.resource.split("/")[0] for c in conflicts] + [c.resource for c in conflicts]
                res_blocked_reason = f"Resource unavailable: {conflicts[0].resource} (held by {conflicts[0].owned_by})"

            # 3. Derive fine-grained state & reason
            derived_state = "QUEUED"
            blocked_reason = None
            if not is_dep_ready:
                derived_state = "BLOCKED_DEPENDENCY"
                blocked_reason = dep_blocked_reason
            elif conflicts:
                derived_state = "BLOCKED_RESOURCE"
                blocked_reason = res_blocked_reason
            else:
                derived_state = "READY"

            # 4. Long-horizon task progress and goal
            goal = item.task.goal if item.task else f"Task {item.task_id}"
            progress_pct = item.task.progress_percent if item.task else 0.0
            retry_count = item.task.recovery_count if item.task else 0
            rec_count = item.task.recovery_count if item.task else 0

            # 5. Extract safe resource strings
            req_resource_strs = [r.canonical_string for r in req_resources]

            # 6. Sanitize metadata
            safe_meta = _scrub_secrets_recursive(item.metadata or {})

            queue_data.append({
                "task_id": item.task_id,
                "goal": goal,
                "state": derived_state,
                "priority": item.base_priority,
                "effective_priority": round(item.effective_priority, 2),
                "wait_bonus": round(item.wait_bonus, 2),
                "enqueued_at": item.enqueued_at,
                "enqueued_at_iso": item.enqueued_at_iso,
                "dependencies": list(item.dependencies),
                "requested_resources": req_resource_strs,
                "retry_count": retry_count,
                "preemption_count": safe_meta.get("preemption_count", 0),
                "recovery_count": rec_count,
                "blocked_reason": blocked_reason,
                "blocked_resources": blocked_resources,
                "progress": progress_pct,
            })

        return {
            "queue": queue_data,
            "count": len(queue_data),
        }
    except Exception as exc:
        logger.error(f"[API/SCHEDULER] Failed to get queue: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve scheduler queue.",
        )


# ---------------------------------------------------------------------------
# GET /api/scheduler/resources
# ---------------------------------------------------------------------------

@scheduler_router.get("/resources")
async def get_scheduler_resources_endpoint() -> Dict[str, Any]:
    """Return authoritative TaskResourceManager status across all 9 canonical resource types.
    
    Shows capacities, active allocations, safe owners, and detected conflicts.
    """
    try:
        snapshot = task_resource_manager.snapshot()
        queue_items = multi_task_scheduler.get_queue()

        resources_list: List[Dict[str, Any]] = []

        for rtype in CANONICAL_RESOURCE_ORDER:
            cap = DEFAULT_RESOURCE_CAPACITIES.get(rtype, 1)

            # Find matching items in snapshot
            matching_items = [
                item for item in snapshot.resources.values()
                if item.resource_type == rtype
            ]

            active_usage = sum(item.used_capacity for item in matching_items)
            avail_cap = max(0, cap - active_usage)

            # Safe owner task IDs
            safe_owners: List[str] = []
            for item in matching_items:
                if item.exclusive_owner:
                    safe_owners.append(item.exclusive_owner)
                if item.shared_owners:
                    safe_owners.extend(item.shared_owners)
            safe_owners = sorted(list(set(safe_owners)))

            # Blocked tasks in queue waiting for this resource type
            blocked_tasks: List[str] = []
            conflicts_for_type: List[Dict[str, Any]] = []

            for q_item in queue_items:
                reqs = task_resource_manager.infer_required_resources(q_item)
                for req in reqs:
                    if req.resource_type == rtype:
                        c_list = task_resource_manager.get_conflicts(q_item.task_id, [req])
                        if c_list:
                            blocked_tasks.append(q_item.task_id)
                            for c in c_list:
                                conflicts_for_type.append({
                                    "resource": c.resource,
                                    "requested_by": c.requested_by,
                                    "owned_by": c.owned_by,
                                    "reason": c.reason,
                                })

            blocked_tasks = sorted(list(set(blocked_tasks)))

            resources_list.append({
                "resource_type": rtype.value,
                "capacity": cap,
                "active_usage": active_usage,
                "available_capacity": avail_cap,
                "safe_owner_task_ids": safe_owners,
                "blocked_tasks": blocked_tasks,
                "conflicts": conflicts_for_type,
            })

        return {
            "resources": resources_list,
            "total_resources": len(resources_list),
            "total_capacity": sum(r["capacity"] for r in resources_list),
            "total_active_usage": sum(r["active_usage"] for r in resources_list),
            "total_available": sum(r["available_capacity"] for r in resources_list),
            "active_leases_count": snapshot.active_leases_count,
            "occupied_count": snapshot.occupied_resources_count,
            "waiting_tasks_count": snapshot.waiting_tasks_count,
        }
    except Exception as exc:
        logger.error(f"[API/SCHEDULER] Failed to get resources: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve resource manager status.",
        )


# ---------------------------------------------------------------------------
# GET /api/scheduler/tasks/{task_id}
# ---------------------------------------------------------------------------

@scheduler_router.get("/tasks/{task_id}")
async def get_scheduler_task_detail_endpoint(task_id: str) -> Dict[str, Any]:
    """Return safe task details from authoritative multi-task scheduler and repository.
    
    Never exposes confirmation tokens, passwords, or secrets.
    """
    if not task_id or not task_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id cannot be empty.")

    clean_id = task_id.strip()
    repo = _get_repo()

    # 1. Check in scheduler memory
    sched_item = multi_task_scheduler.get_task(clean_id)
    if not sched_item:
        # Check paused tasks
        sched_item = multi_task_scheduler._paused_tasks.get(clean_id)

    # 2. Check persistent repository
    lh_task: Optional[LongHorizonTask] = None
    if sched_item and sched_item.task:
        lh_task = sched_item.task
    else:
        lh_task = repo.get_task(clean_id)

    # Check scheduled tasks table
    sch_db = repo.get_scheduled_task(clean_id)

    if not sched_item and not lh_task and not sch_db:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{clean_id}' not found in scheduler or persistent repository.",
        )

    # Resolve fields safely
    goal = lh_task.goal if lh_task else (sch_db.get("metadata", {}).get("goal", clean_id) if sch_db else clean_id)
    state = (
        sched_item.state.value if sched_item and hasattr(sched_item.state, "value")
        else (lh_task.state.value if lh_task else (sch_db.get("state", "QUEUED") if sch_db else "QUEUED"))
    )
    priority = (
        sched_item.base_priority if sched_item
        else (sch_db.get("priority", TaskPriority.NORMAL.value) if sch_db else TaskPriority.NORMAL.value)
    )
    eff_priority = (
        sched_item.effective_priority if sched_item
        else (sch_db.get("effective_priority", float(priority)) if sch_db else float(priority))
    )
    progress_pct = lh_task.progress_percent if lh_task else 0.0
    dependencies = (
        list(sched_item.dependencies) if sched_item
        else (sch_db.get("dependencies", []) if sch_db else [])
    )
    retry_count = lh_task.recovery_count if lh_task else (sch_db.get("retry_count", 0) if sch_db else 0)
    preempt_count = sch_db.get("preemption_count", 0) if sch_db else 0

    # Held or requested resources
    held_resources = task_resource_manager.list_resources_for_task(clean_id)
    req_resources: List[str] = []
    if lh_task:
        req_resources = [r.canonical_string for r in task_resource_manager.infer_required_resources(lh_task)]
    elif sch_db:
        req_resources = sch_db.get("required_resources", [])

    all_resources = sorted(list(set(held_resources + req_resources)))

    # Checkpoint availability
    chk_ref = lh_task.checkpoint_reference if lh_task else (sch_db.get("checkpoint_ref") if sch_db else None)
    checkpoint_available = bool(chk_ref)

    # Recovery state
    rec_state = sch_db.get("recovery_state") if sch_db else None
    if not rec_state and lh_task and lh_task.state == LongHorizonTaskState.INTERRUPTED:
        rec_state = "INTERRUPTED"

    return {
        "task_id": clean_id,
        "goal": goal,
        "state": state,
        "priority": priority,
        "effective_priority": round(eff_priority, 2),
        "progress": progress_pct,
        "dependencies": dependencies,
        "resources": all_resources,
        "retry_count": retry_count,
        "preemption_count": preempt_count,
        "recovery_state": rec_state,
        "recovery_attempts": retry_count,
        "checkpoint_availability": checkpoint_available,
        "last_milestone": lh_task.last_milestone if lh_task else None,
        "created_at": lh_task.created_at if lh_task else None,
        "updated_at": lh_task.updated_at if lh_task else None,
    }


# ---------------------------------------------------------------------------
# GET /api/scheduler/tasks/{task_id}/progress
# ---------------------------------------------------------------------------

@scheduler_router.get("/tasks/{task_id}/progress")
async def get_scheduler_task_progress_endpoint(task_id: str) -> Dict[str, Any]:
    """Retrieve existing task progress snapshot from LongHorizonTaskManager.
    
    Invariants: Strictly removes any confirmation tokens from GET responses.
    """
    if not task_id or not task_id.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id cannot be empty.")

    clean_id = task_id.strip()
    repo = _get_repo()

    progress = None
    t = repo.get_task(clean_id)
    if t:
        progress = t.get_progress_snapshot()
    elif long_horizon_task_manager:
        progress = long_horizon_task_manager.get_progress(clean_id)

    if not progress:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task '{clean_id}' progress not found.",
        )

    p_data = progress.model_dump()
    # Strip confirmation token for safety
    p_data.pop("confirmation_token", None)
    if "safe_metadata" in p_data and isinstance(p_data["safe_metadata"], dict):
        p_data["safe_metadata"] = _scrub_secrets_recursive(p_data["safe_metadata"])

    if t:
        p_data["current_step_index"] = t.current_step_index
        if t.state == LongHorizonTaskState.COMPLETED:
            p_data["progress_percent"] = 100.0
        elif t.progress_percent > 0 and (not t.steps or p_data.get("progress_percent", 0.0) == 0.0):
            p_data["progress_percent"] = t.progress_percent

    return p_data


# ---------------------------------------------------------------------------
# POST /api/scheduler/tasks/{task_id}/pause
# ---------------------------------------------------------------------------

@scheduler_router.get("/tasks/{task_id}/pause")
@scheduler_router.post("/tasks/{task_id}/pause")
async def pause_scheduler_task_endpoint(
    task_id: str,
    payload: Optional[TaskControlRequest] = None,
) -> Dict[str, Any]:
    """Pause an active or queued task safely without corrupting execution state."""
    clean_id = task_id.strip()
    if not clean_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id cannot be empty.")

    repo = _get_repo()
    existing = multi_task_scheduler.get_task(clean_id) or multi_task_scheduler._paused_tasks.get(clean_id)
    lh_task = repo.get_task(clean_id)

    if not existing and not lh_task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{clean_id}' not found.")

    # Check terminal state
    current_state = (
        existing.state if existing
        else (lh_task.state if lh_task else None)
    )
    if current_state in (LongHorizonTaskState.COMPLETED, LongHorizonTaskState.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot pause task '{clean_id}' in terminal state '{current_state.value}'.",
        )

    reason = (payload.reason if payload and payload.reason else "Paused via Scheduler API")
    await multi_task_scheduler.pause_task(clean_id, reason=reason)

    return {
        "status": "PAUSED",
        "task_id": clean_id,
        "message": f"Task '{clean_id}' paused successfully.",
    }


# ---------------------------------------------------------------------------
# POST /api/scheduler/tasks/{task_id}/resume
# ---------------------------------------------------------------------------

@scheduler_router.get("/tasks/{task_id}/resume")
@scheduler_router.post("/tasks/{task_id}/resume")
async def resume_scheduler_task_endpoint(
    task_id: str,
    payload: Optional[TaskControlRequest] = None,
) -> Dict[str, Any]:
    """Safely resume a paused or interrupted task back into the scheduler queue."""
    clean_id = task_id.strip()
    if not clean_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id cannot be empty.")

    repo = _get_repo()
    existing = multi_task_scheduler.get_task(clean_id) or multi_task_scheduler._paused_tasks.get(clean_id)
    lh_task = repo.get_task(clean_id)

    if not existing and not lh_task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{clean_id}' not found.")

    # Check terminal state
    if lh_task and lh_task.state in (LongHorizonTaskState.COMPLETED, LongHorizonTaskState.CANCELLED):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot resume task '{clean_id}' in terminal state '{lh_task.state.value}'.",
        )

    # Check confirmation requirement
    if lh_task and lh_task.state == LongHorizonTaskState.WAITING_CONFIRMATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot resume task '{clean_id}': task requires user confirmation.",
        )

    # Check if already running
    if clean_id in multi_task_scheduler._active_tasks or (lh_task and lh_task.state == LongHorizonTaskState.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task '{clean_id}' is already running.",
        )

    reason = (payload.reason if payload and payload.reason else "Resumed via Scheduler API")
    try:
        item = await multi_task_scheduler.resume_task(clean_id, reason=reason)
        if not item and lh_task:
            # Re-admit via scheduler
            item = multi_task_scheduler.enqueue_task(lh_task)
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(val_err))

    return {
        "status": "RESUMED",
        "state": "QUEUED",
        "task_id": clean_id,
        "message": f"Task '{clean_id}' resumed into scheduler queue successfully.",
    }


# ---------------------------------------------------------------------------
# POST /api/scheduler/tasks/{task_id}/cancel
# ---------------------------------------------------------------------------

@scheduler_router.get("/tasks/{task_id}/cancel")
@scheduler_router.post("/tasks/{task_id}/cancel")
async def cancel_scheduler_task_endpoint(
    task_id: str,
    payload: Optional[TaskControlRequest] = None,
) -> Dict[str, Any]:
    """Cancel a queued, active, or paused task immediately and release all held resources."""
    clean_id = task_id.strip()
    if not clean_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id cannot be empty.")

    repo = _get_repo()
    existing = multi_task_scheduler.get_task(clean_id) or multi_task_scheduler._paused_tasks.get(clean_id)
    lh_task = repo.get_task(clean_id)

    if not existing and not lh_task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{clean_id}' not found.")

    reason = (payload.reason if payload and payload.reason else "Cancelled via Scheduler API")
    await multi_task_scheduler.cancel_task(clean_id, reason=reason)

    # Ensure all resources and concurrency slots are released
    task_resource_manager.release_all_for_task(clean_id)
    safe_concurrency_controller.release_slot(clean_id, reason=reason)

    return {
        "status": "CANCELLED",
        "task_id": clean_id,
        "message": f"Task '{clean_id}' cancelled successfully.",
    }


# ---------------------------------------------------------------------------
# POST /api/scheduler/tasks/{task_id}/retry
# ---------------------------------------------------------------------------

@scheduler_router.get("/tasks/{task_id}/retry")
@scheduler_router.post("/tasks/{task_id}/retry")
async def retry_scheduler_task_endpoint(
    task_id: str,
    payload: Optional[TaskControlRequest] = None,
) -> Dict[str, Any]:
    """Reset failed steps and re-enqueue task up to maximum retry limit (3)."""
    clean_id = task_id.strip()
    if not clean_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id cannot be empty.")

    repo = _get_repo()
    existing = multi_task_scheduler.get_task(clean_id) or multi_task_scheduler._paused_tasks.get(clean_id)
    lh_task = repo.get_task(clean_id)

    if not existing and not lh_task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{clean_id}' not found.")

    # Check if active
    if clean_id in multi_task_scheduler._active_tasks or (lh_task and lh_task.state == LongHorizonTaskState.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry task '{clean_id}': task is currently active.",
        )

    # Check eligibility
    current_state = lh_task.state if lh_task else (existing.state if existing else None)
    if current_state not in (
        LongHorizonTaskState.FAILED,
        LongHorizonTaskState.INTERRUPTED,
        LongHorizonTaskState.RECOVERY_REQUIRED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task '{clean_id}' in state '{current_state.value if current_state else 'UNKNOWN'}' is not eligible for retry.",
        )

    # Check retry limit
    rec_count = lh_task.recovery_count if lh_task else 0
    if rec_count >= 3:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Task '{clean_id}' has reached maximum retry / recovery attempts ({rec_count}/3).",
        )

    reason = (payload.reason if payload and payload.reason else "Retried via Scheduler API")
    try:
        scheduled = await multi_task_scheduler.retry_task(clean_id, reason=reason)
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(val_err))

    return {
        "status": "RETRYING",
        "state": "QUEUED",
        "task_id": clean_id,
        "message": f"Task '{clean_id}' retried and re-enqueued successfully.",
        "retry_count": rec_count + 1,
    }


# ---------------------------------------------------------------------------
# POST /api/scheduler/tasks/{task_id}/priority
# ---------------------------------------------------------------------------

@scheduler_router.post("/tasks/{task_id}/priority")
async def update_scheduler_task_priority_endpoint(
    task_id: str,
    payload: PriorityUpdateRequest,
) -> Dict[str, Any]:
    """Update base priority of a task while preserving bounded aging and deterministic queue order."""
    clean_id = task_id.strip()
    if not clean_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="task_id cannot be empty.")

    # Validate priority
    new_priority = _parse_priority_tier(payload.priority)

    repo = _get_repo()
    existing = multi_task_scheduler.get_task(clean_id) or multi_task_scheduler._paused_tasks.get(clean_id)
    lh_task = repo.get_task(clean_id)
    sch_db = repo.get_scheduled_task(clean_id)

    if not existing and not lh_task and not sch_db:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Task '{clean_id}' not found.")

    try:
        updated = multi_task_scheduler.update_task_priority(clean_id, new_priority)
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(val_err))

    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Could not update priority for '{clean_id}'.")

    return {
        "status": "UPDATED",
        "task_id": clean_id,
        "priority": updated.base_priority,
        "effective_priority": round(updated.effective_priority, 2),
        "message": f"Task '{clean_id}' priority successfully updated to {updated.base_priority}.",
    }


# ---------------------------------------------------------------------------
# POST /api/scheduler/submit
# ---------------------------------------------------------------------------

@scheduler_router.post("/submit")
@scheduler_router.post("/admit")
async def submit_scheduler_task_endpoint(payload: TaskSubmitRequest) -> Dict[str, Any]:
    """Submit a new task into the authoritative MultiTaskScheduler."""
    if not payload.goal or not payload.goal.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="goal parameter cannot be empty.")

    priority_val = _parse_priority_tier(payload.priority or TaskPriority.NORMAL.value)

    # 1. Create durable LongHorizonTask
    lh_task = await long_horizon_task_manager.create_long_horizon_task(
        goal=payload.goal.strip(),
    )

    # 2. Enqueue via scheduler
    scheduled = multi_task_scheduler.admit_task(
        task=lh_task,
        priority=priority_val,
        dependencies=payload.dependencies or [],
        metadata={"resources": payload.resources or []},
    )

    return {
        "status": "ADMITTED",
        "task_id": scheduled.task_id,
        "priority": scheduled.base_priority,
        "effective_priority": scheduled.effective_priority,
        "state": scheduled.state.value if hasattr(scheduled.state, "value") else str(scheduled.state),
    }
