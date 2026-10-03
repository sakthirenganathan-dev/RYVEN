"""Ollama HTTP AI provider implementation for RYVEN."""

import json
from typing import Any, Dict, List, Optional
import httpx
from app.ai.provider import AIProvider, AIResponse, ChatMessage
from app.core.config import settings
from app.core.logging_config import logger


class OllamaUnavailableError(RuntimeError):
    """Raised when Ollama service is unreachable, missing models, or encounters runtime errors."""

    pass


class OllamaProvider(AIProvider):
    """HTTP client communicating with local or network Ollama daemon via /api/chat."""

    provider_name: str = "ollama"

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout_seconds = timeout_seconds or settings.ollama_timeout_seconds

    async def check_health(self) -> Dict[str, Any]:
        """Verify if Ollama service is online and the configured model is installed."""
        health_data: Dict[str, Any] = {
            "provider": self.provider_name,
            "local": True,
            "online": False,
            "version": None,
            "model_available": False,
            "configured_model": self.model,
            "error": None,
        }
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                # 1. Check version
                v_res = await client.get(f"{self.base_url}/api/version")
                if v_res.status_code == 200:
                    health_data["online"] = True
                    health_data["version"] = v_res.json().get("version")

                # 2. Check installed models
                tags_res = await client.get(f"{self.base_url}/api/tags")
                if tags_res.status_code == 200:
                    models_list = tags_res.json().get("models", [])
                    available_names = [m.get("name", "") for m in models_list]
                    # Check exact match or base name (e.g. qwen2.5:7b or qwen2.5:7b-latest)
                    health_data["model_available"] = any(
                        self.model == name or self.model in name
                        for name in available_names
                    )
        except Exception as exc:
            health_data["error"] = str(exc)
            logger.debug(f"Ollama health probe failed: {exc}")

        return health_data

    async def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        messages: Optional[List[ChatMessage]] = None,
        images: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> AIResponse:
        """Send chat messages to Ollama /api/chat and return normalized AIResponse.

        Args:
            prompt: The user text prompt.
            system_prompt: Optional system context.
            messages: Optional prior conversation messages.
            images: Optional list of base64-encoded image strings for multimodal vision
                    requests. Content is never logged. Existing text-only callers are
                    completely unaffected when this is None (default).
        """
        if not self.model:
            raise OllamaUnavailableError(
                "No Ollama model configured. Please set OLLAMA_MODEL in your environment."
            )

        # Build structured messages payload
        payload_messages: List[Dict[str, str]] = []

        # 1. System prompt (always precedes conversation)
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})

        # 2. Conversation history if provided
        if messages:
            for msg in messages:
                # Avoid duplicate system prompts
                if msg.role == "system":
                    continue
                payload_messages.append({"role": msg.role, "content": msg.content})

        # 3. Ensure the current prompt is appended as the latest user message
        #    For vision requests, attach images to the user message (never logged).
        last_matches = (
            payload_messages
            and payload_messages[-1].get("role") == "user"
            and payload_messages[-1].get("content") == prompt
        )
        if not last_matches:
            user_message: Dict[str, Any] = {"role": "user", "content": prompt}
            if images:
                # Images are base64 strings; passed directly, never written to logs
                user_message["images"] = images
            payload_messages.append(user_message)
        elif images and payload_messages:
            # Attach images to the existing final user message
            payload_messages[-1]["images"] = images  # type: ignore[index]

        url = f"{self.base_url}/api/chat"
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": payload_messages,
            "stream": False,
        }

        # Model options (temperature, context length, etc.)
        if kwargs:
            payload["options"] = kwargs

        logger.info(
            f"Dispatching chat request to Ollama ({self.base_url}) using model '{self.model}' with {len(payload_messages)} message(s)"
        )

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=payload)

                # Handle HTTP errors
                if response.status_code == 404:
                    err_msg = f"Model '{self.model}' is not available on Ollama server. Run 'ollama pull {self.model}'."
                    logger.error(err_msg)
                    raise OllamaUnavailableError(err_msg)

                if response.status_code != 200:
                    err_text = response.text
                    logger.error(f"Ollama returned HTTP {response.status_code}: {err_text}")
                    raise OllamaUnavailableError(
                        f"Ollama server error (HTTP {response.status_code})."
                    )

                # Parse JSON
                try:
                    data = response.json()
                except Exception as json_err:
                    logger.error(f"Malformed JSON from Ollama: {json_err}")
                    raise OllamaUnavailableError("RYVEN AI engine returned an unreadable response format.") from json_err

                # Extract message content
                msg_obj = data.get("message")
                if not isinstance(msg_obj, dict):
                    logger.error("Missing or invalid 'message' object in Ollama response")
                    raise OllamaUnavailableError("RYVEN AI engine returned an unexpected message structure.")

                content = msg_obj.get("content", "").strip()
                if not content:
                    logger.error("Empty content returned from Ollama response")
                    raise OllamaUnavailableError("RYVEN AI engine returned an empty response.")

                return AIResponse(
                    content=content,
                    model=data.get("model", self.model),
                    provider=self.provider_name,
                    metadata={
                        "total_duration_ns": data.get("total_duration"),
                        "eval_count": data.get("eval_count"),
                        "done": data.get("done", True),
                    },
                )

        except httpx.ConnectError as exc:
            logger.error(f"Cannot connect to Ollama at {self.base_url}: {exc}")
            raise OllamaUnavailableError(
                f"RYVEN AI engine is currently offline: Ollama service at {self.base_url} is unreachable. Ensure Ollama is running."
            ) from exc

        except httpx.TimeoutException as exc:
            logger.error(f"Ollama request timed out after {self.timeout_seconds}s: {exc}")
            raise OllamaUnavailableError(
                f"RYVEN AI engine request timed out after {self.timeout_seconds} seconds."
            ) from exc

        except OllamaUnavailableError:
            raise

        except Exception as exc:
            logger.error(f"Unexpected error communicating with Ollama: {exc}", exc_info=True)
            raise OllamaUnavailableError("RYVEN AI engine encountered an unexpected internal error.") from exc
