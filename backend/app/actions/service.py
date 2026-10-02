"""RYVEN M14.2 — ActionService: query layer and read-only replay.

Replay is STRICTLY READ-ONLY. It NEVER:
- calls os.startfile()
- calls subprocess
- calls SandboxProcessRunner
- calls Git commands
- executes deployment commands
- executes file modification tools
- invokes any tool from the ToolRegistry

Replay consumes stored ActionEvent history only.
"""

from __future__ import annotations

from typing import List, Optional

from app.actions.event_bus import ActionEventBus, action_bus
from app.actions.models import ActionEvent, ActionStatus, TaskSummary
from app.core.logging_config import logger


class ReplayFrame:
    """A single frame in a read-only replay sequence."""

    def __init__(self, index: int, total: int, event: ActionEvent) -> None:
        self.index = index
        self.total = total
        self.event = event

    def to_dict(self) -> dict:
        return {
            "frame_index": self.index,
            "total_frames": self.total,
            "event": self.event.model_dump(),
        }


class ReplaySession:
    """Read-only task replay session.

    SAFETY CONTRACT:
    Replay NEVER calls any system tool, shell command, Git operation,
    deployment step, or file system operation.
    It only reads historical ActionEvent objects.
    """

    def __init__(self, task_id: str, events: List[ActionEvent]) -> None:
        self.task_id = task_id
        self._frames: List[ReplayFrame] = [
            ReplayFrame(i, len(events), e) for i, e in enumerate(events)
        ]
        self._current_index: int = 0
        self.is_playing = False
        logger.info(
            f"[REPLAY] Session created for task '{task_id}' with {len(self._frames)} frames. "
            "READ-ONLY — no system actions will be executed."
        )

    @property
    def total_frames(self) -> int:
        return len(self._frames)

    @property
    def current_frame(self) -> Optional[ReplayFrame]:
        if 0 <= self._current_index < len(self._frames):
            return self._frames[self._current_index]
        return None

    def play(self) -> None:
        """Mark replay as playing. Does NOT execute any action."""
        self.is_playing = True

    def pause(self) -> None:
        """Pause replay."""
        self.is_playing = False

    def restart(self) -> None:
        """Reset to first frame."""
        self._current_index = 0
        self.is_playing = False

    def next(self) -> Optional[ReplayFrame]:
        """Advance to next frame. Returns frame or None at end."""
        if self._current_index < len(self._frames) - 1:
            self._current_index += 1
        return self.current_frame

    def previous(self) -> Optional[ReplayFrame]:
        """Go to previous frame."""
        if self._current_index > 0:
            self._current_index -= 1
        return self.current_frame

    def get_all_frames(self) -> List[dict]:
        """Return all frames as JSON-safe dicts for frontend rendering."""
        return [f.to_dict() for f in self._frames]

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "total_frames": self.total_frames,
            "current_index": self._current_index,
            "is_playing": self.is_playing,
            "current_frame": self.current_frame.to_dict() if self.current_frame else None,
            "replay_mode": "READ_ONLY",
            "safety_note": "Replay visualizes historical events only. No system actions are executed.",
        }


class ActionService:
    """High-level query and replay service for action events.

    Acts as the boundary between the API layer and the event bus.
    """

    def __init__(self, bus: Optional[ActionEventBus] = None) -> None:
        self._bus = bus or action_bus
        self._replay_sessions: dict[str, ReplaySession] = {}

    # -----------------------------------------------------------------------
    # Event queries
    # -----------------------------------------------------------------------

    def get_recent(self, limit: int = 50) -> List[ActionEvent]:
        """Return the most recent N events."""
        return self._bus.get_recent_events(limit=limit)

    def get_task_events(self, task_id: str) -> List[ActionEvent]:
        """Return all stored events for a given task_id."""
        return self._bus.get_task_events(task_id)

    def get_task_summary(self, task_id: str, goal: str = "") -> TaskSummary:
        """Build and return a structured TaskSummary from stored events."""
        return self._bus.build_task_summary(task_id, goal)

    # -----------------------------------------------------------------------
    # Read-only replay
    # -----------------------------------------------------------------------

    def create_replay(self, task_id: str) -> ReplaySession:
        """Create a READ-ONLY replay session from stored task events.

        SAFETY: This method ONLY reads historical events.
        It NEVER calls any tool, shell command, or system operation.
        """
        events = self._bus.get_task_events(task_id)
        session = ReplaySession(task_id=task_id, events=events)
        self._replay_sessions[task_id] = session
        return session

    def get_replay(self, task_id: str) -> Optional[ReplaySession]:
        """Return an existing replay session for a task."""
        return self._replay_sessions.get(task_id)

    def replay_next(self, task_id: str) -> Optional[dict]:
        """Advance replay one frame forward."""
        session = self._replay_sessions.get(task_id)
        if not session:
            return None
        frame = session.next()
        return frame.to_dict() if frame else None

    def replay_previous(self, task_id: str) -> Optional[dict]:
        """Move replay one frame backward."""
        session = self._replay_sessions.get(task_id)
        if not session:
            return None
        frame = session.previous()
        return frame.to_dict() if frame else None

    def replay_restart(self, task_id: str) -> Optional[dict]:
        """Restart replay from the beginning."""
        session = self._replay_sessions.get(task_id)
        if not session:
            return None
        session.restart()
        return session.to_dict()

    # -----------------------------------------------------------------------
    # Bus stats
    # -----------------------------------------------------------------------

    def get_stats(self) -> dict:
        return {
            "total_events": self._bus.event_count,
            "active_subscribers": self._bus.subscriber_count,
            "max_events": self._bus._max_events,
        }


# ---------------------------------------------------------------------------
# Application singleton
# ---------------------------------------------------------------------------

action_service = ActionService()
