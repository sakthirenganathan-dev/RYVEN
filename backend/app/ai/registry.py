"""RYVEN 3.0 — AI Model Registry.

Manages catalog of local and remote model profiles, task capabilities,
and installation statuses.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from app.ai.models import ModelProfile, ModelProvider, TaskType
from app.core.logging_config import logger


class ModelRegistry:
    """Thread-safe, extensible registry of AI model profiles."""

    def __init__(self) -> None:
        self._models: Dict[str, ModelProfile] = {}
        self._seed_default_catalog()

    def _seed_default_catalog(self) -> None:
        """Seed registry with standard RYVEN local & remote models."""
        defaults = [
            # 1. Primary Local Engine (Ollama Qwen 2.5 7B)
            ModelProfile(
                id="qwen2.5:7b",
                provider=ModelProvider.OLLAMA,
                task_types=[
                    TaskType.GENERAL_REASONING,
                    TaskType.PLANNING,
                    TaskType.CODE,
                    TaskType.CLASSIFICATION,
                ],
                local_or_remote="local",
                model_name="qwen2.5:7b",
                capabilities=["chat", "streaming", "json_format", "system_prompt"],
                memory_estimate_gb=4.36,
                context_length=8192,
                enabled=True,
                installed=True,
                sensitive_data_allowed=True,
                priority=10,
                description="Default local reasoning, planning, and development workhorse",
            ),
            # 2. Local Vision Model — llava:7b (large, uninstalled by default)
            ModelProfile(
                id="llava:7b",
                provider=ModelProvider.OLLAMA,
                task_types=[TaskType.VISION, TaskType.OCR],
                local_or_remote="local",
                model_name="llava:7b",
                capabilities=["multimodal_vision", "chat"],
                memory_estimate_gb=4.5,
                context_length=4096,
                enabled=True,
                installed=False,  # Uninstalled by default until benchmarked/installed
                sensitive_data_allowed=True,
                priority=20,
                description="Local multimodal vision and OCR model",
            ),
            # 2b. Local Vision Model — moondream (lightweight, Iris Xe compatible)
            ModelProfile(
                id="moondream",
                provider=ModelProvider.OLLAMA,
                task_types=[TaskType.VISION, TaskType.OCR],
                local_or_remote="local",
                model_name="moondream",
                capabilities=["multimodal_vision", "chat"],
                memory_estimate_gb=0.8,
                context_length=2048,
                enabled=True,
                installed=False,  # Must be explicitly installed via 'ollama pull moondream'
                sensitive_data_allowed=True,
                priority=15,  # Higher priority than llava:7b when installed
                description="Lightweight local vision model — fits Intel Iris Xe 2 GB constraint",
            ),

            # 3. Local Embedding Model (Candidate)
            ModelProfile(
                id="nomic-embed-text",
                provider=ModelProvider.OLLAMA,
                task_types=[TaskType.EMBEDDING],
                local_or_remote="local",
                model_name="nomic-embed-text",
                capabilities=["embeddings"],
                memory_estimate_gb=0.6,
                context_length=8192,
                enabled=True,
                installed=False,
                sensitive_data_allowed=True,
                priority=10,
                description="High-performance lightweight text embeddings for knowledge graph",
            ),
            # 4. Hugging Face Local Whisper (Candidate)
            ModelProfile(
                id="whisper-small-local",
                provider=ModelProvider.HUGGINGFACE_LOCAL,
                task_types=[TaskType.SPEECH_TO_TEXT],
                local_or_remote="local",
                model_name="openai/whisper-small",
                capabilities=["speech_transcription"],
                memory_estimate_gb=1.2,
                context_length=1500,
                enabled=False,
                installed=False,
                sensitive_data_allowed=True,
                priority=30,
                description="Local Hugging Face offline speech transcription",
            ),
            # 5. Remote Hugging Face Inference Model (Strictly Disabled by default)
            ModelProfile(
                id="hf-remote-qwen-coder",
                provider=ModelProvider.HUGGINGFACE_REMOTE,
                task_types=[TaskType.CODE, TaskType.GENERAL_REASONING],
                local_or_remote="remote",
                model_name="Qwen/Qwen2.5-Coder-32B-Instruct",
                capabilities=["code_generation", "deep_reasoning"],
                memory_estimate_gb=0.0,
                context_length=32768,
                enabled=False,  # STRICTLY DISABLED BY DEFAULT
                installed=False,
                sensitive_data_allowed=False,  # NEVER ALLOWED SENSITIVE DATA
                priority=100,
                description="Remote cloud Hugging Face inference endpoint (Opt-in only)",
            ),
            # 6. Remote Grok Model (Strictly Disabled by default, Opt-in only)
            ModelProfile(
                id="grok-2-latest",
                provider=ModelProvider.GROK,
                task_types=[
                    TaskType.GENERAL_REASONING,
                    TaskType.PLANNING,
                    TaskType.CODE,
                ],
                local_or_remote="remote",
                model_name="grok-2-latest",
                capabilities=["chat", "streaming", "deep_reasoning"],
                memory_estimate_gb=0.0,
                context_length=131072,
                enabled=False,  # STRICTLY DISABLED BY DEFAULT
                installed=False,
                sensitive_data_allowed=False,  # NEVER ALLOWED SENSITIVE DATA
                priority=100,
                description="Remote cloud xAI Grok inference endpoint (Opt-in only)",
            ),
        ]
        for p in defaults:
            self._models[p.id] = p

    def register_model(self, profile: ModelProfile) -> None:
        """Register or update a model profile."""
        self._models[profile.id] = profile
        logger.info(f"Registered model profile: {profile.id} ({profile.provider.value})")

    def unregister_model(self, model_id: str) -> bool:
        """Remove a model profile from the registry."""
        if model_id in self._models:
            del self._models[model_id]
            return True
        return False

    def get_model(self, model_id: str) -> Optional[ModelProfile]:
        """Fetch model profile by ID."""
        return self._models.get(model_id)

    get = get_model

    def list_for_task(
        self,
        task_type: TaskType,
        installed_only: bool = False,
        enabled_only: bool = False,
    ) -> List[ModelProfile]:
        """Fetch models supporting a specific task type."""
        return self.list_models(
            task_type=task_type,
            installed_only=installed_only,
            enabled_only=enabled_only,
        )

    def list_models(
        self,
        provider: Optional[ModelProvider] = None,
        task_type: Optional[TaskType] = None,
        installed_only: bool = False,
        enabled_only: bool = False,
    ) -> List[ModelProfile]:
        """Query models matching filtering constraints."""
        results: List[ModelProfile] = []
        for m in self._models.values():
            if provider and m.provider != provider:
                continue
            if task_type and task_type not in m.task_types:
                continue
            if installed_only and not m.installed:
                continue
            if enabled_only and not m.enabled:
                continue
            results.append(m)

        # Sort by priority ascending (lower number = higher precedence)
        return sorted(results, key=lambda x: x.priority)

    def set_installed(self, model_id: str, installed: bool) -> bool:
        """Update installation status of a model."""
        if model_id in self._models:
            self._models[model_id].installed = installed
            return True
        return False

    def set_enabled(self, model_id: str, enabled: bool) -> bool:
        """Enable or disable a model in the registry."""
        if model_id in self._models:
            self._models[model_id].enabled = enabled
            return True
        return False

    def sync_with_ollama(self, installed_models: List[Dict[str, Any]]) -> None:
        """Synchronize installed status with local Ollama tags output."""
        names = {m.get("name", "") for m in installed_models}
        for model_id, profile in self._models.items():
            if profile.provider == ModelProvider.OLLAMA:
                is_present = any(model_id == name or model_id in name for name in names)
                profile.installed = is_present


# Global default model registry singleton
model_registry = ModelRegistry()
