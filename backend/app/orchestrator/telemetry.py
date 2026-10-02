"""Structured JSON telemetry for RYVEN Milestone 14 Autonomous Development Orchestrator."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional
from app.core.logging_config import logger
from app.orchestrator.models import utc_now_iso


class OrchestrationTelemetry:
    """Emits structured, sanitized telemetry for orchestration events."""

    REDACTED_KEYS = {"password", "token", "secret", "cookie", "authorization", "auth", "api_key", "private_key"}

    @classmethod
    def _sanitize(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """Strip sensitive credentials from telemetry payload."""
        sanitized = {}
        for k, v in data.items():
            if any(rk in k.lower() for rk in cls.REDACTED_KEYS):
                sanitized[k] = "[REDACTED]"
            elif isinstance(v, dict):
                sanitized[k] = cls._sanitize(v)
            else:
                sanitized[k] = v
        return sanitized

    @classmethod
    def emit(cls, event: str, payload: Dict[str, Any]) -> None:
        """Log structured telemetry event in JSON format."""
        entry = {
            "timestamp": utc_now_iso(),
            "subsystem": "orchestrator",
            "event": event,
            **cls._sanitize(payload),
        }
        logger.info(f"[ORCHESTRATION_TELEMETRY] {json.dumps(entry)}")

    @classmethod
    def task_started(cls, task_id: str, goal: str, project_name: str, step_count: int) -> None:
        cls.emit("orchestration_started", {
            "task_id": task_id,
            "goal": goal,
            "project_name": project_name,
            "step_count": step_count,
        })

    @classmethod
    def plan_created(cls, task_id: str, plan_id: str, step_types: list[str]) -> None:
        cls.emit("orchestration_plan_created", {
            "task_id": task_id,
            "plan_id": plan_id,
            "step_types": step_types,
        })

    @classmethod
    def step_started(cls, task_id: str, step_id: str, step_type: str, step_name: str) -> None:
        cls.emit("orchestration_step_started", {
            "task_id": task_id,
            "step_id": step_id,
            "step_type": step_type,
            "step_name": step_name,
        })

    @classmethod
    def step_completed(cls, task_id: str, step_id: str, step_type: str, duration_ms: float) -> None:
        cls.emit("orchestration_step_completed", {
            "task_id": task_id,
            "step_id": step_id,
            "step_type": step_type,
            "duration_ms": round(duration_ms, 2),
        })

    @classmethod
    def step_failed(cls, task_id: str, step_id: str, step_type: str, error: str, retry_count: int) -> None:
        cls.emit("orchestration_step_failed", {
            "task_id": task_id,
            "step_id": step_id,
            "step_type": step_type,
            "error": error,
            "retry_count": retry_count,
        })

    @classmethod
    def confirmation_requested(cls, task_id: str, step_id: str, reason: str) -> None:
        cls.emit("orchestration_confirmation_requested", {
            "task_id": task_id,
            "step_id": step_id,
            "reason": reason,
        })

    @classmethod
    def confirmation_received(cls, task_id: str, step_id: str, confirmed: bool) -> None:
        cls.emit("orchestration_confirmation_received", {
            "task_id": task_id,
            "step_id": step_id,
            "confirmed": confirmed,
        })

    @classmethod
    def task_paused(cls, task_id: str, reason: str) -> None:
        cls.emit("orchestration_paused", {
            "task_id": task_id,
            "reason": reason,
        })

    @classmethod
    def task_resumed(cls, task_id: str) -> None:
        cls.emit("orchestration_resumed", {
            "task_id": task_id,
        })

    @classmethod
    def task_cancelled(cls, task_id: str) -> None:
        cls.emit("orchestration_cancelled", {
            "task_id": task_id,
        })

    @classmethod
    def task_completed(cls, task_id: str, success: bool, duration_ms: float, steps_completed: int) -> None:
        cls.emit("orchestration_completed", {
            "task_id": task_id,
            "success": success,
            "duration_ms": round(duration_ms, 2),
            "steps_completed": steps_completed,
        })
