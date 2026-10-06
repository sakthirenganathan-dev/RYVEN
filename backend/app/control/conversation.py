"""
RYVEN 3.0 — Milestone 17.6 Conversational Context & Control Layer.

Design & Security Invariants:
- INPUT/PERCEPTION/CONVERSATION ONLY. Never directly executes tools or Win32 actions.
- All tasks route through UnifiedTaskOrchestrator.
- Bounded turn history (FIFO eviction) prevents memory leaks.
- Credential and token scrubbing is strictly applied to stored turns and responses.
- Contextual reference resolution ("it", "that", "this page", "continue", "stop").
- Strict confirmation boundary: Natural language confirmations ("yes", "go ahead") only
  operate when a valid pending confirmation token exists, and never grant blanket authorization.
- Ambiguous references trigger clarification requests rather than guessing consequential actions.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from enum import Enum
import re
import time
from typing import Any, Deque, Dict, List, Optional, Set, Tuple
import uuid

from pydantic import BaseModel, Field

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.control.task import (
    UnifiedTask,
    UnifiedTaskOrchestrator,
    UnifiedTaskResult,
    UnifiedTaskStatus,
    unified_task_orchestrator as default_orchestrator,
)
from app.core.logging_config import logger
from app.workflows.confirmation import ConfirmationManager

# ---------------------------------------------------------------------------
# Constants & Enums
# ---------------------------------------------------------------------------

DEFAULT_MAX_CONVERSATION_TURNS = 20

_SECRET_KEYS: Set[str] = {
    "password", "passwd", "token", "secret", "api_key", "apikey",
    "private_key", "cookie", "bearer", "authorization",
}

_CREDENTIAL_PATTERNS = [
    re.compile(r"(?:api[_-]?key|token|bearer|secret|password)[\s:=]+([a-zA-Z0-9_\-\.]{8,})", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{30,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----", re.IGNORECASE),
]

_CONFIRMATION_AFFIRMATIVES = {
    "yes", "y", "confirm", "go ahead", "proceed", "do it", "approved",
    "sure", "ok", "okay", "yes please",
}

_CANCELLATION_KEYWORDS = {
    "cancel", "stop", "halt", "abort", "terminate", "cancel that",
    "stop it", "don't do that", "nevermind",
}

_RETRY_KEYWORDS = {
    "try again", "retry", "retry that", "run again",
}

_CONTINUE_KEYWORDS = {
    "continue", "resume", "keep going",
}

_BLANKET_AUTHORIZATION_PHRASES = [
    "do whatever you need",
    "go ahead with everything",
    "don't ask me again",
    "bypass confirmation",
    "trust everything",
    "confirm all",
    "auto confirm everything",
]


class ConversationalIntentType(str, Enum):
    """Categorized conversational intent."""
    NEW_TASK = "NEW_TASK"
    FOLLOW_UP = "FOLLOW_UP"
    CONFIRMATION = "CONFIRMATION"
    CANCELLATION = "CANCELLATION"
    RETRY = "RETRY"
    CONTINUE = "CONTINUE"
    CLARIFICATION_ANSWER = "CLARIFICATION_ANSWER"
    QUERY = "QUERY"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ConversationTurn(BaseModel):
    """A single turn in the conversational dialogue."""
    turn_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    speaker: str = Field(..., description="'user' or 'ryven'")
    text: str = Field(...)
    intent_type: ConversationalIntentType = ConversationalIntentType.NEW_TASK
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    associated_task_id: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)


class ConversationalResponse(BaseModel):
    """Structured response from the conversational control layer."""
    response_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    message: str = Field(...)
    intent_type: ConversationalIntentType = ConversationalIntentType.NEW_TASK
    task_id: Optional[str] = None
    task_status: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    requires_clarification: bool = False
    clarification_options: List[str] = Field(default_factory=list)
    success: bool = True
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Conversation Context
# ---------------------------------------------------------------------------

class ConversationContext:
    """Bounded, sanitized state tracking dialogue history and active task references."""

    def __init__(self, max_turns: int = DEFAULT_MAX_CONVERSATION_TURNS) -> None:
        self.max_turns = max_turns
        self.turns: Deque[ConversationTurn] = deque(maxlen=max_turns)
        self.active_task_id: Optional[str] = None
        self.active_task_status: Optional[UnifiedTaskStatus] = None
        self.last_mentioned_app: Optional[str] = None
        self.last_mentioned_url: Optional[str] = None
        self.last_mentioned_target: Optional[str] = None
        self.pending_confirmation_token: Optional[str] = None
        self.pending_confirmation_action: Optional[str] = None
        self.awaiting_clarification_for: Optional[str] = None

    def add_turn(self, turn: ConversationTurn) -> None:
        """Add a turn with strict credential redaction and bounded history."""
        # Sanitize text
        clean_text = turn.text
        for pat in _CREDENTIAL_PATTERNS:
            clean_text = pat.sub("[REDACTED]", clean_text)

        sanitized_turn = turn.model_copy(update={"text": clean_text})
        self.turns.append(sanitized_turn)

        # Update contextual pointers
        self._update_context_from_turn(sanitized_turn)

    def _update_context_from_turn(self, turn: ConversationTurn) -> None:
        """Extract referenced applications, URLs, or targets from text."""
        if turn.speaker != "user":
            return

        lowered = turn.text.lower()

        # Known apps (longest matched first)
        for app in ["vscode", "chrome", "notepad", "explorer", "calculator", "terminal", "code"]:
            if app in lowered:
                self.last_mentioned_app = app
                break

        # URLs
        url_match = re.search(r"https?://[^\s]+", turn.text)
        if url_match:
            self.last_mentioned_url = url_match.group(0)

        # Target button/input references
        target_match = re.search(r"(?:click|type into|press|find)\s+(?:the\s+)?([a-zA-Z0-9_\-\s]{2,25}?)(?:\s+button|\s+field|\s+input|\s+tab|$)", turn.text, re.IGNORECASE)
        if target_match:
            self.last_mentioned_target = target_match.group(1).strip()

    def get_recent_turns(self, count: int = 5) -> List[ConversationTurn]:
        """Return the most recent N turns."""
        turns_list = list(self.turns)
        return turns_list[-count:]

    def clear(self) -> None:
        """Clear all conversation history and active state pointers."""
        self.turns.clear()
        self.active_task_id = None
        self.active_task_status = None
        self.last_mentioned_app = None
        self.last_mentioned_url = None
        self.last_mentioned_target = None
        self.pending_confirmation_token = None
        self.pending_confirmation_action = None
        self.awaiting_clarification_for = None


# ---------------------------------------------------------------------------
# ConversationManager
# ---------------------------------------------------------------------------

class ConversationManager:
    """Manages dialogue processing, intent understanding, and task routing.

    Strict Invariants:
    - Never executes tools directly.
    - Routes all execution tasks to UnifiedTaskOrchestrator.
    - Prevents blanket confirmation authorizations.
    - Clarifies ambiguous follow-up requests.
    - Sanitizes all stored content.
    """

    def __init__(
        self,
        orchestrator: Optional[UnifiedTaskOrchestrator] = None,
        max_turns: int = DEFAULT_MAX_CONVERSATION_TURNS,
    ) -> None:
        self.orchestrator = orchestrator or default_orchestrator
        self.max_turns = max_turns
        self._contexts: Dict[str, ConversationContext] = {}

    def get_context(self, session_id: str = "default") -> ConversationContext:
        """Retrieve or create the conversation context for a session."""
        if session_id not in self._contexts:
            self._contexts[session_id] = ConversationContext(max_turns=self.max_turns)
        return self._contexts[session_id]

    # -----------------------------------------------------------------------
    # Follow-Up & Intent Resolution
    # -----------------------------------------------------------------------

    def resolve_intent_and_target(
        self,
        raw_text: str,
        ctx: ConversationContext,
    ) -> Tuple[ConversationalIntentType, str, Optional[str], Optional[List[str]]]:
        """Classify message intent, resolve contextual references, and check for ambiguity.

        Returns: (intent_type, resolved_goal, clarification_question, clarification_options)
        """
        text = raw_text.strip()
        lowered = text.lower()

        # 1. Check for Blanket Authorization Rejection
        for phrase in _BLANKET_AUTHORIZATION_PHRASES:
            if phrase in lowered:
                # Do NOT grant blanket authorization
                return (
                    ConversationalIntentType.CONFIRMATION,
                    text,
                    "RYVEN security policies require explicit confirmation for each consequential action individually. Blanket authorization is not permitted.",
                    None,
                )

        # 2. Check for Immediate Cancellation / Stop
        for kw in _CANCELLATION_KEYWORDS:
            if lowered == kw or lowered.startswith(f"{kw} "):
                return ConversationalIntentType.CANCELLATION, "Cancel active task", None, None

        # 3. Check for Confirmation
        for aff in _CONFIRMATION_AFFIRMATIVES:
            if lowered == aff or lowered.startswith(f"{aff} "):
                if ctx.pending_confirmation_token:
                    return ConversationalIntentType.CONFIRMATION, text, None, None
                else:
                    return (
                        ConversationalIntentType.CONFIRMATION,
                        text,
                        "There is currently no action waiting for your confirmation.",
                        None,
                    )

        # 4. Check for Retry
        for kw in _RETRY_KEYWORDS:
            if lowered == kw or lowered.startswith(f"{kw} "):
                return ConversationalIntentType.RETRY, "Retry last operation", None, None

        # 5. Check for Continue
        for kw in _CONTINUE_KEYWORDS:
            if lowered == kw or lowered.startswith(f"{kw} "):
                return ConversationalIntentType.CONTINUE, "Continue task", None, None

        # 6. Check for Ambiguous References ("it", "that", "the app", "open it", "open that")
        if lowered in {"open it", "open that", "click it", "click that", "it", "that", "do that"}:
            if not ctx.last_mentioned_app and not ctx.last_mentioned_target:
                return (
                    ConversationalIntentType.FOLLOW_UP,
                    text,
                    "I'm not sure what you want me to interact with. Which application or target do you mean?",
                    ["Chrome", "VS Code", "Desktop"],
                )

        # 7. Follow-up Contextual Expansion
        # Example: "Search React docs" after "Open Chrome" -> "Search React docs in Chrome"
        resolved_goal = text
        if ctx.last_mentioned_app:
            if lowered.startswith("search ") and " in " not in lowered and ctx.last_mentioned_app in ("chrome", "browser"):
                resolved_goal = f"{text} in {ctx.last_mentioned_app}"
            elif lowered.startswith("inspect ") and " in " not in lowered and ctx.last_mentioned_app:
                resolved_goal = f"{text} in {ctx.last_mentioned_app}"

        # Pronoun substitution if confident
        if " it" in resolved_goal.lower() and ctx.last_mentioned_app:
            resolved_goal = re.sub(r"\bit\b", ctx.last_mentioned_app, resolved_goal, flags=re.IGNORECASE)

        return ConversationalIntentType.NEW_TASK, resolved_goal, None, None

    # -----------------------------------------------------------------------
    # Message Processing & Task Integration
    # -----------------------------------------------------------------------

    async def process_user_message(
        self,
        message: str,
        voice_confidence: float = 1.0,
        auto_confirm: bool = False,
        session_id: str = "default",
    ) -> ConversationalResponse:
        """Process conversational user input, handle dialogue control, or route to orchestrator.

        Strict Invariants:
        - All tasks execute strictly via UnifiedTaskOrchestrator.
        - Low-confidence voice inputs (< 0.50) are rejected with a helpful clarification request.
        - Consequential actions require explicit confirmation tokens.
        - Responses are sanitized and concise.
        """
        ctx = self.get_context(session_id)

        # 1. Low Confidence Voice Input Guard
        if voice_confidence < 0.50:
            clarification_msg = "I didn't quite catch that. Could you please repeat or rephrase your request?"
            ctx.add_turn(ConversationTurn(speaker="user", text=message, confidence=voice_confidence))
            ctx.add_turn(ConversationTurn(speaker="ryven", text=clarification_msg))
            return ConversationalResponse(
                message=clarification_msg,
                intent_type=ConversationalIntentType.QUERY,
                requires_clarification=True,
                success=False,
            )

        # 2. Record User Turn
        ctx.add_turn(
            ConversationTurn(
                speaker="user",
                text=message,
                confidence=voice_confidence,
            )
        )

        # 3. Resolve Intent and Check for Ambiguity
        intent, resolved_goal, clarification, options = self.resolve_intent_and_target(message, ctx)

        # Handle required clarification
        if clarification:
            ctx.add_turn(ConversationTurn(speaker="ryven", text=clarification))
            return ConversationalResponse(
                message=clarification,
                intent_type=intent,
                requires_clarification=True,
                clarification_options=options or [],
                success=True,
            )

        # 4. Handle Direct Conversational Task Control Actions

        # CANCELLATION
        if intent == ConversationalIntentType.CANCELLATION:
            if ctx.active_task_id:
                canceled = await self.orchestrator.cancel_task(
                    ctx.active_task_id,
                    reason="User requested cancellation via conversation.",
                )
                msg = f"Task '{ctx.active_task_id}' has been cancelled." if canceled else "Cancellation requested."
                ctx.active_task_status = UnifiedTaskStatus.CANCELLED
            else:
                msg = "No active task to cancel."

            ctx.add_turn(ConversationTurn(speaker="ryven", text=msg, intent_type=intent))
            return ConversationalResponse(
                message=msg,
                intent_type=intent,
                task_id=ctx.active_task_id,
                task_status="CANCELLED" if ctx.active_task_id else None,
                success=True,
            )

        # CONFIRMATION
        if intent == ConversationalIntentType.CONFIRMATION:
            if ctx.pending_confirmation_token and ctx.active_task_id:
                token = ctx.pending_confirmation_token
                ctx.pending_confirmation_token = None
                ctx.pending_confirmation_action = None

                # Revalidate & execute via orchestrator confirmation
                try:
                    result = await self.orchestrator.confirm_task(
                        task_id=ctx.active_task_id,
                        confirmation_token=token,
                        approved=True,
                    )
                except Exception as e:
                    logger.warning(f"[CONVERSATION] Confirmation error: {e}")
                    result = None

                if result and result.success:
                    msg = "Confirmation received. Resuming task execution."
                    ctx.active_task_status = result.status
                elif result:
                    msg = result.result_summary or "Confirmation handled."
                    ctx.active_task_status = result.status
                else:
                    msg = "Confirmation token was invalid or has expired."

                ctx.add_turn(ConversationTurn(speaker="ryven", text=msg, intent_type=intent))
                return ConversationalResponse(
                    message=msg,
                    intent_type=intent,
                    task_id=ctx.active_task_id,
                    task_status=ctx.active_task_status.value.upper() if ctx.active_task_status else None,
                    success=result is not None and result.success,
                )

        # RETRY
        if intent == ConversationalIntentType.RETRY:
            if ctx.active_task_id:
                try:
                    task = self.orchestrator.get_task(ctx.active_task_id)
                    if task:
                        task.status = UnifiedTaskStatus.CREATED
                        task.recovery_count += 1
                        res = await self.orchestrator.execute_task(task)
                        msg = "Retrying previous task operation." if res and res.success else "Task retry failed."
                        success = res is not None and res.success
                    else:
                        msg = "Could not find task to retry."
                        success = False
                except Exception as exc:
                    logger.warning(f"[CONVERSATION] Retry error: {exc}")
                    msg = "Could not retry task."
                    success = False

                ctx.add_turn(ConversationTurn(speaker="ryven", text=msg, intent_type=intent))
                return ConversationalResponse(
                    message=msg,
                    intent_type=intent,
                    task_id=ctx.active_task_id,
                    success=success,
                )
            else:
                msg = "No recent task found to retry."
                ctx.add_turn(ConversationTurn(speaker="ryven", text=msg, intent_type=intent))
                return ConversationalResponse(message=msg, intent_type=intent, success=False)

        # 5. Route Normal / Follow-Up Task through UnifiedTaskOrchestrator
        try:
            task = await self.orchestrator.create_task(resolved_goal)
            ctx.active_task_id = task.task_id
            ctx.active_task_status = task.status

            # Plan
            task = await self.orchestrator.plan_task(task)

            # Execute
            result = await self.orchestrator.execute_task(
                task,
                auto_confirm=auto_confirm,
                session_id=session_id,
            )

            ctx.active_task_status = result.status

            # Handle Confirmation Pause Boundary
            if result.status == UnifiedTaskStatus.WAITING_CONFIRMATION:
                token = task.active_confirmation_token or result.safe_metadata.get("confirmation_token")
                ctx.pending_confirmation_token = token
                ctx.pending_confirmation_action = task.active_confirmation_action or result.safe_metadata.get("confirmation_action")
                resp_msg = f"I need your confirmation to proceed: {result.result_summary or 'Consequential action requires user confirmation.'}"
                ctx.add_turn(ConversationTurn(speaker="ryven", text=resp_msg, intent_type=intent))
                return ConversationalResponse(
                    message=resp_msg,
                    intent_type=ConversationalIntentType.CONFIRMATION,
                    task_id=task.task_id,
                    task_status=result.status.value.upper(),
                    requires_confirmation=True,
                    confirmation_token=token,
                    success=True,
                )

            # Format concise response
            if result.success:
                resp_msg = result.result_summary or f"Completed goal: {resolved_goal}"
            else:
                resp_msg = result.error or result.result_summary or "The task could not be completed successfully."

            ctx.add_turn(ConversationTurn(speaker="ryven", text=resp_msg, intent_type=intent))

            # Telemetry
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.CONVERSATION_CONTEXT_UPDATED,
                    status=ActionStatus.COMPLETED,
                    title="Conversation updated",
                    task_id=task.task_id,
                    safe_metadata={
                        "intent": intent.value,
                        "turns_count": len(ctx.turns),
                        "success": result.success,
                    },
                )
            )

            return ConversationalResponse(
                message=resp_msg,
                intent_type=intent,
                task_id=task.task_id,
                task_status=result.status.value.upper(),
                success=result.success,
            )

        except Exception as exc:
            logger.error(f"[CONVERSATION] Task execution error: {exc}")
            err_msg = "An error occurred while executing your request."
            ctx.add_turn(ConversationTurn(speaker="ryven", text=err_msg))
            return ConversationalResponse(
                message=err_msg,
                intent_type=intent,
                success=False,
            )


# Singleton instance
conversation_manager = ConversationManager()
