"""Registered tools for RYVEN Health and Deployment Verification Engine (M13).

Tools:
1. health_check
2. health_status
3. health_history
4. health_monitor_start
5. health_monitor_stop
"""

from __future__ import annotations

from typing import Any, Dict
from app.core.logging_config import logger
from app.health.health_service import health_service
from app.tools.base import BaseTool


class HealthCheckTool(BaseTool):
    """Tool to perform on-demand HTTP health probe against an endpoint."""

    name = "health_check"
    description = "Perform a safe HTTP health check against an endpoint URL to inspect reachability, latency, and HTTP status."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public HTTP or HTTPS endpoint URL to probe"},
            "timeout_seconds": {"type": "number", "description": "Probe timeout in seconds", "default": 10.0},
        },
        "required": ["url"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        url = params.get("url", "")
        timeout = float(params.get("timeout_seconds", 10.0))

        if not url:
            return {"success": False, "error": "Argument 'url' is required."}

        try:
            res = await health_service.check(url=url, timeout_seconds=timeout)
            return {
                "success": res.success,
                "url": res.url,
                "status": res.status.value,
                "http_status": res.http_status,
                "response_time_ms": res.response_time_ms,
                "latency_tier": res.latency_tier.value,
                "failure_code": res.failure_code.value,
                "message": res.safe_error_message,
                "checked_at": res.checked_at,
                "headers": res.headers_checked,
            }
        except Exception as e:
            logger.error(f"health_check error: {e}")
            return {"success": False, "error": str(e)}


class HealthStatusTool(BaseTool):
    """Tool to inspect current health status and uptime summary."""

    name = "health_status"
    description = "Get the latest health status, uptime percentage, and average latency for a target URL."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Target endpoint URL to inspect"},
        },
        "required": ["url"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        url = params.get("url", "")
        if not url:
            return {"success": False, "error": "Argument 'url' is required."}

        try:
            summary = health_service.get_summary(url)
            return {
                "success": True,
                "url": summary.url,
                "status": summary.current_status.value,
                "total_records": summary.total_records,
                "uptime_percentage": summary.uptime_percentage,
                "average_response_time_ms": summary.average_response_time_ms,
            }
        except Exception as e:
            logger.error(f"health_status error: {e}")
            return {"success": False, "error": str(e)}


class HealthHistoryTool(BaseTool):
    """Tool to retrieve historical health check entries."""

    name = "health_history"
    description = "Retrieve recent historical health check records for an endpoint."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Target endpoint URL"},
            "limit": {"type": "integer", "description": "Maximum records to return", "default": 10},
        },
        "required": ["url"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        url = params.get("url", "")
        limit = int(params.get("limit", 10))

        if not url:
            return {"success": False, "error": "Argument 'url' is required."}

        try:
            records = health_service.get_history(url, limit=limit)
            return {
                "success": True,
                "url": url,
                "count": len(records),
                "records": [r.model_dump() for r in records],
            }
        except Exception as e:
            logger.error(f"health_history error: {e}")
            return {"success": False, "error": str(e)}


class HealthMonitorStartTool(BaseTool):
    """Tool to start a background health monitor."""

    name = "health_monitor_start"
    description = "Start continuous background health monitoring for an endpoint."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public URL to monitor"},
            "interval_seconds": {"type": "number", "description": "Polling interval in seconds (min 5.0)", "default": 30.0},
        },
        "required": ["url"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        url = params.get("url", "")
        interval = float(params.get("interval_seconds", 30.0))

        if not url:
            return {"success": False, "error": "Argument 'url' is required."}

        try:
            status = health_service.start_monitoring(url, interval_seconds=interval)
            return {
                "success": True,
                "url": status.url,
                "is_running": status.is_running,
                "interval_seconds": status.interval_seconds,
                "status": status.current_status.value,
            }
        except Exception as e:
            logger.error(f"health_monitor_start error: {e}")
            return {"success": False, "error": str(e)}


class HealthMonitorStopTool(BaseTool):
    """Tool to stop a background health monitor."""

    name = "health_monitor_stop"
    description = "Stop active continuous background monitoring for an endpoint."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Public URL of running monitor"},
        },
        "required": ["url"],
    }
    requires_confirmation = False

    async def execute(self, *args: Any, **kwargs: Any) -> Dict[str, Any]:
        params = args[0] if (args and isinstance(args[0], dict)) else kwargs
        url = params.get("url", "")
        if not url:
            return {"success": False, "error": "Argument 'url' is required."}

        try:
            stopped = health_service.stop_monitoring(url)
            return {
                "success": True,
                "url": url,
                "stopped": stopped,
            }
        except Exception as e:
            logger.error(f"health_monitor_stop error: {e}")
            return {"success": False, "error": str(e)}
