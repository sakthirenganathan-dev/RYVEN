"""RYVEN 3.0 — Model Intelligence Telemetry, Performance Governance & Observability.

Maintains thread-safe, bounded, privacy-preserving operational metrics,
correlation-based attempt tracking, and performance governance across all AI providers (M17.10 Phase 5).

STRICT PRIVACY GUARANTEES:
- Prompts, system messages, and responses are NEVER persisted.
- Secrets, tokens, cookies, auth headers, and raw credentials are NEVER persisted.
- Raw exception bodies are normalized into low-cardinality, secret-free error categories.
- Metadata is recursively filtered against an authoritative allowlist.
- Telemetry failure NEVER weakens ModelSecurityGateway or allows unauthorized inference.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from enum import Enum
import threading
import time
from typing import Any, Deque, Dict, List, Optional, Tuple, Union
import uuid

from pydantic import BaseModel, Field

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.contracts import (
    AuthenticationError,
    ProviderInvalidRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    SecurityViolationError,
)
from app.ai.models import RoutingDecision
from app.core.logging_config import logger


# ============================================================================
# ENUMS & CONSTANTS
# ============================================================================

class TelemetryEventType(str, Enum):
    """Categorized event types emitted during the inference lifecycle."""

    REQUEST_ACCEPTED = "REQUEST_ACCEPTED"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    ROUTING_DECISION = "ROUTING_DECISION"
    PROVIDER_ATTEMPT_STARTED = "PROVIDER_ATTEMPT_STARTED"
    PROVIDER_ATTEMPT_COMPLETED = "PROVIDER_ATTEMPT_COMPLETED"
    FIRST_TOKEN_EMITTED = "FIRST_TOKEN_EMITTED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    FALLBACK_ATTEMPTED = "FALLBACK_ATTEMPTED"
    FALLBACK_SUCCEEDED = "FALLBACK_SUCCEEDED"
    FALLBACK_FAILED = "FALLBACK_FAILED"
    CIRCUIT_STATE_CHANGED = "CIRCUIT_STATE_CHANGED"
    SECURITY_EVALUATION = "SECURITY_EVALUATION"
    SECURITY_DENIED = "SECURITY_DENIED"
    SECURITY_REDACTED = "SECURITY_REDACTED"
    STREAM_COMPLETED = "STREAM_COMPLETED"
    STREAM_CANCELLED = "STREAM_CANCELLED"
    STREAM_TERMINATED = "STREAM_TERMINATED"


# Strictly allowlisted keys for telemetry event metadata
_ALLOWLISTED_METADATA_KEYS = frozenset(
    {
        "request_id",
        "attempt_id",
        "provider",
        "model_id",
        "task_type",
        "complexity_tier",
        "local_or_remote",
        "privacy_mode",
        "status",
        "outcome",
        "error_category",
        "duration_ms",
        "first_token_latency_ms",
        "visible_chunks",
        "tokens_input",
        "tokens_output",
        "cost_estimate_usd",
        "fallback_depth",
        "fallback_from_provider",
        "fallback_from_model",
        "fallback_to_provider",
        "fallback_to_model",
        "fallback_reason_code",
        "circuit_state",
        "circuit_prev_state",
        "reason_code",
        "reason_summary",
        "stream",
        "redacted",
        "allowed",
        "is_fallback",
        "prompt_length",
    }
)

# Forbidden substring markers indicating credentials or sensitive data
_FORBIDDEN_SECRET_MARKERS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "token",
        "secret",
        "cookie",
        "authorization",
        "bearer",
        "api_key",
        "apikey",
        "private_key",
        "privatekey",
        "sk-",
        "gsk_",
        "hf_",
    }
)


def _utc_now_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


def normalize_error_category(exc: Optional[Exception]) -> str:
    """Normalize any exception into a low-cardinality, secret-free category."""
    if exc is None:
        return "UNKNOWN_ERROR"

    if isinstance(exc, ProviderTimeoutError):
        return "PROVIDER_TIMEOUT"
    if isinstance(exc, RateLimitError):
        return "RATE_LIMIT_EXCEEDED"
    if isinstance(exc, AuthenticationError):
        return "AUTHENTICATION_FAILED"
    if isinstance(exc, SecurityViolationError):
        return "SECURITY_VIOLATION"
    if isinstance(exc, ProviderInvalidRequestError):
        return "INVALID_REQUEST"
    if isinstance(exc, ProviderUnavailableError):
        return "PROVIDER_UNAVAILABLE"

    # Handle standard asyncio cancellation
    cls_name = exc.__class__.__name__
    if cls_name in ("CancelledError", "AsyncCancelledError"):
        return "CLIENT_CANCELLED"

    # Low-cardinality standard exceptions
    if "Timeout" in cls_name:
        return "PROVIDER_TIMEOUT"
    if "Connection" in cls_name or "Network" in cls_name:
        return "NETWORK_ERROR"

    return "UNKNOWN_ERROR"


def sanitize_telemetry_metadata(raw_metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively filter metadata dictionary strictly allowing allowlisted keys.

    Drops prompts, responses, secrets, tokens, and non-scalar values.
    """
    clean: Dict[str, Any] = {}
    if not isinstance(raw_metadata, dict):
        return clean

    for k, v in raw_metadata.items():
        if k not in _ALLOWLISTED_METADATA_KEYS:
            continue

        # Check for secret keys or markers in key name
        k_lower = k.lower()
        if any(marker in k_lower for marker in _FORBIDDEN_SECRET_MARKERS):
            continue

        # Validate and sanitize values
        if v is None:
            clean[k] = None
        elif isinstance(v, bool):
            clean[k] = v
        elif isinstance(v, (int, float)):
            clean[k] = round(v, 4) if isinstance(v, float) else v
        elif isinstance(v, str):
            # Check value for credentials
            v_lower = v.lower()
            if any(marker in v_lower for marker in _FORBIDDEN_SECRET_MARKERS):
                clean[k] = "[REDACTED]"
            elif len(v) > 256:
                clean[k] = v[:240] + "…[TRUNCATED]"
            else:
                clean[k] = v
        elif isinstance(v, dict):
            # Nested sanitization
            nested = sanitize_telemetry_metadata(v)
            if nested:
                clean[k] = nested
    return clean


def compute_percentiles(samples: List[float]) -> Dict[str, Optional[float]]:
    """Compute count, avg, median (p50), and p95 for a list of latency samples."""
    if not samples:
        return {
            "count": 0,
            "avg_ms": None,
            "median_ms": None,
            "p50_ms": None,
            "p95_ms": None,
        }

    sorted_samples = sorted(samples)
    n = len(sorted_samples)
    avg_val = round(sum(sorted_samples) / n, 2)

    # Median / P50
    mid = n // 2
    median_val = round(
        (sorted_samples[mid] if n % 2 != 0 else (sorted_samples[mid - 1] + sorted_samples[mid]) / 2.0),
        2,
    )

    # P95
    p95_idx = min(int(0.95 * n), n - 1)
    p95_val = round(sorted_samples[p95_idx], 2)

    return {
        "count": n,
        "avg_ms": avg_val,
        "median_ms": median_val,
        "p50_ms": median_val,
        "p95_ms": p95_val,
    }


# ============================================================================
# TELEMETRY DATA MODELS
# ============================================================================

class TelemetryEvent(BaseModel):
    """Immutable, bounded structured event for telemetry audit."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: TelemetryEventType
    request_id: str
    attempt_id: Optional[str] = None
    timestamp: str = Field(default_factory=_utc_now_iso)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProviderAttemptRecord:
    """Thread-safe record of an individual provider execution attempt."""

    def __init__(
        self,
        attempt_id: str,
        request_id: str,
        provider: str,
        model_id: str,
        local_or_remote: str,
        is_fallback: bool = False,
    ) -> None:
        self.attempt_id: str = attempt_id
        self.request_id: str = request_id
        self.provider: str = provider
        self.model_id: str = model_id
        self.local_or_remote: str = local_or_remote
        self.is_fallback: bool = is_fallback

        self.start_time_monotonic: float = time.monotonic()
        self.start_time_utc: str = _utc_now_iso()
        self.end_time_monotonic: Optional[float] = None
        self.duration_ms: Optional[float] = None
        self.first_token_latency_ms: Optional[float] = None

        self.outcome: Optional[str] = None  # SUCCESS, FAILURE, TIMEOUT, RATE_LIMIT, CANCELLED
        self.error_category: Optional[str] = None
        self.tokens_input: Optional[int] = None
        self.tokens_output: Optional[int] = None
        self.cost_estimate_usd: Optional[float] = None


class LogicalRequestRecord:
    """Thread-safe record of a top-level user or agent inference request."""

    def __init__(
        self,
        request_id: str,
        task_type: str = "GENERAL_REASONING",
        privacy_mode: str = "PRIVACY_FIRST",
        local_or_remote_target: str = "local",
        selected_provider: str = "OLLAMA",
        selected_model: str = "qwen2.5:7b",
    ) -> None:
        self.request_id: str = request_id
        self.task_type: str = task_type
        self.privacy_mode: str = privacy_mode
        self.local_or_remote_target: str = local_or_remote_target
        self.selected_provider: str = selected_provider
        self.selected_model: str = selected_model

        self.start_time_monotonic: float = time.monotonic()
        self.start_time_utc: str = _utc_now_iso()
        self.end_time_monotonic: Optional[float] = None
        self.total_duration_ms: Optional[float] = None

        self.status: str = "PENDING"  # SUCCESS, FAILED, SECURITY_DENIED, CANCELLED
        self.error_category: Optional[str] = None
        self.fallback_occurred: bool = False
        self.fallback_provider: Optional[str] = None
        self.fallback_model: Optional[str] = None

        self.attempts: List[ProviderAttemptRecord] = []


# ============================================================================
# MODEL TELEMETRY SERVICE
# ============================================================================

class ModelTelemetryService:
    """Authoritative, thread-safe, privacy-preserving AI telemetry and governance service.

    Tracks logical requests vs individual provider attempts without double counting,
    calculates latency distributions (p50, p95), monitors streaming first-token latencies,
    and publishes sanitized audit records to action_bus.
    """

    def __init__(
        self,
        max_events: int = 1000,
        max_requests: int = 500,
    ) -> None:
        self.max_events: int = max(1, max_events)
        self.max_requests: int = max(1, max_requests)

        self._lock: threading.RLock = threading.RLock()

        # Bounded ring buffers
        self._events: Deque[TelemetryEvent] = deque(maxlen=self.max_events)
        self._completed_requests: Deque[LogicalRequestRecord] = deque(maxlen=self.max_requests)

        # In-flight tracking maps
        self._active_requests: Dict[str, LogicalRequestRecord] = {}
        self._active_attempts: Dict[str, ProviderAttemptRecord] = {}

        # Aggregate counters & samples
        self._latencies_by_provider: Dict[str, Deque[float]] = {}
        self._first_token_latencies_by_provider: Dict[str, Deque[float]] = {}

        # Pricing configuration: {provider: {"prompt_price_per_million": X, "completion_price_per_million": Y}}
        self._pricing_config: Dict[str, Dict[str, float]] = {}

    # -----------------------------------------------------------------------
    # CONFIGURATION & LIFECYCLE
    # -----------------------------------------------------------------------

    def set_pricing_config(self, pricing: Dict[str, Dict[str, float]]) -> None:
        """Set explicit pricing configuration for cost calculations.

        Prices must be specified in USD per 1,000,000 tokens.
        """
        with self._lock:
            self._pricing_config = {
                k.upper(): dict(v) for k, v in pricing.items() if isinstance(v, dict)
            }

    def clear(self) -> None:
        """Reset internal metrics and ring buffers."""
        with self._lock:
            self._events.clear()
            self._completed_requests.clear()
            self._active_requests.clear()
            self._active_attempts.clear()
            self._latencies_by_provider.clear()
            self._first_token_latencies_by_provider.clear()

    # -----------------------------------------------------------------------
    # LOGICAL REQUEST LIFECYCLE
    # -----------------------------------------------------------------------

    def start_request(
        self,
        request_id: Optional[str] = None,
        task_type: str = "GENERAL_REASONING",
        privacy_mode: str = "PRIVACY_FIRST",
        local_or_remote_target: str = "local",
        selected_provider: str = "OLLAMA",
        selected_model: str = "qwen2.5:7b",
        prompt_length: Optional[int] = None,
    ) -> str:
        """Initiate tracking for a top-level logical inference request."""
        req_id = request_id or str(uuid.uuid4())

        with self._lock:
            # Prevent duplicate in-flight registration
            if req_id in self._active_requests:
                return req_id

            # Prune stale in-flight requests if map exceeds buffer bounds
            if len(self._active_requests) >= self.max_requests:
                self._prune_stale_active_requests()

            req_rec = LogicalRequestRecord(
                request_id=req_id,
                task_type=task_type,
                privacy_mode=privacy_mode,
                local_or_remote_target=local_or_remote_target,
                selected_provider=selected_provider,
                selected_model=selected_model,
            )
            self._active_requests[req_id] = req_rec

            # Record event
            meta: Dict[str, Any] = {
                "request_id": req_id,
                "task_type": task_type,
                "privacy_mode": privacy_mode,
                "local_or_remote": local_or_remote_target,
                "provider": selected_provider,
                "model_id": selected_model,
            }
            if prompt_length is not None:
                meta["prompt_length"] = prompt_length

            self._record_event(
                TelemetryEventType.REQUEST_ACCEPTED,
                request_id=req_id,
                metadata=meta,
            )
            return req_id

    def record_routing_decision(
        self,
        request_id: str,
        decision: RoutingDecision,
    ) -> None:
        """Record model routing assignment for a logical request."""
        with self._lock:
            req_rec = self._active_requests.get(request_id)
            if req_rec:
                req_rec.selected_provider = decision.provider
                req_rec.selected_model = decision.selected_model
                req_rec.local_or_remote_target = decision.local_or_remote

            meta = {
                "request_id": request_id,
                "provider": decision.provider,
                "model_id": decision.selected_model,
                "task_type": decision.task_type.value,
                "local_or_remote": decision.local_or_remote,
                "reason_code": decision.reason_code,
                "reason_summary": decision.reason[:128] if decision.reason else "",
            }
            self._record_event(
                TelemetryEventType.ROUTING_DECISION,
                request_id=request_id,
                metadata=meta,
            )

    def record_security_decision(
        self,
        request_id: str,
        allowed: bool,
        privacy_mode: str,
        reason: Optional[str] = None,
        redacted: bool = False,
    ) -> None:
        """Record ModelSecurityGateway evaluation outcome."""
        with self._lock:
            event_type = (
                TelemetryEventType.SECURITY_EVALUATION
                if allowed
                else TelemetryEventType.SECURITY_DENIED
            )
            if allowed and redacted:
                event_type = TelemetryEventType.SECURITY_REDACTED

            meta = {
                "request_id": request_id,
                "allowed": allowed,
                "privacy_mode": privacy_mode,
                "redacted": redacted,
                "reason_summary": (reason[:128] if reason else "ALLOWED"),
            }
            self._record_event(
                event_type,
                request_id=request_id,
                metadata=meta,
            )

            # If denied by security, mark active request as denied
            if not allowed and request_id in self._active_requests:
                req_rec = self._active_requests[request_id]
                req_rec.status = "SECURITY_DENIED"
                req_rec.error_category = "SECURITY_VIOLATION"

    def complete_request(
        self,
        request_id: str,
        status: str,
        error: Optional[Exception] = None,
        fallback_occurred: bool = False,
        fallback_provider: Optional[str] = None,
        fallback_model: Optional[str] = None,
    ) -> None:
        """Finalize tracking of a logical inference request."""
        with self._lock:
            req_rec = self._active_requests.pop(request_id, None)
            if not req_rec:
                return

            req_rec.end_time_monotonic = time.monotonic()
            req_rec.total_duration_ms = round(
                (req_rec.end_time_monotonic - req_rec.start_time_monotonic) * 1000, 2
            )
            req_rec.status = status
            req_rec.fallback_occurred = fallback_occurred
            req_rec.fallback_provider = fallback_provider
            req_rec.fallback_model = fallback_model

            if error:
                req_rec.error_category = normalize_error_category(error)

            self._completed_requests.append(req_rec)

            meta = {
                "request_id": request_id,
                "status": status,
                "duration_ms": req_rec.total_duration_ms,
                "fallback_occurred": fallback_occurred,
                "fallback_to_provider": fallback_provider,
                "fallback_to_model": fallback_model,
                "error_category": req_rec.error_category,
            }
            event_type = (
                TelemetryEventType.REQUEST_ACCEPTED
                if status == "SUCCESS"
                else (
                    TelemetryEventType.SECURITY_DENIED
                    if status == "SECURITY_DENIED"
                    else TelemetryEventType.REQUEST_REJECTED
                )
            )
            self._record_event(event_type, request_id=request_id, metadata=meta)

    # -----------------------------------------------------------------------
    # PROVIDER ATTEMPT LIFECYCLE
    # -----------------------------------------------------------------------

    def start_provider_attempt(
        self,
        request_id: str,
        provider: str,
        model_id: str,
        local_or_remote: str,
        is_fallback: bool = False,
    ) -> str:
        """Start tracking a single provider execution attempt."""
        attempt_id = str(uuid.uuid4())

        with self._lock:
            attempt_rec = ProviderAttemptRecord(
                attempt_id=attempt_id,
                request_id=request_id,
                provider=provider.upper(),
                model_id=model_id,
                local_or_remote=local_or_remote,
                is_fallback=is_fallback,
            )
            self._active_attempts[attempt_id] = attempt_rec

            # Link to active logical request if present
            req_rec = self._active_requests.get(request_id)
            if req_rec:
                req_rec.attempts.append(attempt_rec)

            meta = {
                "request_id": request_id,
                "attempt_id": attempt_id,
                "provider": provider.upper(),
                "model_id": model_id,
                "local_or_remote": local_or_remote,
                "is_fallback": is_fallback,
            }
            self._record_event(
                TelemetryEventType.PROVIDER_ATTEMPT_STARTED,
                request_id=request_id,
                attempt_id=attempt_id,
                metadata=meta,
            )

            # Publish provider started event to action_bus
            self._emit_action_bus(
                action_type=ActionType.MODEL_PROVIDER_STARTED,
                status=ActionStatus.PROGRESS,
                title=f"Provider Attempt: {provider.upper()}",
                description=f"Inference started on {provider.upper()} ({model_id})",
                safe_metadata=meta,
            )
            return attempt_id

    def record_first_token(self, attempt_id: str) -> Optional[float]:
        """Record the emission of the first visible streaming token (TTFT)."""
        with self._lock:
            attempt_rec = self._active_attempts.get(attempt_id)
            if not attempt_rec or attempt_rec.first_token_latency_ms is not None:
                return None

            now = time.monotonic()
            ttft_ms = round((now - attempt_rec.start_time_monotonic) * 1000, 2)
            attempt_rec.first_token_latency_ms = ttft_ms

            # Append to provider first-token samples
            prov = attempt_rec.provider
            if prov not in self._first_token_latencies_by_provider:
                self._first_token_latencies_by_provider[prov] = deque(maxlen=self.max_requests)
            self._first_token_latencies_by_provider[prov].append(ttft_ms)

            meta = {
                "request_id": attempt_rec.request_id,
                "attempt_id": attempt_id,
                "provider": prov,
                "model_id": attempt_rec.model_id,
                "first_token_latency_ms": ttft_ms,
            }
            self._record_event(
                TelemetryEventType.FIRST_TOKEN_EMITTED,
                request_id=attempt_rec.request_id,
                attempt_id=attempt_id,
                metadata=meta,
            )

            self._emit_action_bus(
                action_type=ActionType.MODEL_STREAM_FIRST_TOKEN,
                status=ActionStatus.PROGRESS,
                title=f"Stream First Token: {prov}",
                description=f"First token emitted after {ttft_ms}ms",
                safe_metadata=meta,
            )
            return ttft_ms

    def complete_provider_attempt(
        self,
        attempt_id: str,
        outcome: str,
        error: Optional[Exception] = None,
        tokens_input: Optional[int] = None,
        tokens_output: Optional[int] = None,
    ) -> None:
        """Finalize an individual provider execution attempt."""
        with self._lock:
            attempt_rec = self._active_attempts.pop(attempt_id, None)
            if not attempt_rec:
                return

            attempt_rec.end_time_monotonic = time.monotonic()
            duration_ms = round(
                (attempt_rec.end_time_monotonic - attempt_rec.start_time_monotonic) * 1000, 2
            )
            attempt_rec.duration_ms = duration_ms
            attempt_rec.outcome = outcome
            attempt_rec.error_category = (
                normalize_error_category(error) if error else None
            )

            # Record token usage only if trustworthy provider data is supplied
            attempt_rec.tokens_input = tokens_input
            attempt_rec.tokens_output = tokens_output

            # Calculate cost estimate only if pricing is configured
            attempt_rec.cost_estimate_usd = self._calculate_cost(
                provider=attempt_rec.provider,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
            )

            # Record provider latency sample if successful
            prov = attempt_rec.provider
            if outcome == "SUCCESS":
                if prov not in self._latencies_by_provider:
                    self._latencies_by_provider[prov] = deque(maxlen=self.max_requests)
                self._latencies_by_provider[prov].append(duration_ms)

            meta = {
                "request_id": attempt_rec.request_id,
                "attempt_id": attempt_id,
                "provider": prov,
                "model_id": attempt_rec.model_id,
                "local_or_remote": attempt_rec.local_or_remote,
                "outcome": outcome,
                "duration_ms": duration_ms,
                "first_token_latency_ms": attempt_rec.first_token_latency_ms,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
                "cost_estimate_usd": attempt_rec.cost_estimate_usd,
                "error_category": attempt_rec.error_category,
                "is_fallback": attempt_rec.is_fallback,
            }

            event_type = (
                TelemetryEventType.PROVIDER_ATTEMPT_COMPLETED
                if outcome == "SUCCESS"
                else (
                    TelemetryEventType.TIMEOUT
                    if attempt_rec.error_category == "PROVIDER_TIMEOUT"
                    else (
                        TelemetryEventType.RATE_LIMIT
                        if attempt_rec.error_category == "RATE_LIMIT_EXCEEDED"
                        else TelemetryEventType.PROVIDER_FAILURE
                    )
                )
            )

            self._record_event(
                event_type,
                request_id=attempt_rec.request_id,
                attempt_id=attempt_id,
                metadata=meta,
            )

            # Action bus publication
            bus_action = (
                ActionType.MODEL_PROVIDER_COMPLETED
                if outcome == "SUCCESS"
                else ActionType.MODEL_PROVIDER_FAILED
            )
            bus_status = ActionStatus.COMPLETED if outcome == "SUCCESS" else ActionStatus.FAILED
            self._emit_action_bus(
                action_type=bus_action,
                status=bus_status,
                title=f"Provider Attempt {outcome}: {prov}",
                description=f"Attempt {attempt_id[:8]} on {prov} finished with {outcome} in {duration_ms}ms",
                safe_metadata=meta,
            )

    def record_fallback_started(
        self,
        request_id: str,
        from_provider: str,
        to_provider: str,
        fallback_model: Optional[str] = None,
        reason_code: Optional[str] = None,
    ) -> None:
        """Record initiation of a controlled fallback."""
        with self._lock:
            meta = {
                "request_id": request_id,
                "fallback_from_provider": from_provider.upper(),
                "fallback_to_provider": to_provider.upper(),
                "fallback_to_model": fallback_model,
                "fallback_reason_code": reason_code,
            }
            self._record_event(
                TelemetryEventType.FALLBACK_ATTEMPTED,
                request_id=request_id,
                metadata=meta,
            )

            self._emit_action_bus(
                action_type=ActionType.MODEL_FALLBACK_ATTEMPTED,
                status=ActionStatus.PROGRESS,
                title=f"Fallback: {from_provider.upper()} -> {to_provider.upper()}",
                description=f"Controlled fallback initiated: {from_provider.upper()} -> {to_provider.upper()}",
                safe_metadata=meta,
            )

    def record_circuit_change(
        self,
        provider: str,
        prev_state: str,
        new_state: str,
    ) -> None:
        """Record circuit-breaker state transition."""
        with self._lock:
            meta = {
                "provider": provider.upper(),
                "circuit_prev_state": prev_state,
                "circuit_state": new_state,
            }
            self._record_event(
                TelemetryEventType.CIRCUIT_STATE_CHANGED,
                request_id="system",
                metadata=meta,
            )

            self._emit_action_bus(
                action_type=ActionType.MODEL_CIRCUIT_STATE_CHANGED,
                status=ActionStatus.PROGRESS,
                title=f"Circuit State: {provider.upper()} {new_state}",
                description=f"Circuit for {provider.upper()} changed from {prev_state} to {new_state}",
                safe_metadata=meta,
            )

    def record_stream_cancelled(
        self,
        request_id: str,
        attempt_id: Optional[str] = None,
    ) -> None:
        """Record explicit client cancellation of an in-flight stream."""
        with self._lock:
            meta = {
                "request_id": request_id,
                "attempt_id": attempt_id,
                "outcome": "CANCELLED",
                "error_category": "CLIENT_CANCELLED",
            }
            self._record_event(
                TelemetryEventType.STREAM_CANCELLED,
                request_id=request_id,
                attempt_id=attempt_id,
                metadata=meta,
            )

            if attempt_id and attempt_id in self._active_attempts:
                self.complete_provider_attempt(
                    attempt_id=attempt_id,
                    outcome="CANCELLED",
                )
            if request_id in self._active_requests:
                self.complete_request(
                    request_id=request_id,
                    status="CANCELLED",
                )

    # -----------------------------------------------------------------------
    # AGGREGATIONS & OPERATIONAL REPORTING
    # -----------------------------------------------------------------------

    def get_summary(self) -> Dict[str, Any]:
        """Generate comprehensive operational telemetry and performance governance summary."""
        with self._lock:
            completed_list = list(self._completed_requests)
            total_requests = len(completed_list)

            success_requests = sum(1 for r in completed_list if r.status == "SUCCESS")
            failed_requests = sum(1 for r in completed_list if r.status == "FAILED")
            security_denied_requests = sum(1 for r in completed_list if r.status == "SECURITY_DENIED")
            cancelled_requests = sum(1 for r in completed_list if r.status == "CANCELLED")
            fallback_requests = sum(1 for r in completed_list if r.fallback_occurred)

            fallback_rate = (
                round(fallback_requests / total_requests, 4) if total_requests > 0 else 0.0
            )

            # Local vs remote counts
            local_requests = sum(1 for r in completed_list if r.local_or_remote_target == "local")
            remote_requests = sum(1 for r in completed_list if r.local_or_remote_target == "remote")

            # Collect all attempts
            all_attempts: List[ProviderAttemptRecord] = []
            for r in completed_list:
                all_attempts.extend(r.attempts)

            # Per-provider summaries
            providers_summary: Dict[str, Any] = {}
            known_providers = set(self._latencies_by_provider.keys()) | {
                a.provider for a in all_attempts
            } | {"OLLAMA", "GROK", "HUGGINGFACE_REMOTE", "HUGGINGFACE_LOCAL"}

            total_cost_usd: Optional[float] = None
            total_tokens_in: Optional[int] = None
            total_tokens_out: Optional[int] = None

            for prov in sorted(known_providers):
                prov_attempts = [a for a in all_attempts if a.provider == prov]
                attempts_count = len(prov_attempts)
                prov_success = sum(1 for a in prov_attempts if a.outcome == "SUCCESS")
                prov_fail = sum(1 for a in prov_attempts if a.outcome not in ("SUCCESS", "CANCELLED", None))
                prov_timeouts = sum(1 for a in prov_attempts if a.error_category == "PROVIDER_TIMEOUT")
                prov_rate_limits = sum(1 for a in prov_attempts if a.error_category == "RATE_LIMIT_EXCEEDED")

                latencies = list(self._latencies_by_provider.get(prov, []))
                latency_stats = compute_percentiles(latencies)

                ttft_samples = list(self._first_token_latencies_by_provider.get(prov, []))
                ttft_stats = compute_percentiles(ttft_samples)

                # Token & Cost tracking
                prov_tokens_in = None
                prov_tokens_out = None
                prov_cost = None

                trusted_in = [a.tokens_input for a in prov_attempts if a.tokens_input is not None]
                if trusted_in:
                    prov_tokens_in = sum(trusted_in)
                    total_tokens_in = (total_tokens_in or 0) + prov_tokens_in

                trusted_out = [a.tokens_output for a in prov_attempts if a.tokens_output is not None]
                if trusted_out:
                    prov_tokens_out = sum(trusted_out)
                    total_tokens_out = (total_tokens_out or 0) + prov_tokens_out

                trusted_costs = [a.cost_estimate_usd for a in prov_attempts if a.cost_estimate_usd is not None]
                if trusted_costs:
                    prov_cost = round(sum(trusted_costs), 6)
                    total_cost_usd = round((total_cost_usd or 0.0) + prov_cost, 6)

                providers_summary[prov.lower()] = {
                    "provider": prov,
                    "attempts_count": attempts_count,
                    "success_count": prov_success,
                    "failure_count": prov_fail,
                    "timeout_count": prov_timeouts,
                    "rate_limit_count": prov_rate_limits,
                    "latency": latency_stats,
                    "first_token_latency": ttft_stats,
                    "tokens_input": prov_tokens_in,
                    "tokens_output": prov_tokens_out,
                    "cost_estimate_usd": prov_cost,
                }

            return {
                "logical_requests": {
                    "total": total_requests,
                    "success": success_requests,
                    "failed": failed_requests,
                    "security_denied": security_denied_requests,
                    "cancelled": cancelled_requests,
                    "fallback_count": fallback_requests,
                    "fallback_rate": fallback_rate,
                    "local_count": local_requests,
                    "remote_count": remote_requests,
                },
                "providers": providers_summary,
                "governance": {
                    "total_tokens_input": total_tokens_in,
                    "total_tokens_output": total_tokens_out,
                    "total_cost_usd": total_cost_usd,
                    "pricing_configured": bool(self._pricing_config),
                },
                "buffer_status": {
                    "events_retained": len(self._events),
                    "max_events": self.max_events,
                    "requests_retained": len(self._completed_requests),
                    "max_requests": self.max_requests,
                    "active_requests": len(self._active_requests),
                    "active_attempts": len(self._active_attempts),
                },
            }

    def get_events(
        self,
        limit: int = 50,
        event_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return bounded list of recent sanitized telemetry audit events."""
        with self._lock:
            lim = min(max(1, limit), 200)
            events_list = list(self._events)
            if event_type:
                events_list = [
                    e for e in events_list if e.event_type.value == event_type.upper()
                ]
            return [e.model_dump() for e in events_list[-lim:]]

    # -----------------------------------------------------------------------
    # INTERNAL HELPERS
    # -----------------------------------------------------------------------

    def _record_event(
        self,
        event_type: TelemetryEventType,
        request_id: str,
        attempt_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Internal helper to sanitize and record an audit event."""
        clean_meta = sanitize_telemetry_metadata(metadata or {})
        evt = TelemetryEvent(
            event_type=event_type,
            request_id=request_id,
            attempt_id=attempt_id,
            metadata=clean_meta,
        )
        self._events.append(evt)

    def _calculate_cost(
        self,
        provider: str,
        tokens_input: Optional[int],
        tokens_output: Optional[int],
    ) -> Optional[float]:
        """Deterministically calculate cost in USD only when explicit pricing is configured."""
        pricing = self._pricing_config.get(provider.upper())
        if not pricing:
            return None

        prompt_price = pricing.get("prompt_price_per_million")
        comp_price = pricing.get("completion_price_per_million")

        cost = 0.0
        calculated = False

        if prompt_price is not None and tokens_input is not None:
            cost += (tokens_input / 1_000_000.0) * prompt_price
            calculated = True

        if comp_price is not None and tokens_output is not None:
            cost += (tokens_output / 1_000_000.0) * comp_price
            calculated = True

        return round(cost, 6) if calculated else None

    def _prune_stale_active_requests(self) -> None:
        """Prune active requests that have been in flight longer than 3600 seconds."""
        now = time.monotonic()
        stale_ids = [
            req_id
            for req_id, rec in self._active_requests.items()
            if (now - rec.start_time_monotonic) > 3600.0
        ]
        for req_id in stale_ids:
            self.complete_request(req_id, status="CANCELLED")

    def _emit_action_bus(
        self,
        action_type: ActionType,
        status: ActionStatus,
        title: str,
        description: str,
        safe_metadata: Dict[str, Any],
    ) -> None:
        """Publish or emit event to action_bus safely without disrupting inference."""
        try:
            event = ActionEvent(
                action_type=action_type,
                status=status,
                title=title,
                description=description,
                safe_metadata=sanitize_telemetry_metadata(safe_metadata),
            )
            action_bus.emit(event)
        except Exception as exc:
            logger.debug(f"[TELEMETRY] Failed emitting to action_bus: {exc}")


# Global default model telemetry service singleton
model_telemetry_service = ModelTelemetryService()
