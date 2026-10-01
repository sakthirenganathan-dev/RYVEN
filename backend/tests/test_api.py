"""API integration tests for FastAPI endpoints."""

from typing import Any, Dict, List, Optional
import pytest
from httpx import ASGITransport, AsyncClient
from app.ai.ollama import OllamaUnavailableError
from app.ai.provider import AIProvider, AIResponse, ChatMessage
from app.core.assistant import Assistant
from app.core.context import ConversationManager
from app.main import create_app
from app.tools.registry import ToolRegistry
from app.tools.system_tool import SystemStatusTool
from app.tools.time_tool import TimeTool


class MockAIProvider(AIProvider):
    provider_name: str = "mock_ollama"

    def __init__(self):
        self.should_fail = False

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        **kwargs: Any,
    ) -> AIResponse:
        if self.should_fail:
            raise OllamaUnavailableError("RYVEN AI engine is currently offline: Ollama service is unreachable.")

        # Check for context recall in messages
        history_str = " ".join([m.content for m in (messages or [])])
        if "name is Sakthi" in history_str and "name" in prompt.lower():
            return AIResponse(
                content="Your name is Sakthi.",
                model="qwen2.5:7b",
                provider=self.provider_name,
                metadata={"context_recalled": True},
            )

        return AIResponse(
            content=f"AI answer to: {prompt}",
            model="qwen2.5:7b",
            provider=self.provider_name,
            metadata={"mocked": True},
        )

    async def check_health(self) -> Dict[str, Any]:
        return {
            "online": not self.should_fail,
            "version": "0.35.0",
            "model_available": True,
            "configured_model": "qwen2.5:7b",
        }


@pytest.fixture
def test_app():
    app = create_app()
    registry = ToolRegistry()
    registry.register(TimeTool())
    registry.register(SystemStatusTool())
    mock_ai = MockAIProvider()
    context_mgr = ConversationManager()
    app.state.assistant = Assistant(
        registry=registry, ai_provider=mock_ai, context_manager=context_mgr
    )
    return app


@pytest.mark.asyncio
async def test_health_endpoint(test_app):
    """GET /api/health returns 200 with registered tools, Ollama status, and model info."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == "RYVEN Backend"
    assert data["backend"] == "online"
    assert data["ollama"] == "online"
    assert data["model"] == "qwen2.5:7b"
    assert data["model_available"] is True
    assert "time" in data["registered_tools"]
    assert "system_status" in data["registered_tools"]
    assert data["ai_provider"] == "mock_ollama"


@pytest.mark.asyncio
async def test_system_status_endpoint(test_app):
    """GET /api/system/status returns 200 with valid hardware telemetry."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/system/status")

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "data" in data
    assert "cpu_percent" in data["data"]
    assert "ram_percent" in data["data"]
    assert "disk" in data["data"]
    assert "battery" in data["data"]


@pytest.mark.asyncio
async def test_chat_endpoint_tool(test_app):
    """POST /api/chat with a time question executes TimeTool."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/chat", json={"message": "What time is it?"})

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["type"] == "tool"
    assert data["tool"] == "time"
    assert "It is " in data["message"]


@pytest.mark.asyncio
async def test_chat_endpoint_ai_with_context(test_app):
    """POST /api/chat maintains conversation context across requests with session_id."""
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Turn 1
        r1 = await client.post(
            "/api/chat",
            json={"message": "My name is Sakthi.", "session_id": "api_session_1"},
        )
        assert r1.status_code == 200
        assert r1.json()["success"] is True

        # Turn 2
        r2 = await client.post(
            "/api/chat",
            json={"message": "What is my name?", "session_id": "api_session_1"},
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["success"] is True
        assert d2["type"] == "ai"
        assert "Sakthi" in d2["message"]


@pytest.mark.asyncio
async def test_chat_endpoint_ollama_unavailable(test_app):
    """POST /api/chat returns structured error when Ollama is unavailable."""
    test_app.state.assistant.ai_provider.should_fail = True

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/chat", json={"message": "Can you hear me?"})

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["type"] == "error"
    assert "offline" in data["message"].lower() or "unreachable" in data["message"].lower()
    assert data["metadata"]["error_type"] == "AI_PROVIDER_UNAVAILABLE"
