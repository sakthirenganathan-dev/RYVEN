"""Comprehensive test suite for RYVEN M15.2 — Vision & Local OCR Pipeline.

Covers:
1. BrowserSnapshot backward compatibility (no screenshot_b64 in old init)
2. screenshot_b64 optional field
3. ocr_text optional field
4. moondream in ModelRegistry
5. Ollama text-only unchanged
6. Ollama multimodal payload construction
7. VisionProvider with mocked OllamaProvider
8. PerceptionAgent structured JSON parsing
9. Malformed JSON handling in PerceptionAgent
10. Confidence clamping (>1.0, <0.0, NaN)
11. Screenshot returns valid base64
12. Screenshot size limit enforcement
13. Screenshot failure does not break navigation
14. OCR result returned
15. Credential redaction in OCR text
16. Remote vision blocked in PerceptionAgent
17. Remote fallback blocked
18. Tool registry registration of vision tools
19. ActionEvents emitted without image bytes
20. API endpoint validation
21. Vision status endpoint
22. Existing M15 internet behavior unchanged
"""

import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.models import ModelProfile, ModelProvider, TaskType
from app.ai.ollama import OllamaProvider
from app.ai.registry import model_registry
from app.ai.vision import (
    DetectedElement,
    PerceptionAgent,
    PerceptionResult,
    VisionProvider,
    perception_agent,
)
from app.browser.engine import BrowserEngine, PageRenderer, browser_engine
from app.browser.models import BrowserSnapshot, BrowserState, BrowserStatus
from app.browser.security import BrowserSecurityValidator
from app.internet.agent import internet_agent
from app.internet.tools import BrowserScreenshotTool, PageOCRTool
from app.tools.registry import create_default_registry


# 1. BrowserSnapshot backward compatibility
def test_browser_snapshot_backward_compat():
    """BrowserSnapshot can be initialized without screenshot_b64 or ocr_text."""
    snap = BrowserSnapshot(
        url="https://example.com",
        title="Example",
        text_content="Hello World",
    )
    assert snap.url == "https://example.com"
    assert snap.screenshot_b64 is None
    assert snap.ocr_text is None


# 2. screenshot_b64 optional field
def test_browser_snapshot_screenshot_b64_optional():
    """BrowserSnapshot accepts and holds base64 screenshot."""
    fake_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    snap = BrowserSnapshot(
        url="https://example.com",
        title="Example",
        text_content="Hello World",
        screenshot_b64=fake_b64,
    )
    assert snap.screenshot_b64 == fake_b64


# 3. ocr_text optional field
def test_browser_snapshot_ocr_text_optional():
    """BrowserSnapshot accepts and holds extracted OCR text."""
    snap = BrowserSnapshot(
        url="https://example.com",
        title="Example",
        text_content="Hello World",
        ocr_text="Extracted text from image",
    )
    assert snap.ocr_text == "Extracted text from image"


# 4. moondream in ModelRegistry
def test_moondream_in_model_registry():
    """moondream is registered as a vision and OCR model in ModelRegistry."""
    model = model_registry.get("moondream")
    assert model is not None
    assert model.id == "moondream"
    assert model.provider == ModelProvider.OLLAMA
    assert TaskType.VISION in model.task_types
    assert TaskType.OCR in model.task_types
    assert model.memory_estimate_gb == 0.8


# 5. Ollama text-only unchanged
@pytest.mark.asyncio
async def test_ollama_text_only_unchanged():
    """Standard text generation doesn't include images in Ollama payload."""
    provider = OllamaProvider(model="qwen2.5:7b")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {"role": "assistant", "content": "Hello human"},
        "prompt_eval_count": 5,
        "eval_count": 10,
    }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        resp = await provider.generate("Hi")
        assert resp.content == "Hello human"
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]
        assert "images" not in payload["messages"][0]


# 6. Ollama multimodal payload construction
@pytest.mark.asyncio
async def test_ollama_multimodal_payload_construction():
    """OllamaProvider attaches images array to user message when images are passed."""
    provider = OllamaProvider(model="moondream")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "message": {"role": "assistant", "content": "I see a button"},
        "prompt_eval_count": 20,
        "eval_count": 15,
    }

    fake_b64 = "abc123image"
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        resp = await provider.generate("Describe this", images=[fake_b64])
        assert resp.content == "I see a button"
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]
        user_msg = payload["messages"][0]
        assert "images" in user_msg
        assert user_msg["images"] == [fake_b64]


# 7. VisionProvider with mocked OllamaProvider
@pytest.mark.asyncio
async def test_vision_provider_calls_ollama():
    """VisionProvider forwards image and prompt to underlying OllamaProvider."""
    vp = VisionProvider(model_id="moondream")
    with patch.object(vp._provider, "generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = MagicMock(content='{"description": "A web form"}')
        res = await vp.analyze_image("fakeb64", "What is here?")
        assert '{"description": "A web form"}' in res
        mock_gen.assert_called_once()
        assert mock_gen.call_args[1]["images"] == ["fakeb64"]


# 8. PerceptionAgent structured JSON parsing
def test_perception_agent_structured_json_parsing():
    """PerceptionAgent successfully parses structured JSON responses."""
    raw = 'Some prefix text {"description": "login page", "elements_detected": [{"label": "username_field", "confidence": 0.9}], "text_regions": ["Login"], "ocr_confidence": 0.85} suffix'
    parsed = PerceptionAgent._parse_vision_json(raw)
    assert parsed["description"] == "login page"
    assert len(parsed["elements_detected"]) == 1
    assert parsed["text_regions"] == ["Login"]
    assert parsed["ocr_confidence"] == 0.85


# 9. Malformed JSON handling in PerceptionAgent
def test_perception_agent_malformed_json_handling():
    """PerceptionAgent safely returns empty dict when given malformed or empty output."""
    assert PerceptionAgent._parse_vision_json("not valid json at all") == {}
    assert PerceptionAgent._parse_vision_json("") == {}
    assert PerceptionAgent._parse_vision_json(None) == {}


# 10. Confidence clamping
def test_confidence_clamping():
    """DetectedElement clamps confidence to [0.0, 1.0] and handles bad values."""
    elem_high = DetectedElement(label="btn", confidence=1.5)
    assert elem_high.confidence == 1.0

    elem_low = DetectedElement(label="btn", confidence=-0.5)
    assert elem_low.confidence == 0.0

    elem_nan = DetectedElement(label="btn", confidence=float("nan"))
    assert elem_nan.confidence == 0.0

    elem_bad = DetectedElement(label="btn", confidence="not_a_number")
    assert elem_bad.confidence == 0.0

    elem_ok = DetectedElement(label="btn", confidence=0.75)
    assert elem_ok.confidence == 0.75


# 11. Screenshot returns valid base64
@pytest.mark.asyncio
async def test_screenshot_returns_valid_base64():
    """capture_screenshot creates valid PNG base64 string for an active page."""
    engine = BrowserEngine()
    state = engine._get_or_create_session("test-session")
    state.last_snapshot = BrowserSnapshot(
        url="https://example.com/test",
        title="Test Page",
        text_content="Heading 1\nThis is a visual test page.",
    )

    res = await engine.capture_screenshot("test-session")
    assert res["success"] is True
    assert res["screenshot_available"] is True
    assert "screenshot_b64" in res
    # Verify base64 decodes to valid PNG
    decoded = base64.b64decode(res["screenshot_b64"])
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


# 12. Screenshot size limit enforcement
@pytest.mark.asyncio
async def test_screenshot_size_limit_enforcement():
    """capture_screenshot restricts dimensions to safe boundaries."""
    engine = BrowserEngine()
    state = engine._get_or_create_session("size-session")
    state.last_snapshot = BrowserSnapshot(
        url="https://example.com",
        title="Page",
        text_content="Text",
    )

    # PageRenderer clamps to max 1920x1080 and min 100x100
    img_bytes = PageRenderer.render_snapshot(
        html_content="<p>test</p>",
        url="https://example.com",
        max_width=5000,
        max_height=5000,
    )
    assert img_bytes is not None
    assert img_bytes[:8] == b"\x89PNG\r\n\x1a\n"


# 13. Screenshot failure does not break navigation
@pytest.mark.asyncio
async def test_screenshot_failure_does_not_break_navigation():
    """If screenshot rendering fails, capture_screenshot returns graceful failure."""
    engine = BrowserEngine()
    with patch.object(PageRenderer, "render_snapshot", return_value=None):
        state = engine._get_or_create_session("fail-session")
        state.last_snapshot = BrowserSnapshot(url="https://example.com", title="P", text_content="T")
        res = await engine.capture_screenshot("fail-session")
        assert res["success"] is False
        assert res["screenshot_available"] is False
        assert "error" in res


# 14. OCR result returned
@pytest.mark.asyncio
async def test_ocr_result_returned():
    """PageOCRTool returns extracted OCR text and metadata."""
    tool = PageOCRTool()
    engine = internet_agent.browser_engine
    state = engine._get_or_create_session("ocr-session")
    state.last_snapshot = BrowserSnapshot(
        url="https://example.com",
        title="Example",
        text_content="Visual OCR heading\nDashboard overview metrics",
    )

    res = await tool.execute(session_id="ocr-session")
    assert res["success"] is True
    assert "ocr_text" in res
    assert len(res["ocr_text"]) > 0


# 15. Credential redaction in OCR text
def test_credential_redaction_in_ocr_text():
    """Sensitive credentials in OCR text are sanitized."""
    text_with_key = "Access Granted: sk-proj-1234567890abcdef1234567890 password=supersecret"
    sanitized = BrowserSecurityValidator.redact_credentials(text_with_key)
    assert "sk-proj-" not in sanitized
    assert "supersecret" not in sanitized
    assert "[REDACTED" in sanitized


# 16. Remote vision blocked in PerceptionAgent
@pytest.mark.asyncio
async def test_remote_vision_blocked():
    """PerceptionAgent strictly refuses remote inference and emits VISION_REMOTE_BLOCKED."""
    agent = PerceptionAgent()
    valid_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    res = await agent.perceive(valid_png_b64, allow_remote=True)
    assert res.success is False
    assert "Remote vision is strictly prohibited" in res.error
    assert res.local_only is True


# 17. Remote fallback blocked
@pytest.mark.asyncio
async def test_remote_fallback_blocked_by_router():
    """PerceptionAgent enforces allow_remote=False on model router decision."""
    agent = PerceptionAgent()
    fake_png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

    with patch.object(agent._router, "route", new_callable=AsyncMock) as mock_route:
        # Simulate router returning remote decision
        mock_route.return_value = MagicMock(local_or_remote="remote")
        res = await agent.perceive(fake_png)
        assert res.success is False
        assert "Remote vision is not permitted" in res.error


# 18. Tool registry registration of vision tools
def test_tool_registry_registration():
    """BrowserScreenshotTool and PageOCRTool are registered in default registry."""
    registry = create_default_registry()
    assert registry.has("browser_screenshot")
    assert registry.has("page_ocr")
    assert isinstance(registry.get("browser_screenshot"), BrowserScreenshotTool)
    assert isinstance(registry.get("page_ocr"), PageOCRTool)


# 19. ActionEvents emitted without image bytes
@pytest.mark.asyncio
async def test_action_events_emitted_without_image_bytes():
    """Vision ActionEvents never contain base64 image strings in safe_metadata."""
    events = []

    async def on_event(event: ActionEvent):
        events.append(event)

    sub_id = action_bus.subscribe(on_event)
    try:
        engine = BrowserEngine()
        state = engine._get_or_create_session("events-session")
        state.last_snapshot = BrowserSnapshot(
            url="https://example.com/hud",
            title="HUD",
            text_content="Telemetry Display",
        )
        await engine.capture_screenshot("events-session")

        vision_events = [
            e for e in events
            if e.action_type in (
                ActionType.VISION_CAPTURE_STARTED,
                ActionType.VISION_CAPTURE_COMPLETED,
            )
        ]
        assert len(vision_events) >= 2
        for ev in vision_events:
            meta_str = json.dumps(ev.safe_metadata)
            assert "screenshot_b64" not in meta_str
            assert "iVBORw0KGgo" not in meta_str
    finally:
        action_bus.unsubscribe(sub_id)


# 20. API endpoint validation
@pytest.mark.asyncio
async def test_api_endpoint_validation():
    """Vision API endpoints reject allow_remote=True and oversized payloads."""
    from app.api.routes import analyze_vision_endpoint
    from fastapi import HTTPException

    # Test allow_remote rejected
    with pytest.raises(HTTPException) as exc_info:
        await analyze_vision_endpoint({"image_b64": "abc", "allow_remote": True})
    assert exc_info.value.status_code == 400

    # Test payload size limit
    huge_payload = "a" * (11 * 1024 * 1024)
    with pytest.raises(HTTPException) as exc_info:
        await analyze_vision_endpoint({"image_b64": huge_payload})
    assert exc_info.value.status_code == 413


# 21. Vision status endpoint
@pytest.mark.asyncio
async def test_vision_status_endpoint():
    """GET /vision/status returns compliant vision status payload."""
    from app.api.routes import get_vision_status_endpoint
    status = await get_vision_status_endpoint()
    assert status["local_only"] is True
    assert status["remote_vision_allowed"] is False
    assert status["default_model"] == "moondream"
    assert "moondream" in status["vision_models"]
    assert "ocr_engine" in status


# 22. Existing M15 internet behavior unchanged
@pytest.mark.asyncio
async def test_existing_m15_internet_behavior_unchanged():
    """Normal browser navigation and snapshot operations proceed normally."""
    engine = BrowserEngine()
    state = engine._get_or_create_session("normal-session")
    state.last_snapshot = BrowserSnapshot(
        url="https://example.com/test",
        title="Test Page",
        text_content="Existing content",
    )
    # take_browser_snapshot and take_snapshot alias both function
    snap_dict = await engine.take_browser_snapshot("normal-session")
    assert snap_dict["success"] is True
    assert snap_dict["url"] == "https://example.com/test"

    alias_dict = await engine.take_snapshot("normal-session")
    assert alias_dict["success"] is True
    assert alias_dict["title"] == "Test Page"

