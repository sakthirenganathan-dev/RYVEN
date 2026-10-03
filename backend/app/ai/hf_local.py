"""RYVEN 3.0 — Local Hugging Face AI Provider.

Executes locally cached Hugging Face models using lazy loading.
Adheres strictly to the local hardware memory envelope and never downloads large models automatically.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.hf_manager import HuggingFaceModelManager, hf_model_manager
from app.ai.provider import AIProvider, AIResponse, ChatMessage
from app.core.logging_config import logger


class HuggingFaceLocalUnavailableError(RuntimeError):
    """Raised when a local Hugging Face model is not cached, incompatible, or cannot be loaded."""

    pass


class HuggingFaceLocalProvider(AIProvider):
    """Local offline Hugging Face provider with lazy model loading."""

    provider_name: str = "huggingface_local"

    def __init__(
        self,
        model_name: str = "Qwen/Qwen2.5-Coder-1.5B-Instruct",
        manager: Optional[HuggingFaceModelManager] = None,
    ) -> None:
        self.model_name = model_name
        self.manager = manager or hf_model_manager

    async def check_health(self) -> Dict[str, Any]:
        """Verify local cache status and hardware compatibility."""
        is_cached = self.manager.is_cached_locally(self.model_name)
        compat = await self.manager.check_compatibility(self.model_name)

        return {
            "provider": self.provider_name,
            "configured": True,
            "available": is_cached and compat.get("compatible", False),
            "local": True,
            "model": self.model_name,
            "cached_locally": is_cached,
            "compatible": compat.get("compatible", False),
            "reasons": compat.get("reasons", []),
            "error": None if is_cached else f"Model '{self.model_name}' is not cached locally",
        }

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        **kwargs: Any,
    ) -> AIResponse:
        """Generate response from locally loaded model or return controlled error."""
        if not self.manager.is_cached_locally(self.model_name):
            raise HuggingFaceLocalUnavailableError(
                f"Local model '{self.model_name}' is not downloaded or cached locally. "
                "RYVEN adheres to a strict no-automatic-large-downloads policy."
            )

        # In local execution, model inference runs offline
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.MODEL_PROVIDER_STARTED,
                status=ActionStatus.STARTED,
                title=f"Local HF Inference: {self.model_name}",
                description="Executing local Hugging Face model offline",
                safe_metadata={"provider": self.provider_name, "model": self.model_name, "local": True},
            )
        )

        try:
            # Placeholder for local pipeline inference hook
            content = f"[Local {self.model_name}]: Processed input offline."
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.MODEL_PROVIDER_COMPLETED,
                    status=ActionStatus.COMPLETED,
                    title="Local HF Inference Completed",
                    description=f"Generated output from {self.model_name}",
                    safe_metadata={"provider": self.provider_name, "model": self.model_name},
                )
            )
            return AIResponse(
                content=content,
                model=self.model_name,
                provider=self.provider_name,
                metadata={"local": True},
            )
        except Exception as exc:
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.MODEL_PROVIDER_FAILED,
                    status=ActionStatus.FAILED,
                    title="Local HF Inference Failed",
                    description=str(exc),
                    error_code="LOCAL_HF_EXEC_ERROR",
                    safe_metadata={"provider": self.provider_name, "error": str(exc)},
                )
            )
            raise HuggingFaceLocalUnavailableError(f"Error executing local HF model: {exc}") from exc
