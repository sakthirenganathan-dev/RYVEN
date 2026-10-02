"""Structured telemetry and observability for RYVEN Health Engine (M13)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict
from app.core.logging_config import logger


class HealthTelemetry:
    """Emits structured, sanitized JSON events for health checks and monitoring."""

    @staticmethod
    def emit(event_type: str, data: Dict[str, Any]) -> None:
        """Log structured JSON event."""
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "subsystem": "health_engine",
            "event": event_type,
            **data,
        }
        logger.info(f"[HEALTH_TELEMETRY] {json.dumps(payload)}")

    @classmethod
    def check_started(cls, url: str) -> None:
        cls.emit("health_check_started", {"url": url})

    @classmethod
    def check_completed(
        cls,
        url: str,
        status: str,
        http_status: Any,
        duration_ms: float,
        failure_code: str,
    ) -> None:
        cls.emit("health_check_completed", {
            "url": url,
            "status": status,
            "http_status": http_status,
            "duration_ms": duration_ms,
            "failure_code": failure_code,
        })

    @classmethod
    def deployment_verified(
        cls,
        deployment_id: str,
        project_name: str,
        url: str,
        status: str,
        http_status: Any,
    ) -> None:
        cls.emit("deployment_verified", {
            "deployment_id": deployment_id,
            "project_name": project_name,
            "url": url,
            "status": status,
            "http_status": http_status,
        })

    @classmethod
    def monitor_started(cls, url: str, interval_seconds: float) -> None:
        cls.emit("monitor_started", {"url": url, "interval_seconds": interval_seconds})

    @classmethod
    def monitor_stopped(cls, url: str) -> None:
        cls.emit("monitor_stopped", {"url": url})
