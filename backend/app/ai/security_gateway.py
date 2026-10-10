"""RYVEN 3.0 — ModelSecurityGateway & Privacy Boundary Enforcement.

Authoritative centralized security gateway through which every local and remote
model execution request must pass (M17.10 Phase 4).

Enforces:
1. Strict privacy mode boundaries (LOCAL_ONLY, PRIVACY_FIRST, BALANCED, MAX_REASONING).
2. Deep structured inspection and redaction of secrets, tokens, credentials, and private keys.
3. Interactive confirmation token validation with replay and tampering protection.
4. Fail-closed authorization: Model output or memory context can NEVER grant authority.
5. Pre-stream policy evaluation and leak-free incremental stream guarding.
6. Safe structured telemetry via ActionEventBus with zero credential leakage.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, AsyncIterator, Dict, List, Optional, Set, Tuple, Union

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.ai.contracts import (
    AIRequest,
    AIStreamChunk,
    SecurityViolationError,
    StreamChunkSanitizer,
    StreamEventType,
    redact_secrets,
    sanitize_dict,
)
from app.ai.models import ModelProvider
from app.ai.privacy import PrivacyMode, parse_privacy_mode
from app.ai.provider import ChatMessage
from app.ai.security import ModelSecurityPolicy, model_security_policy
from app.core.config import settings
from app.core.logging_config import logger
from app.workflows.confirmation import ConfirmationManager, confirmation_manager


# ============================================================================
# STRUCTURED SENSITIVE PATTERN CATALOG
# ============================================================================

# Strictly prohibited secrets that cannot be transmitted remotely even with redaction
_STRICT_PROHIBITED_PATTERNS = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"ssh-(?:rsa|ed25519|dss)\s+[A-Za-z0-9+/=]{40,512}", re.IGNORECASE),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
]

# Sensitive patterns that must be detected and/or safely redacted
_SENSITIVE_PATTERNS_WITH_REPLACEMENT: List[Tuple[re.Pattern, str, str]] = [
    # 1. Private keys
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.IGNORECASE),
        "[REDACTED_PRIVATE_KEY]",
        "PRIVATE_KEY",
    ),
    (
        re.compile(r"ssh-(?:rsa|ed25519|dss)\s+[A-Za-z0-9+/=]{40,512}", re.IGNORECASE),
        "[REDACTED_SSH_KEY]",
        "SSH_KEY",
    ),
    # 2. Database connection strings containing credentials
    (
        re.compile(r"(?i)\b(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|mssql|oracle)://(?:[a-zA-Z0-9_\-\.%]+:[^@\s\r\n]+@)[a-zA-Z0-9_\-\.:]+(?:/[a-zA-Z0-9_\-\.]*)?"),
        "[REDACTED_DB_URL_WITH_CREDENTIALS]",
        "DATABASE_CREDENTIALS",
    ),
    # 3. Credential-bearing URLs (HTTP/HTTPS with user:pass@)
    (
        re.compile(r"https?://(?:[a-zA-Z0-9_\-\.%]+:[^@\s\r\n]+@)[a-zA-Z0-9_\-\.]+"),
        "[REDACTED_CREDENTIAL_URL]",
        "CREDENTIAL_URL",
    ),
    # 4. Bearer tokens
    (
        re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{15,256}"),
        "Bearer [REDACTED_BEARER_TOKEN]",
        "BEARER_TOKEN",
    ),
    # 5. Known API Key prefixes (xai, hf, sk, ghp, gho, github_pat)
    (
        re.compile(r"\b(?:xai-[a-zA-Z0-9_\-]{16,80})\b"),
        "[REDACTED_API_KEY]",
        "XAI_API_KEY",
    ),
    (
        re.compile(r"\b(?:hf_[a-zA-Z0-9]{20,80})\b"),
        "[REDACTED_API_KEY]",
        "HF_API_KEY",
    ),
    (
        re.compile(r"\b(?:sk-(?:ant-api[0-9]{2}-)?[a-zA-Z0-9_\-]{16,90})\b"),
        "[REDACTED_API_KEY]",
        "OPENAI_API_KEY",
    ),
    (
        re.compile(r"\b(?:ghp_[a-zA-Z0-9]{20,80}|gho_[a-zA-Z0-9]{20,80}|github_pat_[a-zA-Z0-9_]{22,100})\b", re.IGNORECASE),
        "[REDACTED_GITHUB_TOKEN]",
        "GITHUB_TOKEN",
    ),
    (
        re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        "[REDACTED_AWS_KEY]",
        "AWS_KEY",
    ),
    # 6. Key-value style credential assignments
    (
        re.compile(r"(?i)\b(?:api[_-]?key)[\s:=]+['\"]?([a-zA-Z0-9_\-\.]{12,128})['\"]?"),
        "api_key=[REDACTED_API_KEY]",
        "API_KEY",
    ),
    (
        re.compile(r"(?i)\b(?:password|passwd|pwd)[\s:=]+['\"]?([^\s'\"]{4,128})['\"]?"),
        "password=[REDACTED_PASSWORD]",
        "PASSWORD",
    ),
    (
        re.compile(r"(?i)\b(?:client[_-]?secret|aws_secret_access_key)[\s:=]+['\"]?([^\s'\"]{10,128})['\"]?"),
        "secret=[REDACTED_SECRET]",
        "SECRET",
    ),
    # 7. Session cookies & auth tokens
    (
        re.compile(r"(?i)\b(?:set-cookie|cookie)[\s:=]+['\"]?([a-zA-Z0-9_=\-; ]{8,256})['\"]?"),
        "cookie=[REDACTED_COOKIE]",
        "COOKIE",
    ),
    (
        re.compile(r"(?i)\b(?:sessionid|session_id|jwt)[\s:=]+['\"]?([a-zA-Z0-9_\-\.]{12,256})['\"]?"),
        "session_id=[REDACTED_SESSION]",
        "SESSION",
    ),
    # 8. Confirmation tokens (must never be transmitted to models)
    (
        re.compile(r"\b(?:CONF-[0-9A-Fa-f]{8,16})\b"),
        "[REDACTED_CONFIRMATION_TOKEN]",
        "CONFIRMATION_TOKEN",
    ),
    (
        re.compile(r"(?i)\b(?:confirmation[_-]?token|confirm[_-]?token)[\s:=]+['\"]?([a-zA-Z0-9_\-]{8,64})['\"]?"),
        "confirmation_token=[REDACTED_CONFIRMATION_TOKEN]",
        "CONFIRMATION_TOKEN",
    ),
    # 9. Sensitive environment-variable references
    (
        re.compile(r"\.env(?:\.local|\.production|\.development|\.staging)?\b", re.IGNORECASE),
        "[REDACTED_ENV_FILE]",
        "ENV_FILE",
    ),
]


# ============================================================================
# AUTHORITATIVE MODEL SECURITY GATEWAY
# ============================================================================

class ModelSecurityGateway:
    """Authoritative centralized security gateway for all AI model execution."""

    def __init__(
        self,
        security_policy: Optional[ModelSecurityPolicy] = None,
        confirmation_mgr: Optional[ConfirmationManager] = None,
        default_privacy_mode: Optional[PrivacyMode] = None,
    ) -> None:
        self.security_policy = security_policy or model_security_policy
        self.confirmation_mgr = confirmation_mgr or confirmation_manager
        self.default_privacy_mode = default_privacy_mode

        self._consumed_confirmation_tokens: Set[str] = set()
        self._lock: threading.RLock = threading.RLock()

    def resolve_privacy_mode(self, request_mode: Optional[Union[PrivacyMode, str]] = None) -> PrivacyMode:
        """Resolve active privacy mode from request, explicit config, or default system policy."""
        if request_mode is not None:
            return parse_privacy_mode(request_mode)
        if self.default_privacy_mode is not None:
            return self.default_privacy_mode
        configured_mode = getattr(settings, "privacy_mode", None)
        if configured_mode:
            return parse_privacy_mode(configured_mode)
        # Compatibility with Phase 1-3: if allow_remote_inference was explicitly enabled on policy
        # without specifying a privacy mode, operate in BALANCED mode.
        if self.security_policy and getattr(self.security_policy, "allow_remote_inference", False):
            return PrivacyMode.BALANCED
        return PrivacyMode.PRIVACY_FIRST

    def is_remote_provider(self, provider: Union[ModelProvider, str], model_id: str = "") -> bool:
        """Deterministically classify whether the provider/model runs remotely in cloud."""
        p_str = provider.value.upper() if isinstance(provider, ModelProvider) else str(provider).strip().upper()
        if p_str in ("GROK", "HUGGINGFACE_REMOTE"):
            return True
        if p_str == "OLLAMA" or p_str == "HUGGINGFACE_LOCAL":
            return False
        # Fallback check on model_id name
        return "remote" in model_id.lower()

    # ------------------------------------------------------------------------
    # SENSITIVE DATA SCANNING & REDACTION
    # ------------------------------------------------------------------------

    def scan_sensitive_data(self, text: str) -> Tuple[bool, List[str]]:
        """Inspect text for presence of secrets, tokens, or credentials without leaking them."""
        if not text or not isinstance(text, str):
            return False, []

        findings: List[str] = []
        for pattern, _, category in _SENSITIVE_PATTERNS_WITH_REPLACEMENT:
            match = pattern.search(text)
            if match:
                findings.append(category)

        return len(findings) > 0, list(set(findings))

    def has_strictly_prohibited_data(self, text: str) -> bool:
        """Check for strictly prohibited secrets (private keys, AWS root keys)."""
        if not text or not isinstance(text, str):
            return False
        return any(pat.search(text) for pat in _STRICT_PROHIBITED_PATTERNS)

    def redact_sensitive_data(self, text: str) -> Tuple[str, int]:
        """Safely redact sensitive patterns preserving code syntax and harmless text."""
        if not text or not isinstance(text, str):
            return text, 0

        redacted_text = text
        total_redactions = 0

        for pattern, replacement, _ in _SENSITIVE_PATTERNS_WITH_REPLACEMENT:
            matches = list(pattern.finditer(redacted_text))
            if matches:
                total_redactions += len(matches)
                redacted_text = pattern.sub(replacement, redacted_text)

        return redacted_text, total_redactions

    def inspect_request(self, request: AIRequest) -> Tuple[bool, List[str]]:
        """Inspect all prompt and message components of an AIRequest for secrets."""
        all_findings: List[str] = []

        # 1. System prompt
        if request.system_prompt:
            has_sens, f = self.scan_sensitive_data(request.system_prompt)
            if has_sens:
                all_findings.extend(f)

        # 2. Conversation messages
        for msg in request.messages:
            if msg.content:
                has_sens, f = self.scan_sensitive_data(msg.content)
                if has_sens:
                    all_findings.extend(f)

        # 3. Metadata inspection
        for k, v in request.metadata.items():
            if isinstance(v, str):
                has_sens, f = self.scan_sensitive_data(v)
                if has_sens:
                    all_findings.extend(f)

        return len(all_findings) > 0, list(set(all_findings))

    def sanitize_request(self, request: AIRequest) -> Tuple[AIRequest, int]:
        """Produce a safe drop-in copy of AIRequest with all text components redacted."""
        total_redactions = 0

        # Sanitize system prompt
        safe_sys = request.system_prompt
        if safe_sys:
            safe_sys, count = self.redact_sensitive_data(safe_sys)
            total_redactions += count

        # Sanitize messages
        safe_messages: List[ChatMessage] = []
        for msg in request.messages:
            if msg.content:
                redacted_content, count = self.redact_sensitive_data(msg.content)
                total_redactions += count
                safe_messages.append(ChatMessage(role=msg.role, content=redacted_content))
            else:
                safe_messages.append(msg)

        # Sanitize metadata
        safe_meta = sanitize_dict(request.metadata)

        safe_req = request.model_copy(
            update={
                "system_prompt": safe_sys,
                "messages": safe_messages,
                "metadata": safe_meta,
            }
        )
        return safe_req, total_redactions

    # ------------------------------------------------------------------------
    # AUTHORIZATION & CONFIRMATION GATING
    # ------------------------------------------------------------------------

    def verify_authorization(
        self,
        request: AIRequest,
        provider: Union[ModelProvider, str],
        model_id: str,
        confirmation_token: Optional[str] = None,
    ) -> bool:
        """Verify explicit user confirmation binding for remote execution with replay defense.

        Invariants:
        1. Model output or untrusted prompts CANNOT approve remote execution.
        2. Memory context CANNOT grant authorization.
        3. Confirmation tokens cannot be replayed or reused.
        """
        token = confirmation_token or request.metadata.get("confirmation_token")
        if not token or not isinstance(token, str):
            return False

        clean_token = token.strip()

        # Invariant 1: Ensure token was not supplied by model output or embedded inside user prompt
        prompt_text = request.get_prompt_text()
        if clean_token in prompt_text:
            logger.error("Security Violation: Confirmation token detected within user prompt text. Rejection enforced.")
            return False

        for msg in request.messages:
            if msg.role == "assistant" and clean_token in msg.content:
                logger.error("Security Violation: Confirmation token generated by model output. Rejection enforced.")
                return False

        # Invariant 2: Ensure token did not originate from untrusted memory context
        if request.metadata.get("source") in ("memory", "semantic_memory", "episodic_memory"):
            logger.error("Security Violation: Memory context cannot satisfy remote confirmation requirement.")
            return False

        with self._lock:
            # Replay defense: token cannot be reused
            if clean_token in self._consumed_confirmation_tokens:
                logger.warning(f"Rejected replayed confirmation token: '{clean_token}'")
                return False

            # Verify with ConfirmationManager
            is_confirmed = self.confirmation_mgr.is_token_confirmed(clean_token)
            if not is_confirmed:
                return False

            # Check parameter binding if token has registered parameters
            token_data = self.confirmation_mgr._pending_tokens.get(clean_token, {})
            params = token_data.get("parameters", {})
            if params:
                bound_provider = params.get("provider")
                bound_model = params.get("model_id")
                prov_str = provider.value if isinstance(provider, ModelProvider) else str(provider)
                if bound_provider and bound_provider.upper() != prov_str.upper():
                    logger.warning(f"Confirmation token provider mismatch: expected {bound_provider}, got {prov_str}")
                    return False
                if bound_model and bound_model != model_id:
                    logger.warning(f"Confirmation token model mismatch: expected {bound_model}, got {model_id}")
                    return False

            # Mark token as consumed
            self._consumed_confirmation_tokens.add(clean_token)
            logger.info(f"Consumed valid confirmation token '{clean_token}' for remote model '{model_id}'")
            return True

    # ------------------------------------------------------------------------
    # GATEWAY REQUEST VALIDATION (ENTRY POINT)
    # ------------------------------------------------------------------------

    async def validate_request(
        self,
        request: AIRequest,
        provider: Union[ModelProvider, str],
        model_id: str,
        allow_remote: bool = False,
        privacy_mode: Optional[Union[PrivacyMode, str]] = None,
        confirmation_token: Optional[str] = None,
    ) -> AIRequest:
        """Authoritative execution boundary check for non-streaming requests.

        Returns safe prepared AIRequest or raises SecurityViolationError.
        """
        mode = self.resolve_privacy_mode(privacy_mode)
        is_remote = self.is_remote_provider(provider, model_id)
        prov_str = provider.value if isinstance(provider, ModelProvider) else str(provider)

        # 1. Emit audit evaluation event (fail-closed if telemetry fails)
        try:
            await action_bus.publish(
                ActionEvent(
                    action_type=ActionType.MODEL_SECURITY_EVALUATION,
                    status=ActionStatus.PROGRESS,
                    title="Model Security Evaluation",
                    description=f"Evaluating {prov_str} ({model_id}) under {mode.value}",
                    safe_metadata={
                        "provider": prov_str,
                        "model_id": model_id,
                        "privacy_mode": mode.value,
                        "is_remote": is_remote,
                        "timestamp": time.time(),
                    },
                )
            )
        except Exception as exc:
            logger.error(f"Telemetry failure during security evaluation: {exc}")
            raise SecurityViolationError(
                message="Security evaluation telemetry failure. Enforcing fail-closed policy.",
                provider=provider,
                model_id=model_id,
                details={"error_code": "SECURITY_EVALUATION_FAILURE"},
            ) from exc

        # 2. LOCAL_ONLY Privacy Mode
        if mode == PrivacyMode.LOCAL_ONLY:
            if is_remote:
                await self._emit_denial_event(prov_str, model_id, mode, "PRIVACY_POLICY_DENIAL", "Remote execution forbidden in LOCAL_ONLY mode")
                raise SecurityViolationError(
                    message=f"Remote model '{model_id}' on provider '{prov_str}' is forbidden in LOCAL_ONLY privacy mode.",
                    provider=provider,
                    model_id=model_id,
                    details={"error_code": "PRIVACY_POLICY_DENIAL"},
                )
            await self._emit_allowed_event(prov_str, model_id, mode, is_remote)
            return request

        # 3. PRIVACY_FIRST Privacy Mode (Secure Default)
        elif mode == PrivacyMode.PRIVACY_FIRST:
            if not is_remote:
                await self._emit_allowed_event(prov_str, model_id, mode, is_remote)
                return request

            # Remote requires explicit allowance and security policy opt-in
            if not (allow_remote and self.security_policy.allow_remote_inference):
                await self._emit_denial_event(prov_str, model_id, mode, "REMOTE_AUTHORIZATION_REQUIRED", "Remote inference requires explicit authorization")
                raise SecurityViolationError(
                    message=f"Remote inference with model '{model_id}' requires explicit authorization.",
                    provider=provider,
                    model_id=model_id,
                    details={"error_code": "REMOTE_AUTHORIZATION_REQUIRED"},
                )

            # Inspect request for sensitive data
            try:
                has_sensitive, findings = self.inspect_request(request)
            except Exception as exc:
                logger.error(f"Failed to inspect request: {exc}")
                raise SecurityViolationError(
                    message="Security inspection failure. Enforcing fail-closed policy.",
                    provider=provider,
                    model_id=model_id,
                    details={"error_code": "SECURITY_EVALUATION_FAILURE"},
                ) from exc
            if has_sensitive:
                reason = f"Sensitive pattern(s) detected ({', '.join(findings)})"
                await self._emit_denial_event(prov_str, model_id, mode, "SENSITIVE_CONTENT_BLOCKED", reason)
                raise SecurityViolationError(
                    message=f"Sensitive content detected. Remote inference blocked in PRIVACY_FIRST mode: {reason}.",
                    provider=provider,
                    model_id=model_id,
                    details={"error_code": "SENSITIVE_CONTENT_BLOCKED", "findings": findings},
                )

            # Validate interactive confirmation
            is_confirmed = self.verify_authorization(request, provider, model_id, confirmation_token)
            if not is_confirmed:
                await self._emit_denial_event(prov_str, model_id, mode, "REMOTE_AUTHORIZATION_REQUIRED", "Confirmation missing or unconfirmed")
                raise SecurityViolationError(
                    message=f"Remote model '{model_id}' requires verified user confirmation in PRIVACY_FIRST mode.",
                    provider=provider,
                    model_id=model_id,
                    details={"error_code": "REMOTE_AUTHORIZATION_REQUIRED"},
                )

            await self._emit_allowed_event(prov_str, model_id, mode, is_remote)
            return request

        # 4. BALANCED Privacy Mode
        elif mode == PrivacyMode.BALANCED:
            if not is_remote:
                await self._emit_allowed_event(prov_str, model_id, mode, is_remote)
                return request

            if not (allow_remote and self.security_policy.allow_remote_inference):
                await self._emit_denial_event(prov_str, model_id, mode, "REMOTE_AUTHORIZATION_REQUIRED", "Remote inference is not authorized")
                raise SecurityViolationError(
                    message=f"Remote inference with model '{model_id}' is not authorized in BALANCED mode.",
                    provider=provider,
                    model_id=model_id,
                    details={"error_code": "REMOTE_AUTHORIZATION_REQUIRED"},
                )

            try:
                # Check strictly prohibited secrets (private keys, AWS keys)
                prompt_text = request.get_prompt_text()
                if self.has_strictly_prohibited_data(prompt_text):
                    await self._emit_denial_event(prov_str, model_id, mode, "SENSITIVE_CONTENT_BLOCKED", "Strictly prohibited private key detected")
                    raise SecurityViolationError(
                        message="Private keys and critical credentials cannot be transmitted remotely.",
                        provider=provider,
                        model_id=model_id,
                        details={"error_code": "SENSITIVE_CONTENT_BLOCKED"},
                    )

                # Redact sensitive data safely
                safe_req, redaction_count = self.sanitize_request(request)
            except SecurityViolationError:
                raise
            except Exception as exc:
                logger.error(f"Failed to sanitize request in BALANCED mode: {exc}")
                raise SecurityViolationError(
                    message="Security sanitization failure. Enforcing fail-closed policy.",
                    provider=provider,
                    model_id=model_id,
                    details={"error_code": "SECURITY_EVALUATION_FAILURE"},
                ) from exc
            if redaction_count > 0:
                await action_bus.publish(
                    ActionEvent(
                        action_type=ActionType.MODEL_SECURITY_REDACTED,
                        status=ActionStatus.COMPLETED,
                        title="Sensitive Data Redacted",
                        description=f"Redacted {redaction_count} sensitive pattern(s) before remote dispatch",
                        safe_metadata={
                            "provider": prov_str,
                            "model_id": model_id,
                            "privacy_mode": mode.value,
                            "redaction_count": redaction_count,
                        },
                    )
                )

            await self._emit_allowed_event(prov_str, model_id, mode, is_remote)
            return safe_req

        # 5. MAX_REASONING Privacy Mode
        elif mode == PrivacyMode.MAX_REASONING:
            if not is_remote:
                await self._emit_allowed_event(prov_str, model_id, mode, is_remote)
                return request

            if not (allow_remote and self.security_policy.allow_remote_inference):
                await self._emit_denial_event(prov_str, model_id, mode, "REMOTE_AUTHORIZATION_REQUIRED", "Remote inference not authorized")
                raise SecurityViolationError(
                    message=f"Remote reasoning with model '{model_id}' requires explicit user authorization.",
                    provider=provider,
                    model_id=model_id,
                    details={"error_code": "REMOTE_AUTHORIZATION_REQUIRED"},
                )

            # Security denials cannot be overridden in MAX_REASONING
            prompt_text = request.get_prompt_text()
            if self.has_strictly_prohibited_data(prompt_text):
                await self._emit_denial_event(prov_str, model_id, mode, "SENSITIVE_CONTENT_BLOCKED", "Prohibited private key detected")
                raise SecurityViolationError(
                    message="Security policy blocks transmission of private keys and root credentials in MAX_REASONING mode.",
                    provider=provider,
                    model_id=model_id,
                    details={"error_code": "SENSITIVE_CONTENT_BLOCKED"},
                )

            safe_req, _ = self.sanitize_request(request)
            await self._emit_allowed_event(prov_str, model_id, mode, is_remote)
            return safe_req

        # Default fallback
        raise SecurityViolationError(
            message=f"Unsupported privacy mode: '{mode}'",
            provider=provider,
            model_id=model_id,
            details={"error_code": "UNSUPPORTED_PRIVACY_MODE"},
        )

    # ------------------------------------------------------------------------
    # STREAMING SECURITY VALIDATION & CHUNK WRAPPER
    # ------------------------------------------------------------------------

    async def validate_stream(
        self,
        request: AIRequest,
        provider: Union[ModelProvider, str],
        model_id: str,
        allow_remote: bool = False,
        privacy_mode: Optional[Union[PrivacyMode, str]] = None,
        confirmation_token: Optional[str] = None,
    ) -> AIRequest:
        """Validate privacy mode and authorization BEFORE stream network connection opens."""
        # Non-streaming request validation enforces identical privacy & authorization checks
        return await self.validate_request(
            request=request,
            provider=provider,
            model_id=model_id,
            allow_remote=allow_remote,
            privacy_mode=privacy_mode,
            confirmation_token=confirmation_token,
        )

    async def wrap_stream(
        self,
        stream_iter: AsyncIterator[AIStreamChunk],
        provider: Union[ModelProvider, str],
        model_id: str,
    ) -> AsyncIterator[AIStreamChunk]:
        """Incremental stream guard ensuring boundary secret redaction and safe termination.

        Invariants:
        1. Strips <think> internal blocks across chunk boundaries.
        2. Redacts credentials split across boundaries.
        3. Never flushes unsafe buffered tail on unexpected or error stream termination.
        """
        sanitizer = StreamChunkSanitizer()
        has_error: bool = False

        try:
            async for chunk in stream_iter:
                if chunk.is_error:
                    has_error = True
                    # Discard buffer immediately without flushing potentially unsafe tail
                    sanitizer.buffer = ""
                    yield chunk
                    return

                if chunk.is_delta and chunk.delta:
                    clean_delta = sanitizer.feed(chunk.delta)
                    if clean_delta:
                        yield chunk.model_copy(update={"delta": clean_delta})

                elif chunk.is_done:
                    # Flush remaining tail only if no error occurred
                    flushed = sanitizer.flush()
                    if flushed:
                        yield AIStreamChunk(
                            event_type=StreamEventType.DELTA,
                            delta=flushed,
                            model_id=model_id,
                            provider=provider,
                        )
                    yield chunk
                    return
                else:
                    yield chunk

            # Stream finished without explicit DONE chunk
            if not has_error:
                flushed = sanitizer.flush()
                if flushed:
                    yield AIStreamChunk(
                        event_type=StreamEventType.DELTA,
                        delta=flushed,
                        model_id=model_id,
                        provider=provider,
                    )
                yield AIStreamChunk(
                    event_type=StreamEventType.DONE,
                    model_id=model_id,
                    provider=provider,
                    finish_reason="stop",
                )

        except Exception as exc:
            # On unexpected termination or exception: do NOT emit unsafe buffered tail!
            sanitizer.buffer = ""
            logger.warning(f"Stream terminated unexpectedly ({exc}). Suppressed unsafe buffer flush.")
            yield AIStreamChunk(
                event_type=StreamEventType.ERROR,
                error=SecurityViolationError(
                    message="Stream terminated by policy.",
                    provider=provider,
                    model_id=model_id,
                    details={"error_code": "STREAM_SECURITY_TERMINATED"},
                ),
                model_id=model_id,
                provider=provider,
                metadata={"error": "STREAM_SECURITY_TERMINATED"},
            )
            raise

    # ------------------------------------------------------------------------
    # AUDIT EVENT HELPERS
    # ------------------------------------------------------------------------

    async def _emit_allowed_event(self, provider: str, model_id: str, mode: PrivacyMode, is_remote: bool) -> None:
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.MODEL_SECURITY_ALLOWED,
                status=ActionStatus.COMPLETED,
                title=f"Inference Allowed: {model_id}",
                description=f"Authorized {'remote' if is_remote else 'local'} execution on {provider}",
                safe_metadata={
                    "provider": provider,
                    "model_id": model_id,
                    "privacy_mode": mode.value,
                    "is_remote": is_remote,
                },
            )
        )

    async def _emit_denial_event(self, provider: str, model_id: str, mode: PrivacyMode, error_code: str, reason: str) -> None:
        await action_bus.publish(
            ActionEvent(
                action_type=ActionType.MODEL_SECURITY_DENIED,
                status=ActionStatus.FAILED,
                title=f"Inference Denied: {model_id}",
                description=f"Policy denial on {provider} ({error_code})",
                safe_metadata={
                    "provider": provider,
                    "model_id": model_id,
                    "privacy_mode": mode.value,
                    "error_code": error_code,
                    "reason": reason,
                },
            )
        )


# Global default model security gateway singleton
model_security_gateway = ModelSecurityGateway()
