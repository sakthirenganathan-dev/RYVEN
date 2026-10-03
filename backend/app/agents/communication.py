"""RYVEN 3.0 — Structured Agent Communication and Handoff Subsystem.

Provides typed inter-agent communication, structured handoffs, and context budgeting.
Prevents unrestricted natural language loops between agents.
Enforces depth ceilings (MAX_HANDOFFS) and performs recursive secret scrubbing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from collections import deque

from app.actions.action_tracker import action_tracker
from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.agents.models import (
    MAX_HANDOFFS,
    AgentContext,
    AgentHandoff,
    AgentMessage,
    AgentRole,
    MessageType,
    _utc_now_iso,
    redact_secrets,
)
from app.core.logging_config import logger


class CommunicationManager:
    """Manages typed messages, structured handoffs, and bounded context between agents."""

    def __init__(self, max_history: int = 200) -> None:
        self.message_history: deque[AgentMessage] = deque(maxlen=max_history)
        self.handoff_history: deque[AgentHandoff] = deque(maxlen=max_history)
        self.handoff_count_by_graph: Dict[str, int] = {}

    # -----------------------------------------------------------------------
    # Message Dispatch
    # -----------------------------------------------------------------------

    async def send_message(
        self,
        sender_role: AgentRole,
        recipient_role: AgentRole,
        message_type: MessageType,
        payload: Dict[str, Any],
        task_id: Optional[str] = None,
        graph_id: Optional[str] = None,
    ) -> AgentMessage:
        """Send a typed, bounded, and sanitized message between agent roles."""
        clean_payload = redact_secrets(payload)
        msg = AgentMessage(
            sender_role=sender_role,
            recipient_role=recipient_role,
            message_type=message_type,
            task_id=task_id,
            payload=clean_payload,
        )
        self.message_history.append(msg)
        logger.info(
            f"[AGENT_COMM] {sender_role.value} -> {recipient_role.value} [{message_type.value}] task={task_id}"
        )

        # Emit observable ActionEvent
        try:
            event = ActionEvent(
                action_type=ActionType.AGENT_MESSAGE,
                status=ActionStatus.COMPLETED,
                title=f"Message: {sender_role.value} -> {recipient_role.value} ({message_type.value})",
                description=f"Type: {message_type.value}",
                task_id=task_id,
                safe_metadata={
                    "sender": sender_role.value,
                    "recipient": recipient_role.value,
                    "message_type": message_type.value,
                    "graph_id": graph_id,
                },
            )
            await action_bus.publish(event)
        except Exception as exc:
            logger.debug(f"[AGENT_COMM] Event emit suppressed: {exc}")

        return msg

    # -----------------------------------------------------------------------
    # Structured Handoff
    # -----------------------------------------------------------------------

    async def execute_handoff(
        self,
        source_role: AgentRole,
        target_role: AgentRole,
        task_id: str,
        findings: List[Dict[str, Any]],
        sources: Optional[List[str]] = None,
        constraints: Optional[List[str]] = None,
        artifacts: Optional[Dict[str, Any]] = None,
        recommended_next_action: str = "",
        graph_id: Optional[str] = None,
    ) -> AgentHandoff:
        """Execute a formal, structured handoff from one specialized role to another.

        Enforces MAX_HANDOFFS to prevent infinite delegation ping-pong.
        """
        g_id = graph_id or "default"
        current_handoffs = self.handoff_count_by_graph.get(g_id, 0)

        if current_handoffs >= MAX_HANDOFFS:
            logger.warning(f"[AGENT_HANDOFF] Graph '{g_id}' exceeded MAX_HANDOFFS ({MAX_HANDOFFS}).")
            handoff = AgentHandoff(
                source_role=source_role,
                target_role=target_role,
                task_id=task_id,
                accepted=False,
                reason=f"Exceeded maximum handoff limit of {MAX_HANDOFFS}.",
            )
            self.handoff_history.append(handoff)
            return handoff

        self.handoff_count_by_graph[g_id] = current_handoffs + 1

        handoff = AgentHandoff(
            source_role=source_role,
            target_role=target_role,
            task_id=task_id,
            findings=findings,
            sources=sources or [],
            constraints=constraints or [],
            artifacts=artifacts or {},
            recommended_next_action=recommended_next_action,
            accepted=True,
        )
        self.handoff_history.append(handoff)
        logger.info(
            f"[AGENT_HANDOFF] SUCCESS: {source_role.value} -> {target_role.value} "
            f"for task {task_id} (findings={len(findings)}, sources={len(sources or [])})"
        )

        # Emit observable ActionEvent
        try:
            event = ActionEvent(
                action_type=ActionType.AGENT_HANDOFF,
                status=ActionStatus.COMPLETED,
                title=f"Handoff: {source_role.value} -> {target_role.value}",
                description=f"Action: {recommended_next_action or 'Proceed'}",
                task_id=task_id,
                safe_metadata={
                    "source": source_role.value,
                    "target": target_role.value,
                    "findings_count": len(findings),
                    "graph_id": g_id,
                },
            )
            await action_bus.publish(event)
        except Exception as exc:
            logger.debug(f"[AGENT_HANDOFF] Event emit suppressed: {exc}")

        return handoff

    # -----------------------------------------------------------------------
    # Context Synthesis Helper
    # -----------------------------------------------------------------------

    def build_budgeted_context(
        self,
        task_id: str,
        user_intent: str,
        task_objective: str,
        dependency_results: Optional[Dict[str, Any]] = None,
        findings: Optional[List[Dict[str, Any]]] = None,
        relevant_files: Optional[List[str]] = None,
    ) -> AgentContext:
        """Construct a scoped AgentContext containing only necessary information."""
        return AgentContext(
            task_id=task_id,
            user_intent=user_intent,
            task_objective=task_objective,
            dependency_results=dependency_results or {},
            findings=findings or [],
            relevant_files=relevant_files or [],
        )


# Global singleton instance
communication_manager = CommunicationManager()
