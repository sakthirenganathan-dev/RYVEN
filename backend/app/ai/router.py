"""RYVEN 3.0 — Local-First AI Model Router.

Determines optimal, deterministic model assignment for various cognitive tasks
(general reasoning, planning, code, vision, OCR, embedding, speech) adhering to
local-first privacy and hardware constraints.
"""

from __future__ import annotations

from typing import Optional
from app.ai.hardware import HardwareDiagnostics, HardwareProfile
from app.ai.models import ModelProfile, ModelProvider, RoutingDecision, TaskType
from app.ai.registry import ModelRegistry, model_registry
from app.ai.security import ModelSecurityPolicy, model_security_policy
from app.core.logging_config import logger


class ModelRouter:
    """Intelligent, deterministic local-first model router."""

    def __init__(
        self,
        registry: Optional[ModelRegistry] = None,
        security_policy: Optional[ModelSecurityPolicy] = None,
    ) -> None:
        self.registry = registry or model_registry
        self.security_policy = security_policy or model_security_policy

    async def route(
        self,
        task_type: TaskType,
        prompt: str = "",
        allow_remote: bool = False,
        preferred_model_id: Optional[str] = None,
        sync_hardware: bool = False,
    ) -> RoutingDecision:
        """Deterministically select the best model profile for the requested task."""
        # 1. Fetch hardware status safely
        hardware = await HardwareDiagnostics.get_profile()

        # Update registry status from hardware Ollama detection if requested
        if sync_hardware and hardware.ollama_online:
            self.registry.sync_with_ollama(hardware.installed_models)

        # 2. Check user preferred model if explicitly specified
        if preferred_model_id:
            profile = self.registry.get_model(preferred_model_id)
            if profile and profile.enabled:
                if profile.local_or_remote == "local" and profile.installed:
                    return RoutingDecision(
                        selected_model=profile.id,
                        provider=profile.provider.value,
                        task_type=task_type,
                        reason=f"Explicit user preference for local model '{profile.id}'",
                        fallback="qwen2.5:7b",
                        remote_allowed=False,
                        local_or_remote="local",
                        memory_estimate_gb=profile.memory_estimate_gb,
                    )
                elif profile.local_or_remote == "remote":
                    if allow_remote and self.security_policy.allow_remote_inference:
                        # Validate data privacy
                        is_sensitive, _ = self.security_policy.scan_for_sensitive_data(prompt)
                        if not is_sensitive:
                            return RoutingDecision(
                                selected_model=profile.id,
                                provider=profile.provider.value,
                                task_type=task_type,
                                reason=f"Explicit user preference for authorized remote model '{profile.id}'",
                                fallback="qwen2.5:7b",
                                remote_allowed=True,
                                local_or_remote="remote",
                                memory_estimate_gb=0.0,
                            )

        # 3. Standard Local-First Task Routing Rules
        # Rule A: General Reasoning, Planning, Code, Classification -> Qwen 2.5 7B
        if task_type in (
            TaskType.GENERAL_REASONING,
            TaskType.PLANNING,
            TaskType.CODE,
            TaskType.CLASSIFICATION,
        ):
            qwen = self.registry.get_model("qwen2.5:7b")
            is_installed = qwen.installed if qwen else True
            return RoutingDecision(
                selected_model="qwen2.5:7b",
                provider="OLLAMA",
                task_type=task_type,
                reason=f"Standard local-first engine assigned for {task_type.value}",
                fallback="qwen2.5:7b",
                remote_allowed=False,
                local_or_remote="local",
                memory_estimate_gb=4.36,
            )

        # Rule B: Vision Tasks
        elif task_type == TaskType.VISION:
            candidates = self.registry.list_models(task_type=TaskType.VISION, installed_only=True, enabled_only=True)
            if candidates:
                chosen = candidates[0]
                return RoutingDecision(
                    selected_model=chosen.id,
                    provider=chosen.provider.value,
                    task_type=task_type,
                    reason=f"Installed local vision model '{chosen.id}' selected for image analysis",
                    fallback="qwen2.5:7b",
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=chosen.memory_estimate_gb,
                )
            else:
                return RoutingDecision(
                    selected_model="qwen2.5:7b",
                    provider="OLLAMA",
                    task_type=task_type,
                    reason="No local vision model (e.g. llava:7b) is currently installed; falling back to Qwen 2.5 7B for text description guidance",
                    fallback="qwen2.5:7b",
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=4.36,
                )

        # Rule C: OCR Tasks
        elif task_type == TaskType.OCR:
            candidates = self.registry.list_models(task_type=TaskType.OCR, installed_only=True, enabled_only=True)
            if candidates:
                chosen = candidates[0]
                return RoutingDecision(
                    selected_model=chosen.id,
                    provider=chosen.provider.value,
                    task_type=task_type,
                    reason=f"Installed local OCR model '{chosen.id}' selected",
                    fallback="qwen2.5:7b",
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=chosen.memory_estimate_gb,
                )
            else:
                return RoutingDecision(
                    selected_model="qwen2.5:7b",
                    provider="OLLAMA",
                    task_type=task_type,
                    reason="No specialized local OCR model installed; falling back to default local engine",
                    fallback="qwen2.5:7b",
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=4.36,
                )

        # Rule D: Embedding / Semantic Retrieval
        elif task_type == TaskType.EMBEDDING:
            candidates = self.registry.list_models(task_type=TaskType.EMBEDDING, installed_only=True, enabled_only=True)
            if candidates:
                chosen = candidates[0]
                return RoutingDecision(
                    selected_model=chosen.id,
                    provider=chosen.provider.value,
                    task_type=task_type,
                    reason=f"Local embedding model '{chosen.id}' selected for vector retrieval",
                    fallback=None,
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=chosen.memory_estimate_gb,
                )
            else:
                return RoutingDecision(
                    selected_model="qwen2.5:7b",
                    provider="OLLAMA",
                    task_type=task_type,
                    reason="Specialized embedding model not installed; using standard local model context",
                    fallback=None,
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=4.36,
                )

        # Rule E: Speech to text / Audio
        elif task_type == TaskType.SPEECH_TO_TEXT:
            candidates = self.registry.list_models(task_type=TaskType.SPEECH_TO_TEXT, installed_only=True, enabled_only=True)
            if candidates:
                chosen = candidates[0]
                return RoutingDecision(
                    selected_model=chosen.id,
                    provider=chosen.provider.value,
                    task_type=task_type,
                    reason=f"Local speech model '{chosen.id}' selected",
                    fallback="qwen2.5:7b",
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=chosen.memory_estimate_gb,
                )
            else:
                return RoutingDecision(
                    selected_model="qwen2.5:7b",
                    provider="OLLAMA",
                    task_type=task_type,
                    reason="No local speech model installed; falling back to default reasoning engine",
                    fallback="qwen2.5:7b",
                    remote_allowed=False,
                    local_or_remote="local",
                    memory_estimate_gb=4.36,
                )

        # Default fallback
        return RoutingDecision(
            selected_model="qwen2.5:7b",
            provider="OLLAMA",
            task_type=task_type,
            reason="Default local Qwen 2.5 7B assigned",
            fallback="qwen2.5:7b",
            remote_allowed=False,
            local_or_remote="local",
            memory_estimate_gb=4.36,
        )


# Global default model router singleton
model_router = ModelRouter()
