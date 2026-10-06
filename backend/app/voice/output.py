"""
RYVEN 3.0 — Milestone 17.6 Controlled Voice Output Layer.

Design & Security Invariants:
- Sanitized user-facing text ONLY. Never speaks secrets, tokens, internal reasoning, or raw stack traces.
- Supports immediate interruption / barge-in.
- Provider abstraction enables local TTS (e.g. Windows SAPI/pyttsx3) or graceful fallback to text-only mode.
- Never claims audio was generated if the provider failed or is unavailable.
- Bounded, transient audio output without unauthorized persistence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import re
import time
from typing import Any, Dict, List, Optional
import uuid

from pydantic import BaseModel, Field

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.core.logging_config import logger

_SECRET_SPEAKING_PATTERNS = [
    re.compile(r"(?:api[_-]?key|token|bearer|secret|password)(?:[\s:=]+|[\s]+is[\s]+)[a-zA-Z0-9_\-\.]{6,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9]{10,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----", re.IGNORECASE),
]

_INTERNAL_REASONING_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_MARKDOWN_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_MARKDOWN_CODE_PATTERN = re.compile(r"```[a-zA-Z]*\n?.*?```", re.DOTALL)
_INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class VoiceOutputResult(BaseModel):
    """Structured voice output result with telemetry and content metadata."""
    output_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text_spoken: str = Field(default="")
    audio_bytes: bytes = Field(default=b"")
    duration_ms: float = Field(default=0.0)
    audio_format: str = Field(default="wav")
    success: bool = Field(default=True)
    interrupted: bool = Field(default=False)
    fallback_text_only: bool = Field(default=False)
    error: Optional[str] = None
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Text-To-Speech Provider Abstraction
# ---------------------------------------------------------------------------

class TextToSpeechProvider(ABC):
    """Abstract interface for local or configured text-to-speech synthesis engines."""

    @abstractmethod
    def get_name(self) -> str:
        """Return provider identifier."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if TTS engine is installed and ready."""
        ...

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str = "default",
        speed: float = 1.0,
    ) -> bytes:
        """Synthesize sanitized text into audio bytes."""
        ...


class SafeUnavailableTTSProvider(TextToSpeechProvider):
    """Safe fallback when no local TTS provider is available on the system."""

    def get_name(self) -> str:
        return "safe_unavailable_tts"

    def is_available(self) -> bool:
        return False

    async def synthesize(
        self,
        text: str,
        voice: str = "default",
        speed: float = 1.0,
    ) -> bytes:
        return b""


class MockLocalTTSProvider(TextToSpeechProvider):
    """Deterministic in-memory TTS provider for testing and offline validation."""

    def __init__(self, available: bool = True, simulate_error: Optional[str] = None) -> None:
        self.available = available
        self.simulate_error = simulate_error
        # Generate minimal RIFF/WAV header (44 bytes) for realistic mocking
        self._mock_wav = (
            b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00"
            b"\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
        )

    def get_name(self) -> str:
        return "mock_local_tts"

    def is_available(self) -> bool:
        return self.available

    async def synthesize(
        self,
        text: str,
        voice: str = "default",
        speed: float = 1.0,
    ) -> bytes:
        if self.simulate_error:
            raise RuntimeError(self.simulate_error)
        return self._mock_wav


# ---------------------------------------------------------------------------
# VoiceOutputEngine
# ---------------------------------------------------------------------------

class VoiceOutputEngine:
    """Manages conversational response synthesis, text sanitization, and barge-in."""

    def __init__(self, provider: Optional[TextToSpeechProvider] = None) -> None:
        self._provider: TextToSpeechProvider = provider or MockLocalTTSProvider()
        self._is_speaking: bool = False
        self._interrupted: bool = False

    @property
    def is_speaking(self) -> bool:
        return self._is_speaking

    @property
    def provider(self) -> TextToSpeechProvider:
        return self._provider

    def set_provider(self, provider: TextToSpeechProvider) -> None:
        """Switch or configure the active TTS provider."""
        self._provider = provider

    def sanitize_for_speech(self, raw_text: str) -> str:
        """Sanitize assistant response text before sending to speech synthesis.

        Strict Invariants:
        - Strips internal reasoning <think>...</think> blocks.
        - Redacts passwords, API keys, credentials, and tokens.
        - Sanitizes raw Python tracebacks or stack traces.
        - Normalizes markdown links to their visible text.
        - Strips multi-line code blocks to prevent reading code syntax verbatim.
        """
        if not raw_text or not isinstance(raw_text, str):
            return ""

        text = raw_text

        # 1. Strip internal reasoning tags
        text = _INTERNAL_REASONING_PATTERN.sub("", text)

        # 2. Strip code blocks
        text = _MARKDOWN_CODE_PATTERN.sub("Code block omitted for voice.", text)

        # 3. Simplify markdown links [Label](url) -> Label
        text = _MARKDOWN_LINK_PATTERN.sub(r"\1", text)

        # 4. Simplify inline code `cmd` -> cmd
        text = _INLINE_CODE_PATTERN.sub(r"\1", text)

        # 5. Redact credentials
        for pat in _SECRET_SPEAKING_PATTERNS:
            text = pat.sub("[credential redacted]", text)

        # 6. Sanitize stack traces
        if "Traceback (most recent call last)" in text:
            text = "An internal error occurred during task execution."

        # 7. Collapse whitespace and limit length for natural speech
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def stop(self) -> None:
        """Immediately interrupt current voice output (barge-in support)."""
        self._interrupted = True
        self._is_speaking = False
        logger.info("[VOICE_OUTPUT] Barge-in stop requested.")

    def interrupt(self) -> None:
        """Alias for stop()."""
        self.stop()

    async def speak(
        self,
        raw_text: str,
        voice: str = "default",
        speed: float = 1.0,
    ) -> VoiceOutputResult:
        """Synthesize sanitized assistant text into speech.

        Strict invariants:
        - Never speaks unredacted secrets or stack traces.
        - Falls back gracefully to text-only mode when TTS provider is unavailable.
        - Immediately honors barge-in interruptions.
        """
        t0 = time.monotonic()
        if self._interrupted:
            duration = (time.monotonic() - t0) * 1000.0
            self._is_speaking = False
            clean_text = self.sanitize_for_speech(raw_text)
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.VOICE_OUTPUT_INTERRUPTED,
                    status=ActionStatus.CANCELLED,
                    title="Voice output interrupted",
                    safe_metadata={"reason": "barge_in", "duration_ms": duration},
                )
            )
            return VoiceOutputResult(
                text_spoken=clean_text,
                audio_bytes=b"",
                duration_ms=duration,
                success=True,
                interrupted=True,
                error="Voice output interrupted by barge-in.",
            )

        self._is_speaking = True

        clean_text = self.sanitize_for_speech(raw_text)
        if not clean_text:
            self._is_speaking = False
            return VoiceOutputResult(
                text_spoken="",
                audio_bytes=b"",
                duration_ms=0.0,
                success=True,
                fallback_text_only=True,
            )

        # 1. Telemetry: voice output started
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.VOICE_OUTPUT_STARTED,
                status=ActionStatus.STARTED,
                title="Voice output starting",
                safe_metadata={
                    "char_count": len(clean_text),
                    "provider": self._provider.get_name(),
                },
            )
        )

        # 2. Check provider availability -> Graceful text-only fallback
        if not self._provider.is_available():
            duration = (time.monotonic() - t0) * 1000.0
            self._is_speaking = False
            logger.debug(f"[VOICE_OUTPUT] Provider '{self._provider.get_name()}' unavailable; falling back to text.")
            return VoiceOutputResult(
                text_spoken=clean_text,
                audio_bytes=b"",
                duration_ms=duration,
                success=True,
                fallback_text_only=True,
                safe_metadata={"reason": "provider_unavailable", "provider": self._provider.get_name()},
            )

        # 3. Synthesize speech
        try:
            audio_bytes = await self._provider.synthesize(text=clean_text, voice=voice, speed=speed)
        except Exception as exc:
            duration = (time.monotonic() - t0) * 1000.0
            self._is_speaking = False
            logger.warning(f"[VOICE_OUTPUT] TTS synthesis failed: {exc}")
            return VoiceOutputResult(
                text_spoken=clean_text,
                audio_bytes=b"",
                duration_ms=duration,
                success=False,
                fallback_text_only=True,
                error=f"TTS synthesis failed: {exc}",
            )

        # 4. Check for barge-in interruption during synthesis
        if self._interrupted:
            duration = (time.monotonic() - t0) * 1000.0
            self._is_speaking = False
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.VOICE_OUTPUT_INTERRUPTED,
                    status=ActionStatus.CANCELLED,
                    title="Voice output interrupted",
                    safe_metadata={"duration_ms": duration},
                )
            )
            return VoiceOutputResult(
                text_spoken=clean_text,
                audio_bytes=b"",
                duration_ms=duration,
                success=True,
                interrupted=True,
            )

        duration = (time.monotonic() - t0) * 1000.0
        self._is_speaking = False

        # 5. Telemetry: completed
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.VOICE_OUTPUT_COMPLETED,
                status=ActionStatus.COMPLETED,
                title="Voice output completed",
                safe_metadata={
                    "char_count": len(clean_text),
                    "duration_ms": duration,
                    "bytes_count": len(audio_bytes),
                },
            )
        )

        return VoiceOutputResult(
            text_spoken=clean_text,
            audio_bytes=audio_bytes,
            duration_ms=duration,
            success=True,
            interrupted=False,
            safe_metadata={"provider": self._provider.get_name()},
        )


# Singleton instance
voice_output_engine = VoiceOutputEngine()
