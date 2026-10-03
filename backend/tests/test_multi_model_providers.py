"""Comprehensive tests for RYVEN 3.0 M15.1 Phase 2 Multi-Model Providers & Routing."""

import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock

from app.actions.event_bus import action_bus
from app.actions.models import ActionType
from app.ai.grok import GrokProvider, GrokUnavailableError
from app.ai.hf_local import HuggingFaceLocalProvider, HuggingFaceLocalUnavailableError
from app.ai.hf_remote import HuggingFaceRemoteProvider, HuggingFaceRemoteUnavailableError
from app.ai.models import TaskType
from app.ai.ollama import OllamaProvider
from app.ai.provider import AIResponse
from app.ai.router import ModelRouter
from app.ai.security import ModelSecurityPolicy, ModelSecurityViolationError
from app.ai.unified import UnifiedAIProvider
from app.core.assistant import Assistant


@pytest.mark.asyncio
async def test_ollama_default_routing():
    """Verify Ollama Qwen 2.5 7B remains the default local reasoning and planning provider."""
    unified = UnifiedAIProvider()
    health = await unified.check_health()

    assert health["active_default"] == "ollama"
    assert "qwen" in health["active_default_model"].lower()
    assert health["providers"]["ollama"]["provider"] == "ollama"


@pytest.mark.asyncio
async def test_grok_missing_api_key_handling():
    """Verify Grok provider cleanly reports unavailable when GROK_API_KEY is not configured."""
    grok = GrokProvider(api_key=None)
    health = await grok.check_health()

    assert health["configured"] is False
    assert health["available"] is False
    assert "not configured" in health["error"]

    # Calling generate directly without API key raises GrokUnavailableError without crash
    with pytest.raises(GrokUnavailableError) as exc_info:
        await grok.generate("Hello")
    assert "not configured" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_grok_mocked_generation():
    """Verify Grok provider generates normalized AIResponse with mocked HTTP response."""
    grok = GrokProvider(api_key="xai-test-key-12345")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "model": "grok-2-latest",
        "choices": [{"message": {"content": "Hello from Grok!"}}],
        "usage": {"total_tokens": 42},
    }

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp)):
        res = await grok.generate("Explain recursion")
        assert isinstance(res, AIResponse)
        assert res.content == "Hello from Grok!"
        assert res.provider == "grok"
        assert res.model == "grok-2-latest"


@pytest.mark.asyncio
async def test_hf_remote_disabled_by_default():
    """Verify Hugging Face remote provider reports disabled when allow_remote is false."""
    security = ModelSecurityPolicy(allow_remote_inference=False)
    hf = HuggingFaceRemoteProvider(api_key="hf_test_token_12345", security_policy=security)

    health = await hf.check_health()
    assert health["configured"] is True
    assert health["available"] is False
    assert "disabled" in health["error"].lower()

    with pytest.raises(HuggingFaceRemoteUnavailableError) as exc_info:
        await hf.generate("Translate to French")
    assert "disabled" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_secret_leakage_blocked_for_remote_providers():
    """Verify secrets and .env files are blocked before transmission to Grok or HF."""
    security = ModelSecurityPolicy(allow_remote_inference=True)
    grok = GrokProvider(api_key="xai-test-key", security_policy=security)
    hf = HuggingFaceRemoteProvider(api_key="hf_test_token", security_policy=security)

    leak_prompt = "Review this config: ghp_123456789012345678901234567890123456 and .env"

    with pytest.raises(GrokUnavailableError) as g_exc:
        await grok.generate(leak_prompt)
    assert "security policy blocked" in str(g_exc.value).lower()

    with pytest.raises(HuggingFaceRemoteUnavailableError) as hf_exc:
        await hf.generate(leak_prompt)
    assert "security policy blocked" in str(hf_exc.value).lower() or "sensitive" in str(hf_exc.value).lower()


@pytest.mark.asyncio
async def test_local_hf_unavailable_when_not_cached():
    """Verify Local Hugging Face provider returns controlled error when weights are not downloaded."""
    local_hf = HuggingFaceLocalProvider(model_name="Qwen/Qwen2.5-Coder-1.5B-Instruct")
    health = await local_hf.check_health()

    assert health["provider"] == "huggingface_local"
    assert health["local"] is True

    # Generation should fail with a clean message that weights are not cached locally
    with pytest.raises(HuggingFaceLocalUnavailableError) as exc_info:
        await local_hf.generate("Write a function")
    assert "not downloaded" in str(exc_info.value).lower() or "not cached" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_fallback_chain_from_failed_remote_to_ollama():
    """Verify that when a remote provider fails, the unified engine falls back to local Ollama."""
    mock_ollama = MagicMock(spec=OllamaProvider)
    mock_ollama.model = "qwen2.5:7b"
    mock_ollama.generate = AsyncMock(
        return_value=AIResponse(
            content="Fallback answer from local Qwen 2.5 7B",
            model="qwen2.5:7b",
            provider="ollama",
        )
    )

    # Grok configured to fail
    failing_grok = GrokProvider(api_key="xai-key")
    failing_grok.generate = AsyncMock(side_effect=GrokUnavailableError("Grok service connection timeout"))

    unified = UnifiedAIProvider(
        ollama_provider=mock_ollama,
        grok_provider=failing_grok,
    )

    # Force preference to grok
    res = await unified.generate(
        prompt="Tell me about gravity",
        task_type=TaskType.GENERAL_REASONING,
        preferred_model_id="grok-2-latest",
        allow_remote=True,
    )

    # Must fall back successfully to Ollama
    assert res.provider == "ollama"
    assert "local Qwen" in res.content
    assert mock_ollama.generate.called


@pytest.mark.asyncio
async def test_action_events_emitted_during_routing():
    """Verify MODEL_ROUTE_SELECTED events are published during routing."""
    events = []

    async def listener(evt):
        events.append(evt)

    action_bus.subscribe(listener)

    mock_ollama = MagicMock(spec=OllamaProvider)
    mock_ollama.model = "qwen2.5:7b"
    mock_ollama.generate = AsyncMock(
        return_value=AIResponse(content="Response", model="qwen2.5:7b", provider="ollama")
    )

    unified = UnifiedAIProvider(ollama_provider=mock_ollama)
    await unified.generate("Plan a trip to Mars", task_type=TaskType.PLANNING)

    action_bus.unsubscribe(listener)

    route_events = [e for e in events if e.action_type == ActionType.MODEL_ROUTE_SELECTED]
    assert len(route_events) >= 1
    assert "qwen" in route_events[0].title.lower() or "route selected" in route_events[0].title.lower()


@pytest.mark.asyncio
async def test_assistant_regression_backward_compatibility():
    """Verify Assistant initialization and default chat flow remain completely operational."""
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.provider_name = "ollama"
    mock_provider.model = "qwen2.5:7b"
    mock_provider.generate = AsyncMock(
        return_value=AIResponse(content="Hello! I am RYVEN.", model="qwen2.5:7b", provider="ollama")
    )

    assistant = Assistant(ai_provider=mock_provider)
    resp = await assistant.process("Hi there", session_id="test-session")

    assert resp.success is True
    assert resp.type == "ai"
    assert resp.message == "Hello! I am RYVEN."
    assert resp.metadata.get("provider") == "ollama"
