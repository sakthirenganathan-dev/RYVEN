"""Telemetry event recorder for deployment operations ensuring zero secret leakage."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from app.core.logging_config import logger
from app.deployment.deployment_security import sanitize_deployment_output


class DeploymentTelemetry:
    """Records sanitized deployment lifecycle events."""

    def __init__(self) -> None:
        self._events: List[Dict[str, Any]] = []

    def record_event(self, event_name: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Record an event with timestamp and sanitized payload."""
        clean_payload: Dict[str, Any] = {}
        if payload:
            for k, v in payload.items():
                # Strictly protect secrets
                if any(secret_term in k.lower() for secret_term in ["token", "key", "secret", "password", "auth"]):
                    clean_payload[k] = "[REDACTED]"
                elif isinstance(v, str):
                    clean_payload[k] = sanitize_deployment_output(v)
                else:
                    clean_payload[k] = v

        event_record = {
            "event": event_name,
            "timestamp": time.time(),
            "payload": clean_payload,
        }
        self._events.append(event_record)
        logger.info(f"Deployment Telemetry Event: {event_name}")
        return event_record

    def get_events(self) -> List[Dict[str, Any]]:
        """Return all recorded telemetry events."""
        return list(self._events)

    def clear(self) -> None:
        """Clear recorded events."""
        self._events.clear()


# Global singleton
deployment_telemetry = DeploymentTelemetry()
