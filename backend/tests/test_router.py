"""Unit tests for IntentRouter intent classification."""

import pytest
from app.core.router import IntentRouter


@pytest.fixture
def router():
    return IntentRouter()


def test_time_intent_routing(router):
    """Verify that time and date queries route to the time tool."""
    time_queries = [
        "What time is it?",
        "what's the time right now?",
        "tell me the time please",
        "current time",
        "what is today's date?",
        "what day is it today?",
        "today's date",
    ]
    for query in time_queries:
        decision = router.route(query)
        assert decision.intent == "tool", f"Query '{query}' failed to route to tool"
        assert decision.tool_name == "time", f"Query '{query}' routed to wrong tool '{decision.tool_name}'"


def test_system_intent_routing(router):
    """Verify that system metrics queries route to system_status tool."""
    system_queries = [
        "What is the system status?",
        "system telemetry please",
        "how much ram is used?",
        "what is the cpu usage?",
        "check memory usage",
        "how much disk space do I have left?",
        "what's my battery level?",
    ]
    for query in system_queries:
        decision = router.route(query)
        assert decision.intent == "tool", f"Query '{query}' failed to route to tool"
        assert decision.tool_name == "system_status", f"Query '{query}' routed to wrong tool"


def test_ai_intent_routing(router):
    """Verify that open-ended questions and general instructions route to AI."""
    ai_queries = [
        "Who was Alan Turing?",
        "Explain how transformer neural networks function.",
        "Write a Python function to compute Fibonacci numbers.",
        "Tell me a short story about Mars exploration.",
        "Hello RYVEN, how are you today?",
    ]
    for query in ai_queries:
        decision = router.route(query)
        assert decision.intent == "ai", f"Query '{query}' failed to route to AI"
        assert decision.tool_name is None
