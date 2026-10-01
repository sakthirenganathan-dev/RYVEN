"""Unit tests for Phase 3 intent and tool routing."""

import pytest
from app.core.router import IntentRouter


@pytest.fixture
def router():
    return IntentRouter()


def test_router_open_application(router):
    """Verify application launch queries route to open_application tool."""
    test_cases = [
        ("Open VS Code.", "open_application", "vscode"),
        ("Launch Chrome.", "open_application", "chrome"),
        ("RYVEN, open Notepad.", "open_application", "notepad"),
        ("Start Calculator", "open_application", "calculator"),
        ("Run Windows Terminal", "open_application", "windows terminal"),
    ]
    for query, expected_tool, expected_app in test_cases:
        decision = router.route(query)
        assert decision.intent == "tool", f"Query '{query}' failed to route to tool"
        assert decision.tool_name == expected_tool, f"Query '{query}' got wrong tool '{decision.tool_name}'"
        assert expected_app in decision.tool_arguments.get("application", "").lower()


def test_router_open_website(router):
    """Verify website queries route to open_website tool."""
    test_cases = [
        ("Open GitHub.", "open_website", "github"),
        ("Visit YouTube.", "open_website", "youtube"),
        ("RYVEN, open Google.", "open_website", "google"),
        ("Open my portfolio", "open_website", "portfolio"),
        ("Go to https://python.org", "open_website", "https://python.org"),
    ]
    for query, expected_tool, expected_site in test_cases:
        decision = router.route(query)
        assert decision.intent == "tool"
        assert decision.tool_name == expected_tool
        assert expected_site in decision.tool_arguments.get("url", "").lower()


def test_router_open_folder(router):
    """Verify folder queries route to open_folder tool."""
    test_cases = [
        ("Open my Downloads folder.", "open_folder", "downloads"),
        ("RYVEN, open Desktop.", "open_folder", "desktop"),
        ("Show Documents folder", "open_folder", "documents"),
        ("Open project workspace", "open_folder", "workspace"),
    ]
    for query, expected_tool, expected_folder in test_cases:
        decision = router.route(query)
        assert decision.intent == "tool"
        assert decision.tool_name == expected_tool
        assert decision.tool_arguments.get("folder") == expected_folder


def test_router_search_files(router):
    """Verify file search queries route to search_files tool."""
    test_cases = [
        ("Find my Java files.", "search_files", "Java"),
        ("Search for python files", "search_files", "python"),
        ("RYVEN, find my notes files", "search_files", "notes"),
    ]
    for query, expected_tool, expected_query in test_cases:
        decision = router.route(query)
        assert decision.intent == "tool"
        assert decision.tool_name == expected_tool
        assert decision.tool_arguments.get("query").lower() == expected_query.lower()


def test_router_open_file(router):
    """Verify opening files routes to open_file tool."""
    test_cases = [
        ("Open the Java file you found.", "open_file"),
        ("Open the file you just found.", "open_file"),
    ]
    for query, expected_tool in test_cases:
        decision = router.route(query)
        assert decision.intent == "tool"
        assert decision.tool_name == expected_tool


def test_router_clipboard(router):
    """Verify clipboard get and set queries route properly."""
    # Get clipboard
    get_queries = [
        "What is in my clipboard?",
        "What's on my clipboard?",
        "RYVEN, read clipboard",
        "Show my clipboard",
    ]
    for query in get_queries:
        decision = router.route(query)
        assert decision.intent == "tool"
        assert decision.tool_name == "get_clipboard"

    # Set clipboard
    set_queries = [
        ("Copy this text to my clipboard: Hello RYVEN.", "Hello RYVEN"),
        ("Copy 'Hello from RYVEN' to my clipboard.", "Hello from RYVEN"),
        ("RYVEN, copy 'Phase 3 verified' to my clipboard", "Phase 3 verified"),
    ]
    for query, expected_text in set_queries:
        decision = router.route(query)
        assert decision.intent == "tool"
        assert decision.tool_name == "set_clipboard"
        assert expected_text in decision.tool_arguments.get("text", "")


def test_router_system_info(router):
    """Verify system info queries route to system_info tool."""
    queries = [
        "What is my Windows version?",
        "Show system information",
        "What is my hostname?",
        "What is the battery status?",
    ]
    for query in queries:
        decision = router.route(query)
        assert decision.intent == "tool"
        assert decision.tool_name in ("system_info", "system_status")


def test_router_ai_conversational(router):
    """Verify open-ended questions route to AI without tool execution."""
    ai_queries = [
        "Explain Java inheritance.",
        "How do asynchronous coroutines work in Python?",
        "What is the difference between CPU and GPU?",
        "Write a poem about space exploration.",
        "What is CPU?",
        "What is Python?",
        "Explain RAM.",
    ]
    for query in ai_queries:
        decision = router.route(query)
        assert decision.intent == "ai", f"Query '{query}' was incorrectly routed to tool '{decision.tool_name}'"
        assert decision.tool_name is None
