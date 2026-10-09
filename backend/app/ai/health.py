"""RYVEN 3.0 — AI Provider Health Tracker & Circuit Breaker.

Maintains thread-safe, bounded, secret-free health telemetry and deterministic
circuit breakers for local and remote model providers (M17.10 Phase 3).
"""

from __future__ import annotations

from collections import deque
from enum import Enum
import threading
import time
from typing import Any, Deque, Dict, Optional, Union

from app.ai.contracts import (
    AuthenticationError,
    ProviderInvalidRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    SecurityViolationError,
)
from app.ai.models import ModelProvider
from app.core.logging_config import logger


class CircuitState(str, Enum):
    """Operational states of the provider circuit breaker."""

    CLOSED = "CLOSED"        # Normal operational state; calls pass through
    OPEN = "OPEN"            # Tripped; ordinary calls are suppressed
    HALF_OPEN = "HALF_OPEN"  # Testing recovery with a single probe call


class ProviderHealthRecord:
    """Thread-safe bounded health metric record for a single inference provider."""

    def __init__(self, provider_name: str, max_latencies: int = 50) -> None:
        self.provider_name: str = provider_name
        self.max_latencies: int = max_latencies

        self.success_count: int = 0
        self.failure_count: int = 0
        self.consecutive_failures: int = 0
        self.consecutive_successes: int = 0
        self.timeout_count: int = 0
        self.rate_limit_count: int = 0

        self.last_success_time: Optional[float] = None
        self.last_failure_time: Optional[float] = None
        self.last_error_type: Optional[str] = None

        self._latencies: Deque[float] = deque(maxlen=max_latencies)
        self._lock: threading.RLock = threading.RLock()

    def record_success(self, latency_ms: float) -> None:
        """Record successful invocation."""
        with self._lock:
            self.success_count += 1
            self.consecutive_successes += 1
            self.consecutive_failures = 0
            self.last_success_time = time.monotonic()
            if latency_ms >= 0:
                self._latencies.append(round(latency_ms, 2))

    def record_failure(self, error: Exception, latency_ms: Optional[float] = None) -> None:
        """Record failed invocation classifying error category without secret leakage."""
        with self._lock:
            self.failure_count += 1
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            self.last_failure_time = time.monotonic()

            # Categorize error type safely (never store raw error messages)
            if isinstance(error, ProviderTimeoutError):
                self.timeout_count += 1
                self.last_error_type = "PROVIDER_TIMEOUT"
            elif isinstance(error, RateLimitError):
                self.rate_limit_count += 1
                self.last_error_type = "RATE_LIMIT_EXCEEDED"
            elif isinstance(error, AuthenticationError):
                self.last_error_type = "AUTHENTICATION_FAILED"
            elif isinstance(error, SecurityViolationError):
                self.last_error_type = "SECURITY_VIOLATION"
            elif isinstance(error, ProviderInvalidRequestError):
                self.last_error_type = "INVALID_REQUEST"
            elif isinstance(error, ProviderUnavailableError):
                self.last_error_type = "PROVIDER_UNAVAILABLE"
            else:
                self.last_error_type = error.__class__.__name__

            if latency_ms is not None and latency_ms >= 0:
                self._latencies.append(round(latency_ms, 2))

    def get_summary(self) -> Dict[str, Any]:
        """Export sanitized telemetry summary without credentials or unbounded data."""
        with self._lock:
            latencies_list = list(self._latencies)
            avg_latency = round(sum(latencies_list) / len(latencies_list), 2) if latencies_list else None
            p95_latency = None
            if latencies_list:
                sorted_lats = sorted(latencies_list)
                idx = int(0.95 * len(sorted_lats))
                p95_latency = sorted_lats[min(idx, len(sorted_lats) - 1)]

            return {
                "provider": self.provider_name,
                "success_count": self.success_count,
                "failure_count": self.failure_count,
                "consecutive_failures": self.consecutive_failures,
                "consecutive_successes": self.consecutive_successes,
                "timeout_count": self.timeout_count,
                "rate_limit_count": self.rate_limit_count,
                "last_error_type": self.last_error_type,
                "recent_samples_count": len(latencies_list),
                "avg_latency_ms": avg_latency,
                "p95_latency_ms": p95_latency,
                "has_succeeded": self.success_count > 0,
            }

    def reset(self) -> None:
        """Reset internal metrics."""
        with self._lock:
            self.success_count = 0
            self.failure_count = 0
            self.consecutive_failures = 0
            self.consecutive_successes = 0
            self.timeout_count = 0
            self.rate_limit_count = 0
            self.last_success_time = None
            self.last_failure_time = None
            self.last_error_type = None
            self._latencies.clear()


class CircuitBreaker:
    """Configurable circuit breaker with half-open recovery probing."""

    def __init__(
        self,
        threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.threshold: int = max(1, threshold)
        self.cooldown_seconds: float = max(0.1, cooldown_seconds)

        self._state: CircuitState = CircuitState.CLOSED
        self._consecutive_failures: int = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_probe_in_flight: bool = False
        self._lock: threading.RLock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        """Inspect current circuit breaker state, transitioning if cooldown elapsed."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._last_failure_time is not None:
                    elapsed = time.monotonic() - self._last_failure_time
                    if elapsed >= self.cooldown_seconds:
                        self._state = CircuitState.HALF_OPEN
                        self._half_open_probe_in_flight = False
            return self._state

    def can_execute(self) -> bool:
        """Determine if a call is permitted through the breaker."""
        with self._lock:
            current_state = self.state
            if current_state == CircuitState.CLOSED:
                return True
            if current_state == CircuitState.HALF_OPEN:
                # In half-open, allow a single probe execution
                if not self._half_open_probe_in_flight:
                    self._half_open_probe_in_flight = True
                    return True
                return False
            # CircuitState.OPEN
            return False

    def is_open(self) -> bool:
        """Return True if circuit is currently open and ordinary calls are suppressed."""
        return self.state == CircuitState.OPEN

    def record_success(self) -> None:
        """Reset circuit breaker to CLOSED on successful execution."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        """Record failure, tripping circuit if threshold is reached or probe fails."""
        with self._lock:
            self._consecutive_failures += 1
            self._last_failure_time = time.monotonic()
            self._half_open_probe_in_flight = False

            if self._state == CircuitState.HALF_OPEN:
                # Probe failed: immediate trip back to OPEN
                self._state = CircuitState.OPEN
            elif self._consecutive_failures >= self.threshold:
                self._state = CircuitState.OPEN

    def trip(self) -> None:
        """Manually trip the circuit breaker open."""
        with self._lock:
            self._state = CircuitState.OPEN
            self._last_failure_time = time.monotonic()
            self._half_open_probe_in_flight = False

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._last_failure_time = None
            self._half_open_probe_in_flight = False


class ProviderHealthTracker:
    """Central registry and tracker for provider health metrics and circuit breakers."""

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
        history_buffer_size: int = 50,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.history_buffer_size = history_buffer_size

        self._records: Dict[str, ProviderHealthRecord] = {}
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock: threading.RLock = threading.RLock()

    def _canonical_key(self, provider: Union[ModelProvider, str]) -> str:
        """Normalize provider name to uppercase canonical string."""
        if isinstance(provider, ModelProvider):
            return provider.value.upper()
        return str(provider).strip().upper()

    def _get_or_create(self, provider: Union[ModelProvider, str]) -> tuple[ProviderHealthRecord, CircuitBreaker]:
        key = self._canonical_key(provider)
        with self._lock:
            if key not in self._records:
                self._records[key] = ProviderHealthRecord(key, max_latencies=self.history_buffer_size)
            if key not in self._breakers:
                self._breakers[key] = CircuitBreaker(
                    threshold=self.failure_threshold,
                    cooldown_seconds=self.cooldown_seconds,
                )
            return self._records[key], self._breakers[key]

    def record_success(self, provider: Union[ModelProvider, str], latency_ms: float) -> None:
        """Record successful invocation for a provider and reset its circuit breaker."""
        rec, breaker = self._get_or_create(provider)
        rec.record_success(latency_ms)
        prev_state = breaker.state.value
        breaker.record_success()
        new_state = breaker.state.value
        if prev_state != new_state:
            try:
                from app.ai.telemetry import model_telemetry_service
                model_telemetry_service.record_circuit_change(self._canonical_key(provider), prev_state, new_state)
            except Exception:
                pass

    def record_failure(
        self,
        provider: Union[ModelProvider, str],
        error: Exception,
        latency_ms: Optional[float] = None,
    ) -> None:
        """Record failure for a provider and update circuit breaker state."""
        rec, breaker = self._get_or_create(provider)
        rec.record_failure(error, latency_ms)

        # Do not trip circuit breaker on client validation or authentication errors;
        # Trip only on availability, timeout, and connection failures
        if isinstance(error, (ProviderUnavailableError, ProviderTimeoutError, RateLimitError)):
            prev_state = breaker.state.value
            breaker.record_failure()
            new_state = breaker.state.value
            if prev_state != new_state:
                try:
                    from app.ai.telemetry import model_telemetry_service
                    model_telemetry_service.record_circuit_change(self._canonical_key(provider), prev_state, new_state)
                except Exception:
                    pass

    def can_execute(self, provider: Union[ModelProvider, str]) -> bool:
        """Check if the provider circuit breaker allows execution."""
        _, breaker = self._get_or_create(provider)
        return breaker.can_execute()

    def is_circuit_open(self, provider: Union[ModelProvider, str]) -> bool:
        """Check if provider circuit is currently open."""
        _, breaker = self._get_or_create(provider)
        return breaker.is_open()

    def get_circuit_state(self, provider: Union[ModelProvider, str]) -> CircuitState:
        """Get circuit state enum for provider."""
        _, breaker = self._get_or_create(provider)
        return breaker.state

    def get_health_summary(self, provider: Optional[Union[ModelProvider, str]] = None) -> Dict[str, Any]:
        """Fetch sanitized health and circuit status for one or all providers."""
        with self._lock:
            if provider is not None:
                rec, breaker = self._get_or_create(provider)
                summary = rec.get_summary()
                summary["circuit_state"] = breaker.state.value
                summary["can_execute"] = breaker.can_execute()
                return summary

            result: Dict[str, Any] = {}
            for key, rec in self._records.items():
                breaker = self._breakers.get(key)
                summary = rec.get_summary()
                if breaker:
                    summary["circuit_state"] = breaker.state.value
                    summary["can_execute"] = breaker.can_execute()
                result[key] = summary
            return result

    def reset(self, provider: Optional[Union[ModelProvider, str]] = None) -> None:
        """Reset metrics and circuit breakers."""
        with self._lock:
            if provider is not None:
                key = self._canonical_key(provider)
                if key in self._records:
                    self._records[key].reset()
                if key in self._breakers:
                    self._breakers[key].reset()
            else:
                for rec in self._records.values():
                    rec.reset()
                for breaker in self._breakers.values():
                    breaker.reset()


# Global default health tracker singleton
provider_health_tracker = ProviderHealthTracker()
provider_circuit_breaker = provider_health_tracker
