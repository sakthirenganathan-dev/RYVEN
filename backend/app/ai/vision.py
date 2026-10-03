"""RYVEN 3.0 M15.2 Vision and OCR Layer.

SECURITY CONTRACT:
    ALL vision/OCR calls are LOCAL ONLY.
    Image bytes are NEVER logged or stored to disk.
    OCR text passes credential redaction before return.
    Vision output is READ-ONLY advisory data.
    Remote inference is NEVER used.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.models import TaskType
from app.ai.ollama import OllamaProvider, OllamaUnavailableError
from app.ai.router import model_router
from app.browser.security import BrowserSecurityValidator
from app.core.logging_config import logger


_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB encoded limit

_VISION_SYSTEM_PROMPT = (
    "You are a visual perception assistant. Analyse the provided image and respond ONLY with "
    "a single valid JSON object using this exact schema: "
    '{"description": "<scene description>", '
    '"elements_detected": [{"label": "<name>", "confidence": 0.9, '
    '"location": {"x": 0.5, "y": 0.2, "width": 0.1, "height": 0.05}}], '
    '"text_regions": ["<text>"], '
    '"ocr_confidence": 0.85} '
    "Respond with ONLY the JSON object. No markdown. No explanation."
)


# ---------------------------------------------------------------------------
# Typed output models
# ---------------------------------------------------------------------------


class DetectedElement(BaseModel):
    """An element identified in a visual scene."""

    label: str = Field(..., description="Semantic label of the detected element")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    location: Dict[str, float] = Field(default_factory=dict)
    text: Optional[str] = None

    @property
    def bbox(self) -> Dict[str, float]:
        return self.location

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, v: Any) -> float:
        """Clamp confidence to [0.0, 1.0]. Never hard-fail on bad numeric input."""
        try:
            fv = float(v)
            if fv != fv:  # check for NaN
                return 0.0
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, fv))


class PerceptionResult(BaseModel):
    """Structured, validated output from the PerceptionAgent.

    READ-ONLY advisory data. No action may be based solely on this output.
    """

    success: bool = True
    description: str = ""
    elements_detected: List[DetectedElement] = Field(default_factory=list)
    text_regions: List[str] = Field(default_factory=list)
    ocr_text: Optional[str] = None
    ocr_confidence: float = Field(0.0, ge=0.0, le=1.0)
    model_used: str = ""
    duration_ms: float = 0.0
    error: Optional[str] = None
    vision_available: bool = True
    local_only: bool = True  # Always True; remote inference never used

    @property
    def elements(self) -> List[DetectedElement]:
        return self.elements_detected



# ---------------------------------------------------------------------------
# VisionProvider
# ---------------------------------------------------------------------------


class VisionProvider:
    """Wraps OllamaProvider for multimodal image+text requests.

    Never invokes remote providers.
    Images are never logged or stored outside request scope.
    """

    def __init__(self, model_id: str = "moondream") -> None:
        self._model_id = model_id
        self._provider = OllamaProvider(model=model_id)

    async def analyze_image(
        self,
        image_b64: str,
        prompt: str = "Describe this image and detect all visible UI elements and text.",
    ) -> str:
        """Send image to local Ollama vision model; return raw text response.

        image_b64 is never written to logs or disk.
        """
        response = await self._provider.generate(
            prompt=prompt,
            system_prompt=_VISION_SYSTEM_PROMPT,
            images=[image_b64],  # Never logged by OllamaProvider
        )
        return response.content


# ---------------------------------------------------------------------------
# PerceptionAgent
# ---------------------------------------------------------------------------


class PerceptionAgent:
    """Safe, read-only visual perception agent for RYVEN.

    Responsibilities (in order):
        1. Receive image data (base64 PNG/JPEG).
        2. Validate image size and type.
        3. Mark visual input sensitive.
        4. Enforce LOCAL-ONLY routing via ModelRouter.
        5. Ask ModelRouter for TaskType.VISION model.
        6. Invoke selected local VisionProvider.
        7. Parse structured JSON from model output.
        8. Validate confidence values 0.0-1.0.
        9. Sanitize OCR text through credential redactor.
       10. Return typed PerceptionResult.

    NEVER executes actions. Perception is advisory only.
    """

    def __init__(self) -> None:
        self._router = model_router

    async def perceive(
        self,
        image_b64: str,
        prompt: str = "Describe this image and detect all visible UI elements and text.",
        is_visual_sensitive: bool = True,
        url: Optional[str] = None,
        allow_remote: bool = False,
        **kwargs: Any,
    ) -> PerceptionResult:
        """Analyse an image and return structured perception data.

        Args:
            image_b64: Base64-encoded PNG string. Memory-only; never logged.
            prompt: Optional analytical question.
            is_visual_sensitive: When True (default), remote inference is blocked.
            url: Optional source URL of the visual snapshot.
            allow_remote: Must remain False. Remote inference is prohibited.

        Returns:
            Typed PerceptionResult with sanitized OCR text and validated confidences.
        """
        t0 = time.monotonic()

        # Remote vision is strictly prohibited
        if allow_remote:
            await self._emit_failed(
                ActionType.VISION_REMOTE_BLOCKED,
                "Remote vision refused: allow_remote requested but forbidden.",
            )
            return PerceptionResult(
                success=False,
                error="Remote vision is strictly prohibited in RYVEN. All vision processing must remain local.",
                local_only=True,
                vision_available=False,
            )

        # 1. Validate payload
        if not image_b64 or not isinstance(image_b64, str):
            await self._emit_failed(ActionType.VISION_ANALYSIS_FAILED, "Empty image payload")
            return PerceptionResult(
                success=False, error="Invalid image: empty or non-string", vision_available=False
            )

        try:
            raw_bytes = base64.b64decode(image_b64, validate=True)
        except Exception:
            await self._emit_failed(ActionType.VISION_ANALYSIS_FAILED, "Base64 decode error")
            return PerceptionResult(
                success=False, error="Invalid image: base64 decode failed", vision_available=False
            )

        if len(raw_bytes) > _MAX_IMAGE_BYTES:
            msg = f"Image too large: {len(raw_bytes)} bytes (max {_MAX_IMAGE_BYTES})"
            await self._emit_failed(ActionType.VISION_ANALYSIS_FAILED, "Image exceeds 2 MB limit")
            return PerceptionResult(success=False, error=msg, vision_available=False)

        is_png  = raw_bytes[:4] == b"\x89PNG"
        is_jpeg = raw_bytes[:2] == b"\xff\xd8"
        if not (is_png or is_jpeg):
            logger.warning("PerceptionAgent: image may not be PNG or JPEG (proceeding)")

        # 2. Security: enforce local-only routing
        decision = await self._router.route(
            task_type=TaskType.VISION,
            prompt="[VISUAL_SENSITIVE]" if is_visual_sensitive else prompt,
            allow_remote=False,  # HARD FALSE -- visual data never leaves local
        )

        if decision.local_or_remote != "local":
            await self._emit_failed(
                ActionType.VISION_REMOTE_BLOCKED,
                f"Remote vision refused: routing returned '{decision.local_or_remote}'",
            )
            return PerceptionResult(
                success=False,
                error="Remote vision is not permitted. Visual data is sensitive.",
                local_only=True,
            )

        # 3. Check if a vision-capable model is installed
        from app.ai.registry import model_registry
        vision_models = model_registry.list_models(
            task_type=TaskType.VISION, installed_only=True, enabled_only=True
        )
        if not vision_models:
            await self._emit_failed(
                ActionType.VISION_ANALYSIS_FAILED, "No local vision model installed"
            )
            return PerceptionResult(
                success=False,
                vision_available=False,
                description="No local vision model installed. Run: ollama pull moondream",
                error="vision_model_not_installed",
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        model_id = vision_models[0].id

        # 4. Emit started event (no image bytes in event)
        await action_bus.publish(ActionEvent(
            action_type=ActionType.VISION_ANALYSIS_STARTED,
            status=ActionStatus.STARTED,
            title="Vision Analysis Started",
            description=f"Local model '{model_id}' analysing image",
            safe_metadata={"model": model_id, "local_only": True},
        ))

        # 5. Run inference
        try:
            vp = VisionProvider(model_id=model_id)
            raw_output = await vp.analyze_image(image_b64=image_b64, prompt=prompt)
        except OllamaUnavailableError as exc:
            logger.warning(f"PerceptionAgent: vision model '{model_id}' unavailable: {exc}")
            await self._emit_failed(ActionType.VISION_ANALYSIS_FAILED, f"Model unavailable: {model_id}")
            return PerceptionResult(
                success=False,
                error=f"Vision model '{model_id}' unreachable. Ensure Ollama is running.",
                vision_available=False,
                model_used=model_id,
                duration_ms=(time.monotonic() - t0) * 1000,
            )

        # 6. Parse JSON
        parsed = self._parse_vision_json(raw_output)

        # 7. Sanitize OCR text
        sanitized_regions = [
            BrowserSecurityValidator.redact_credentials(r)
            for r in parsed.get("text_regions", [])
            if isinstance(r, str)
        ]

        # 8. Validate elements
        elements: List[DetectedElement] = []
        for elem in parsed.get("elements_detected", []):
            if not isinstance(elem, dict):
                continue
            try:
                elements.append(DetectedElement(
                    label=str(elem.get("label", "unknown")),
                    confidence=elem.get("confidence", 0.0),
                    location=elem.get("location", {}),
                ))
            except Exception as e:
                logger.debug(f"PerceptionAgent: skipping malformed element: {e}")

        try:
            ocr_conf = max(0.0, min(1.0, float(parsed.get("ocr_confidence", 0.0))))
        except (TypeError, ValueError):
            ocr_conf = 0.0

        duration_ms = (time.monotonic() - t0) * 1000

        # 9. Emit completed event (no image/OCR secret content)
        await action_bus.publish(ActionEvent(
            action_type=ActionType.VISION_ANALYSIS_COMPLETED,
            status=ActionStatus.COMPLETED,
            title="Vision Analysis Complete",
            description=f"Detected {len(elements)} element(s); OCR confidence: {ocr_conf:.0%}",
            safe_metadata={
                "model": model_id,
                "elements_count": len(elements),
                "ocr_confidence": round(ocr_conf, 3),
                "text_regions_count": len(sanitized_regions),
                "duration_ms": round(duration_ms, 1),
                "local_only": True,
            },
        ))

        return PerceptionResult(
            success=True,
            description=str(parsed.get("description", ""))[:500],
            elements_detected=elements,
            text_regions=sanitized_regions,
            ocr_text=" ".join(sanitized_regions) if sanitized_regions else "",
            ocr_confidence=ocr_conf,
            model_used=model_id,
            duration_ms=duration_ms,
            local_only=True,
        )

    @staticmethod
    def _parse_vision_json(raw: str) -> Dict[str, Any]:
        """Extract valid JSON object from model output; never raises."""
        if not raw or not isinstance(raw, str):
            return {}
        try:
            return json.loads(raw.strip())
        except json.JSONDecodeError:
            pass
        start = raw.find("{")
        end   = raw.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
        logger.warning("PerceptionAgent: could not parse JSON from model output; returning empty")
        return {}

    async def _emit_failed(self, action_type: ActionType, reason: str) -> None:
        """Emit a safe failure event. Never includes image bytes or secrets."""
        await action_bus.publish(ActionEvent(
            action_type=action_type,
            status=ActionStatus.FAILED,
            title="Vision Error",
            description=reason,
            safe_metadata={"reason": reason, "local_only": True},
        ))


# Module-level singleton
perception_agent = PerceptionAgent()

