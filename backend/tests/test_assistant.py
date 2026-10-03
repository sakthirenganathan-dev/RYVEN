"""Unit tests for the central Assistant class with mocked AI provider and context."""

from typing import Any, Dict, List, Optional
import pytest
from app.ai.ollama import OllamaUnavailableError
from app.ai.provider import AIProvider, AIResponse, ChatMessage
from app.core.assistant import Assistant
from app.core.context import ConversationManager
from app.tools.registry import ToolRegistry
from app.tools.system_tool import SystemStatusTool
from app.tools.time_tool import TimeTool


class MockAIProvider(AIProvider):
    """Mock AI Provider for deterministic unit testing."""

    provider_name: str = "mock_ai"

    def __init__(self, response_text: str = "This is a simulated AI response."):
        self.response_text = response_text
        self.should_fail = False
        self.failure_message = "Ollama connection refused"
        self.received_messages: List[ChatMessage] = []

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        **kwargs: Any,
    ) -> AIResponse:
        if self.should_fail:
            raise OllamaUnavailableError(self.failure_message)

        self.received_messages = list(messages or [])

        # Check if user previously gave their name in context
        for msg in self.received_messages:
            if "name is Sakthi" in msg.content:
                if "what is my name" in prompt.lower():
                    return AIResponse(
                        content="Your name is Sakthi.",
                        model="qwen2.5:7b",
                        provider=self.provider_name,
                        metadata={"context_recalled": True},
                    )

        return AIResponse(
            content=self.response_text,
            model="qwen2.5:7b",
            provider=self.provider_name,
            metadata={"simulated": True},
        )

    async def check_health(self) -> Dict[str, Any]:
        return {
            "online": not self.should_fail,
            "version": "0.35.0",
            "model_available": True,
            "configured_model": "qwen2.5:7b",
        }


@pytest.fixture
def assistant():
    registry = ToolRegistry()
    registry.register(TimeTool())
    registry.register(SystemStatusTool())
    mock_ai = MockAIProvider()
    context_mgr = ConversationManager()
    return Assistant(registry=registry, ai_provider=mock_ai, context_manager=context_mgr)


@pytest.mark.asyncio
async def test_assistant_time_routing(assistant):
    """Assistant processes time request and executes TimeTool."""
    response = await assistant.process("What time is it right now?")

    assert response.success is True
    assert response.type == "tool"
    assert response.tool == "time"
    assert "It is " in response.message
    assert "iso" in response.metadata


@pytest.mark.asyncio
async def test_assistant_system_routing(assistant):
    """Assistant processes system request and executes SystemStatusTool."""
    response = await assistant.process("Check my system telemetry.")

    assert response.success is True
    assert response.type == "tool"
    assert response.tool == "system_status"
    assert "System Diagnostics:" in response.message
    assert "cpu_percent" in response.metadata


@pytest.mark.asyncio
async def test_single_tool_request_runs_through_agent_control_plane(assistant):
    """A deterministic single-tool request creates an observable agent task."""
    assert assistant.agent_engine.workflow_engine is assistant.workflow_engine
    response = await assistant.process("What time is it right now?")

    assert response.success is True
    assert response.type == "tool"
    assert response.tool == "time"
    assert "iso" in response.metadata
    task_id = response.metadata.get("task_id")
    assert task_id
    task = assistant.agent_engine.get_task_state(task_id)
    assert task is not None
    assert task.status.value == "COMPLETED"
    assert task.current_plan is not None
    assert task.current_plan.total_steps == 1


@pytest.mark.asyncio
async def test_assistant_ai_routing(assistant):
    """Assistant routes general conversation to the AI provider."""
    response = await assistant.process("What is quantum computing?")

    assert response.success is True
    assert response.type == "ai"
    assert response.tool is None
    assert response.message == "This is a simulated AI response."
    assert response.metadata.get("model") == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_assistant_conversation_context(assistant):
    """Assistant retains multi-turn short-term context across session turns."""
    session = "session_user_sakthi"

    # Turn 1: Introduce user
    r1 = await assistant.process("My name is Sakthi.", session_id=session)
    assert r1.success is True

    # Turn 2: Ask for name
    r2 = await assistant.process("What is my name?", session_id=session)
    assert r2.success is True
    assert "Sakthi" in r2.message


@pytest.mark.asyncio
async def test_assistant_ollama_unavailable(assistant):
    """Assistant gracefully handles Ollama failure without raising an unhandled exception."""
    assistant.ai_provider.should_fail = True
    assistant.ai_provider.failure_message = "Connection refused at http://localhost:11434"

    response = await assistant.process("What is relativity?")

    assert response.success is False
    assert response.type == "error"
    assert response.tool is None
    assert "Connection refused" in response.message
    assert response.metadata.get("error_type") == "AI_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_assistant_empty_message(assistant):
    """Assistant rejects empty messages gracefully."""
    response = await assistant.process("   ")

    assert response.success is False
    assert response.type == "error"
    assert "cannot be empty" in response.message.lower()
