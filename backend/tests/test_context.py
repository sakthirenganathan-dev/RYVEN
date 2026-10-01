"""Unit tests for ConversationManager context retention and pruning."""

from app.core.context import ConversationManager


def test_conversation_manager_basic_retention():
    """Verify adding user and assistant messages and retrieving history."""
    manager = ConversationManager(max_messages=10)

    manager.add_user_message("session_1", "My name is Sakthi.")
    manager.add_assistant_message("session_1", "Understood, Sakthi.")

    history = manager.get_history("session_1")
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "My name is Sakthi."
    assert history[1].role == "assistant"
    assert history[1].content == "Understood, Sakthi."


def test_conversation_manager_session_isolation():
    """Verify histories for different sessions remain strictly isolated."""
    manager = ConversationManager(max_messages=10)

    manager.add_user_message("user_a", "I prefer Python.")
    manager.add_user_message("user_b", "I prefer TypeScript.")

    assert len(manager.get_history("user_a")) == 1
    assert manager.get_history("user_a")[0].content == "I prefer Python."

    assert len(manager.get_history("user_b")) == 1
    assert manager.get_history("user_b")[0].content == "I prefer TypeScript."


def test_conversation_manager_pruning():
    """Verify pruning when message count exceeds max_messages."""
    manager = ConversationManager(max_messages=4)

    for i in range(6):
        manager.add_user_message("session_test", f"Message {i}")

    history = manager.get_history("session_test")
    assert len(history) == 4
    # Oldest 2 should have been pruned
    assert history[0].content == "Message 2"
    assert history[3].content == "Message 5"


def test_conversation_manager_clear():
    """Verify clearing a specific session."""
    manager = ConversationManager()
    manager.add_user_message("session_clear", "Hello")
    assert len(manager.get_history("session_clear")) == 1

    manager.clear("session_clear")
    assert len(manager.get_history("session_clear")) == 0
