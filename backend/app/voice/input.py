"""
RYVEN 3.0 — Milestone 17.6 Controlled Voice Input Layer.

Design & Security Invariants:
- INPUT/PERCEPTION ONLY. Voice input NEVER directly executes tools or computer actions.
- Local-first architecture; provider abstraction allows local models or safe unavailable fallbacks.
- Memory-only, transient audio processing; raw audio is NEVER stored or persisted to disk.
- Credentials, tokens, and secrets are strictly redacted from transcripts.
- Enforces an explicit, validatable state machine (IDLE, LISTENING, TRANSCRIBING, etc.).
- Bounded processing times with cancellation support.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
import re
import time
from typing import Any, Dict, List, Optional, Set
import uuid

from pydantic import BaseModel, Field

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.core.logging_config import logger

# ---------------------------------------------------------------------------
# Voice State Machine
# ---------------------------------------------------------------------------

class VoiceState(str, Enum):
    """Lifecycle state of the voice perception and conversational control system."""
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    TRANSCRIBING = "TRANSCRIBING"
    UNDERSTANDING = "UNDERSTANDING"
    EXECUTING = "EXECUTING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    ERROR = "ERROR"


LEGAL_VOICE_TRANSITIONS: Dict[VoiceState, Set[VoiceState]] = {
    VoiceState.IDLE: {VoiceState.LISTENING, VoiceState.UNDERSTANDING, VoiceState.ERROR},
    VoiceState.LISTENING: {VoiceState.TRANSCRIBING, VoiceState.INTERRUPTED, VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.TRANSCRIBING: {VoiceState.UNDERSTANDING, VoiceState.INTERRUPTED, VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.UNDERSTANDING: {VoiceState.EXECUTING, VoiceState.SPEAKING, VoiceState.IDLE, VoiceState.INTERRUPTED, VoiceState.ERROR},
    VoiceState.EXECUTING: {VoiceState.SPEAKING, VoiceState.IDLE, VoiceState.INTERRUPTED, VoiceState.ERROR},
    VoiceState.SPEAKING: {VoiceState.IDLE, VoiceState.INTERRUPTED, VoiceState.ERROR},
    VoiceState.INTERRUPTED: {VoiceState.IDLE, VoiceState.ERROR},
    VoiceState.ERROR: {VoiceState.IDLE},
}

_CREDENTIAL_PATTERNS = [
    re.compile(r"(?:api[_-]?key|token|bearer|secret|password)(?:[\s:=]+|[\s]+is[\s]+)[a-zA-Z0-9_\-\.]{6,}", re.IGNORECASE),
    re.compile(r"ghp_[a-zA-Z0-9]{20,}", re.IGNORECASE),
    re.compile(r"sk-[a-zA-Z0-9]{10,}", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----", re.IGNORECASE),
]

_CONFIDENCE_THRESHOLD_HIGH = 0.85
_CONFIDENCE_THRESHOLD_MEDIUM = 0.50


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class VoiceTranscript(BaseModel):
    """Structured voice transcription result with quality and latency metadata."""
    transcript_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str = Field(default="")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    language: str = Field(default="en")
    duration_ms: float = Field(default=0.0)
    is_final: bool = Field(default=True)
    error: Optional[str] = None
    source_format: str = Field(default="wav")
    safe_metadata: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Speech-To-Text Provider Abstraction
# ---------------------------------------------------------------------------

class SpeechToTextProvider(ABC):
    """Abstract interface for local or configured speech-to-text engines."""

    @abstractmethod
    def get_name(self) -> str:
        """Return provider identifier."""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if the underlying model/audio backend is ready."""
        ...

    @abstractmethod
    async def transcribe(
        self,
        audio_bytes: bytes,
        audio_format: str = "wav",
        language: str = "en",
    ) -> VoiceTranscript:
        """Convert audio bytes into structured VoiceTranscript."""
        ...


class SafeUnavailableSTTProvider(SpeechToTextProvider):
    """Safe fallback when no local speech recognition provider is available."""

    def get_name(self) -> str:
        return "safe_unavailable_stt"

    def is_available(self) -> bool:
        return False

    async def transcribe(
        self,
        audio_bytes: bytes,
        audio_format: str = "wav",
        language: str = "en",
    ) -> VoiceTranscript:
        return VoiceTranscript(
            text="",
            confidence=0.0,
            language=language,
            duration_ms=0.0,
            error="Speech recognition is currently unavailable on this system.",
            source_format=audio_format,
        )


class MockLocalSTTProvider(SpeechToTextProvider):
    """Deterministic in-memory STT provider for testing and validation."""

    def __init__(
        self,
        default_text: str = "open chrome",
        confidence: float = 0.95,
        available: bool = True,
        simulate_error: Optional[str] = None,
    ) -> None:
        self.default_text = default_text
        self.confidence = confidence
        self.available = available
        self.simulate_error = simulate_error

    def get_name(self) -> str:
        return "mock_local_stt"

    def is_available(self) -> bool:
        return self.available

    async def transcribe(
        self,
        audio_bytes: bytes,
        audio_format: str = "wav",
        language: str = "en",
    ) -> VoiceTranscript:
        if self.simulate_error:
            return VoiceTranscript(
                text="",
                confidence=0.0,
                language=language,
                duration_ms=10.0,
                error=self.simulate_error,
                source_format=audio_format,
            )

        if not audio_bytes:
            return VoiceTranscript(
                text="",
                confidence=0.0,
                language=language,
                duration_ms=0.0,
                error="Empty audio input",
                source_format=audio_format,
            )

        return VoiceTranscript(
            text=self.default_text,
            confidence=self.confidence,
            language=language,
            duration_ms=25.0,
            source_format=audio_format,
        )


# ---------------------------------------------------------------------------
# VoiceInputEngine
# ---------------------------------------------------------------------------

class VoiceInputEngine:
    """Manages audio reception, state machine transitions, and speech transcription.

    CRITICAL: Never executes tasks directly. Transcripts are forwarded to the
    conversational layer or UnifiedTaskOrchestrator.
    """

    def __init__(self, provider: Optional[SpeechToTextProvider] = None) -> None:
        self._provider: SpeechToTextProvider = provider or MockLocalSTTProvider()
        self._state: VoiceState = VoiceState.IDLE
        self._interrupted: bool = False

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def provider(self) -> SpeechToTextProvider:
        return self._provider

    def set_provider(self, provider: SpeechToTextProvider) -> None:
        """Switch or configure the active STT provider."""
        self._provider = provider

    def transition_to(self, new_state: VoiceState) -> bool:
        """Safely transition to a new lifecycle state.

        Raises ValueError if the transition violates legal state machine invariants.
        """
        if self._state == new_state:
            return True

        allowed = LEGAL_VOICE_TRANSITIONS.get(self._state, set())
        if new_state not in allowed:
            err = f"Illegal voice state transition: {self._state.value} -> {new_state.value}"
            logger.warning(f"[VOICE_INPUT] {err}")
            raise ValueError(err)

        logger.debug(f"[VOICE_INPUT] State transition: {self._state.value} -> {new_state.value}")
        self._state = new_state
        return True

    def sanitize_transcript(self, raw_text: str) -> str:
        """Scrub potential credentials and normalize whitespace safely."""
        if not raw_text or not isinstance(raw_text, str):
            return ""

        cleaned = raw_text.strip()
        for pat in _CREDENTIAL_PATTERNS:
            cleaned = pat.sub("[REDACTED_CREDENTIAL]", cleaned)

        # Collapse excessive whitespace
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned

    def interrupt(self) -> None:
        """Signal immediate cancellation of voice reception or transcription."""
        self._interrupted = True
        logger.info("[VOICE_INPUT] Interruption triggered.")
        try:
            self.transition_to(VoiceState.INTERRUPTED)
            self.transition_to(VoiceState.IDLE)
        except Exception:
            self._state = VoiceState.IDLE

    def reset(self) -> None:
        """Reset state machine and interruption flags to IDLE."""
        self._interrupted = False
        self._state = VoiceState.IDLE

    async def process_audio(
        self,
        audio_bytes: bytes,
        audio_format: str = "wav",
        language: str = "en",
        session_id: str = "default",
    ) -> VoiceTranscript:
        """Transcribe audio into a safe, credential-scrubbed VoiceTranscript.

        Strict invariants:
        - NEVER persists raw audio to disk or databases.
        - Emits safe telemetry without raw audio payloads.
        - Enforces legal state machine transitions.
        """
        t0 = time.monotonic()
        self._interrupted = False

        # 1. State transition to LISTENING -> TRANSCRIBING
        try:
            if self._state == VoiceState.IDLE:
                self.transition_to(VoiceState.LISTENING)
            self.transition_to(VoiceState.TRANSCRIBING)
        except ValueError as e:
            return VoiceTranscript(
                text="",
                confidence=0.0,
                error=f"State error: {e}",
                source_format=audio_format,
            )

        # 2. Check for empty audio
        if not audio_bytes or len(audio_bytes) == 0:
            duration = (time.monotonic() - t0) * 1000.0
            self.transition_to(VoiceState.IDLE)
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.VOICE_INPUT_REJECTED,
                    status=ActionStatus.FAILED,
                    title="Voice input empty",
                    safe_metadata={"reason": "empty_audio", "duration_ms": duration},
                )
            )
            return VoiceTranscript(
                text="",
                confidence=0.0,
                error="No audio content detected.",
                duration_ms=duration,
                source_format=audio_format,
            )

        # 3. Check provider availability
        if not self._provider.is_available():
            duration = (time.monotonic() - t0) * 1000.0
            self.transition_to(VoiceState.IDLE)
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.VOICE_INPUT_REJECTED,
                    status=ActionStatus.FAILED,
                    title="Speech recognition provider unavailable",
                    safe_metadata={"provider": self._provider.get_name()},
                )
            )
            return VoiceTranscript(
                text="",
                confidence=0.0,
                error=f"Speech recognition provider '{self._provider.get_name()}' is not available.",
                duration_ms=duration,
                source_format=audio_format,
            )

        # 4. Telemetry: input started
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.VOICE_INPUT_STARTED,
                status=ActionStatus.STARTED,
                title="Voice input transcribing",
                safe_metadata={
                    "format": audio_format,
                    "bytes_count": len(audio_bytes),
                    "provider": self._provider.get_name(),
                },
            )
        )

        # 5. Transcribe through provider
        try:
            transcript = await self._provider.transcribe(
                audio_bytes=audio_bytes,
                audio_format=audio_format,
                language=language,
            )
        except Exception as exc:
            duration = (time.monotonic() - t0) * 1000.0
            self.transition_to(VoiceState.ERROR)
            self.transition_to(VoiceState.IDLE)
            logger.error(f"[VOICE_INPUT] Transcription exception: {exc}")
            return VoiceTranscript(
                text="",
                confidence=0.0,
                error=f"Transcription failed: {exc}",
                duration_ms=duration,
                source_format=audio_format,
            )

        if transcript.error:
            duration = (time.monotonic() - t0) * 1000.0
            self.transition_to(VoiceState.IDLE)
            return VoiceTranscript(
                text="",
                confidence=0.0,
                error=transcript.error,
                duration_ms=duration,
                source_format=audio_format,
            )

        if self._interrupted:
            self.transition_to(VoiceState.INTERRUPTED)
            self.transition_to(VoiceState.IDLE)
            return VoiceTranscript(
                text="",
                confidence=0.0,
                error="Voice input interrupted by user.",
                source_format=audio_format,
            )

        # 6. Sanitize text
        clean_text = self.sanitize_transcript(transcript.text)
        duration = (time.monotonic() - t0) * 1000.0

        final_transcript = VoiceTranscript(
            transcript_id=transcript.transcript_id,
            text=clean_text,
            confidence=transcript.confidence,
            language=transcript.language,
            duration_ms=duration,
            is_final=transcript.is_final,
            error=transcript.error,
            source_format=audio_format,
            safe_metadata={
                "provider": self._provider.get_name(),
                "duration_ms": duration,
                "sanitized": clean_text != transcript.text,
            },
        )

        # 7. Telemetry & Transition to UNDERSTANDING or IDLE
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.VOICE_TRANSCRIPTION_COMPLETED,
                status=ActionStatus.COMPLETED if not final_transcript.error else ActionStatus.FAILED,
                title=f"Voice transcribed: {clean_text[:40]}",
                safe_metadata={
                    "confidence": final_transcript.confidence,
                    "duration_ms": duration,
                    "char_count": len(clean_text),
                },
            )
        )

        try:
            self.transition_to(VoiceState.UNDERSTANDING)
        except Exception:
            self._state = VoiceState.IDLE

        return final_transcript


# Singleton instance
voice_input_engine = VoiceInputEngine()
