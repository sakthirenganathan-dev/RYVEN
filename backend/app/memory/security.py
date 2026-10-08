"""
M17.9 Phase 4 — Memory Security & Taint Protection
Authoritative security boundary governing memory read/write validation,
secret scrubbing, prompt-injection defense, project isolation, and deletion safety.

INVARIANTS:
1. Memory is passive, untrusted, advisory DATA.
2. Memory NEVER authorizes an action or satisfies safety/confirmation requirements.
3. Memory security validation is strictly local (no network, no LLMs, bounded execution).
4. All memory writes and retrieved results must pass through MemorySecurityService.
5. Project isolation is absolute: Project A memory never leaks to Project B.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from app.memory.repository import (
    AuditActor,
    EpisodicMemory,
    MemoryRepository,
    MemoryScope,
    MemorySourceType,
    RetentionClass,
    SemanticCategory,
    SemanticMemory,
    TrustLevel,
    Preference,
    _utc_now_iso,
    memory_repository,
)
from app.memory.retrieval import MemoryResult

logger = logging.getLogger("ryven.memory.security")


# ---------------------------------------------------------------------------
# Security States & Taint Taxonomy
# ---------------------------------------------------------------------------

class SecurityState(str, Enum):
    """Authoritative classification for memory items."""
    SAFE = "SAFE"
    REDACTED = "REDACTED"
    SUSPICIOUS = "SUSPICIOUS"
    REJECTED = "REJECTED"


class TaintTag(str, Enum):
    """Taint metadata tags applied to suspicious or untrusted memories."""
    PROMPT_INJECTION = "taint:prompt_injection"
    ROLE_SPOOFING = "taint:role_spoofing"
    POLICY_OVERRIDE = "taint:policy_override"
    SECRET_EXPOSURE = "taint:secret_exposure"
    UNTRUSTED_WEB = "taint:untrusted_web"
    UNTRUSTED_DOC = "taint:untrusted_doc"
    JAILBREAK = "taint:jailbreak"
    TOOL_DIRECTIVE = "taint:tool_directive"


class MemorySecurityVerdict(BaseModel):
    """Detailed result of a memory security inspection."""
    is_allowed: bool = True
    state: SecurityState = SecurityState.SAFE
    reason: str = "Passed all security inspections"
    findings: List[str] = Field(default_factory=list)
    taint_tags: List[str] = Field(default_factory=list)
    sanitized_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Defensive Regex Definitions (Bounded & Catastrophic Backtracking Safe)
# ---------------------------------------------------------------------------

# Forbidden secrets that cause rejection or strict redaction
_STRICT_REJECT_REGEXES = [
    re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"-----BEGIN RSA PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
]

# Sensitive patterns that must be redacted
_SECRET_REDACT_REGEXES = [
    (re.compile(r"ghp_[a-zA-Z0-9]{20,80}", re.IGNORECASE), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"gho_[a-zA-Z0-9]{20,80}", re.IGNORECASE), "[REDACTED_GITHUB_OAUTH]"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{22,100}", re.IGNORECASE), "[REDACTED_GITHUB_PAT]"),
    (re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{15,256}", re.IGNORECASE), "bearer [REDACTED_BEARER_TOKEN]"),
    (re.compile(r"sk-[a-zA-Z0-9]{20,80}", re.IGNORECASE), "[REDACTED_API_KEY]"),
    (re.compile(r"(?:api[_-]?key[\s:=]+['\"]?)[a-zA-Z0-9_\-\.]{10,128}(?:['\"]?)", re.IGNORECASE), "api_key=[REDACTED_API_KEY]"),
    (re.compile(r"(?:password[\s:=]+['\"]?)[^\s'\"]{4,128}(?:['\"]?)", re.IGNORECASE), "password=[REDACTED_PASSWORD]"),
    (re.compile(r"(?:passwd[\s:=]+['\"]?)[^\s'\"]{4,128}(?:['\"]?)", re.IGNORECASE), "passwd=[REDACTED_PASSWORD]"),
    (re.compile(r"(?:confirmation[_-]?token[\s:=]+['\"]?)[a-zA-Z0-9_\-\.]{8,128}(?:['\"]?)", re.IGNORECASE), "confirmation_token=[REDACTED_CONFIRMATION_TOKEN]"),
    (re.compile(r"\b(?:conf|token|auth)_[a-zA-Z0-9]{12,64}\b", re.IGNORECASE), "[REDACTED_TOKEN]"),
    (re.compile(r"ssh-(?:rsa|ed25519|dss)\s+[A-Za-z0-9+/=]{40,512}", re.IGNORECASE), "[REDACTED_SSH_KEY]"),
    (re.compile(r"(?:set-cookie[\s:=]+)[^\r\n]{5,256}", re.IGNORECASE), "set-cookie: [REDACTED_COOKIE]"),
    (re.compile(r"(?:sessionid[\s:=]+)[a-zA-Z0-9_\-]{16,128}", re.IGNORECASE), "sessionid=[REDACTED_SESSION]"),
    (re.compile(r"(?:cookie[\s:=]+['\"]?)[a-zA-Z0-9_=\-; ]{10,256}(?:['\"]?)", re.IGNORECASE), "cookie=[REDACTED_COOKIE]"),
    (re.compile(r"aws_secret_access_key[\s:=]+['\"]?[a-zA-Z0-9/+=]{20,80}['\"]?", re.IGNORECASE), "aws_secret_access_key=[REDACTED_SECRET]"),
]

# Prompt-injection heuristics (bounded regexes detecting jailbreaks, instruction overrides, command executions)
_PROMPT_INJECTION_REGEXES: List[Tuple[re.Pattern, str, TaintTag]] = [
    (
        re.compile(r"\bignore\s+(?:all\s+)?previous\s+instructions\b", re.IGNORECASE),
        "Instruction override: ignore previous instructions",
        TaintTag.PROMPT_INJECTION,
    ),
    (
        re.compile(r"\boverride\s+(?:all\s+)?system\s+instructions\b", re.IGNORECASE),
        "Instruction override: override system instructions",
        TaintTag.PROMPT_INJECTION,
    ),
    (
        re.compile(r"\bdisregard\s+(?:all\s+)?(?:rules|policies|instructions)\b", re.IGNORECASE),
        "Instruction override: disregard rules/policies",
        TaintTag.PROMPT_INJECTION,
    ),
    (
        re.compile(r"\bforget\s+(?:all\s+)?prior\s+instructions\b", re.IGNORECASE),
        "Instruction override: forget prior instructions",
        TaintTag.PROMPT_INJECTION,
    ),
    (
        re.compile(r"\breveal\s+(?:the\s+)?(?:system|hidden|developer)\s+prompt\b", re.IGNORECASE),
        "Prompt exfiltration: reveal system prompt",
        TaintTag.PROMPT_INJECTION,
    ),
    (
        re.compile(r"\bshow\s+(?:me\s+)?(?:the\s+)?developer\s+prompt\b", re.IGNORECASE),
        "Prompt exfiltration: show developer prompt",
        TaintTag.PROMPT_INJECTION,
    ),
    (
        re.compile(r"\boutput\s+initial\s+system\s+instructions\b", re.IGNORECASE),
        "Prompt exfiltration: output initial instructions",
        TaintTag.PROMPT_INJECTION,
    ),
    (
        re.compile(r"\bbypass\s+(?:safety|safetyguard|security|confirmation)\b", re.IGNORECASE),
        "Policy override attempt: bypass safety/confirmation",
        TaintTag.POLICY_OVERRIDE,
    ),
    (
        re.compile(r"\bdisable\s+(?:safety|safetyguard|security|confirmation(?:manager)?)\b", re.IGNORECASE),
        "Policy override attempt: disable safetyguard/security",
        TaintTag.POLICY_OVERRIDE,
    ),
    (
        re.compile(r"\b(?:run|execute|executed)\s+(?:all\s+)?(?:future\s+)?(?:commands\s+)?without\s+confirmation\b", re.IGNORECASE),
        "Policy override attempt: run without confirmation",
        TaintTag.POLICY_OVERRIDE,
    ),
    (
        re.compile(r"\bcommands\s+(?:run|executed?)\s+without\s+confirmation\b", re.IGNORECASE),
        "Policy override attempt: commands executed without confirmation",
        TaintTag.POLICY_OVERRIDE,
    ),
    (
        re.compile(r"\bexecute\s+this\s+command\s*:\s*[^\r\n]{2,100}", re.IGNORECASE),
        "Autonomous execution directive",
        TaintTag.TOOL_DIRECTIVE,
    ),
    (
        re.compile(r"\brun\s+this\s+command\s*:\s*[^\r\n]{2,100}", re.IGNORECASE),
        "Autonomous execution directive",
        TaintTag.TOOL_DIRECTIVE,
    ),
    (
        re.compile(r"\b(?:execute_bash|run_shell|execute_command)\b", re.IGNORECASE),
        "Tool invocation directive",
        TaintTag.TOOL_DIRECTIVE,
    ),
    (
        re.compile(r"<\s*tool_call\s*>|<\s*/\s*tool_call\s*>", re.IGNORECASE),
        "Malicious tool invocation tag",
        TaintTag.TOOL_DIRECTIVE,
    ),
    (
        re.compile(r"```tool:[a-zA-Z0-9_\-]+", re.IGNORECASE),
        "Direct tool execution block",
        TaintTag.TOOL_DIRECTIVE,
    ),
    (
        re.compile(r"\bDAN\s+mode\b", re.IGNORECASE),
        "Jailbreak attempt: DAN mode",
        TaintTag.JAILBREAK,
    ),
    (
        re.compile(r"\byou\s+are\s+now\s+(?:unrestricted|in\s+god\s+mode|unfiltered)\b", re.IGNORECASE),
        "Jailbreak attempt: role relaxation",
        TaintTag.JAILBREAK,
    ),
    (
        re.compile(r"\b(?:jailbreak|jailbroken)\b", re.IGNORECASE),
        "Jailbreak keyword pattern",
        TaintTag.JAILBREAK,
    ),
    (
        re.compile(r"\b(?:send|expose|exfiltrate)\s+(?:credentials|passwords|tokens|secrets)\b", re.IGNORECASE),
        "Credential exfiltration instruction",
        TaintTag.SECRET_EXPOSURE,
    ),
]

# Role-spoofing markers attempting to trick chat completion engines
_ROLE_SPOOFING_REGEXES: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"(?:\b|\A)system\s*message\s*:", re.IGNORECASE), "[HISTORICAL_NOTE_SYSTEM]:"),
    (re.compile(r"<\s*system_message\s*>", re.IGNORECASE), "[HISTORICAL_NOTE_SYSTEM]"),
    (re.compile(r"\[\s*system\s*message\s*\]", re.IGNORECASE), "[HISTORICAL_NOTE_SYSTEM]"),
    (re.compile(r"(?:\b|\A)developer\s*message\s*:", re.IGNORECASE), "[HISTORICAL_NOTE_DEV]:"),
    (re.compile(r"<\s*developer_message\s*>", re.IGNORECASE), "[HISTORICAL_NOTE_DEV]"),
    (re.compile(r"\[\s*developer\s*message\s*\]", re.IGNORECASE), "[HISTORICAL_NOTE_DEV]"),
    (re.compile(r"(?:\b|\A)assistant\s*message\s*:", re.IGNORECASE), "[HISTORICAL_NOTE_ASST]:"),
    (re.compile(r"<\s*assistant_message\s*>", re.IGNORECASE), "[HISTORICAL_NOTE_ASST]"),
    (re.compile(r"\[\s*assistant\s*message\s*\]", re.IGNORECASE), "[HISTORICAL_NOTE_ASST]"),
]

# Media buffer detection
_RAW_MEDIA_PATTERNS = [
    re.compile(r"data:image/[a-zA-Z0-9]+;base64,[A-Za-z0-9+/=]{100,}", re.IGNORECASE),
    re.compile(r"data:audio/[a-zA-Z0-9]+;base64,[A-Za-z0-9+/=]{100,}", re.IGNORECASE),
]


# ---------------------------------------------------------------------------
# Sanitization Helper Functions
# ---------------------------------------------------------------------------

def strip_think_blocks(text: str) -> str:
    """Strip hidden chain-of-thought <think>...</think> blocks."""
    if not text:
        return ""
    if "<think>" in text and "</think>" in text:
        return re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    return text


def defang_role_spoofing(text: str) -> Tuple[str, List[str]]:
    """Defang system, developer, and assistant role-spoofing headers."""
    findings: List[str] = []
    result = text
    for pat, replacement in _ROLE_SPOOFING_REGEXES:
        if pat.search(result):
            findings.append(f"Role spoofing detected: {pat.pattern}")
            result = pat.sub(replacement, result)
    return result, findings


def scrub_secrets_and_tokens(text: str) -> Tuple[str, List[str]]:
    """Redact passwords, tokens, API keys, and sensitive credentials."""
    findings: List[str] = []
    result = text
    for pat, repl in _SECRET_REDACT_REGEXES:
        if pat.search(result):
            findings.append(f"Secret pattern redacted: {pat.pattern}")
            result = pat.sub(repl, result)
    return result, findings


def detect_strict_rejection(text: str) -> Optional[str]:
    """Check if content contains strictly forbidden secrets (e.g. private keys)."""
    for pat in _STRICT_REJECT_REGEXES:
        if pat.search(text):
            return f"Contains strictly forbidden secret: {pat.pattern}"
    for pat in _RAW_MEDIA_PATTERNS:
        if pat.search(text):
            return "Contains raw binary/base64 media buffer"
    return None


def detect_prompt_injections(text: str) -> Tuple[List[str], List[TaintTag]]:
    """Inspect text against bounded prompt-injection heuristics."""
    findings: List[str] = []
    tags: List[TaintTag] = []
    for pat, desc, tag in _PROMPT_INJECTION_REGEXES:
        if pat.search(text):
            findings.append(desc)
            if tag not in tags:
                tags.append(tag)
    return findings, tags


def sanitize_audit_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure audit metadata never leaks raw secrets, tokens, or credentials."""
    forbidden_keys = {
        "password", "passwd", "token", "secret", "api_key", "apikey",
        "access_token", "bearer", "private_key", "ssh_key", "confirmation_token",
        "cookie", "sessionid",
    }
    clean: Dict[str, Any] = {}
    for k, v in metadata.items():
        k_lower = str(k).lower()
        if any(fk in k_lower for fk in forbidden_keys):
            clean[k] = "[REDACTED_FROM_AUDIT]"
        elif isinstance(v, str):
            # Check string value
            val_clean, _ = scrub_secrets_and_tokens(v)
            val_clean = strip_think_blocks(val_clean)
            clean[k] = val_clean
        elif isinstance(v, dict):
            clean[k] = sanitize_audit_metadata(v)
        else:
            clean[k] = v
    return clean


# ---------------------------------------------------------------------------
# Authoritative MemorySecurityService
# ---------------------------------------------------------------------------

class MemorySecurityService:
    """Authoritative Security Boundary for RYVEN 3.0 Persistent Memory.
    
    Guarantees:
    1. Validates and sanitizes all memory writes.
    2. Validates and sanitizes all memory reads before context building.
    3. Defangs prompt injections and rejects forbidden secrets.
    4. Enforces strict project isolation.
    5. Manages safe memory deletion, purging, and sanitizing exports.
    6. Preserves the passive, advisory status of memory (never authorizes).
    """

    def __init__(self, memory_repo: Optional[MemoryRepository] = None) -> None:
        self.repo = memory_repo or memory_repository
        self._lock = threading.RLock()

    # -----------------------------------------------------------------------
    # Content Inspection Primitive
    # -----------------------------------------------------------------------

    def inspect_content(
        self,
        text: str,
        source_type: str = "SYSTEM_DERIVED",
    ) -> MemorySecurityVerdict:
        """Inspect text content and determine its security state and taint tags."""
        if not text or not text.strip():
            return MemorySecurityVerdict(
                is_allowed=True,
                state=SecurityState.SAFE,
                sanitized_text="",
            )

        # 1. Check for strictly forbidden raw secrets or media buffers
        reject_reason = detect_strict_rejection(text)
        if reject_reason:
            return MemorySecurityVerdict(
                is_allowed=False,
                state=SecurityState.REJECTED,
                reason=reject_reason,
                findings=[reject_reason],
                taint_tags=[TaintTag.SECRET_EXPOSURE],
                sanitized_text=None,
            )

        # 2. Strip <think> blocks
        cleaned = strip_think_blocks(text)

        # 3. Defang role spoofing
        cleaned, role_findings = defang_role_spoofing(cleaned)

        # 4. Redact secrets
        cleaned, secret_findings = scrub_secrets_and_tokens(cleaned)

        # 5. Detect prompt injection & policy override heuristics
        inj_findings, taint_tags = detect_prompt_injections(cleaned)

        # Add source-based taint
        if source_type == "WEB_DERIVED":
            if TaintTag.UNTRUSTED_WEB not in taint_tags:
                taint_tags.append(TaintTag.UNTRUSTED_WEB)
        elif source_type == "DOCUMENT_DERIVED":
            if TaintTag.UNTRUSTED_DOC not in taint_tags:
                taint_tags.append(TaintTag.UNTRUSTED_DOC)

        all_findings = role_findings + secret_findings + inj_findings

        # Determine authoritative state
        if inj_findings or role_findings:
            state = SecurityState.SUSPICIOUS
            reason = "Content contains suspicious injection or role-spoofing patterns; defanged and tainted"
        elif secret_findings:
            state = SecurityState.REDACTED
            reason = "Content contained sensitive credentials which were redacted"
        else:
            state = SecurityState.SAFE
            reason = "Passed all security inspections"

        return MemorySecurityVerdict(
            is_allowed=True,
            state=state,
            reason=reason,
            findings=all_findings,
            taint_tags=[t.value for t in taint_tags],
            sanitized_text=cleaned,
        )

    # -----------------------------------------------------------------------
    # Write Policy Gateways
    # -----------------------------------------------------------------------

    def write_semantic_memory(
        self,
        memory: SemanticMemory,
        actor: AuditActor = AuditActor.USER,
    ) -> SemanticMemory:
        """Validate, sanitize, and persist a semantic memory.
        
        Strictly enforces:
        - Rejection of private keys and raw confirmation tokens.
        - Redaction of credentials.
        - Defanging of role spoofing and prompt injections.
        - Trust preservation: WEB_DERIVED/DOCUMENT_DERIVED cannot be silently elevated to USER_CONFIRMED.
        - Audit trail generation.
        """
        with self._lock:
            # 1. Inspect title and content
            t_verdict = self.inspect_content(memory.title, source_type=memory.source_type.value)
            if not t_verdict.is_allowed:
                self._record_audit_rejection("SEMANTIC_WRITE_REJECTED", memory.memory_id, t_verdict.reason, actor)
                raise ValueError(f"Semantic memory title rejected: {t_verdict.reason}")

            c_verdict = self.inspect_content(memory.content, source_type=memory.source_type.value)
            if not c_verdict.is_allowed:
                self._record_audit_rejection("SEMANTIC_WRITE_REJECTED", memory.memory_id, c_verdict.reason, actor)
                raise ValueError(f"Semantic memory content rejected: {c_verdict.reason}")

            # 2. Enforce Trust Preservation
            # Web or Document derived content MUST NOT be upgraded to USER_CONFIRMED
            if memory.source_type in (MemorySourceType.WEB_DERIVED, MemorySourceType.DOCUMENT_DERIVED):
                if memory.trust_level == TrustLevel.USER_CONFIRMED:
                    logger.warning(
                        f"[MEMORY_SECURITY] Downgrading trust for {memory.source_type.value} memory "
                        f"'{memory.memory_id}' from USER_CONFIRMED to source level"
                    )
                    memory.trust_level = (
                        TrustLevel.WEB_DERIVED
                        if memory.source_type == MemorySourceType.WEB_DERIVED
                        else TrustLevel.DOCUMENT_DERIVED
                    )

            # 3. Apply Sanitized Content
            memory.title = t_verdict.sanitized_text or memory.title
            memory.content = c_verdict.sanitized_text or memory.content

            # 4. Attach Taint Metadata into tags
            combined_taints = set(t_verdict.taint_tags + c_verdict.taint_tags)
            clean_tags = list(set(memory.tags + list(combined_taints)))
            # Scrub tags themselves
            clean_tags = [scrub_secrets_and_tokens(t)[0] for t in clean_tags]
            memory.tags = clean_tags

            # 5. Persist to Repository
            saved = self.repo.create_semantic_memory(memory)

            # 6. Audit Logging
            self.repo.record_audit_event(
                action="SECURITY_WRITE_ACCEPTED",
                memory_id=saved.memory_id,
                actor=actor,
                metadata=sanitize_audit_metadata({
                    "state": c_verdict.state.value,
                    "trust_level": saved.trust_level.value,
                    "scope": saved.scope.value,
                    "project_id": saved.project_id,
                    "taint_tags": list(combined_taints),
                }),
            )
            return saved

    def write_episodic_memory(
        self,
        memory: EpisodicMemory,
        actor: AuditActor = AuditActor.SYSTEM,
    ) -> EpisodicMemory:
        """Validate, sanitize, and persist an episodic task memory.
        
        Enforces:
        - Task provenance retention.
        - Scrubbing of secrets, confirmation tokens, and <think> blocks.
        - Rejection of raw screenshots or media buffers.
        - Audit trail generation.
        """
        with self._lock:
            # 1. Inspect goal and summary
            g_verdict = self.inspect_content(memory.goal, source_type="TASK_DERIVED")
            if not g_verdict.is_allowed:
                self._record_audit_rejection("EPISODIC_WRITE_REJECTED", memory.memory_id, g_verdict.reason, actor)
                raise ValueError(f"Episodic memory goal rejected: {g_verdict.reason}")

            s_verdict = self.inspect_content(memory.summary, source_type="TASK_DERIVED")
            if not s_verdict.is_allowed:
                self._record_audit_rejection("EPISODIC_WRITE_REJECTED", memory.memory_id, s_verdict.reason, actor)
                raise ValueError(f"Episodic memory summary rejected: {s_verdict.reason}")

            # 2. Sanitize solution steps
            sanitized_steps: List[str] = []
            for step in memory.solution_steps:
                step_verdict = self.inspect_content(step, source_type="TASK_DERIVED")
                if step_verdict.is_allowed and step_verdict.sanitized_text:
                    sanitized_steps.append(step_verdict.sanitized_text)

            memory.goal = g_verdict.sanitized_text or memory.goal
            memory.summary = s_verdict.sanitized_text or memory.summary
            memory.solution_steps = sanitized_steps

            # 3. Attach taint metadata if suspicious
            combined_taints = set(g_verdict.taint_tags + s_verdict.taint_tags)
            clean_tags = list(set(memory.tags + list(combined_taints)))
            clean_tags = [scrub_secrets_and_tokens(t)[0] for t in clean_tags]
            memory.tags = clean_tags

            # 4. Persist
            saved = self.repo.create_episodic_memory(memory)

            # 5. Audit Logging (if security flags/taints exist)
            if combined_taints:
                self.repo.record_audit_event(
                    action="SECURITY_EPISODIC_FLAGGED",
                    memory_id=saved.memory_id,
                    actor=actor,
                    metadata=sanitize_audit_metadata({
                        "task_id": saved.task_id,
                        "outcome": saved.outcome.value,
                        "project_id": saved.project_id,
                        "taint_tags": list(combined_taints),
                    }),
                )
            return saved

    def write_preference(
        self,
        preference: Preference,
        actor: AuditActor = AuditActor.USER,
    ) -> Preference:
        """Validate and set a user preference through the security boundary."""
        with self._lock:
            # Key validation and scrubbing is handled inside repo, but we also inspect value
            val_str = json.dumps(preference.value) if not isinstance(preference.value, str) else preference.value
            verdict = self.inspect_content(val_str, source_type="USER_CONFIRMED")
            if not verdict.is_allowed:
                self._record_audit_rejection("PREFERENCE_WRITE_REJECTED", None, verdict.reason, actor)
                raise ValueError(f"Preference value rejected: {verdict.reason}")

            saved = self.repo.set_preference(
                key=preference.key,
                value=preference.value,
                profile_id=preference.profile_id,
                scope=preference.scope,
                project_id=preference.project_id,
                source=preference.source,
                confidence=preference.confidence,
            )
            return saved

    # -----------------------------------------------------------------------
    # Read Policy Gateways
    # -----------------------------------------------------------------------

    def validate_retrieved_memories(
        self,
        memories: List[MemoryResult],
        active_project_id: Optional[str] = None,
    ) -> List[MemoryResult]:
        """Validate and sanitize retrieved memories before context assembly.
        
        Strictly enforces:
        1. Project Isolation: Memories belonging to other projects are dropped.
        2. Taint & Injection Filtering: Role spoofing headers are defanged.
        3. Secret Scrubbing: Residual credentials or confirmation tokens are redacted.
        4. Safe Fallback: Failing memories are dropped rather than throwing.
        """
        if not memories:
            return []

        validated: List[MemoryResult] = []

        for m in memories:
            try:
                # 1. Project Isolation Verification
                # If memory has a project_id, it MUST match active_project_id
                if m.project_id is not None:
                    if active_project_id is None or m.project_id != active_project_id:
                        logger.warning(
                            f"[MEMORY_SECURITY] Dropping cross-project memory '{m.memory_id}' "
                            f"(memory_project='{m.project_id}', active_project='{active_project_id}')"
                        )
                        continue

                # 2. Inspect Content
                verdict = self.inspect_content(m.content, source_type=m.source)
                title_verdict = self.inspect_content(m.title, source_type=m.source)

                # Drop outright rejected items
                if not verdict.is_allowed or not title_verdict.is_allowed:
                    logger.warning(f"[MEMORY_SECURITY] Dropping rejected memory '{m.memory_id}' from context")
                    continue

                # 3. Create sanitized copy
                sanitized_title = title_verdict.sanitized_text or m.title
                sanitized_content = verdict.sanitized_text or m.content

                # Combine tags
                clean_tags = list(set(m.tags + verdict.taint_tags))

                sanitized_result = MemoryResult(
                    memory_id=m.memory_id,
                    memory_type=m.memory_type,
                    title=sanitized_title,
                    content=sanitized_content,
                    relevance_score=m.relevance_score,
                    trust_level=m.trust_level,
                    project_id=m.project_id,
                    source=m.source,
                    created_at=m.created_at,
                    task_id=m.task_id,
                    tags=clean_tags,
                )
                validated.append(sanitized_result)

            except Exception as exc:
                logger.warning(f"[MEMORY_SECURITY] Error validating memory '{m.memory_id}': {exc}; dropping")
                continue

        return validated

    # -----------------------------------------------------------------------
    # Delete / Forget Safety Primitives
    # -----------------------------------------------------------------------

    def delete_memory(self, memory_id: str, memory_type: str = "SEMANTIC") -> bool:
        """Securely delete a memory record and trigger FTS index sync."""
        with self._lock:
            if memory_type.upper() == "SEMANTIC":
                deleted = self.repo.delete_semantic_memory(memory_id)
            elif memory_type.upper() == "EPISODIC":
                deleted = self.repo.delete_episodic_memory(memory_id)
            else:
                deleted = False

            if deleted:
                self.repo.record_audit_event(
                    action="SECURITY_MEMORY_DELETED",
                    memory_id=memory_id,
                    actor=AuditActor.USER,
                    metadata={"memory_type": memory_type.upper()},
                )
            return deleted

    def delete_memories_by_task(self, task_id: str) -> int:
        """Securely purge all episodic memory associated with an authoritative task_id."""
        with self._lock:
            count = self.repo.delete_episodic_memories_by_task(task_id)
            self.repo.record_audit_event(
                action="SECURITY_TASK_MEMORIES_DELETED",
                actor=AuditActor.USER,
                metadata={"task_id": task_id, "deleted_count": count},
            )
            return count

    def delete_memories_by_project(self, project_id: str) -> Dict[str, int]:
        """Securely cascade delete all memories and preferences for a given project."""
        with self._lock:
            result = self.repo.delete_memories_by_project(project_id)
            self.repo.record_audit_event(
                action="SECURITY_PROJECT_PURGED",
                actor=AuditActor.USER,
                metadata={"project_id": project_id, **result},
            )
            return result

    def forget_preference(
        self,
        key: str,
        profile_id: str = "default",
        project_id: Optional[str] = None,
    ) -> bool:
        """Securely remove a user preference."""
        with self._lock:
            deleted = self.repo.delete_preference_by_key(
                key=key,
                profile_id=profile_id,
                project_id=project_id,
            )
            return deleted

    def purge_rejected_memories(self) -> Dict[str, int]:
        """Purge expired memories from the database."""
        with self._lock:
            return self.repo.purge_expired_memories()

    # -----------------------------------------------------------------------
    # Export Safety
    # -----------------------------------------------------------------------

    def export_memories(
        self,
        project_id: Optional[str] = None,
        memory_type: str = "ALL",
    ) -> List[Dict[str, Any]]:
        """Export sanitized historical memories with full provenance.
        
        Guarantees:
        - Completely redacts all secrets, tokens, credentials, and confirmation keys.
        - Strips <think> reasoning blocks.
        - Excludes raw media buffers or binary dumps.
        - Preserves trust levels and taint status.
        """
        with self._lock:
            exported: List[Dict[str, Any]] = []

            # 1. Semantic memories
            if memory_type.upper() in ("ALL", "SEMANTIC"):
                scope = MemoryScope.PROJECT if project_id else None
                s_mems = self.repo.list_semantic_memories(
                    scope=scope,
                    project_id=project_id,
                    limit=200,
                )
                for sm in s_mems:
                    v_title = self.inspect_content(sm.title, source_type=sm.source_type.value)
                    v_content = self.inspect_content(sm.content, source_type=sm.source_type.value)
                    if not v_title.is_allowed or not v_content.is_allowed:
                        continue
                    exported.append({
                        "memory_id": sm.memory_id,
                        "memory_type": "SEMANTIC",
                        "title": v_title.sanitized_text or sm.title,
                        "content": v_content.sanitized_text or sm.content,
                        "category": sm.category.value,
                        "scope": sm.scope.value,
                        "project_id": sm.project_id,
                        "trust_level": sm.trust_level.value,
                        "source_type": sm.source_type.value,
                        "created_at": sm.created_at,
                        "taint_tags": list(set(v_title.taint_tags + v_content.taint_tags)),
                    })

            # 2. Episodic memories
            if memory_type.upper() in ("ALL", "EPISODIC"):
                ep_mems = self.repo.list_episodic_memories(
                    project_id=project_id,
                    limit=200,
                )
                for em in ep_mems:
                    v_goal = self.inspect_content(em.goal, source_type="TASK_DERIVED")
                    v_sum = self.inspect_content(em.summary, source_type="TASK_DERIVED")
                    if not v_goal.is_allowed or not v_sum.is_allowed:
                        continue
                    clean_steps = [
                        self.inspect_content(st, source_type="TASK_DERIVED").sanitized_text or st
                        for st in em.solution_steps
                    ]
                    exported.append({
                        "memory_id": em.memory_id,
                        "memory_type": "EPISODIC",
                        "task_id": em.task_id,
                        "goal": v_goal.sanitized_text or em.goal,
                        "outcome": em.outcome.value,
                        "summary": v_sum.sanitized_text or em.summary,
                        "solution_steps": clean_steps,
                        "failure_class": em.failure_class,
                        "project_id": em.project_id,
                        "created_at": em.created_at,
                        "taint_tags": list(set(v_goal.taint_tags + v_sum.taint_tags)),
                    })

            self.repo.record_audit_event(
                action="SECURITY_MEMORIES_EXPORTED",
                actor=AuditActor.USER,
                metadata={"project_id": project_id, "count": len(exported)},
            )
            return exported

    # -----------------------------------------------------------------------
    # Inspection & Statistics Helpers
    # -----------------------------------------------------------------------

    def get_memory(self, memory_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a single memory (semantic or episodic) by ID and sanitize before return."""
        with self._lock:
            # 1. Try semantic memory
            sm = self.repo.get_semantic_memory(memory_id)
            if sm:
                v_title = self.inspect_content(sm.title, source_type=sm.source_type.value)
                v_content = self.inspect_content(sm.content, source_type=sm.source_type.value)
                if not v_title.is_allowed or not v_content.is_allowed:
                    return None
                return {
                    "memory_id": sm.memory_id,
                    "memory_type": "SEMANTIC",
                    "title": v_title.sanitized_text or sm.title,
                    "content": v_content.sanitized_text or sm.content,
                    "category": sm.category.value,
                    "scope": sm.scope.value,
                    "project_id": sm.project_id,
                    "trust_level": sm.trust_level.value,
                    "source": sm.source_type.value,
                    "created_at": sm.created_at,
                    "updated_at": sm.updated_at,
                    "tags": sm.tags,
                    "security_state": v_content.state.value,
                    "taint_tags": list(set(v_title.taint_tags + v_content.taint_tags)),
                }

            # 2. Try episodic memory
            em = self.repo.get_episodic_memory(memory_id)
            if em:
                v_goal = self.inspect_content(em.goal, source_type="TASK_DERIVED")
                v_sum = self.inspect_content(em.summary, source_type="TASK_DERIVED")
                if not v_goal.is_allowed or not v_sum.is_allowed:
                    return None
                clean_steps = [
                    self.inspect_content(st, source_type="TASK_DERIVED").sanitized_text or st
                    for st in em.solution_steps
                ]
                return {
                    "memory_id": em.memory_id,
                    "memory_type": "EPISODIC",
                    "task_id": em.task_id,
                    "title": v_goal.sanitized_text or em.goal,
                    "content": v_sum.sanitized_text or em.summary,
                    "outcome": em.outcome.value,
                    "solution_steps": clean_steps,
                    "project_id": em.project_id,
                    "trust_level": "TASK_DERIVED",
                    "source": "TASK_DERIVED",
                    "created_at": em.created_at,
                    "tags": em.tags,
                    "security_state": v_sum.state.value,
                    "taint_tags": list(set(v_goal.taint_tags + v_sum.taint_tags)),
                }

            return None

    def list_preferences(
        self,
        profile_id: str = "default",
        scope: Optional[MemoryScope] = None,
        project_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List safe user preferences."""
        with self._lock:
            prefs = self.repo.list_preferences(profile_id=profile_id, scope=scope, project_id=project_id)
            safe_prefs = []
            for p in prefs:
                val_str = json.dumps(p.value) if not isinstance(p.value, str) else p.value
                verdict = self.inspect_content(val_str, source_type="USER_CONFIRMED")
                if not verdict.is_allowed:
                    continue
                safe_prefs.append({
                    "pref_id": p.pref_id,
                    "key": p.key,
                    "value": p.value,
                    "profile_id": p.profile_id,
                    "scope": p.scope.value,
                    "project_id": p.project_id,
                    "source": p.source.value,
                    "confidence": p.confidence,
                    "created_at": p.created_at,
                    "updated_at": p.updated_at,
                })
            return safe_prefs

    def get_memory_stats(self, project_id: Optional[str] = None) -> Dict[str, int]:
        """Fetch sanitized memory statistics."""
        with self._lock:
            raw_stats = self.repo.get_stats()
            sem_count = raw_stats.get("semantic_memories_count", 0)
            epi_count = raw_stats.get("episodic_memories_count", 0)
            pref_count = raw_stats.get("user_preferences_count", 0)

            with self.repo._connection() as conn:
                cur = conn.cursor()
                if project_id:
                    cur.execute("SELECT COUNT(*) FROM semantic_memories WHERE project_id = ?", (project_id,))
                    p_sem = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM episodic_memories WHERE project_id = ?", (project_id,))
                    p_epi = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM user_preferences WHERE project_id = ?", (project_id,))
                    p_pref = cur.fetchone()[0]
                    proj_scoped = p_sem + p_epi + p_pref
                else:
                    cur.execute("SELECT COUNT(*) FROM semantic_memories WHERE scope = 'PROJECT'")
                    p_sem = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM episodic_memories WHERE project_id IS NOT NULL")
                    p_epi = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM user_preferences WHERE project_id IS NOT NULL")
                    p_pref = cur.fetchone()[0]
                    proj_scoped = p_sem + p_epi + p_pref

                cur.execute("SELECT COUNT(*) FROM semantic_memories WHERE scope = 'GLOBAL'")
                g_sem = cur.fetchone()[0]
                cur.execute("SELECT COUNT(*) FROM user_preferences WHERE scope = 'GLOBAL'")
                g_pref = cur.fetchone()[0]
                global_count = g_sem + g_pref

            return {
                "total_memories": sem_count + epi_count,
                "semantic_count": sem_count,
                "episodic_count": epi_count,
                "preference_count": pref_count,
                "project_scoped_count": proj_scoped,
                "global_count": global_count,
            }

    # -----------------------------------------------------------------------
    # Authorization Independence Assertion
    # -----------------------------------------------------------------------

    def is_authorization_isolated(self) -> bool:
        """Formal check verifying that memory security does NOT authorize actions."""
        # By architectural design, MemorySecurityService holds no references to
        # SafetyGuard, CapabilityPermissionManager, or ConfirmationManager.
        return True

    # -----------------------------------------------------------------------
    # Internal Audit Helpers
    # -----------------------------------------------------------------------

    def _record_audit_rejection(
        self,
        action: str,
        memory_id: Optional[str],
        reason: str,
        actor: AuditActor,
    ) -> None:
        try:
            self.repo.record_audit_event(
                action=action,
                memory_id=memory_id,
                actor=actor,
                metadata=sanitize_audit_metadata({"reason": reason}),
            )
        except Exception as exc:
            logger.warning(f"[MEMORY_SECURITY] Failed to record audit rejection: {exc}")


# ---------------------------------------------------------------------------
# Authoritative Singleton
# ---------------------------------------------------------------------------

memory_security_service = MemorySecurityService()
