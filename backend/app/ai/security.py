"""RYVEN 3.0 — AI Model Security Policy.

Enforces strict local-first data isolation:
1. Remote inference is disabled by default (opt-in only).
2. Secrets, API keys, credentials, tokens, .env files, private keys, and auth sessions
   must NEVER be transmitted to remote models.
3. Every remote request emits an auditable ActionEvent.
"""

from __future__ import annotations

import re
from typing import Optional, Tuple
from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.models import ModelProfile, ModelProvider
from app.core.config import settings
from app.core.logging_config import logger


class ModelSecurityViolationError(PermissionError):
    """Raised when an inference request violates the local-first security policy."""

    pass


class ModelSecurityPolicy:
    """Security policy validator for local and remote AI model invocations."""

    # Patterns matching sensitive secrets and credentials
    _SENSITIVE_PATTERNS = [
        re.compile(r"(?:api[_-]?key|secret|token|password|auth[_-]?header|bearer|private[_-]?key)[\s:=]+['\"]?([a-zA-Z0-9_\-\.]{12,})['\"]?", re.IGNORECASE),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.IGNORECASE),
        re.compile(r"\bghp_[a-zA-Z0-9]{36}\b"),  # GitHub Personal Access Token
        re.compile(r"\b(?:sk-[a-zA-Z0-9]{20,}|hf_[a-zA-Z0-9]{34,})\b"),  # OpenAI / Hugging Face keys
        re.compile(r"\.env(?:\.local|\.production|\.development)?\b", re.IGNORECASE),  # .env file references
        re.compile(r"(?:cookie|session[_-]?id|jwt|auth[_-]?token)[\s:=]+['\"]?([a-zA-Z0-9_\-\.]{16,})['\"]?", re.IGNORECASE),
    ]

    def __init__(self, allow_remote_inference: Optional[bool] = None) -> None:
        # Remote inference is strictly opt-in and disabled by default
        self.allow_remote_inference: bool = (
            allow_remote_inference
            if allow_remote_inference is not None
            else getattr(settings, "allow_remote_ai_inference", False)
        )

    def scan_for_sensitive_data(self, text: str) -> Tuple[bool, Optional[str]]:
        """Inspect prompt or payload text for secrets, tokens, or credential leaks."""
        for pattern in self._SENSITIVE_PATTERNS:
            match = pattern.search(text)
            if match:
                matched_snippet = match.group(0)[:30] + "..." if len(match.group(0)) > 30 else match.group(0)
                return True, f"Sensitive token/credential pattern detected ('{matched_snippet}')"
        return False, None

    async def validate_invocation(
        self,
        profile: ModelProfile,
        prompt: str,
        user_override_remote: bool = False,
        source: str = "orchestrator",
    ) -> bool:
        """Validate if the given model is permitted to process the given prompt."""
        is_remote = profile.local_or_remote == "remote" or profile.provider == ModelProvider.HUGGINGFACE_REMOTE

        # 1. Local models: Always permitted for local operations
        if not is_remote:
            return True

        # 2. Remote models: Check if remote inference is permitted
        if not (self.allow_remote_inference or user_override_remote):
            logger.warning(
                f"Blocked remote model '{profile.id}': Remote inference is disabled by default policy."
            )
            raise ModelSecurityViolationError(
                f"Remote inference is disabled by default policy. Model '{profile.id}' requires explicit user authorization."
            )

        # 3. Remote models: Check if model profile allows sensitive data
        if not profile.sensitive_data_allowed:
            has_sensitive, reason = self.scan_for_sensitive_data(prompt)
            if has_sensitive:
                logger.error(
                    f"CRITICAL: Prevented transmission of sensitive data to remote model '{profile.id}': {reason}"
                )
                # Emit high severity action event
                await action_bus.publish(
                    ActionEvent(
                        action_type=ActionType.TASK_FAILED,
                        status=ActionStatus.FAILED,
                        title=f"Blocked Remote Data Leak to {profile.id}",
                        description=f"Prompt contains sensitive pattern. Transmission to remote inference denied: {reason}",
                        error_code="SECURITY_POLICY_VIOLATION",
                        safe_metadata={"model_id": profile.id, "reason": reason, "source": source},
                    )
                )
                raise ModelSecurityViolationError(
                    f"Data security policy blocked remote inference: {reason}. "
                    "Private keys, tokens, credentials, and .env files cannot be sent to remote endpoints."
                )

        # 4. Remote model invocation approved: Emit auditable ActionEvent
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.TASK_STARTED,
                status=ActionStatus.STARTED,
                title=f"Remote AI Inference: {profile.id}",
                description=f"Authorized remote model inference via {profile.provider.value}",
                safe_metadata={"model_id": profile.id, "remote": True, "source": source},
            )
        )
        return True


# Global default model security policy
model_security_policy = ModelSecurityPolicy()
