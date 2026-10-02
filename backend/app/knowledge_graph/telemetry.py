"""Structured telemetry and observability for Knowledge Graph Engine (M11.5)."""

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from app.core.logging_config import logger


class KnowledgeGraphTelemetry:
    """Emits structured audit and performance events for knowledge graph operations."""

    @staticmethod
    def emit(event_type: str, data: Dict[str, Any]) -> None:
        """Log structured JSON event for observability."""
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "subsystem": "knowledge_graph",
            "event": event_type,
            **data,
        }
        logger.info(f"[KG_TELEMETRY] {json.dumps(payload)}")

    @classmethod
    def build_started(cls, project_id: str, file_count: int) -> None:
        cls.emit("graph_build_started", {"project_id": project_id, "file_count": file_count})

    @classmethod
    def build_completed(cls, project_id: str, file_count: int, node_count: int, edge_count: int, duration_ms: float) -> None:
        cls.emit("graph_build_completed", {
            "project_id": project_id,
            "file_count": file_count,
            "node_count": node_count,
            "edge_count": edge_count,
            "duration_ms": duration_ms,
        })

    @classmethod
    def build_failed(cls, project_id: str, error: str) -> None:
        cls.emit("graph_build_failed", {"project_id": project_id, "error": error})

    @classmethod
    def cache_hit(cls, project_id: str, node_count: int) -> None:
        cls.emit("graph_cache_hit", {"project_id": project_id, "node_count": node_count})

    @classmethod
    def cache_miss(cls, project_id: str) -> None:
        cls.emit("graph_cache_miss", {"project_id": project_id})

    @classmethod
    def graph_stale(cls, project_id: str) -> None:
        cls.emit("graph_stale", {"project_id": project_id})

    @classmethod
    def query(cls, project_id: str, query_type: str, target: str, matched_count: int) -> None:
        cls.emit("graph_query", {
            "project_id": project_id,
            "query_type": query_type,
            "target": target,
            "matched_count": matched_count,
        })

    @classmethod
    def context_optimized(
        cls,
        project_id: str,
        task: str,
        baseline_tokens: int,
        optimized_tokens: int,
        reduction_pct: float,
    ) -> None:
        cls.emit("context_optimized", {
            "project_id": project_id,
            "task": task[:80],
            "baseline_tokens": baseline_tokens,
            "optimized_tokens": optimized_tokens,
            "reduction_percentage": reduction_pct,
        })

    @classmethod
    def context_fallback(cls, project_id: str, reason: str) -> None:
        cls.emit("context_fallback", {"project_id": project_id, "reason": reason})
