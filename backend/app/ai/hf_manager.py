"""RYVEN 3.0 — Hugging Face Model Manager (Architecture & Planning).

Provides catalog management, hardware compatibility verification, lazy loading
mechanisms, and lifecycle hooks for Hugging Face models without downloading large
weights during Phase 1.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.ai.hardware import HardwareDiagnostics, HardwareProfile
from app.ai.models import ModelProfile, ModelProvider, TaskType
from app.core.logging_config import logger


class HFModelCatalogItem(BaseModel):
    """Specification of a candidate Hugging Face model."""

    repo_id: str = Field(..., description="Hugging Face hub repository ID (e.g. 'Qwen/Qwen2.5-Coder-1.5B')")
    task_type: TaskType = Field(..., description="Primary task domain")
    parameter_size: str = Field(..., description="Parameter count label (e.g. '1.5B', '7B')")
    ram_required_gb: float = Field(..., description="Minimum system RAM required")
    vram_recommended_gb: float = Field(0.0, description="Recommended GPU VRAM")
    download_size_gb: float = Field(..., description="Estimated model download disk space")
    quantization: str = Field("none", description="Quantization scheme (e.g. '4bit', '8bit', 'fp16')")
    license: str = Field("Apache 2.0", description="Open source license")
    description: str = Field("", description="Catalog summary")


class HuggingFaceModelManager:
    """Manages Hugging Face models lifecycle, validation, and lazy-loading."""

    def __init__(self, cache_dir: Optional[str] = None) -> None:
        self.cache_dir = cache_dir or os.path.expanduser("~/.cache/ryven/models")
        self._catalog: Dict[str, HFModelCatalogItem] = {}
        self._loaded_models: Dict[str, Any] = {}
        self._seed_catalog()

    def _seed_catalog(self) -> None:
        """Seed supported, vetted model candidates."""
        candidates = [
            HFModelCatalogItem(
                repo_id="Qwen/Qwen2.5-Coder-1.5B-Instruct",
                task_type=TaskType.CODE,
                parameter_size="1.5B",
                ram_required_gb=3.5,
                vram_recommended_gb=2.0,
                download_size_gb=1.6,
                quantization="none",
                license="Apache 2.0",
                description="Ultra-lightweight specialized code completion model",
            ),
            HFModelCatalogItem(
                repo_id="HuggingFaceTB/SmolLM2-1.7B-Instruct",
                task_type=TaskType.GENERAL_REASONING,
                parameter_size="1.7B",
                ram_required_gb=3.5,
                vram_recommended_gb=2.0,
                download_size_gb=1.8,
                quantization="none",
                license="Apache 2.0",
                description="Compact lightweight reasoning model for resource-constrained environments",
            ),
            HFModelCatalogItem(
                repo_id="openai/whisper-tiny",
                task_type=TaskType.SPEECH_TO_TEXT,
                parameter_size="39M",
                ram_required_gb=1.0,
                vram_recommended_gb=0.5,
                download_size_gb=0.15,
                quantization="fp16",
                license="MIT",
                description="Fast offline audio speech-to-text transcription",
            ),
            HFModelCatalogItem(
                repo_id="Babelscape/wikineural-multilingual-ner",
                task_type=TaskType.CLASSIFICATION,
                parameter_size="110M",
                ram_required_gb=1.0,
                vram_recommended_gb=0.0,
                download_size_gb=0.4,
                quantization="fp32",
                license="CC BY-NC 4.0",
                description="Entity recognition and classification for knowledge graphs",
            ),
        ]
        for c in candidates:
            self._catalog[c.repo_id] = c

    def get_catalog(self) -> List[HFModelCatalogItem]:
        """Return all cataloged Hugging Face candidates."""
        return list(self._catalog.values())

    def get_catalog_item(self, repo_id: str) -> Optional[HFModelCatalogItem]:
        """Find catalog metadata for a specific repository ID."""
        return self._catalog.get(repo_id)

    async def check_compatibility(self, repo_id: str) -> Dict[str, Any]:
        """Assess host hardware compatibility for running the model locally."""
        item = self.get_catalog_item(repo_id)
        if not item:
            return {"compatible": False, "reason": f"Model '{repo_id}' not found in catalog"}

        hw: HardwareProfile = await HardwareDiagnostics.get_profile()

        # Check RAM
        ram_ok = hw.ram_available_gb >= item.ram_required_gb
        # Check Disk
        disk_ok = hw.disk_free_gb >= (item.download_size_gb * 1.5)  # 50% safety buffer
        # Check VRAM if GPU available
        vram_ok = True
        if item.vram_recommended_gb > 0:
            if hw.gpu_available and hw.vram_gb:
                vram_ok = hw.vram_gb >= item.vram_recommended_gb

        compatible = ram_ok and disk_ok

        reasons = []
        if not ram_ok:
            reasons.append(
                f"Insufficient available RAM: requires {item.ram_required_gb} GB, host has {hw.ram_available_gb} GB"
            )
        if not disk_ok:
            reasons.append(
                f"Insufficient free disk space: requires ~{round(item.download_size_gb * 1.5, 2)} GB, host has {hw.disk_free_gb} GB"
            )

        return {
            "compatible": compatible,
            "repo_id": repo_id,
            "ram_ok": ram_ok,
            "disk_ok": disk_ok,
            "vram_ok": vram_ok,
            "host_ram_available_gb": hw.ram_available_gb,
            "host_disk_free_gb": hw.disk_free_gb,
            "reasons": reasons if not compatible else ["Hardware meets all execution requirements"],
        }

    def is_cached_locally(self, repo_id: str) -> bool:
        """Check if weights for the repo_id exist in the local cache dir."""
        safe_name = repo_id.replace("/", "--")
        target_path = os.path.join(self.cache_dir, safe_name)
        return os.path.exists(target_path)

    async def unload_all_models(self) -> None:
        """Unload all in-memory HF model tensors to reclaim system RAM/VRAM."""
        for name in list(self._loaded_models.keys()):
            logger.info(f"Unloading model '{name}' from memory")
            del self._loaded_models[name]

        # Trigger garbage collection if torch is present
        try:
            import gc
            gc.collect()
            import torch  # type: ignore
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


# Global default HF Model Manager singleton
hf_model_manager = HuggingFaceModelManager()
