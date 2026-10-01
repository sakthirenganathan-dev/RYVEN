"""Abstract AI provider contract for RYVEN."""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """Structured message for multi-turn conversations."""

    role: Literal["system", "user", "assistant"] = Field(..., description="Message author role")
    content: str = Field(..., description="Message text content")


class AIResponse(BaseModel):
    """Normalized response returned by any AI provider."""

    content: str = Field(..., description="Generated text response from the model")
    model: str = Field(..., description="Model identifier used for generation")
    provider: str = Field(..., description="Provider name, e.g. 'ollama'")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Generation metadata (tokens, duration, etc.)")


class AIProvider(ABC):
    """Abstract interface for large language model providers."""

    provider_name: str = "generic_ai"

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        **kwargs: Any,
    ) -> AIResponse:
        """Generate a response for the provided prompt and optional conversation history."""
        raise NotImplementedError("AI providers must implement a generate method")

    @abstractmethod
    async def check_health(self) -> Dict[str, Any]:
        """Check if the AI provider endpoint is reachable and return health diagnostics."""
        raise NotImplementedError("AI providers must implement a check_health method")
