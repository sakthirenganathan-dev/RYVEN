"""RYVEN 3.0 — Milestone 17.9 Phase 3 Semantic Memory Retriever.

Authoritative local retrieval engine querying SQLite FTS5 full-text search
and episodic task memories with deterministic ranking, project isolation,
and trust-level weighting.

Design & Security Invariants:
- ADVISORY CONTEXT ONLY. Never executes retrieved content or grants authorizations.
- Pure local SQLite FTS5 (BM25) search; zero vector database or cloud dependencies.
- Hard project isolation: Project A memories NEVER leak into Project B context.
- Deterministic ranking: BM25 relevance * trust weighting * project boost * recency decay.
- Defensive sanitization: Query syntax sanitized against FTS syntax errors;
  retrieved content scrubbed of secrets, tokens, <think> tags, and raw media.
- Fail-closed / Fail-safe: Retrieval errors fall back gracefully to empty results;
  never halts assistant or task execution.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, Field

from app.core.logging_config import logger
from app.memory.repository import (
    EpisodicMemory,
    EpisodicOutcome,
    MemoryRepository,
    MemoryScope,
    RetentionClass,
    SemanticCategory,
    SemanticMemory,
    TrustLevel,
    _scrub_value_defensive,
    _utc_now_iso,
    memory_repository as default_memory_repo,
)


# ---------------------------------------------------------------------------
# Constants & Defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_RESULTS = 5
MAX_SEARCH_QUERY_TOKENS = 32

# Trust weighting scale (higher trust receives score multiplier)
TRUST_WEIGHTS: Dict[str, float] = {
    TrustLevel.USER_CONFIRMED.value: 1.0,
    TrustLevel.SYSTEM_DERIVED.value: 0.85,
    TrustLevel.TASK_DERIVED.value: 0.80,
    TrustLevel.DOCUMENT_DERIVED.value: 0.50,
    TrustLevel.WEB_DERIVED.value: 0.30,
}

# FTS syntax characters to sanitize
_FTS_OPERATORS = {"and", "or", "not", "near"}


# ---------------------------------------------------------------------------
# Structured Result Model
# ---------------------------------------------------------------------------

class MemoryResult(BaseModel):
    """Structured, sanitized memory item returned by retrieval."""
    memory_id: str
    memory_type: str = "SEMANTIC"  # "SEMANTIC" or "EPISODIC"
    title: str
    content: str
    relevance_score: float = Field(default=0.0, ge=0.0)
    trust_level: str = "SYSTEM_DERIVED"
    project_id: Optional[str] = None
    source: str = "SYSTEM_DERIVED"
    created_at: str = Field(default_factory=_utc_now_iso)
    task_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Query Sanitization & Relevance Helpers
# ---------------------------------------------------------------------------

def sanitize_fts_query(query: str) -> str:
    """Sanitize user query for SQLite FTS5 MATCH clause.
    
    Prevents syntax errors from unbalanced quotes, wildcards, operators,
    and special characters while retaining search terms.
    """
    if not query or not query.strip():
        return ""

    # Extract word tokens (letters, numbers, underscores)
    tokens = re.findall(r"\b[a-zA-Z0-9_\-\.]{2,}\b", query)
    if not tokens:
        return ""

    # Bound query length
    tokens = tokens[:MAX_SEARCH_QUERY_TOKENS]

    # Filter out bare boolean operators and quote tokens
    safe_terms: List[str] = []
    for tok in tokens:
        clean = tok.strip().lower()
        if clean in _FTS_OPERATORS:
            continue
        # Escape any internal double-quotes
        escaped = clean.replace('"', '""')
        safe_terms.append(f'"{escaped}"')

    if not safe_terms:
        return ""

    # Join with OR to ensure high recall; BM25 handles ranking
    return " OR ".join(safe_terms)


def _compute_recency_multiplier(created_at_iso: str) -> float:
    """Compute recency decay factor (1.0 for recent, slowly decays to ~0.7 over 90 days)."""
    try:
        dt = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_old = max(0.0, (now - dt).total_seconds() / 86400.0)
        # Slow asymptotic decay
        return 1.0 / (1.0 + 0.003 * days_old)
    except Exception:
        return 1.0


def _compute_episodic_overlap(tokens: Set[str], text: str) -> float:
    """Compute token overlap score between query tokens and episodic memory text."""
    if not tokens or not text:
        return 0.0
    text_words = set(re.findall(r"\b[a-zA-Z0-9_\-\.]{2,}\b", text.lower()))
    if not text_words:
        return 0.0
    matched = tokens.intersection(text_words)
    return len(matched) / max(1, len(tokens))


# ---------------------------------------------------------------------------
# Semantic Memory Retriever
# ---------------------------------------------------------------------------

class SemanticMemoryRetriever:
    """Authoritative retrieval engine over SQLite FTS5 and episodic task stores."""

    def __init__(self, memory_repo: Optional[MemoryRepository] = None) -> None:
        self.memory_repo = memory_repo or default_memory_repo
        self._lock = threading.RLock()

    def retrieve(
        self,
        query: str,
        project_id: Optional[str] = None,
        memory_type: str = "ALL",  # "ALL", "SEMANTIC", or "EPISODIC"
        limit: int = DEFAULT_MAX_RESULTS,
        min_relevance: float = 0.0,
        min_trust: Optional[str] = None,
    ) -> List[MemoryResult]:
        """Retrieve relevant, project-isolated, trust-ranked memory records.
        
        Args:
            query: Search query string.
            project_id: Active project ID for strict isolation.
            memory_type: Filter by 'ALL', 'SEMANTIC', or 'EPISODIC'.
            limit: Maximum number of memories to return (default 5).
            min_relevance: Minimum score threshold (0.0 to 1.0).
            min_trust: Minimum trust level filter.
            
        Returns:
            List of sanitized MemoryResult objects sorted by relevance descending.
        """
        try:
            with self._lock:
                effective_limit = max(1, min(limit, 20))
                query_clean = query.strip() if query else ""

                results: List[MemoryResult] = []

                # 1. Retrieve Semantic Memories (FTS5)
                if memory_type.upper() in ("ALL", "SEMANTIC"):
                    semantic_results = self._search_semantic(
                        query=query_clean,
                        project_id=project_id,
                        limit=effective_limit * 2,
                    )
                    results.extend(semantic_results)

                # 2. Retrieve Episodic Memories
                if memory_type.upper() in ("ALL", "EPISODIC"):
                    episodic_results = self._search_episodic(
                        query=query_clean,
                        project_id=project_id,
                        limit=effective_limit * 2,
                    )
                    results.extend(episodic_results)

                # 3. Apply Trust Threshold Filter
                if min_trust:
                    min_trust_val = TRUST_WEIGHTS.get(min_trust.upper(), 0.0)
                    results = [
                        r for r in results
                        if TRUST_WEIGHTS.get(r.trust_level, 0.0) >= min_trust_val
                    ]

                # 4. Filter by Minimum Relevance Score
                if min_relevance > 0.0:
                    results = [r for r in results if r.relevance_score >= min_relevance]

                # 5. Deterministic Deduplication by Memory ID & Content
                deduped: List[MemoryResult] = []
                seen_ids: Set[str] = set()
                seen_signatures: Set[str] = set()

                for r in results:
                    if r.memory_id in seen_ids:
                        continue
                    sig = f"{r.title.lower()}|{r.content[:80].lower()}"
                    if sig in seen_signatures:
                        continue

                    seen_ids.add(r.memory_id)
                    seen_signatures.add(sig)
                    deduped.append(r)

                # 6. Sort Deterministically: Score DESC, Trust DESC, Recency DESC, ID ASC
                deduped.sort(
                    key=lambda x: (
                        -x.relevance_score,
                        -TRUST_WEIGHTS.get(x.trust_level, 0.5),
                        x.created_at,
                        x.memory_id,
                    )
                )

                # Return strictly bounded count
                return deduped[:effective_limit]

        except Exception as exc:
            # Critical invariant: Fail closed/safe without halting execution
            logger.warning(f"[MEMORY_RETRIEVER] Retrieval failure: {exc}")
            return []

    # -----------------------------------------------------------------------
    # Semantic Search via FTS5
    # -----------------------------------------------------------------------

    def _search_semantic(
        self,
        query: str,
        project_id: Optional[str],
        limit: int,
    ) -> List[MemoryResult]:
        """Query semantic_memory_fts virtual table with project isolation."""
        if not query:
            # Empty query: return most recent relevant memories within scope
            return self._fetch_recent_semantic(project_id, limit)

        fts_query = sanitize_fts_query(query)
        if not fts_query:
            return self._fetch_recent_semantic(project_id, limit)

        results: List[MemoryResult] = []

        try:
            with self.memory_repo._connection() as conn:
                cur = conn.cursor()

                # Project Isolation:
                # If project_id provided -> scope = GLOBAL OR project_id = ?
                # If project_id is None -> scope = GLOBAL
                if project_id:
                    cur.execute(
                        """
                        SELECT 
                            fts.memory_id,
                            fts.title,
                            fts.content,
                            fts.tags,
                            fts.project_id,
                            fts.category,
                            bm25(semantic_memory_fts) as rank,
                            sm.trust_level,
                            sm.source_type,
                            sm.importance,
                            sm.created_at,
                            sm.scope
                        FROM semantic_memory_fts fts
                        JOIN semantic_memories sm ON fts.memory_id = sm.memory_id
                        WHERE semantic_memory_fts MATCH ?
                          AND (sm.scope = 'GLOBAL' OR sm.project_id = ?)
                        ORDER BY rank ASC
                        LIMIT ?
                        """,
                        (fts_query, project_id, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT 
                            fts.memory_id,
                            fts.title,
                            fts.content,
                            fts.tags,
                            fts.project_id,
                            fts.category,
                            bm25(semantic_memory_fts) as rank,
                            sm.trust_level,
                            sm.source_type,
                            sm.importance,
                            sm.created_at,
                            sm.scope
                        FROM semantic_memory_fts fts
                        JOIN semantic_memories sm ON fts.memory_id = sm.memory_id
                        WHERE semantic_memory_fts MATCH ?
                          AND sm.scope = 'GLOBAL'
                        ORDER BY rank ASC
                        LIMIT ?
                        """,
                        (fts_query, limit),
                    )

                rows = cur.fetchall()
                query_words = set(re.findall(r"\b[a-zA-Z0-9_\-\.]{2,}\b", query.lower()))

                for r in rows:
                    raw_rank = float(r["rank"])
                    # BM25 in SQLite is negative; more negative = better match
                    # Compute lexical token overlap across title, content, and tags
                    doc_text = f"{r['title']} {r['content']} {r['tags']}".lower()
                    doc_words = set(re.findall(r"\b[a-zA-Z0-9_\-\.]{2,}\b", doc_text))
                    overlap = (
                        len(query_words.intersection(doc_words)) / max(1, len(query_words))
                        if query_words
                        else 0.5
                    )

                    # Scale raw_rank signal: larger magnitude = higher term specificity
                    abs_rank = abs(raw_rank)
                    bm25_factor = 1.0 + min(1.0, abs_rank * 10000.0)

                    # Base relevance incorporates both lexical token overlap and BM25 term weighting
                    base_rel = min(1.0, max(0.1, overlap) * 0.8 * bm25_factor)

                    # Trust weighting
                    trust_lvl = str(r["trust_level"])
                    t_weight = TRUST_WEIGHTS.get(trust_lvl, 0.5)

                    # Project boost
                    p_boost = 1.2 if (project_id and r["project_id"] == project_id) else 1.0

                    # Recency decay
                    r_decay = _compute_recency_multiplier(r["created_at"])

                    # Importance factor
                    imp = float(r["importance"]) if r["importance"] is not None else 0.5
                    imp_factor = 0.8 + (0.4 * imp)  # range 0.8 to 1.2

                    final_score = base_rel * t_weight * p_boost * r_decay * imp_factor

                    # Clean tags
                    tags_raw = json.loads(r["tags"]) if r["tags"] else []

                    results.append(
                        MemoryResult(
                            memory_id=r["memory_id"],
                            memory_type="SEMANTIC",
                            title=_scrub_value_defensive(r["title"]),
                            content=_scrub_value_defensive(r["content"]),
                            relevance_score=round(final_score, 4),
                            trust_level=trust_lvl,
                            project_id=r["project_id"],
                            source=str(r["source_type"]),
                            created_at=r["created_at"],
                            tags=_scrub_value_defensive(tags_raw),
                        )
                    )

        except sqlite3.OperationalError as op_err:
            logger.debug(f"[MEMORY_RETRIEVER] FTS query operational warning: {op_err}")
            return self._fetch_recent_semantic(project_id, limit)

        return results

    def _fetch_recent_semantic(
        self,
        project_id: Optional[str],
        limit: int,
    ) -> List[MemoryResult]:
        """Fallback: Return recent memories within scope if FTS query is empty or failed."""
        results: List[MemoryResult] = []
        try:
            with self.memory_repo._connection() as conn:
                cur = conn.cursor()
                if project_id:
                    cur.execute(
                        """
                        SELECT * FROM semantic_memories
                        WHERE scope = 'GLOBAL' OR project_id = ?
                        ORDER BY updated_at DESC LIMIT ?
                        """,
                        (project_id, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM semantic_memories
                        WHERE scope = 'GLOBAL'
                        ORDER BY updated_at DESC LIMIT ?
                        """,
                        (limit,),
                    )
                rows = cur.fetchall()
                for r in rows:
                    t_weight = TRUST_WEIGHTS.get(r["trust_level"], 0.5)
                    results.append(
                        MemoryResult(
                            memory_id=r["memory_id"],
                            memory_type="SEMANTIC",
                            title=_scrub_value_defensive(r["title"]),
                            content=_scrub_value_defensive(r["content"]),
                            relevance_score=round(0.2 * t_weight, 4),  # modest baseline score
                            trust_level=r["trust_level"],
                            project_id=r["project_id"],
                            source=r["source_type"],
                            created_at=r["created_at"],
                            tags=_scrub_value_defensive(json.loads(r["tags_json"])),
                        )
                    )
        except Exception:
            pass
        return results

    # -----------------------------------------------------------------------
    # Episodic Search
    # -----------------------------------------------------------------------

    def _search_episodic(
        self,
        query: str,
        project_id: Optional[str],
        limit: int,
    ) -> List[MemoryResult]:
        """Query episodic_memories with project isolation and token overlap."""
        results: List[MemoryResult] = []
        query_tokens = set(re.findall(r"\b[a-zA-Z0-9_\-\.]{2,}\b", query.lower())) if query else set()

        try:
            with self.memory_repo._connection() as conn:
                cur = conn.cursor()
                # Query episodic memories respecting project isolation
                if project_id:
                    cur.execute(
                        """
                        SELECT * FROM episodic_memories
                        WHERE project_id IS NULL OR project_id = ?
                        ORDER BY created_at DESC LIMIT ?
                        """,
                        (project_id, limit * 3),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM episodic_memories
                        WHERE project_id IS NULL
                        ORDER BY created_at DESC LIMIT ?
                        """,
                        (limit * 3,),
                    )

                rows = cur.fetchall()

                for r in rows:
                    # Score based on token overlap against goal, summary, and tags
                    text_corpus = f"{r['goal']} {r['summary']} {r['tags_json']}"
                    overlap = _compute_episodic_overlap(query_tokens, text_corpus)

                    if query_tokens and overlap <= 0.0:
                        # Skip completely irrelevant episodic tasks unless empty query
                        continue

                    # Outcome weighting (SUCCESS gives higher utility)
                    outcome_str = str(r["outcome"]).upper()
                    outcome_factor = 1.0 if outcome_str == "SUCCESS" else (0.8 if outcome_str == "PARTIAL" else 0.6)

                    # Project boost
                    p_boost = 1.2 if (project_id and r["project_id"] == project_id) else 1.0

                    # Recency factor
                    r_decay = _compute_recency_multiplier(r["created_at"])

                    base_rel = overlap if query_tokens else 0.2
                    final_score = base_rel * TRUST_WEIGHTS[TrustLevel.TASK_DERIVED.value] * outcome_factor * p_boost * r_decay

                    tags_raw = json.loads(r["tags_json"]) if r["tags_json"] else []

                    results.append(
                        MemoryResult(
                            memory_id=r["memory_id"],
                            memory_type="EPISODIC",
                            title=_scrub_value_defensive(f"Task: {r['goal'][:60]} ({outcome_str})"),
                            content=_scrub_value_defensive(r["summary"]),
                            relevance_score=round(final_score, 4),
                            trust_level=TrustLevel.TASK_DERIVED.value,
                            project_id=r["project_id"],
                            source="TASK_DERIVED",
                            created_at=r["created_at"],
                            task_id=r["task_id"],
                            tags=_scrub_value_defensive(tags_raw),
                        )
                    )

        except Exception as exc:
            logger.debug(f"[MEMORY_RETRIEVER] Episodic search warning: {exc}")

        return results


# Global singleton retriever instance
semantic_memory_retriever = SemanticMemoryRetriever()
