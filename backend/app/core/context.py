"""In-memory session conversation context management for RYVEN."""

from typing import Dict, List, Literal, Optional
from app.ai.provider import ChatMessage
from app.core.config import settings
from app.core.logging_config import logger


class ConversationManager:
    """Manages short-term in-memory conversation histories keyed by session_id."""

    def __init__(self, max_messages: Optional[int] = None) -> None:
        self.max_messages = max_messages or settings.max_history_messages
        self._sessions: Dict[str, List[ChatMessage]] = {}

    def get_history(self, session_id: str = "default") -> List[ChatMessage]:
        """Return a copy of the conversation history for a given session."""
        return list(self._sessions.get(session_id, []))

    def add_message(
        self,
        session_id: str,
        role: Literal["user", "assistant", "system"],
        content: str,
    ) -> None:
        """Append a message to the session's conversation history and prune overflow."""
        clean_content = content.strip()
        if not clean_content:
            return

        if session_id not in self._sessions:
            self._sessions[session_id] = []

        self._sessions[session_id].append(ChatMessage(role=role, content=clean_content))
        self._prune(session_id)

    def add_user_message(self, session_id: str, content: str) -> None:
        """Record a user query."""
        self.add_message(session_id, "user", content)

    def add_assistant_message(self, session_id: str, content: str) -> None:
        """Record an assistant reply."""
        self.add_message(session_id, "assistant", content)

    def _prune(self, session_id: str) -> None:
        """Ensure session history does not exceed max_messages limit."""
        history = self._sessions.get(session_id, [])
        if len(history) > self.max_messages:
            overflow = len(history) - self.max_messages
            self._sessions[session_id] = history[overflow:]
            logger.debug(
                f"Pruned {overflow} older message(s) from session '{session_id}' (limit: {self.max_messages})"
            )

    def clear(self, session_id: str = "default") -> None:
        """Clear history for a specific session."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            logger.info(f"Cleared conversation context for session '{session_id}'")

    def clear_all(self) -> None:
        """Clear all active sessions."""
        self._sessions.clear()
        logger.info("Cleared all conversation sessions")
