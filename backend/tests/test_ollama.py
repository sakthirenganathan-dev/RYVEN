"""Unit tests for OllamaProvider and AIProvider error handling."""

import pytest
import httpx
from app.ai.ollama import OllamaProvider, OllamaUnavailableError
from app.ai.provider import AIProvider, ChatMessage


def test_ai_provider_interface():
    """Verify that OllamaProvider conforms to AIProvider interface."""
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:7b")
    assert isinstance(provider, AIProvider)
    assert provider.provider_name == "ollama"
    assert provider.model == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_ollama_provider_success(monkeypatch):
    """Verify successful Ollama completion response parsing."""
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:7b")

    mock_response_data = {
        "model": "qwen2.5:7b",
        "message": {"role": "assistant", "content": "I am RYVEN. What are we working on?"},
        "done": True,
        "total_duration": 5000000,
        "eval_count": 12,
    }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None):
            req = httpx.Request("POST", url)
            return httpx.Response(200, json=mock_response_data, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    messages = [ChatMessage(role="user", content="Introduce yourself.")]
    response = await provider.generate(
        prompt="Introduce yourself.",
        system_prompt="You are RYVEN.",
        messages=messages,
    )

    assert response.content == "I am RYVEN. What are we working on?"
    assert response.model == "qwen2.5:7b"
    assert response.provider == "ollama"
    assert response.metadata["eval_count"] == 12


@pytest.mark.asyncio
async def test_ollama_provider_timeout(monkeypatch):
    """Verify clean exception handling on connection timeout."""
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:7b", timeout_seconds=1.0)

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None):
            raise httpx.TimeoutException("Request timed out")

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    with pytest.raises(OllamaUnavailableError, match="timed out"):
        await provider.generate(prompt="Hello")


@pytest.mark.asyncio
async def test_ollama_provider_unavailable(monkeypatch):
    """Verify clean exception handling when Ollama daemon is unreachable."""
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:7b")

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None):
            raise httpx.ConnectError("Connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    with pytest.raises(OllamaUnavailableError, match="offline|unreachable"):
        await provider.generate(prompt="Hello")


@pytest.mark.asyncio
async def test_ollama_missing_model(monkeypatch):
    """Verify clean error when requested model is not found (HTTP 404)."""
    provider = OllamaProvider(base_url="http://localhost:11434", model="nonexistent:model")

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None):
            req = httpx.Request("POST", url)
            return httpx.Response(404, json={"error": "model 'nonexistent:model' not found"}, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    with pytest.raises(OllamaUnavailableError, match="not available on Ollama server"):
        await provider.generate(prompt="Hello")


@pytest.mark.asyncio
async def test_ollama_empty_response(monkeypatch):
    """Verify error raised on empty content returned from Ollama."""
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:7b")

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None):
            req = httpx.Request("POST", url)
            return httpx.Response(200, json={"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "  "}}, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    with pytest.raises(OllamaUnavailableError, match="empty response"):
        await provider.generate(prompt="Hello")
