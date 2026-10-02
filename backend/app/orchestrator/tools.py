"""Registered BaseTools for RYVEN Milestone 14 Autonomous Development Orchestrator."""

from __future__ import annotations

from typing import Any, Dict
from app.core.logging_config import logger
from app.orchestrator.engine import orchestrator_engine
from app.tools.base import BaseTool


class OrchestrateTaskTool(BaseTool):
    """Tool to create and execute an end-to-end development orchestration plan."""

    name = "orchestrate_task"
    description = "Plan and execute an end-to-end autonomous development workflow: understanding, planning, modification, build, test, quality gate, git, deploy, and health verification."
    input_schema = {
        "type": "object",
        "properties": {
            "goal": {"type": "string", "description": "High-level development objective"},
            "project_name": {"type": "string", "description": "Optional target project name"},
            "auto_confirm": {"type": "boolean", "description": "Whether to auto-confirm protected boundaries", "default": False},
        },
        "required": ["goal"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        goal = params.get("goal", "")
        project_name = params.get("project_name")
        auto_confirm = bool(params.get("auto_confirm", False))

        if not goal:
            return {"success": False, "error": "Argument 'goal' is required."}

        try:
            task = orchestrator_engine.create_and_plan(goal, project_name_hint=project_name)
            res = await orchestrator_engine.execute_task(task.task_id, auto_confirm=auto_confirm)
            return res.model_dump()
        except Exception as e:
            logger.error(f"orchestrate_task error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}


class GetOrchestrationStatusTool(BaseTool):
    """Tool to inspect active or completed orchestration task status."""

    name = "get_orchestration_status"
    description = "Query status, checkpoints, and current step for an active or past orchestration task."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Unique task identifier"},
        },
        "required": ["task_id"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        task_id = params.get("task_id", "")
        if not task_id:
            return {"success": False, "error": "Argument 'task_id' is required."}

        task = orchestrator_engine.get_task(task_id)
        if not task:
            return {"success": False, "error": f"Task '{task_id}' not found."}

        return {
            "success": True,
            "task_id": task.task_id,
            "status": task.status.value,
            "current_step": task.current_step_id,
            "completed_steps": task.plan.completed_steps_count,
            "total_steps": task.plan.total_steps,
            "checkpoints": [c.model_dump() for c in task.checkpoints],
        }


class ConfirmOrchestrationTool(BaseTool):
    """Tool to confirm or reject a pending protected orchestration step."""

    name = "confirm_orchestration"
    description = "Submit confirmation decision for an orchestration step paused in WAITING_CONFIRMATION state."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task identifier"},
            "step_id": {"type": "string", "description": "Step identifier waiting for confirmation"},
            "confirmed": {"type": "boolean", "description": "True to confirm, False to reject"},
        },
        "required": ["task_id", "step_id", "confirmed"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        task_id = params.get("task_id", "")
        step_id = params.get("step_id", "")
        confirmed = bool(params.get("confirmed", True))

        if not task_id or not step_id:
            return {"success": False, "error": "Both 'task_id' and 'step_id' are required."}

        try:
            res = orchestrator_engine.confirm_step(task_id, step_id, confirmed)
            if confirmed and res.status != "CANCELLED":
                # Continue execution
                res = await orchestrator_engine.execute_task(task_id, auto_confirm=False)
            return res.model_dump()
        except Exception as e:
            logger.error(f"confirm_orchestration error: {e}")
            return {"success": False, "error": str(e)}


class PauseOrchestrationTool(BaseTool):
    """Tool to pause a running orchestration task."""

    name = "pause_orchestration"
    description = "Pause execution of a running orchestration task safely at the step boundary."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task identifier"},
            "reason": {"type": "string", "description": "Optional reason for pause", "default": "User requested pause"},
        },
        "required": ["task_id"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        task_id = params.get("task_id", "")
        reason = params.get("reason", "User requested pause")
        if not task_id:
            return {"success": False, "error": "Argument 'task_id' is required."}

        try:
            task = orchestrator_engine.pause_task(task_id, reason=reason)
            return {"success": True, "task_id": task.task_id, "status": task.status.value}
        except Exception as e:
            return {"success": False, "error": str(e)}


class ResumeOrchestrationTool(BaseTool):
    """Tool to resume a paused orchestration task."""

    name = "resume_orchestration"
    description = "Resume execution of a paused orchestration task."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task identifier to resume"},
        },
        "required": ["task_id"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        task_id = params.get("task_id", "")
        if not task_id:
            return {"success": False, "error": "Argument 'task_id' is required."}

        try:
            task = orchestrator_engine.resume_task(task_id)
            res = await orchestrator_engine.execute_task(task.task_id, auto_confirm=False)
            return res.model_dump()
        except Exception as e:
            return {"success": False, "error": str(e)}


class CancelOrchestrationTool(BaseTool):
    """Tool to cleanly cancel an orchestration task."""

    name = "cancel_orchestration"
    description = "Cancel execution of an orchestration task and prevent future steps from running."
    input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task identifier to cancel"},
        },
        "required": ["task_id"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        task_id = params.get("task_id", "")
        if not task_id:
            return {"success": False, "error": "Argument 'task_id' is required."}

        try:
            task = orchestrator_engine.cancel_task(task_id)
            return {"success": True, "task_id": task.task_id, "status": task.status.value}
        except Exception as e:
            return {"success": False, "error": str(e)}
