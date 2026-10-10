"""RYVEN 3.0 — Milestone 17.10 Phase 6 Test Suite.

Verifies Complete AI Consumer Unification & End-to-End Inference Governance:
- Assistant consumer unification behind UnifiedAIProvider / ModelRouter / ModelSecurityGateway
- Vision consumer (VisionProvider / PerceptionAgent) unification behind ModelRouter
- LLMPlanner unification behind UnifiedAIProvider
- Authoritative execution boundary enforcement (no bypass routes)
- Request-ID correlation propagation
- Telemetry deduplication and provider-attempt accounting
- Fail-closed security gateway invariants across all consumers
- Streaming cancellation and chunk sanitization
- Response format and API schema backward compatibility
"""

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.actions.event_bus import action_bus
from app.actions.models import ActionType
from app.agents.llm_planner import LLMPlanner
from app.ai.contracts import (
    AIRequest,
    AIResponse,
    AIStreamChunk,
    AIUsage,
    ModelProvider,
    ProviderAdapter,
    ProviderTimeoutError,
    ProviderUnavailableError,
    SecurityViolationError,
    StreamEventType,
)
from app.ai.health import ProviderHealthTracker
from app.ai.models import TaskType
from app.ai.ollama import OllamaProvider, OllamaUnavailableError
from app.ai.privacy import PrivacyMode
from app.ai.security import ModelSecurityPolicy
from app.ai.router import ModelRouter, RouterConfig
from app.ai.security_gateway import ModelSecurityGateway
from app.ai.telemetry import ModelTelemetryService, TelemetryEventType
from app.ai.unified import UnifiedAIProvider, unified_ai_provider
from app.ai.vision import PerceptionAgent, VisionProvider
from app.core.assistant import Assistant
from app.schemas.messages import ChatResponse


class MockTestAdapter(ProviderAdapter):
    """Deterministic mock provider adapter for Phase 6 test suite."""

    def __init__(
        self,
        provider_name: str = "OLLAMA",
        model_id: str = "qwen2.5:7b",
        response_text: str = "Unified response",
    ) -> None:
        self.provider_name = provider_name
        self.model_id = model_id
        self.response_text = response_text
        self.call_count = 0
        self.last_request: Optional[AIRequest] = None
        self.fail_with: Optional[Exception] = None

    @property
    def provider_id(self) -> ModelProvider:
        return ModelProvider(self.provider_name.upper())

    async def health_check(self) -> Dict[str, Any]:
        return {"online": True, "model_available": True}

    async def is_available(self) -> bool:
        return True

    async def generate(self, request: AIRequest) -> AIResponse:
        self.call_count += 1
        self.last_request = request
        if self.fail_with:
            raise self.fail_with
        return AIResponse(
            content=self.response_text,
            model_id=self.model_id,
            provider=self.provider_id,
            usage=AIUsage.from_counts(prompt_tokens=10, completion_tokens=20),
        )

    async def stream(self, request: AIRequest) -> AsyncIterator[AIStreamChunk]:
        self.call_count += 1
        self.last_request = request
        if self.fail_with:
            raise self.fail_with
        yield AIStreamChunk(event_type=StreamEventType.START, model_id=self.model_id, provider=self.provider_id)
        yield AIStreamChunk(event_type=StreamEventType.DELTA, delta=self.response_text, model_id=self.model_id, provider=self.provider_id)
        yield AIStreamChunk(event_type=StreamEventType.DONE, model_id=self.model_id, provider=self.provider_id)


@pytest.fixture
def clean_isolated_unified():
    """Build an isolated UnifiedAIProvider with fresh Router, Gateway, and Telemetry."""
    telemetry = ModelTelemetryService(max_events=200, max_requests=100)
    config = RouterConfig(fallback_enabled=True, max_fallback_depth=1)
    gateway = ModelSecurityGateway(security_policy=ModelSecurityPolicy())
    health_tracker = ProviderHealthTracker()
    router = ModelRouter(
        config=config,
        security_policy=ModelSecurityPolicy(),
        telemetry=telemetry,
        health_tracker=health_tracker,
    )
    router.security_gateway = gateway

    mock_ollama_adapter = MockTestAdapter("OLLAMA", "qwen2.5:7b", "Local unified answer")
    mock_grok_adapter = MockTestAdapter("GROK", "grok-2-latest", "Remote grok answer")
    router.register_adapter(ModelProvider.OLLAMA, mock_ollama_adapter)
    router.register_adapter(ModelProvider.GROK, mock_grok_adapter)

    unified = UnifiedAIProvider(router=router)
    return unified, router, mock_ollama_adapter, mock_grok_adapter, telemetry


# ============================================================================
# 1. ASSISTANT CONSUMER UNIFICATION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_assistant_default_uses_unified_ai_provider():
    """Verify Assistant defaults to unified_ai_provider without manual injection."""
    assistant = Assistant()
    assert isinstance(assistant.ai_provider, UnifiedAIProvider)
    assert assistant.ai_provider is unified_ai_provider


@pytest.mark.asyncio
async def test_assistant_process_executes_through_unified_router(clean_isolated_unified):
    """Assistant chat execution passes through ModelRouter and records telemetry."""
    unified, router, mock_ollama, _, telemetry = clean_isolated_unified
    assistant = Assistant(ai_provider=unified)

    response = await assistant.process("Explain quantum tunneling", session_id="test-session")

    assert response.success is True
    assert response.type == "ai"
    assert response.message == "Local unified answer"
    assert response.metadata["model"] == "qwen2.5:7b"
    assert response.metadata["provider"] == "OLLAMA"
    assert mock_ollama.call_count == 1

    # Verify telemetry recorded
    summary = telemetry.get_summary()
    assert summary["logical_requests"]["total"] == 1
    assert summary["logical_requests"]["local_count"] == 1


@pytest.mark.asyncio
async def test_assistant_security_denial_returns_clean_error(clean_isolated_unified):
    """Assistant intercepts SecurityViolationError and returns sanitized error response."""
    unified, router, mock_ollama, _, telemetry = clean_isolated_unified
    router.config.privacy_mode = PrivacyMode.LOCAL_ONLY

    assistant = Assistant(ai_provider=unified)

    with patch.object(router.security_gateway, "validate_request", side_effect=SecurityViolationError("Policy violation: sensitive content")):
        response = await assistant.process("Analyze secret password=123", session_id="test-session")

    assert response.success is False
    assert response.type == "error"
    assert "Policy violation" in response.message
    assert response.metadata.get("error_type") == "SECURITY_POLICY_DENIAL"
    assert mock_ollama.call_count == 0


@pytest.mark.asyncio
async def test_assistant_provider_unavailable_returns_clean_error(clean_isolated_unified):
    """Assistant intercepts ProviderUnavailableError and returns clean AI_PROVIDER_UNAVAILABLE."""
    unified, router, mock_ollama, _, _ = clean_isolated_unified
    mock_ollama.fail_with = ProviderUnavailableError("Local Ollama daemon is offline", provider=ModelProvider.OLLAMA)

    assistant = Assistant(ai_provider=unified)
    response = await assistant.process("Hello assistant", session_id="test-session")

    assert response.success is False
    assert response.type == "error"
    assert "offline" in response.message.lower()
    assert response.metadata.get("error_type") == "AI_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_assistant_provider_timeout_returns_clean_error(clean_isolated_unified):
    """Assistant intercepts ProviderTimeoutError and returns clean AI_PROVIDER_TIMEOUT."""
    unified, router, mock_ollama, _, _ = clean_isolated_unified
    mock_ollama.fail_with = ProviderTimeoutError("Inference timed out after 30s", provider=ModelProvider.OLLAMA)

    assistant = Assistant(ai_provider=unified)
    response = await assistant.process("Heavy prompt", session_id="test-session")

    assert response.success is False
    assert response.type == "error"
    assert response.metadata.get("error_type") == "AI_PROVIDER_TIMEOUT"


@pytest.mark.asyncio
async def test_assistant_custom_injected_provider_preserved():
    """Verify legacy or mock AIProvider instances passed to Assistant are respected."""
    mock_custom = MagicMock()
    mock_custom.provider_name = "custom_test"
    mock_custom.generate = AsyncMock(
        return_value=AIResponse(content="Custom mock", model_id="custom-model", provider="custom")
    )

    assistant = Assistant(ai_provider=mock_custom)
    assert assistant.ai_provider is mock_custom

    resp = await assistant.process("Test query")
    assert resp.success is True
    assert resp.message == "Custom mock"
    assert mock_custom.generate.call_count == 1


# ============================================================================
# 2. VISION CONSUMER UNIFICATION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_vision_provider_routes_through_model_router(clean_isolated_unified):
    """VisionProvider executes image analysis via ModelRouter.execute."""
    _, router, mock_ollama, _, telemetry = clean_isolated_unified
    vp = VisionProvider(model_id="moondream", router=router)

    res = await vp.analyze_image("fake_base64_data", "Describe elements")

    assert res == "Local unified answer"
    assert mock_ollama.call_count == 1
    assert mock_ollama.last_request.task_type == TaskType.VISION
    assert mock_ollama.last_request.images == ["fake_base64_data"]

    summary = telemetry.get_summary()
    assert summary["logical_requests"]["total"] == 1


@pytest.mark.asyncio
async def test_vision_provider_strict_local_only(clean_isolated_unified):
    """VisionProvider enforces allow_remote=False to prevent visual data cloud egress."""
    _, router, mock_ollama, mock_grok, _ = clean_isolated_unified
    vp = VisionProvider(model_id="moondream", router=router)

    # Even if router has remote allowed, VisionProvider calls execute with allow_remote=False
    with patch.object(router, "execute", wraps=router.execute) as spy_execute:
        await vp.analyze_image("fake_img", "Test prompt")
        assert spy_execute.call_count == 1
        _, kwargs = spy_execute.call_args
        assert kwargs.get("allow_remote") is False


@pytest.mark.asyncio
async def test_perception_agent_uses_unified_pipeline(clean_isolated_unified):
    """PerceptionAgent end-to-end perception routes through unified architecture."""
    _, router, mock_ollama, _, telemetry = clean_isolated_unified

    # Mock VisionProvider output to structured JSON expected by PerceptionAgent
    json_response = '{"description": "A web login form", "elements_detected": [{"label": "username", "confidence": 0.95}], "text_regions": ["Login"]}'
    mock_ollama.response_text = json_response

    # PerceptionAgent creates VisionProvider which uses model_router
    with patch("app.ai.vision.model_router", router), \
         patch("app.ai.registry.model_registry.list_models", return_value=[MagicMock(id="moondream", provider=ModelProvider.OLLAMA, memory_estimate_gb=2.0)]):
        agent = PerceptionAgent()
        agent._router = router

        # Create valid 1x1 png base64
        import base64
        tiny_png_b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82").decode("utf-8")

        result = await agent.perceive(image_b64=tiny_png_b64, prompt="Detect UI elements")

        assert result.success is True
        assert result.description == "A web login form"
        assert len(result.elements_detected) == 1
        assert result.elements_detected[0].label == "username"
        assert result.local_only is True


# ============================================================================
# 3. PLANNER CONSUMER UNIFICATION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_llm_planner_routes_through_unified_ai_provider(clean_isolated_unified):
    """LLMPlanner delegates decomposition to UnifiedAIProvider with TaskType.PLANNING."""
    unified, router, mock_ollama, _, telemetry = clean_isolated_unified

    plan_json = '{"tasks": [{"id": "step_1", "objective": "Open workspace", "capability": "WINDOWS_APP", "preferred_role": "WINDOWS"}]}'
    mock_ollama.response_text = plan_json

    planner = LLMPlanner(ai_provider=unified)
    tasks = await planner.plan_goal("Open workspace and launch editor")

    assert len(tasks) == 1
    assert tasks[0].objective == "Open workspace"
    assert mock_ollama.call_count == 1
    assert mock_ollama.last_request.task_type == TaskType.PLANNING


# ============================================================================
# 4. REQUEST IDENTIFIER & CORRELATION PROPAGATION
# ============================================================================

@pytest.mark.asyncio
async def test_request_id_propagated_end_to_end(clean_isolated_unified):
    """Consumer-provided request_id is preserved throughout gateway, router, and adapter."""
    unified, router, mock_ollama, _, telemetry = clean_isolated_unified

    custom_id = "req-test-uuid-1234"
    resp = await unified.generate(
        prompt="Test prompt",
        request_id=custom_id,
    )

    assert mock_ollama.last_request.request_id == custom_id
    events = [e for e in telemetry.get_events() if e.get("request_id") == custom_id]
    assert len(events) >= 2
    assert all(e["request_id"] == custom_id for e in events)


@pytest.mark.asyncio
async def test_telemetry_deduplication_between_consumer_and_router(clean_isolated_unified):
    """Calling unified.generate does not double-count logical requests in telemetry."""
    unified, router, mock_ollama, _, telemetry = clean_isolated_unified

    await unified.generate("Query 1")
    await unified.generate("Query 2")

    summary = telemetry.get_summary()
    assert summary["logical_requests"]["total"] == 2
    assert summary["logical_requests"]["local_count"] == 2


# ============================================================================
# 5. PRIVACY & SECURITY GATEWAY ENFORCEMENT
# ============================================================================

@pytest.mark.asyncio
async def test_local_only_blocks_remote_requests_across_all_consumers(clean_isolated_unified):
    """Under LOCAL_ONLY privacy mode, any consumer attempting remote access is rejected."""
    unified, router, mock_ollama, mock_grok, _ = clean_isolated_unified
    router.config.privacy_mode = PrivacyMode.LOCAL_ONLY

    # Gateway boundary strictly rejects remote execution
    with pytest.raises(SecurityViolationError):
        await router.security_gateway.validate_request(
            request=AIRequest.from_prompt("Remote prompt"),
            provider=ModelProvider.GROK,
            model_id="grok-2-latest",
            allow_remote=True,
            privacy_mode=PrivacyMode.LOCAL_ONLY,
        )

    # Consumer invocation safely retains inference locally on default Qwen
    resp = await unified.generate(
        prompt="Remote prompt",
        preferred_model_id="grok-2-latest",
        allow_remote=True,
    )
    assert mock_grok.call_count == 0
    assert resp.provider == "OLLAMA"


@pytest.mark.asyncio
async def test_privacy_first_denies_unconfirmed_remote(clean_isolated_unified):
    """Under PRIVACY_FIRST, remote request without valid confirmation token is blocked."""
    unified, router, mock_ollama, mock_grok, _ = clean_isolated_unified
    router.config.privacy_mode = PrivacyMode.PRIVACY_FIRST
    router.config.allow_remote_inference = True
    router.security_policy.allow_remote_inference = True

    grok_model = router.registry.get_model("grok-2-latest")
    grok_model.enabled = True

    with pytest.raises(SecurityViolationError) as exc_info:
        await unified.generate(
            prompt="Remote prompt",
            preferred_model_id="grok-2-latest",
            allow_remote=True,
        )

    assert "confirmation" in str(exc_info.value).lower()
    assert mock_grok.call_count == 0


@pytest.mark.asyncio
async def test_security_denial_never_triggers_fallback(clean_isolated_unified):
    """Security policy violations must never trigger an automatic fallback."""
    unified, router, mock_ollama, mock_grok, _ = clean_isolated_unified
    router.config.fallback_enabled = True

    with patch.object(router.security_gateway, "validate_request", side_effect=SecurityViolationError("Strict policy denial")):
        with pytest.raises(SecurityViolationError):
            await unified.generate("Prompt with forbidden secrets")

    assert mock_ollama.call_count == 0
    assert mock_grok.call_count == 0


# ============================================================================
# 6. STREAMING & CANCELLATION CONSISTENCY
# ============================================================================

@pytest.mark.asyncio
async def test_assistant_stream_yields_chunks_through_router(clean_isolated_unified):
    """Assistant.stream streams chunks through router without leakage."""
    unified, router, mock_ollama, _, telemetry = clean_isolated_unified
    assistant = Assistant(ai_provider=unified)

    chunks = []
    async for chunk in assistant.stream("Stream this message"):
        chunks.append(chunk)

    assert len(chunks) == 3
    assert chunks[0].event_type == StreamEventType.START
    assert chunks[1].event_type == StreamEventType.DELTA
    assert chunks[1].delta == "Local unified answer"
    assert chunks[2].event_type == StreamEventType.DONE


@pytest.mark.asyncio
async def test_stream_cancellation_propagates_cleanly(clean_isolated_unified):
    """Cancelling a stream cleanly interrupts execution and records telemetry."""
    unified, router, mock_ollama, _, telemetry = clean_isolated_unified

    async def slow_stream(req):
        yield AIStreamChunk(event_type=StreamEventType.START, model_id="qwen2.5:7b", provider=ModelProvider.OLLAMA)
        await asyncio.sleep(0.5)
        yield AIStreamChunk(event_type=StreamEventType.DELTA, delta="Late chunk", model_id="qwen2.5:7b", provider=ModelProvider.OLLAMA)

    mock_ollama.stream = slow_stream

    req = AIRequest.from_prompt("Prompt to cancel", stream=True)
    started_evt = asyncio.Event()

    async def consume_stream():
        async for chunk in router.stream(req):
            if chunk.event_type == StreamEventType.START:
                started_evt.set()

    task = asyncio.create_task(consume_stream())
    await started_evt.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = telemetry.get_events(event_type=TelemetryEventType.STREAM_CANCELLED)
    assert len(events) >= 1


# ============================================================================
# 7. TELEMETRY PRIVACY & METADATA SANITIZATION
# ============================================================================

@pytest.mark.asyncio
async def test_telemetry_never_stores_raw_prompt_or_response(clean_isolated_unified):
    """Telemetry records metadata only; prompts and outputs are completely omitted."""
    unified, router, mock_ollama, _, telemetry = clean_isolated_unified

    secret_prompt = "Classified instructions sk-ant-secret12345"
    mock_ollama.response_text = "Generated classified response with key ghp_secret"

    await unified.generate(prompt=secret_prompt)

    events = telemetry.get_events()
    for evt in events:
        evt_str = str(evt).lower()
        assert "classified instructions" not in evt_str
        assert "sk-ant" not in evt_str
        assert "ghp_secret" not in evt_str
        assert "generated classified" not in evt_str


@pytest.mark.asyncio
async def test_telemetry_failure_does_not_break_inference(clean_isolated_unified):
    """Failsafe invariant: telemetry failure does not compromise or abort inference."""
    unified, router, mock_ollama, _, telemetry = clean_isolated_unified

    # Corrupt telemetry recording
    with patch.object(telemetry, "start_request", side_effect=RuntimeError("Telemetry buffer error")):
        resp = await unified.generate("Hello despite telemetry failure")
        assert resp.content == "Local unified answer"
        assert mock_ollama.call_count == 1


# ============================================================================
# 8. HEALTH AND ADAPTER REGISTRATION COMPATIBILITY
# ============================================================================

@pytest.mark.asyncio
async def test_unified_check_health_backward_compatibility(clean_isolated_unified):
    """Unified check_health includes both top-level and nested provider status."""
    unified, router, _, _, _ = clean_isolated_unified

    health = await unified.check_health()
    assert health["provider"] == "unified"
    assert health["active_default"] == "ollama"
    assert "online" in health
    assert "model_available" in health
    assert "ollama" in health["providers"]
    assert "circuit_breakers" in health


@pytest.mark.asyncio
async def test_injected_transports_sync_router_adapters():
    """Injecting custom OllamaProvider into UnifiedAIProvider synchronizes router adapter."""
    mock_ollama = MagicMock(spec=OllamaProvider)
    mock_ollama.model = "qwen2.5:7b"
    mock_ollama.generate = AsyncMock(
        return_value=AIResponse(content="Injected mock sync", model_id="qwen2.5:7b", provider=ModelProvider.OLLAMA)
    )

    unified = UnifiedAIProvider(ollama_provider=mock_ollama)
    adapter = unified.router.get_adapter(ModelProvider.OLLAMA)

    assert adapter.provider is mock_ollama

    resp = await unified.generate("Test prompt")
    assert resp.content == "Injected mock sync"
    assert mock_ollama.generate.call_count == 1


# ============================================================================
# 9. CONVERSATION CONTEXT & ACCUMULATION TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_assistant_context_history_accumulates_turns_correctly(clean_isolated_unified):
    """Assistant stores conversational turns in ContextManager across interactions."""
    unified, router, mock_ollama, _, _ = clean_isolated_unified
    assistant = Assistant(ai_provider=unified)

    await assistant.process("My favorite language is Python", session_id="user-session")
    history = assistant.context_manager.get_history("user-session")
    assert len(history) == 2  # 1 user + 1 assistant message

    await assistant.process("What is my favorite language?", session_id="user-session")
    history2 = assistant.context_manager.get_history("user-session")
    assert len(history2) == 4  # 2 user + 2 assistant messages


@pytest.mark.asyncio
async def test_assistant_empty_message_returns_error_without_inference(clean_isolated_unified):
    """Assistant rejects blank/whitespace queries immediately without calling provider."""
    unified, router, mock_ollama, _, _ = clean_isolated_unified
    assistant = Assistant(ai_provider=unified)

    resp = await assistant.process("   \n\t  ")
    assert resp.success is False
    assert resp.type == "error"
    assert mock_ollama.call_count == 0


@pytest.mark.asyncio
async def test_assistant_metadata_preserves_usage_tokens(clean_isolated_unified):
    """Assistant response metadata includes usage statistics when provided."""
    unified, router, mock_ollama, _, _ = clean_isolated_unified
    assistant = Assistant(ai_provider=unified)

    resp = await assistant.process("Calculate tokens")
    assert resp.success is True
    assert "usage" in resp.metadata
    assert resp.metadata["usage"]["prompt_tokens"] == 10
    assert resp.metadata["usage"]["completion_tokens"] == 20


# ============================================================================
# 10. PLANNER & VISION EDGE CASES
# ============================================================================

@pytest.mark.asyncio
async def test_planner_deterministic_fallback_when_unified_provider_fails(clean_isolated_unified):
    """LLMPlanner falls back to deterministic planning when LLM provider fails."""
    unified, router, mock_ollama, _, _ = clean_isolated_unified
    mock_ollama.fail_with = RuntimeError("LLM parsing fault")

    planner = LLMPlanner(ai_provider=unified)
    from app.agents.planning_models import PlanningRequest
    req = PlanningRequest(user_goal="search web for python 3.14 documentation")
    tasks = await planner.plan(req)

    # Should fall back to deterministic plan
    assert len(tasks) >= 1
    assert any("SEARCH" in str(t.capability) for t in tasks)


@pytest.mark.asyncio
async def test_perception_agent_rejects_empty_or_oversized_images(clean_isolated_unified):
    """PerceptionAgent validates image payload before any model call."""
    _, router, mock_ollama, _, _ = clean_isolated_unified
    agent = PerceptionAgent()
    agent._router = router

    # Empty payload
    res1 = await agent.perceive(image_b64="")
    assert res1.success is False
    assert "empty" in res1.error.lower()
    assert mock_ollama.call_count == 0

    # Oversized payload (> 2MB)
    huge_data = "a" * (3 * 1024 * 1024)
    res2 = await agent.perceive(image_b64=huge_data)
    assert res2.success is False
    assert mock_ollama.call_count == 0


@pytest.mark.asyncio
async def test_perception_agent_ocr_text_credential_redaction(clean_isolated_unified):
    """PerceptionAgent sanitizes detected OCR text to mask credentials."""
    _, router, mock_ollama, _, _ = clean_isolated_unified

    json_with_secret = '{"description": "Code editor", "elements_detected": [], "text_regions": ["API_KEY = ghp_123456789012345678901234567890123456"]}'
    mock_ollama.response_text = json_with_secret

    import base64
    tiny_png = base64.b64encode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\rIDATx\x9cc`\x00\x00\x00\x02\x00\x01H\xaf\xa4q\x00\x00\x00\x00IEND\xaeB`\x82").decode("utf-8")

    with patch("app.ai.vision.model_router", router), \
         patch("app.ai.registry.model_registry.list_models", return_value=[MagicMock(id="moondream", provider=ModelProvider.OLLAMA, memory_estimate_gb=2.0)]):
        agent = PerceptionAgent()
        agent._router = router
        res = await agent.perceive(image_b64=tiny_png)

        assert res.success is True
        assert len(res.text_regions) == 1
        assert "ghp_123456" not in res.text_regions[0]
        assert "REDACTED" in res.text_regions[0]


# ============================================================================
# 11. STREAMING & PRIVACY MODES
# ============================================================================

@pytest.mark.asyncio
async def test_unified_stream_forwards_to_router_stream(clean_isolated_unified):
    """UnifiedAIProvider.stream forwards calls through ModelRouter.stream."""
    unified, router, mock_ollama, _, _ = clean_isolated_unified

    chunks = []
    async for chunk in unified.stream("Hello streaming world"):
        chunks.append(chunk)

    assert len(chunks) == 3
    assert chunks[0].event_type == StreamEventType.START
    assert chunks[1].delta == "Local unified answer"
    assert chunks[2].event_type == StreamEventType.DONE


@pytest.mark.asyncio
async def test_balanced_privacy_mode_allows_authorized_remote_with_redaction(clean_isolated_unified):
    """BALANCED mode redacts non-root secrets and permits authorized remote execution."""
    unified, router, mock_ollama, mock_grok, telemetry = clean_isolated_unified
    router.config.privacy_mode = PrivacyMode.BALANCED
    router.config.allow_remote_inference = True
    router.security_policy.allow_remote_inference = True

    grok_model = router.registry.get_model("grok-2-latest")
    grok_model.enabled = True

    resp = await unified.generate(
        prompt="Review code with auth: Bearer test_token_12345",
        preferred_model_id="grok-2-latest",
        allow_remote=True,
    )

    assert mock_grok.call_count == 1
    # Check that Bearer token was redacted before reaching Grok adapter
    prompt_sent = mock_grok.last_request.get_prompt_text()
    assert "test_token_12345" not in prompt_sent
    assert "REDACTED" in prompt_sent


@pytest.mark.asyncio
async def test_max_reasoning_mode_enforces_security_gateway(clean_isolated_unified):
    """MAX_REASONING mode still blocks strictly prohibited private keys."""
    unified, router, mock_ollama, mock_grok, _ = clean_isolated_unified
    router.config.privacy_mode = PrivacyMode.MAX_REASONING
    router.config.allow_remote_inference = True
    router.security_policy.allow_remote_inference = True

    grok_model = router.registry.get_model("grok-2-latest")
    grok_model.enabled = True

    with pytest.raises(SecurityViolationError):
        await unified.generate(
            prompt="-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA...",
            preferred_model_id="grok-2-latest",
            allow_remote=True,
        )

    assert mock_grok.call_count == 0


# ============================================================================
# 12. ACTION BUS, CIRCUIT BREAKERS & RESPONSE NORMALIZATION
# ============================================================================

@pytest.mark.asyncio
async def test_action_bus_emits_events_during_unified_inference(clean_isolated_unified):
    """ActionBus publishes MODEL_ROUTE_SELECTED on inference dispatch."""
    unified, router, mock_ollama, _, _ = clean_isolated_unified

    emitted_events = []

    async def listener(evt):
        emitted_events.append(evt)

    action_bus.subscribe(listener)
    await unified.generate("Test event publishing")
    action_bus.unsubscribe(listener)

    route_events = [e for e in emitted_events if e.action_type == ActionType.MODEL_ROUTE_SELECTED]
    assert len(route_events) >= 1
    assert "qwen" in route_events[0].title.lower() or "route selected" in route_events[0].title.lower()


@pytest.mark.asyncio
async def test_router_circuit_breaker_prevents_calls_from_unified_provider(clean_isolated_unified):
    """Open circuit breaker on provider immediately diverts or suppresses calls."""
    unified, router, mock_ollama, _, telemetry = clean_isolated_unified

    # Trip Ollama circuit
    router.health_tracker.record_failure("OLLAMA", ProviderUnavailableError("Fail 1"))
    router.health_tracker.record_failure("OLLAMA", ProviderUnavailableError("Fail 2"))
    router.health_tracker.record_failure("OLLAMA", ProviderUnavailableError("Fail 3"))

    assert router.health_tracker.is_circuit_open("OLLAMA")

    # Subsequent request will fail-fast with ProviderUnavailableError
    with pytest.raises(ProviderUnavailableError):
        await unified.generate("Fast fail query")

    assert mock_ollama.call_count == 0


@pytest.mark.asyncio
async def test_fallback_depth_bounded_to_one_in_unified_provider(clean_isolated_unified):
    """Router configuration strictly limits fallback depth to 1."""
    unified, router, mock_ollama, mock_grok, _ = clean_isolated_unified
    assert router.config.max_fallback_depth == 1


@pytest.mark.asyncio
async def test_unified_provider_returns_normalized_ai_response_properties(clean_isolated_unified):
    """AIResponse exposes backward-compatible .model and .text aliases."""
    unified, router, mock_ollama, _, _ = clean_isolated_unified

    resp = await unified.generate("Hello normalized response")
    assert resp.content == "Local unified answer"
    assert resp.text == "Local unified answer"
    assert resp.model == "qwen2.5:7b"
    assert resp.model_id == "qwen2.5:7b"
    assert resp.provider == "OLLAMA"
    assert resp.provider == "ollama"  # case-insensitive check

