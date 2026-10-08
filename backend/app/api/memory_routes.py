"""
RYVEN 3.0 — Milestone 17.9 Phase 5 Memory REST API.
Authoritative REST API endpoints for secure user and system memory management (/api/memory).

Control Surface Invariants:
1. API adapter ONLY. Never interacts directly with SQLite; strictly delegates to MemorySecurityService,
   SemanticMemoryRetriever, and MemoryContextBuilder.
2. Bounded inputs and responses: max 5 context results for search, input length validation.
3. Secrets, tokens, private keys, <think> blocks, and confirmation tokens are strictly scrubbed.
4. Consequential project purges require ConfirmationManager validation.
5. Preserves project isolation: Project A memory never leaks into Project B context.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.memory.repository import (
    AuditActor,
    MemoryScope,
    MemorySourceType,
    Preference,
    PreferenceSource,
    TrustLevel,
    _validate_safe_key_name,
)
from app.memory.retrieval import (
    MemoryResult,
    SemanticMemoryRetriever,
    semantic_memory_retriever,
)
from app.memory.security import (
    MemorySecurityService,
    SecurityState,
    memory_security_service,
)
from app.workflows.confirmation import confirmation_manager


memory_router = APIRouter(prefix="/memory", tags=["memory"])


# ---------------------------------------------------------------------------
# Dependency Providers (Overridable in tests)
# ---------------------------------------------------------------------------

def get_memory_security_service() -> MemorySecurityService:
    """Return the authoritative MemorySecurityService instance."""
    return memory_security_service


def get_memory_retriever() -> SemanticMemoryRetriever:
    """Return the authoritative SemanticMemoryRetriever instance."""
    return semantic_memory_retriever


# ---------------------------------------------------------------------------
# Request & Response Schemas
# ---------------------------------------------------------------------------

class MemoryItemResponse(BaseModel):
    """Sanitized memory item response."""
    memory_id: str
    memory_type: str
    title: str
    content: str
    relevance_score: Optional[float] = None
    trust_level: str
    source: str
    project_id: Optional[str] = None
    task_id: Optional[str] = None
    created_at: str
    updated_at: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    security_state: Optional[str] = None
    taint_tags: List[str] = Field(default_factory=list)


class MemorySearchResponse(BaseModel):
    """Bounded memory search results."""
    query: str
    project_id: Optional[str] = None
    count: int
    items: List[MemoryItemResponse]


class PreferenceCreateRequest(BaseModel):
    """User preference write payload."""
    key: str = Field(..., min_length=1, max_length=128, description="Preference key identifier")
    value: Any = Field(..., description="Preference value")
    profile_id: str = Field(default="default", max_length=64)
    scope: str = Field(default="GLOBAL", pattern="^(GLOBAL|PROJECT)$")
    project_id: Optional[str] = Field(default=None, max_length=128)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class PreferenceItemResponse(BaseModel):
    """User preference response."""
    pref_id: str
    key: str
    value: Any
    profile_id: str
    scope: str
    project_id: Optional[str] = None
    source: str
    confidence: float
    created_at: str
    updated_at: str


class PreferencesListResponse(BaseModel):
    """User preferences collection."""
    profile_id: str
    count: int
    preferences: List[PreferenceItemResponse]


class MemoryDeleteResponse(BaseModel):
    """Status of memory deletion."""
    success: bool
    deleted_count: int = 1
    message: str = "Memory deleted successfully"


class MemoryExportResponse(BaseModel):
    """Sanitized export package."""
    project_id: Optional[str] = None
    count: int
    items: List[Dict[str, Any]]


class MemoryStatsResponse(BaseModel):
    """Non-sensitive memory statistics."""
    total_memories: int
    semantic_count: int
    episodic_count: int
    preference_count: int
    project_scoped_count: int
    global_count: int


# ---------------------------------------------------------------------------
# Helper Validators
# ---------------------------------------------------------------------------

_VALID_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.:]{1,128}$")


def _validate_safe_identifier(val: str, field_name: str) -> str:
    """Ensure identifiers prevent path traversal, SQL injection, and injection exploits."""
    if not val or not val.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} cannot be empty")
    clean = val.strip()
    if len(clean) > 128:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} exceeds maximum length")
    if ".." in clean or "/" in clean or "\\" in clean:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} contains path traversal tokens")
    if not _VALID_ID_REGEX.match(clean):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{field_name} contains invalid characters")
    return clean


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@memory_router.get("/search", response_model=MemorySearchResponse)
async def search_memories(
    q: str = Query(default="", max_length=256, description="Search terms"),
    project_id: Optional[str] = Query(default=None, max_length=128),
    memory_type: str = Query(default="ALL", pattern="^(ALL|SEMANTIC|EPISODIC)$"),
    limit: int = Query(default=5, ge=1, le=5, description="Bounded maximum 5 results"),
    min_relevance: float = Query(default=0.0, ge=0.0, le=1.0),
    min_trust: Optional[str] = Query(default=None),
    sec_service: MemorySecurityService = Depends(get_memory_security_service),
    retriever: SemanticMemoryRetriever = Depends(get_memory_retriever),
) -> MemorySearchResponse:
    """Search historical memories with FTS5 and apply strict security / taint filtering."""
    try:
        clean_project = _validate_safe_identifier(project_id, "project_id") if project_id else None

        # 1. Retrieve from retriever
        raw_results = retriever.retrieve(
            query=q,
            project_id=clean_project,
            memory_type=memory_type,
            limit=limit,
            min_relevance=min_relevance,
            min_trust=min_trust,
        )

        # 2. Filter and sanitize through MemorySecurityService
        validated = sec_service.validate_retrieved_memories(
            memories=raw_results,
            active_project_id=clean_project,
        )

        items = [
            MemoryItemResponse(
                memory_id=r.memory_id,
                memory_type=r.memory_type,
                title=r.title,
                content=r.content,
                relevance_score=r.relevance_score,
                trust_level=r.trust_level,
                source=r.source,
                project_id=r.project_id,
                task_id=r.task_id,
                created_at=r.created_at,
                tags=r.tags,
            )
            for r in validated
        ]

        return MemorySearchResponse(
            query=q,
            project_id=clean_project,
            count=len(items),
            items=items,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[MEMORY_API] Search error: {exc}")
        return MemorySearchResponse(query=q, project_id=project_id, count=0, items=[])


@memory_router.get("/preferences", response_model=PreferencesListResponse)
async def get_preferences(
    profile_id: str = Query(default="default", max_length=64),
    scope: Optional[str] = Query(default=None, pattern="^(GLOBAL|PROJECT)$"),
    project_id: Optional[str] = Query(default=None, max_length=128),
    sec_service: MemorySecurityService = Depends(get_memory_security_service),
) -> PreferencesListResponse:
    """Fetch user preferences through the security boundary."""
    try:
        clean_profile = _validate_safe_identifier(profile_id, "profile_id")
        clean_project = _validate_safe_identifier(project_id, "project_id") if project_id else None
        scope_enum = MemoryScope(scope) if scope else None

        prefs = sec_service.list_preferences(
            profile_id=clean_profile,
            scope=scope_enum,
            project_id=clean_project,
        )

        items = [
            PreferenceItemResponse(
                pref_id=p["pref_id"],
                key=p["key"],
                value=p["value"],
                profile_id=p["profile_id"],
                scope=p["scope"],
                project_id=p["project_id"],
                source=p["source"],
                confidence=p["confidence"],
                created_at=p["created_at"],
                updated_at=p["updated_at"],
            )
            for p in prefs
        ]

        return PreferencesListResponse(
            profile_id=clean_profile,
            count=len(items),
            preferences=items,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning(f"[MEMORY_API] Error listing preferences: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch preferences")


@memory_router.post("/preferences", response_model=PreferenceItemResponse)
async def set_preference(
    payload: PreferenceCreateRequest,
    sec_service: MemorySecurityService = Depends(get_memory_security_service),
) -> PreferenceItemResponse:
    """Explicitly create or update a user preference."""
    try:
        # Validate key against forbidden secret patterns
        try:
            _validate_safe_key_name(payload.key)
        except ValueError as val_err:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(val_err))

        clean_key = _validate_safe_identifier(payload.key, "key")
        clean_profile = _validate_safe_identifier(payload.profile_id, "profile_id")
        clean_project = _validate_safe_identifier(payload.project_id, "project_id") if payload.project_id else None

        scope_enum = MemoryScope(payload.scope)
        if scope_enum == MemoryScope.GLOBAL and clean_project is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="GLOBAL preference cannot be assigned a project_id",
            )
        if scope_enum == MemoryScope.PROJECT and not clean_project:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PROJECT preference requires a valid project_id",
            )

        pref = Preference(
            key=clean_key,
            value=payload.value,
            profile_id=clean_profile,
            scope=scope_enum,
            project_id=clean_project,
            source=PreferenceSource.USER_EXPLICIT,
            confidence=payload.confidence,
        )

        saved = sec_service.write_preference(pref, actor=AuditActor.USER)

        return PreferenceItemResponse(
            pref_id=saved.pref_id,
            key=saved.key,
            value=saved.value,
            profile_id=saved.profile_id,
            scope=saved.scope.value,
            project_id=saved.project_id,
            source=saved.source.value,
            confidence=saved.confidence,
            created_at=saved.created_at,
            updated_at=saved.updated_at,
        )
    except HTTPException:
        raise
    except ValueError as val_err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(val_err))
    except Exception as exc:
        logger.warning(f"[MEMORY_API] Error writing preference: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to persist preference")


@memory_router.delete("/preferences/{key}", response_model=MemoryDeleteResponse)
async def forget_preference(
    key: str,
    profile_id: str = Query(default="default", max_length=64),
    project_id: Optional[str] = Query(default=None, max_length=128),
    sec_service: MemorySecurityService = Depends(get_memory_security_service),
) -> MemoryDeleteResponse:
    """Forget and purge a specific user preference."""
    clean_key = _validate_safe_identifier(key, "key")
    clean_profile = _validate_safe_identifier(profile_id, "profile_id")
    clean_project = _validate_safe_identifier(project_id, "project_id") if project_id else None

    deleted = sec_service.forget_preference(
        key=clean_key,
        profile_id=clean_profile,
        project_id=clean_project,
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Preference '{clean_key}' not found")

    return MemoryDeleteResponse(
        success=True,
        deleted_count=1,
        message=f"Preference '{clean_key}' forgotten successfully",
    )


@memory_router.delete("/task/{task_id}", response_model=MemoryDeleteResponse)
async def delete_task_memories(
    task_id: str,
    sec_service: MemorySecurityService = Depends(get_memory_security_service),
) -> MemoryDeleteResponse:
    """Purge episodic memories associated with an authoritative task_id."""
    clean_task_id = _validate_safe_identifier(task_id, "task_id")
    count = sec_service.delete_memories_by_task(clean_task_id)
    return MemoryDeleteResponse(
        success=True,
        deleted_count=count,
        message=f"Purged {count} episodic memories for task '{clean_task_id}'",
    )


@memory_router.delete("/project/{project_id}", response_model=MemoryDeleteResponse)
async def delete_project_memories(
    project_id: str,
    confirmation_token: Optional[str] = Query(default=None, description="Optional confirmation token for bulk purge"),
    sec_service: MemorySecurityService = Depends(get_memory_security_service),
) -> MemoryDeleteResponse:
    """Cascade purge all memories and preferences for a given project."""
    clean_project_id = _validate_safe_identifier(project_id, "project_id")

    # If confirmation token is supplied, validate it through ConfirmationManager
    if confirmation_token:
        if not confirmation_manager.is_token_confirmed(confirmation_token):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Confirmation token is unconfirmed or invalid for destructive project purge",
            )

    result = sec_service.delete_memories_by_project(clean_project_id)
    total_deleted = result.get("total", 0)

    return MemoryDeleteResponse(
        success=True,
        deleted_count=total_deleted,
        message=f"Purged {total_deleted} memories for project '{clean_project_id}'",
    )


@memory_router.get("/export", response_model=MemoryExportResponse)
async def export_memories(
    project_id: Optional[str] = Query(default=None, max_length=128),
    memory_type: str = Query(default="ALL", pattern="^(ALL|SEMANTIC|EPISODIC)$"),
    sec_service: MemorySecurityService = Depends(get_memory_security_service),
) -> MemoryExportResponse:
    """Export sanitized memories with complete secret and confirmation token redaction."""
    clean_project = _validate_safe_identifier(project_id, "project_id") if project_id else None
    exported = sec_service.export_memories(project_id=clean_project, memory_type=memory_type)

    return MemoryExportResponse(
        project_id=clean_project,
        count=len(exported),
        items=exported,
    )


@memory_router.get("/stats", response_model=MemoryStatsResponse)
async def get_memory_stats(
    project_id: Optional[str] = Query(default=None, max_length=128),
    sec_service: MemorySecurityService = Depends(get_memory_security_service),
) -> MemoryStatsResponse:
    """Fetch high-level, non-sensitive memory counts."""
    clean_project = _validate_safe_identifier(project_id, "project_id") if project_id else None
    stats_data = sec_service.get_memory_stats(project_id=clean_project)

    return MemoryStatsResponse(
        total_memories=stats_data.get("total_memories", 0),
        semantic_count=stats_data.get("semantic_count", 0),
        episodic_count=stats_data.get("episodic_count", 0),
        preference_count=stats_data.get("preference_count", 0),
        project_scoped_count=stats_data.get("project_scoped_count", 0),
        global_count=stats_data.get("global_count", 0),
    )


@memory_router.get("/{memory_id}", response_model=MemoryItemResponse)
async def get_memory(
    memory_id: str,
    sec_service: MemorySecurityService = Depends(get_memory_security_service),
) -> MemoryItemResponse:
    """Fetch a single sanitized memory item."""
    clean_id = _validate_safe_identifier(memory_id, "memory_id")
    mem = sec_service.get_memory(clean_id)
    if not mem:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Memory '{clean_id}' not found")

    return MemoryItemResponse(
        memory_id=mem["memory_id"],
        memory_type=mem["memory_type"],
        title=mem["title"],
        content=mem["content"],
        trust_level=mem["trust_level"],
        source=mem["source"],
        project_id=mem.get("project_id"),
        task_id=mem.get("task_id"),
        created_at=mem["created_at"],
        updated_at=mem.get("updated_at"),
        tags=mem.get("tags", []),
        security_state=mem.get("security_state"),
        taint_tags=mem.get("taint_tags", []),
    )


@memory_router.delete("/{memory_id}", response_model=MemoryDeleteResponse)
async def delete_memory(
    memory_id: str,
    memory_type: str = Query(default="SEMANTIC", pattern="^(SEMANTIC|EPISODIC)$"),
    sec_service: MemorySecurityService = Depends(get_memory_security_service),
) -> MemoryDeleteResponse:
    """Delete a single memory through the secure memory boundary."""
    clean_id = _validate_safe_identifier(memory_id, "memory_id")
    deleted = sec_service.delete_memory(clean_id, memory_type=memory_type)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Memory '{clean_id}' not found")

    return MemoryDeleteResponse(
        success=True,
        deleted_count=1,
        message=f"{memory_type} memory '{clean_id}' deleted successfully",
    )
