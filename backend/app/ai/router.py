"""RYVEN 3.0 — Local-First AI Model Router & Controlled Fallback Engine.

Determines optimal, deterministic, policy-aware model assignment for cognitive tasks
adhering to local-first privacy, hardware constraints, provider health metrics, and
circuit-breaker states (M17.10 Phase 3).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple, Union

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.config import RouterConfig, default_router_config
from app.ai.contracts import (
    AIProviderError,
    AIRequest,
    AIResponse,
    AIStreamChunk,
    AuthenticationError,
    ProviderAdapter,
    ProviderInvalidRequestError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    SecurityViolationError,
    StreamEventType,
    sanitize_dict,
)
from app.ai.hardware import HardwareDiagnostics, HardwareProfile
from app.ai.health import (
    CircuitState,
    ProviderHealthTracker,
    provider_health_tracker,
)
from app.ai.models import (
    ComplexityTier,
    ModelProfile,
    ModelProvider,
    RoutingDecision,
    TaskType,
)
from app.ai.registry import ModelRegistry, model_registry
from app.ai.security import ModelSecurityPolicy, model_security_policy
from app.core.logging_config import logger


# ============================================================================
# DETERMINISTIC TASK COMPLEXITY & HEURISTICS ESTIMATOR
# ============================================================================

_ALGORITHMIC_KEYWORDS = {
    "algorithm",
    "optimize",
    "optimization",
    "refactor",
    "complexity",
    "recursion",
    "recursive",
    "dynamic programming",
    "memoization",
    "binary search",
    "backtracking",
    "asymptotic",
    "concurrency",
    "deadlock",
    "mutex",
    "race condition",
    "system architecture",
    "distributed",
}

_REASONING_KEYWORDS = {
    "step-by-step",
    "mathematical proof",
    "theorem",
    "derivation",
    "formal logic",
    "deductive",
    "inductive",
    "root cause analysis",
    "rca",
    "debug kernel",
    "disassemble",
}

_CODE_SNIPPET_PATTERNS = [
    "```",
    "def ",
    "class ",
    "async def ",
    "import ",
    "from ",
    "return ",
    "SELECT ",
    "FROM ",
    "WHERE ",
    "function ",
    "const ",
    "interface ",
]


def estimate_task_complexity(
    prompt: str,
    task_type: TaskType,
    structured_output_schema: Optional[Dict[str, Any]] = None,
) -> Tuple[ComplexityTier, int, bool]:
    """Deterministically classify task complexity, required context tokens, and latency sensitivity.

    Does NOT invoke any secondary LLM.
    Returns: (complexity_tier, estimated_context_length, is_latency_sensitive)
    """
    clean_p = prompt.strip()
    p_len = len(clean_p)
    lower_p = clean_p.lower()

    # Context window estimate based on character length (~4 chars per token) + safety buffer
    estimated_input_tokens = max(128, p_len // 4)
    context_size_requirement = min(32768, max(4096, (estimated_input_tokens + 512) * 2))

    # Latency sensitivity
    latency_sensitive = task_type == TaskType.CLASSIFICATION or (
        task_type == TaskType.GENERAL_REASONING and p_len < 120
    )

    # Heuristic scoring
    has_code_snippet = any(pat in clean_p for pat in _CODE_SNIPPET_PATTERNS)
    algo_keyword_hits = sum(1 for kw in _ALGORITHMIC_KEYWORDS if kw in lower_p)
    reasoning_keyword_hits = sum(1 for kw in _REASONING_KEYWORDS if kw in lower_p)
    has_complex_schema = False
    if structured_output_schema:
        props = structured_output_schema.get("properties", {})
        has_complex_schema = len(props) > 4 or any(
            isinstance(v, dict) and v.get("type") in ("object", "array")
            for v in props.values()
        )

    # High complexity triggers
    if task_type in (TaskType.GENERAL_REASONING, TaskType.CODE, TaskType.PLANNING):
        if (
            algo_keyword_hits >= 2
            or (has_code_snippet and algo_keyword_hits >= 1)
            or reasoning_keyword_hits >= 2
            or has_complex_schema
            or (p_len > 2500 and (has_code_snippet or algo_keyword_hits >= 1))
            or p_len > 4000
        ):
            return ComplexityTier.HIGH, context_size_requirement, latency_sensitive

    # Medium complexity triggers
    if (
        task_type in (TaskType.PLANNING, TaskType.CODE)
        or has_code_snippet
        or algo_keyword_hits >= 1
        or reasoning_keyword_hits >= 1
        or p_len > 600
        or structured_output_schema is not None
    ):
        return ComplexityTier.MEDIUM, context_size_requirement, latency_sensitive

    # Default to Low complexity
    return ComplexityTier.LOW, context_size_requirement, latency_sensitive


# ============================================================================
# AUTHORITATIVE MODEL ROUTER
# ============================================================================

class ModelRouter:
    """Intelligent, deterministic local-first multi-model router with controlled fallback."""

    def __init__(
        self,
        registry: Optional[ModelRegistry] = None,
        security_policy: Optional[ModelSecurityPolicy] = None,
        health_tracker: Optional[ProviderHealthTracker] = None,
        config: Optional[RouterConfig] = None,
        adapters: Optional[Dict[str, ProviderAdapter]] = None,
    ) -> None:
        self.registry = registry or model_registry
        self.security_policy = security_policy or model_security_policy
        self.health_tracker = health_tracker or provider_health_tracker
        self.config = config or default_router_config

        self._adapters: Dict[str, ProviderAdapter] = adapters or {}

    def get_adapter(self, provider: Union[ModelProvider, str]) -> ProviderAdapter:
        """Resolve or lazily initialize the adapter for the requested provider."""
        key = provider.value.upper() if isinstance(provider, ModelProvider) else str(provider).strip().upper()
        if key in self._adapters:
            return self._adapters[key]

        if key == "OLLAMA":
            from app.ai.adapters import OllamaAdapter
            adapter = OllamaAdapter()
            self._adapters[key] = adapter
            return adapter
        elif key == "GROK":
            from app.ai.adapters import GrokAdapter
            adapter = GrokAdapter()
            self._adapters[key] = adapter
            return adapter
        elif key in ("HUGGINGFACE_LOCAL", "HUGGINGFACE_REMOTE", "HUGGINGFACE"):
            from app.ai.adapters import HuggingFaceAdapter
            prefer_local = "REMOTE" not in key
            adapter = HuggingFaceAdapter(prefer_local=prefer_local)
            self._adapters[key] = adapter
            return adapter

        raise ProviderUnavailableError(f"No adapter registered for provider '{provider}'")

    def register_adapter(self, provider: Union[ModelProvider, str], adapter: ProviderAdapter) -> None:
        """Register or override an adapter for testing or specialized transports."""
        key = provider.value.upper() if isinstance(provider, ModelProvider) else str(provider).strip().upper()
        self._adapters[key] = adapter

    async def route(
        self,
        task_type: TaskType,
        prompt: str = "",
        allow_remote: bool = False,
        preferred_model_id: Optional[str] = None,
        sync_hardware: bool = False,
        structured_output_schema: Optional[Dict[str, Any]] = None,
    ) -> RoutingDecision:
        """Deterministically select the optimal model profile adhering to safety and health policies."""
        # 1. Hardware diagnostics & sync
        hardware = await HardwareDiagnostics.get_profile()
        if sync_hardware and hardware.ollama_online:
            self.registry.sync_with_ollama(hardware.installed_models)

        # 2. Deterministic complexity analysis
        complexity, context_size, latency_sensitive = estimate_task_complexity(
            prompt=prompt,
            task_type=task_type,
            structured_output_schema=structured_output_schema,
        )

        default_local = self.config.default_local_model
        default_local_provider = "OLLAMA"

        # Check for sensitive data whenever remote is requested
        if allow_remote:
            is_sensitive, sens_reason = self.security_policy.scan_for_sensitive_data(prompt)
            if is_sensitive:
                return RoutingDecision(
                    selected_model=default_local,
                    provider=default_local_provider,
                    task_type=task_type,
                    reason=f"Remote inference requested but prompt contains sensitive data ({sens_reason}); remaining local",
                    reason_code="PRIVACY_POLICY_BLOCKED",
                    fallback=default_local,
                    fallback_eligible=True,
                    fallback_model=default_local,
                    fallback_provider=default_local_provider,
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=4.36,
                    estimated_complexity=complexity.value,
                    context_size_requirement=context_size,
                    latency_sensitive=latency_sensitive,
                    health_summary=self.health_tracker.get_health_summary(default_local_provider),
                )

        # 3. Check explicit user preference if specified
        if preferred_model_id:
            profile = self.registry.get_model(preferred_model_id)
            if profile and profile.enabled:
                if profile.local_or_remote == "local" and profile.installed:
                    # Check circuit breaker for preferred local provider
                    if self.health_tracker.is_circuit_open(profile.provider):
                        return RoutingDecision(
                            selected_model=default_local,
                            provider=default_local_provider,
                            task_type=task_type,
                            reason=f"Preferred model '{profile.id}' provider circuit is OPEN; routing to local default",
                            reason_code="CIRCUIT_OPEN",
                            fallback=default_local,
                            fallback_eligible=True,
                            fallback_model=default_local,
                            fallback_provider=default_local_provider,
                            remote_allowed=False,
                            local_or_remote="local",
                            memory_estimate_gb=4.36,
                            estimated_complexity=complexity.value,
                            context_size_requirement=context_size,
                            latency_sensitive=latency_sensitive,
                            health_summary=self.health_tracker.get_health_summary(profile.provider),
                        )

                    return RoutingDecision(
                        selected_model=profile.id,
                        provider=profile.provider.value,
                        task_type=task_type,
                        reason=f"Explicit user preference for local model '{profile.id}'",
                        reason_code="EXPLICIT_PREFERENCE",
                        fallback=default_local,
                        fallback_eligible=True,
                        fallback_model=default_local,
                        fallback_provider=default_local_provider,
                        remote_allowed=False,
                        local_or_remote="local",
                        memory_estimate_gb=profile.memory_estimate_gb,
                        estimated_complexity=complexity.value,
                        context_size_requirement=context_size,
                        latency_sensitive=latency_sensitive,
                        health_summary=self.health_tracker.get_health_summary(profile.provider),
                    )

                elif profile.local_or_remote == "remote":
                    # Remote preference requires explicit user allowance and system security permission
                    remote_permitted = (
                        allow_remote
                        and (self.security_policy.allow_remote_inference or self.config.allow_remote_inference)
                    )
                    if remote_permitted:
                        is_sensitive, sens_reason = self.security_policy.scan_for_sensitive_data(prompt)
                        if is_sensitive:
                            return RoutingDecision(
                                selected_model=default_local,
                                provider=default_local_provider,
                                task_type=task_type,
                                reason=f"Remote model '{profile.id}' blocked by privacy scan ({sens_reason}); remaining local",
                                reason_code="PRIVACY_POLICY_BLOCKED",
                                fallback=default_local,
                                fallback_eligible=True,
                                fallback_model=default_local,
                                fallback_provider=default_local_provider,
                                remote_allowed=False,
                                local_or_remote="local",
                                memory_estimate_gb=4.36,
                                estimated_complexity=complexity.value,
                                context_size_requirement=context_size,
                                latency_sensitive=latency_sensitive,
                                health_summary=self.health_tracker.get_health_summary(profile.provider),
                            )

                        if self.health_tracker.is_circuit_open(profile.provider):
                            return RoutingDecision(
                                selected_model=default_local,
                                provider=default_local_provider,
                                task_type=task_type,
                                reason=f"Remote provider '{profile.provider.value}' circuit is OPEN; falling back to local engine",
                                reason_code="CIRCUIT_OPEN",
                                fallback=default_local,
                                fallback_eligible=True,
                                fallback_model=default_local,
                                fallback_provider=default_local_provider,
                                remote_allowed=False,
                                local_or_remote="local",
                                memory_estimate_gb=4.36,
                                estimated_complexity=complexity.value,
                                context_size_requirement=context_size,
                                latency_sensitive=latency_sensitive,
                                health_summary=self.health_tracker.get_health_summary(profile.provider),
                            )

                        return RoutingDecision(
                            selected_model=profile.id,
                            provider=profile.provider.value,
                            task_type=task_type,
                            reason=f"Explicit user preference for authorized remote model '{profile.id}'",
                            reason_code="REMOTE_EXPLICITLY_ALLOWED",
                            fallback=default_local,
                            fallback_eligible=True,
                            fallback_model=default_local,
                            fallback_provider=default_local_provider,
                            remote_allowed=True,
                            local_or_remote="remote",
                            memory_estimate_gb=0.0,
                            estimated_complexity=complexity.value,
                            context_size_requirement=context_size,
                            latency_sensitive=latency_sensitive,
                            health_summary=self.health_tracker.get_health_summary(profile.provider),
                        )
                    else:
                        # Remote preference not permitted by security policy
                        return RoutingDecision(
                            selected_model=default_local,
                            provider=default_local_provider,
                            task_type=task_type,
                            reason=f"Remote model '{profile.id}' not permitted by security policy; assigned local default",
                            reason_code="PRIVACY_POLICY_BLOCKED",
                            fallback=default_local,
                            fallback_eligible=True,
                            fallback_model=default_local,
                            fallback_provider=default_local_provider,
                            remote_allowed=False,
                            local_or_remote="local",
                            memory_estimate_gb=4.36,
                            estimated_complexity=complexity.value,
                            context_size_requirement=context_size,
                            latency_sensitive=latency_sensitive,
                            health_summary=self.health_tracker.get_health_summary(profile.provider),
                        )

        # 4. Multimodal / Vision Tasks
        if task_type == TaskType.VISION:
            candidates = self.registry.list_models(task_type=TaskType.VISION, installed_only=True, enabled_only=True)
            for cand in candidates:
                if not self.health_tracker.is_circuit_open(cand.provider):
                    return RoutingDecision(
                        selected_model=cand.id,
                        provider=cand.provider.value,
                        task_type=task_type,
                        reason=f"Installed local vision model '{cand.id}' selected for image analysis",
                        reason_code="TASK_CAPABILITY_MATCH",
                        fallback=default_local,
                        fallback_eligible=True,
                        fallback_model=default_local,
                        fallback_provider=default_local_provider,
                        remote_allowed=False,
                        local_or_remote="local",
                        memory_estimate_gb=cand.memory_estimate_gb,
                        estimated_complexity=complexity.value,
                        context_size_requirement=context_size,
                        latency_sensitive=latency_sensitive,
                        health_summary=self.health_tracker.get_health_summary(cand.provider),
                    )

            # Fallback to local reasoning model if no vision model is installed
            return RoutingDecision(
                selected_model=default_local,
                provider=default_local_provider,
                task_type=task_type,
                reason="No local vision model (e.g. llava:7b) is currently installed; falling back to Qwen 2.5 7B for text description guidance",
                reason_code="LOCAL_DEFAULT",
                fallback=default_local,
                fallback_eligible=True,
                fallback_model=default_local,
                fallback_provider=default_local_provider,
                remote_allowed=False,
                local_or_remote="local",
                memory_estimate_gb=4.36,
                estimated_complexity=complexity.value,
                context_size_requirement=context_size,
                latency_sensitive=latency_sensitive,
                health_summary=self.health_tracker.get_health_summary(default_local_provider),
            )

        # 5. OCR Tasks
        elif task_type == TaskType.OCR:
            candidates = self.registry.list_models(task_type=TaskType.OCR, installed_only=True, enabled_only=True)
            for cand in candidates:
                if not self.health_tracker.is_circuit_open(cand.provider):
                    return RoutingDecision(
                        selected_model=cand.id,
                        provider=cand.provider.value,
                        task_type=task_type,
                        reason=f"Installed local OCR model '{cand.id}' selected",
                        reason_code="TASK_CAPABILITY_MATCH",
                        fallback=default_local,
                        fallback_eligible=True,
                        fallback_model=default_local,
                        fallback_provider=default_local_provider,
                        remote_allowed=False,
                        local_or_remote="local",
                        memory_estimate_gb=cand.memory_estimate_gb,
                        estimated_complexity=complexity.value,
                        context_size_requirement=context_size,
                        latency_sensitive=latency_sensitive,
                        health_summary=self.health_tracker.get_health_summary(cand.provider),
                    )

            return RoutingDecision(
                selected_model=default_local,
                provider=default_local_provider,
                task_type=task_type,
                reason="No specialized local OCR model installed; falling back to default local engine",
                reason_code="LOCAL_DEFAULT",
                fallback=default_local,
                fallback_eligible=True,
                fallback_model=default_local,
                fallback_provider=default_local_provider,
                remote_allowed=False,
                local_or_remote="local",
                memory_estimate_gb=4.36,
                estimated_complexity=complexity.value,
                context_size_requirement=context_size,
                latency_sensitive=latency_sensitive,
                health_summary=self.health_tracker.get_health_summary(default_local_provider),
            )

        # 6. Embedding Tasks
        elif task_type == TaskType.EMBEDDING:
            candidates = self.registry.list_models(task_type=TaskType.EMBEDDING, installed_only=True, enabled_only=True)
            if candidates:
                cand = candidates[0]
                return RoutingDecision(
                    selected_model=cand.id,
                    provider=cand.provider.value,
                    task_type=task_type,
                    reason=f"Local embedding model '{cand.id}' selected for vector retrieval",
                    reason_code="TASK_CAPABILITY_MATCH",
                    fallback=None,
                    fallback_eligible=False,
                    fallback_model=None,
                    fallback_provider=None,
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=cand.memory_estimate_gb,
                    estimated_complexity=complexity.value,
                    context_size_requirement=context_size,
                    latency_sensitive=latency_sensitive,
                    health_summary=self.health_tracker.get_health_summary(cand.provider),
                )
            else:
                return RoutingDecision(
                    selected_model=default_local,
                    provider=default_local_provider,
                    task_type=task_type,
                    reason="Specialized embedding model not installed; using standard local model context",
                    reason_code="LOCAL_DEFAULT",
                    fallback=None,
                    fallback_eligible=False,
                    fallback_model=None,
                    fallback_provider=None,
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=4.36,
                    estimated_complexity=complexity.value,
                    context_size_requirement=context_size,
                    latency_sensitive=latency_sensitive,
                    health_summary=self.health_tracker.get_health_summary(default_local_provider),
                )

        # 7. Speech-to-Text Tasks
        elif task_type == TaskType.SPEECH_TO_TEXT:
            candidates = self.registry.list_models(task_type=TaskType.SPEECH_TO_TEXT, installed_only=True, enabled_only=True)
            if candidates:
                cand = candidates[0]
                return RoutingDecision(
                    selected_model=cand.id,
                    provider=cand.provider.value,
                    task_type=task_type,
                    reason=f"Local speech model '{cand.id}' selected",
                    reason_code="TASK_CAPABILITY_MATCH",
                    fallback=default_local,
                    fallback_eligible=True,
                    fallback_model=default_local,
                    fallback_provider=default_local_provider,
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=cand.memory_estimate_gb,
                    estimated_complexity=complexity.value,
                    context_size_requirement=context_size,
                    latency_sensitive=latency_sensitive,
                    health_summary=self.health_tracker.get_health_summary(cand.provider),
                )
            return RoutingDecision(
                selected_model=default_local,
                provider=default_local_provider,
                task_type=task_type,
                reason="No local speech model installed; falling back to default reasoning engine",
                reason_code="LOCAL_DEFAULT",
                fallback=default_local,
                fallback_eligible=True,
                fallback_model=default_local,
                fallback_provider=default_local_provider,
                remote_allowed=False,
                local_or_remote="local",
                memory_estimate_gb=4.36,
                estimated_complexity=complexity.value,
                context_size_requirement=context_size,
                latency_sensitive=latency_sensitive,
                health_summary=self.health_tracker.get_health_summary(default_local_provider),
            )

        # 8. High-Complexity Cognitive Reasoning / Code / Planning
        # Remote reasoning is ONLY considered when complexity is HIGH and remote is explicitly authorized
        if (
            complexity == ComplexityTier.HIGH
            and allow_remote
            and (self.security_policy.allow_remote_inference or self.config.allow_remote_inference)
        ):
            # Check for sensitive content
            is_sensitive, sens_reason = self.security_policy.scan_for_sensitive_data(prompt)
            if is_sensitive:
                return RoutingDecision(
                    selected_model=default_local,
                    provider=default_local_provider,
                    task_type=task_type,
                    reason=f"High complexity task retained locally: prompt contains sensitive data ({sens_reason})",
                    reason_code="PRIVACY_POLICY_BLOCKED",
                    fallback=default_local,
                    fallback_eligible=True,
                    fallback_model=default_local,
                    fallback_provider=default_local_provider,
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=4.36,
                    estimated_complexity=complexity.value,
                    context_size_requirement=context_size,
                    latency_sensitive=latency_sensitive,
                    health_summary=self.health_tracker.get_health_summary(default_local_provider),
                )

            # Find active remote candidate
            remote_candidates = self.registry.list_models(
                task_type=task_type,
                enabled_only=True,
            )
            remote_cand = next((m for m in remote_candidates if m.local_or_remote == "remote"), None)
            if remote_cand:
                # Check circuit breaker for remote provider
                if self.health_tracker.is_circuit_open(remote_cand.provider):
                    return RoutingDecision(
                        selected_model=default_local,
                        provider=default_local_provider,
                        task_type=task_type,
                        reason=f"Remote provider '{remote_cand.provider.value}' circuit is OPEN; routing to local default",
                        reason_code="CIRCUIT_OPEN",
                        fallback=default_local,
                        fallback_eligible=True,
                        fallback_model=default_local,
                        fallback_provider=default_local_provider,
                        remote_allowed=False,
                        local_or_remote="local",
                        memory_estimate_gb=4.36,
                        estimated_complexity=complexity.value,
                        context_size_requirement=context_size,
                        latency_sensitive=latency_sensitive,
                        health_summary=self.health_tracker.get_health_summary(remote_cand.provider),
                    )

                return RoutingDecision(
                    selected_model=remote_cand.id,
                    provider=remote_cand.provider.value,
                    task_type=task_type,
                    reason=f"High complexity task routed to authorized remote model '{remote_cand.id}'",
                    reason_code="REMOTE_EXPLICITLY_ALLOWED",
                    fallback=default_local,
                    fallback_eligible=True,
                    fallback_model=default_local,
                    fallback_provider=default_local_provider,
                    remote_allowed=True,
                    local_or_remote="remote",
                    memory_estimate_gb=0.0,
                    estimated_complexity=complexity.value,
                    context_size_requirement=context_size,
                    latency_sensitive=latency_sensitive,
                    health_summary=self.health_tracker.get_health_summary(remote_cand.provider),
                )

        # 9. Standard Local-First Default (Low / Medium complexity, routine coding, or remote disallowed)
        # Check if local Ollama circuit is tripped
        if self.health_tracker.is_circuit_open(ModelProvider.OLLAMA):
            return RoutingDecision(
                selected_model=default_local,
                provider=default_local_provider,
                task_type=task_type,
                reason="Default local engine assigned; provider circuit is currently OPEN",
                reason_code="CIRCUIT_OPEN",
                fallback=default_local,
                fallback_eligible=False,
                fallback_model=default_local,
                fallback_provider=default_local_provider,
                remote_allowed=False,
                local_or_remote="local",
                memory_estimate_gb=4.36,
                estimated_complexity=complexity.value,
                context_size_requirement=context_size,
                latency_sensitive=latency_sensitive,
                health_summary=self.health_tracker.get_health_summary(ModelProvider.OLLAMA),
            )

        return RoutingDecision(
            selected_model=default_local,
            provider=default_local_provider,
            task_type=task_type,
            reason=f"Standard local-first engine assigned for {task_type.value}",
            reason_code="LOCAL_DEFAULT",
            fallback=default_local,
            fallback_eligible=True,
            fallback_model=default_local,
            fallback_provider=default_local_provider,
            remote_allowed=False,
            local_or_remote="local",
            memory_estimate_gb=4.36,
            estimated_complexity=complexity.value,
            context_size_requirement=context_size,
            latency_sensitive=latency_sensitive,
            health_summary=self.health_tracker.get_health_summary(ModelProvider.OLLAMA),
        )

    # ========================================================================
    # EXECUTION BOUNDARY WITH CONTROLLED FALLBACK
    # ========================================================================

    async def execute(
        self,
        request: AIRequest,
        preferred_model_id: Optional[str] = None,
        allow_remote: bool = False,
    ) -> AIResponse:
        """Route request deterministically and execute with bounded fallback (depth <= 1)."""
        task_type = request.task_type or TaskType.GENERAL_REASONING
        prompt = request.get_prompt_text()

        # 1. Resolve deterministic route
        decision = await self.route(
            task_type=task_type,
            prompt=prompt,
            allow_remote=allow_remote,
            preferred_model_id=preferred_model_id or request.model_id,
            structured_output_schema=request.structured_output_schema,
        )

        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.MODEL_ROUTE_SELECTED,
                status=ActionStatus.COMPLETED,
                title=f"Route Selected: {decision.selected_model}",
                description=f"Assigned {decision.provider} for {decision.task_type.value}: {decision.reason}",
                safe_metadata={
                    "selected_model": decision.selected_model,
                    "provider": decision.provider,
                    "task_type": decision.task_type.value,
                    "reason_code": decision.reason_code,
                    "local": decision.local_or_remote == "local",
                },
            )
        )

        # 2. Prepare request for primary provider
        primary_adapter = self.get_adapter(decision.provider)
        req_copy = request.model_copy(update={"model_id": decision.selected_model})

        t0 = time.monotonic()
        try:
            resp = await primary_adapter.generate(req_copy)
            latency_ms = (time.monotonic() - t0) * 1000
            self.health_tracker.record_success(decision.provider, latency_ms)
            return resp

        except (ProviderUnavailableError, ProviderTimeoutError, RateLimitError) as exc:
            # Eligible failures: Record failure and evaluate fallback
            latency_ms = (time.monotonic() - t0) * 1000
            self.health_tracker.record_failure(decision.provider, exc, latency_ms)

            # Verify fallback eligibility
            fallback_provider = decision.fallback_provider or "OLLAMA"
            fallback_model = decision.fallback_model or self.config.default_local_model
            is_fallback_cycle = fallback_provider.upper() == decision.provider.upper() and fallback_model == decision.selected_model

            can_fallback = (
                self.config.fallback_enabled
                and self.config.max_fallback_depth >= 1
                and decision.fallback_eligible
                and not is_fallback_cycle
            )

            if not can_fallback:
                logger.warning(
                    f"Provider '{decision.provider}' failed ({exc.error_code}). Fallback not eligible or disabled. Propagating error."
                )
                raise

            logger.info(
                f"Provider '{decision.provider}' failed ({exc.error_code}). "
                f"Initiating controlled fallback to '{fallback_provider}' ({fallback_model})."
            )

            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.MODEL_ROUTE_SELECTED,
                    status=ActionStatus.PROGRESS,
                    title="Model Provider Fallback",
                    description=f"Provider {decision.provider} failed ({exc.error_code}). Bounded fallback to {fallback_provider} ({fallback_model}).",
                    safe_metadata={
                        "fallback_from": decision.provider,
                        "fallback_to": fallback_provider,
                        "fallback_model": fallback_model,
                        "reason": exc.error_code,
                    },
                )
            )

            # Execute fallback with hard cap depth = 1 (never re-fallback on failure)
            fb_adapter = self.get_adapter(fallback_provider)
            fb_request = request.model_copy(update={"model_id": fallback_model})
            t_fb = time.monotonic()
            try:
                fb_resp = await fb_adapter.generate(fb_request)
                fb_latency = (time.monotonic() - t_fb) * 1000
                self.health_tracker.record_success(fallback_provider, fb_latency)

                # Attach sanitized fallback metadata to response
                meta = dict(fb_resp.metadata)
                meta["fallback_occurred"] = True
                meta["fallback_from_provider"] = decision.provider
                meta["fallback_from_model"] = decision.selected_model
                meta["fallback_reason_code"] = exc.error_code
                return fb_resp.model_copy(update={"metadata": sanitize_dict(meta)})

            except Exception as fb_exc:
                fb_latency = (time.monotonic() - t_fb) * 1000
                self.health_tracker.record_failure(fallback_provider, fb_exc, fb_latency)
                logger.error(f"Fallback provider '{fallback_provider}' also failed ({fb_exc}). Hard cap reached.")
                raise

        except (AuthenticationError, SecurityViolationError, ProviderInvalidRequestError, asyncio.CancelledError) as exc:
            # Non-eligible failures: propagate immediately without fallback
            latency_ms = (time.monotonic() - t0) * 1000
            self.health_tracker.record_failure(decision.provider, exc, latency_ms)
            logger.warning(
                f"Non-retryable failure on provider '{decision.provider}': {exc.__class__.__name__}. Fallback forbidden."
            )
            raise
        except Exception as exc:
            # Unclassified errors
            latency_ms = (time.monotonic() - t0) * 1000
            self.health_tracker.record_failure(decision.provider, exc, latency_ms)
            raise

    async def stream(
        self,
        request: AIRequest,
        preferred_model_id: Optional[str] = None,
        allow_remote: bool = False,
    ) -> AsyncIterator[AIStreamChunk]:
        """Stream chunks incrementally with deterministic fallback only BEFORE user-visible output."""
        task_type = request.task_type or TaskType.GENERAL_REASONING
        prompt = request.get_prompt_text()

        decision = await self.route(
            task_type=task_type,
            prompt=prompt,
            allow_remote=allow_remote,
            preferred_model_id=preferred_model_id or request.model_id,
            structured_output_schema=request.structured_output_schema,
        )

        primary_adapter = self.get_adapter(decision.provider)
        req_copy = request.model_copy(update={"model_id": decision.selected_model})

        visible_chunks_emitted: int = 0
        t0 = time.monotonic()

        try:
            async for chunk in primary_adapter.stream(req_copy):
                if chunk.is_delta and chunk.delta:
                    visible_chunks_emitted += 1
                yield chunk

            latency_ms = (time.monotonic() - t0) * 1000
            self.health_tracker.record_success(decision.provider, latency_ms)
            return

        except (ProviderUnavailableError, ProviderTimeoutError, RateLimitError) as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            self.health_tracker.record_failure(decision.provider, exc, latency_ms)

            # CRITICAL STREAMING INVARIANT:
            # If user-visible text was already emitted, NEVER switch providers mid-stream!
            if visible_chunks_emitted > 0:
                logger.warning(
                    f"Stream error after {visible_chunks_emitted} visible chunk(s) emitted. "
                    "Refusing provider switch to avoid corrupted stream output."
                )
                yield AIStreamChunk(
                    event_type=StreamEventType.ERROR,
                    error=exc,
                    model_id=decision.selected_model,
                    provider=decision.provider,
                    metadata={"error": exc.error_code, "visible_chunks_emitted": visible_chunks_emitted},
                )
                raise

            # Fallback is eligible ONLY if 0 visible chunks have been delivered
            fallback_provider = decision.fallback_provider or "OLLAMA"
            fallback_model = decision.fallback_model or self.config.default_local_model
            is_fallback_cycle = fallback_provider.upper() == decision.provider.upper() and fallback_model == decision.selected_model

            can_fallback = (
                self.config.fallback_enabled
                and self.config.max_fallback_depth >= 1
                and decision.fallback_eligible
                and not is_fallback_cycle
            )

            if not can_fallback:
                yield AIStreamChunk(
                    event_type=StreamEventType.ERROR,
                    error=exc,
                    model_id=decision.selected_model,
                    provider=decision.provider,
                    metadata={"error": exc.error_code, "visible_chunks_emitted": 0},
                )
                raise

            logger.info(
                f"Stream failure prior to output. Initiating fallback from {decision.provider} to {fallback_provider}."
            )

            fb_adapter = self.get_adapter(fallback_provider)
            fb_request = request.model_copy(update={"model_id": fallback_model})
            t_fb = time.monotonic()

            try:
                async for chunk in fb_adapter.stream(fb_request):
                    yield chunk
                fb_latency = (time.monotonic() - t_fb) * 1000
                self.health_tracker.record_success(fallback_provider, fb_latency)
                return
            except Exception as fb_exc:
                fb_latency = (time.monotonic() - t_fb) * 1000
                self.health_tracker.record_failure(fallback_provider, fb_exc, fb_latency)
                logger.error(f"Fallback stream provider '{fallback_provider}' failed ({fb_exc}).")
                raise

        except (AuthenticationError, SecurityViolationError, ProviderInvalidRequestError, asyncio.CancelledError) as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            self.health_tracker.record_failure(decision.provider, exc, latency_ms)
            raise
        except Exception as exc:
            latency_ms = (time.monotonic() - t0) * 1000
            self.health_tracker.record_failure(decision.provider, exc, latency_ms)
            raise


# Global default model router singleton
model_router = ModelRouter()
