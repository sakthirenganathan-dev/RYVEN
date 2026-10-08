"""RYVEN 3.0 — Milestone 17.9 Phase 2 Episodic Task Memory & Indexer.

Automatically extracts compact, searchable episodic summaries from terminal
long-horizon and unified multimodal tasks, preserving authoritative task provenance.

Design & Security Invariants:
- CONTEXT SERVICE ONLY. Never alters task execution state or executes commands.
- The authoritative detailed execution record remains TaskPersistenceRepository.
- Episodic memories are compact semantic summaries (~100–300 words max);
  NEVER duplicate entire action graphs, journal logs, or raw media.
- Idempotent: repeated terminal events for the same task_id do not create duplicate memories.
- Hook isolation: indexing errors are caught and logged; they NEVER cause task failure.
- Defensive secret boundary: credentials, tokens, private keys, and <think> tags are stripped.
- Trust level: strictly TASK_DERIVED (cannot grant permissions or override SafetyGuard).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import re
import threading
from typing import Any, Dict, List, Optional, Set, Union
import uuid

from app.actions.event_bus import ActionEventBus, action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.core.logging_config import logger
from app.memory.repository import (
    EpisodicMemory,
    EpisodicOutcome,
    MemoryRepository,
    MemoryScope,
    RetentionClass,
    TrustLevel,
    _scrub_value_defensive,
    memory_repository as default_memory_repo,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_SUMMARY_WORDS = 300
MAX_SUMMARY_CHARS = 1500
MAX_SOLUTION_STEPS = 10
MAX_STEP_DESC_CHARS = 100
MAX_TAGS = 10

_TERMINAL_ACTION_TYPES = {
    ActionType.LONG_TASK_COMPLETED,
    ActionType.LONG_TASK_FAILED,
    ActionType.LONG_TASK_CANCELLED,
    ActionType.TASK_COMPLETED,
    ActionType.TASK_FAILED,
    ActionType.TASK_CANCELLED,
}

_TERMINAL_STATUSES = {
    ActionStatus.COMPLETED,
    ActionStatus.FAILED,
    ActionStatus.CANCELLED,
}


def _clean_whitespace(text: str) -> str:
    """Normalize excess whitespace and linebreaks."""
    return re.sub(r"\s+", " ", text).strip()


def _truncate_words(text: str, max_words: int = MAX_SUMMARY_WORDS) -> str:
    """Truncate text cleanly to a maximum word count."""
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "..."


# ---------------------------------------------------------------------------
# Episodic Memory Indexer
# ---------------------------------------------------------------------------

class EpisodicMemoryIndexer:
    """Observes task completion/failure events and indexes compact episodic memories."""

    def __init__(
        self,
        memory_repo: Optional[MemoryRepository] = None,
        task_repo: Optional[Any] = None,
        event_bus: Optional[ActionEventBus] = None,
        security_service: Optional[Any] = None,
        auto_subscribe: bool = True,
    ) -> None:
        self.memory_repo = memory_repo or default_memory_repo
        self._task_repo = task_repo
        self.event_bus = event_bus or action_bus
        self._security_service = security_service
        self._lock = threading.RLock()
        self._subscriber_id: Optional[str] = None
        self._indexed_task_ids: Set[str] = set()

        if auto_subscribe and self.event_bus:
            self.subscribe()

    @property
    def security_service(self) -> Any:
        """Lazy load MemorySecurityService bound to current memory_repo."""
        if self._security_service is None:
            try:
                from app.memory.security import MemorySecurityService
                self._security_service = MemorySecurityService(memory_repo=self.memory_repo)
            except Exception:
                pass
        return self._security_service

    @property
    def task_repo(self) -> Any:
        """Lazy load TaskPersistenceRepository if not explicitly injected."""
        if self._task_repo is None:
            try:
                from app.control.long_horizon import TaskPersistenceRepository
                self._task_repo = TaskPersistenceRepository()
            except Exception as exc:
                logger.debug(f"[EPISODIC_INDEXER] Could not lazy-init TaskPersistenceRepository: {exc}")
        return self._task_repo

    def subscribe(self) -> Optional[str]:
        """Subscribe to terminal task events on ActionEventBus."""
        with self._lock:
            if self._subscriber_id is not None:
                return self._subscriber_id
            if self.event_bus:
                self._subscriber_id = self.event_bus.subscribe(self.handle_event)
                logger.info(f"[EPISODIC_INDEXER] Subscribed to action_bus as '{self._subscriber_id}'")
            return self._subscriber_id

    def unsubscribe(self) -> None:
        """Safely detach subscriber from ActionEventBus."""
        with self._lock:
            if self._subscriber_id and self.event_bus:
                try:
                    self.event_bus.unsubscribe(self._subscriber_id)
                except Exception:
                    pass
                self._subscriber_id = None

    def close(self) -> None:
        """Release resources and unsubscribe."""
        self.unsubscribe()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    async def handle_event(self, event: ActionEvent) -> Optional[EpisodicMemory]:
        """Process an ActionEvent from the event bus and index if terminal."""
        try:
            # Check if this is a terminal task event
            is_terminal_type = event.action_type in _TERMINAL_ACTION_TYPES
            is_terminal_status = event.status in _TERMINAL_STATUSES and bool(event.task_id)

            if not (is_terminal_type or is_terminal_status):
                return None

            task_id = event.task_id
            if not task_id:
                return None

            # Non-blocking indexing
            return self.index_task(task_id, event_hint=event)

        except Exception as exc:
            # Critical hook safety invariant: indexing errors NEVER bubble up
            logger.warning(f"[EPISODIC_INDEXER] Non-fatal error handling event: {exc}")
            return None

    def index_task(
        self,
        task_or_id: Union[str, Any],
        event_hint: Optional[ActionEvent] = None,
        force_update: bool = False,
    ) -> Optional[EpisodicMemory]:
        """Extract and persist a compact episodic memory from task data.
        
        Args:
            task_or_id: Task ID string, LongHorizonTask, UnifiedTask, or dict.
            event_hint: Optional ActionEvent providing metadata or status hints.
            force_update: Whether to overwrite existing episodic memory for this task_id.
            
        Returns:
            The created/updated EpisodicMemory, or None if indexing was skipped/failed.
        """
        try:
            task_id, task_obj, meta = self._resolve_task_payload(task_or_id, event_hint)
            if not task_id:
                return None

            with self._lock:
                # 1. Idempotency Check
                existing = self.memory_repo.get_episodic_memory_by_task_id(task_id)
                if existing and not force_update:
                    self._indexed_task_ids.add(task_id)
                    logger.debug(f"[EPISODIC_INDEXER] Task '{task_id}' already indexed (memory_id={existing.memory_id})")
                    return existing

                # 2. Extract Fields
                goal = self._extract_goal(task_obj, meta, event_hint)
                normalized_goal = _clean_whitespace(goal.lower())
                outcome = self._classify_outcome(task_obj, meta, event_hint)
                failure_class = self._extract_failure_class(task_obj, meta, outcome)
                project_id = self._extract_project_id(task_obj, meta)
                solution_steps = self._extract_solution_steps(task_obj, meta)
                summary = self._build_compact_summary(
                    goal=goal,
                    outcome=outcome,
                    task_obj=task_obj,
                    meta=meta,
                    failure_class=failure_class,
                    event_hint=event_hint,
                )
                tags = self._extract_tags(task_obj, meta, outcome, project_id)
                retention_class = self._extract_retention_class(task_obj, meta)

                # 3. Assemble and Persist
                if existing and force_update:
                    existing.goal = goal
                    existing.normalized_goal = normalized_goal
                    existing.outcome = outcome
                    existing.summary = summary
                    existing.solution_steps = solution_steps
                    existing.failure_class = failure_class
                    existing.project_id = project_id
                    existing.tags = tags
                    existing.retention_class = retention_class
                    saved = self.memory_repo.update_episodic_memory(existing)
                else:
                    new_mem = EpisodicMemory(
                        task_id=task_id,
                        goal=goal,
                        normalized_goal=normalized_goal,
                        outcome=outcome,
                        summary=summary,
                        solution_steps=solution_steps,
                        failure_class=failure_class,
                        project_id=project_id,
                        tags=tags,
                        retention_class=retention_class,
                    )
                    if self.security_service:
                        saved = self.security_service.write_episodic_memory(new_mem)
                    else:
                        saved = self.memory_repo.create_episodic_memory(new_mem)

                self._indexed_task_ids.add(task_id)
                logger.info(
                    f"[EPISODIC_INDEXER] Indexed episodic memory for task '{task_id}' "
                    f"(outcome={outcome.value}, words={len(summary.split())})"
                )
                return saved

        except Exception as exc:
            # Isolated error containment
            logger.warning(f"[EPISODIC_INDEXER] Failed to index task {task_or_id!r}: {exc}")
            return None

    # -----------------------------------------------------------------------
    # Task Resolution & Data Extraction Primitives
    # -----------------------------------------------------------------------

    def _resolve_task_payload(
        self,
        task_or_id: Union[str, Any],
        event_hint: Optional[ActionEvent],
    ) -> Tuple[Optional[str], Optional[Any], Dict[str, Any]]:
        """Normalize task_or_id into (task_id, task_object, metadata_dict)."""
        task_id: Optional[str] = None
        task_obj: Optional[Any] = None
        meta: Dict[str, Any] = {}

        if isinstance(task_or_id, str):
            task_id = task_or_id
            if self.task_repo and hasattr(self.task_repo, "get_task"):
                try:
                    task_obj = self.task_repo.get_task(task_id)
                except Exception:
                    task_obj = None
        elif isinstance(task_or_id, dict):
            task_id = task_or_id.get("task_id") or task_or_id.get("id")
            task_obj = task_or_id
            meta = task_or_id
        elif hasattr(task_or_id, "task_id"):
            task_id = getattr(task_or_id, "task_id")
            task_obj = task_or_id

        # Merge event metadata if available
        if event_hint:
            if not task_id:
                task_id = event_hint.task_id
            if event_hint.safe_metadata:
                meta = {**meta, **event_hint.safe_metadata}

        return task_id, task_obj, meta

    def _extract_goal(self, task_obj: Any, meta: Dict[str, Any], event_hint: Optional[ActionEvent]) -> str:
        """Extract a clean, non-fabricated goal string."""
        if task_obj:
            if hasattr(task_obj, "goal") and getattr(task_obj, "goal"):
                return str(getattr(task_obj, "goal")).strip()
            if isinstance(task_obj, dict) and task_obj.get("goal"):
                return str(task_obj["goal"]).strip()

        if meta.get("goal"):
            return str(meta["goal"]).strip()

        if event_hint and event_hint.title:
            return event_hint.title.strip()

        return "Unnamed task"

    def _classify_outcome(
        self,
        task_obj: Any,
        meta: Dict[str, Any],
        event_hint: Optional[ActionEvent],
    ) -> EpisodicOutcome:
        """Classify task outcome into SUCCESS, FAILURE, or PARTIAL without inventing states."""
        # 1. Explicit metadata override
        if meta.get("partial") is True or meta.get("outcome") == "PARTIAL":
            return EpisodicOutcome.PARTIAL

        # 2. Check task_obj state / progress
        state_str = ""
        total_steps = 0
        completed_steps = 0
        progress_pct = 0.0

        if task_obj:
            if hasattr(task_obj, "state"):
                s = getattr(task_obj, "state")
                state_str = s.value if hasattr(s, "value") else str(s)
            elif isinstance(task_obj, dict):
                state_str = str(task_obj.get("state") or task_obj.get("status") or "")

            if hasattr(task_obj, "steps") and getattr(task_obj, "steps"):
                val = getattr(task_obj, "steps")
                total_steps = len(val) if isinstance(val, (list, tuple, set)) else 0
            elif isinstance(task_obj, dict) and "steps" in task_obj:
                val = task_obj.get("steps")
                total_steps = len(val) if isinstance(val, (list, tuple, set)) else 0

            if hasattr(task_obj, "completed_step_ids"):
                val = getattr(task_obj, "completed_step_ids")
                completed_steps = len(val) if isinstance(val, (list, tuple, set)) else 0
            elif isinstance(task_obj, dict) and "completed_step_ids" in task_obj:
                val = task_obj.get("completed_step_ids")
                completed_steps = len(val) if isinstance(val, (list, tuple, set)) else 0

            if hasattr(task_obj, "progress_percent"):
                try:
                    progress_pct = float(getattr(task_obj, "progress_percent") or 0.0)
                except (ValueError, TypeError):
                    progress_pct = 0.0
            elif isinstance(task_obj, dict) and "progress_percent" in task_obj:
                try:
                    progress_pct = float(task_obj.get("progress_percent") or 0.0)
                except (ValueError, TypeError):
                    progress_pct = 0.0

        # 3. Check event_hint status
        event_status_str = ""
        if event_hint:
            s = event_hint.status
            event_status_str = s.value if hasattr(s, "value") else str(s)

        effective_state = (state_str or event_status_str).upper()

        if "COMPLETED" in effective_state:
            # If completed but explicitly did not finish all steps: PARTIAL
            if total_steps > 0 and completed_steps > 0 and completed_steps < total_steps:
                return EpisodicOutcome.PARTIAL
            return EpisodicOutcome.SUCCESS

        if "CANCELLED" in effective_state or "BLOCKED" in effective_state or "FAILED" in effective_state:
            # If failed/cancelled but made measurable partial progress: PARTIAL
            if completed_steps > 0 or progress_pct >= 30.0:
                return EpisodicOutcome.PARTIAL
            return EpisodicOutcome.FAILURE

        return EpisodicOutcome.FAILURE

    def _extract_failure_class(
        self,
        task_obj: Any,
        meta: Dict[str, Any],
        outcome: EpisodicOutcome,
    ) -> Optional[str]:
        """Extract failure classification if the task failed or partially succeeded."""
        if outcome == EpisodicOutcome.SUCCESS:
            return None

        if task_obj:
            if hasattr(task_obj, "failure_class") and getattr(task_obj, "failure_class"):
                return str(getattr(task_obj, "failure_class"))
            if isinstance(task_obj, dict) and task_obj.get("failure_class"):
                return str(task_obj["failure_class"])

        if meta.get("failure_class"):
            return str(meta["failure_class"])

        return "UNKNOWN_FAILURE" if outcome == EpisodicOutcome.FAILURE else None

    def _extract_project_id(self, task_obj: Any, meta: Dict[str, Any]) -> Optional[str]:
        """Extract authoritative project_id; never guess from arbitrary user strings."""
        if task_obj:
            if hasattr(task_obj, "safe_metadata") and isinstance(getattr(task_obj, "safe_metadata"), dict):
                p = getattr(task_obj, "safe_metadata").get("project_id")
                if p:
                    return str(p).strip()
            if isinstance(task_obj, dict):
                if task_obj.get("project_id"):
                    return str(task_obj["project_id"]).strip()
                if isinstance(task_obj.get("safe_metadata"), dict) and task_obj["safe_metadata"].get("project_id"):
                    return str(task_obj["safe_metadata"]["project_id"]).strip()

        if meta.get("project_id"):
            return str(meta["project_id"]).strip()

        return None

    def _extract_solution_steps(self, task_obj: Any, meta: Dict[str, Any]) -> List[str]:
        """Extract bounded list of key solution steps (names/actions only, no raw payloads)."""
        steps_out: List[str] = []
        raw_steps: List[Any] = []

        if task_obj:
            if hasattr(task_obj, "steps") and getattr(task_obj, "steps"):
                raw_steps = list(getattr(task_obj, "steps"))
            elif isinstance(task_obj, dict) and "steps" in task_obj:
                raw_steps = list(task_obj.get("steps") or [])

        for s in raw_steps:
            name = ""
            status = ""
            if hasattr(s, "name"):
                name = str(getattr(s, "name"))
                st = getattr(s, "status", "")
                status = st.value if hasattr(st, "value") else str(st)
            elif isinstance(s, dict):
                name = str(s.get("name") or s.get("action") or "")
                status = str(s.get("status") or "")

            if name:
                clean_name = _clean_whitespace(name)[:MAX_STEP_DESC_CHARS]
                # Note completion status if relevant
                if "COMPLETED" in status.upper():
                    steps_out.append(f"Completed: {clean_name}")
                elif "FAILED" in status.upper():
                    steps_out.append(f"Failed: {clean_name}")
                else:
                    steps_out.append(clean_name)

            if len(steps_out) >= MAX_SOLUTION_STEPS:
                break

        # Fallback to metadata steps if object steps empty
        if not steps_out and meta.get("solution_steps"):
            for s in meta["solution_steps"][:MAX_SOLUTION_STEPS]:
                steps_out.append(_clean_whitespace(str(s))[:MAX_STEP_DESC_CHARS])

        return _scrub_value_defensive(steps_out)

    def _build_compact_summary(
        self,
        goal: str,
        outcome: EpisodicOutcome,
        task_obj: Any,
        meta: Dict[str, Any],
        failure_class: Optional[str],
        event_hint: Optional[ActionEvent],
    ) -> str:
        """Construct a deterministic 100–300 word summary from execution facts."""
        parts: List[str] = []

        # 1. Header statement
        parts.append(f"Task Objective: {goal}")
        parts.append(f"Final Outcome: {outcome.value}")

        # 2. Result summary from task if available
        task_summary = ""
        if task_obj:
            if hasattr(task_obj, "result_summary") and getattr(task_obj, "result_summary"):
                task_summary = str(getattr(task_obj, "result_summary"))
            elif isinstance(task_obj, dict) and task_obj.get("result_summary"):
                task_summary = str(task_obj["result_summary"])

        if not task_summary and event_hint and event_hint.title:
            task_summary = event_hint.title

        if task_summary:
            parts.append(f"Execution Summary: {_clean_whitespace(task_summary)}")

        # 3. Execution metrics (steps, adaptation, recovery)
        if task_obj:
            total_steps = len(getattr(task_obj, "steps", [])) if hasattr(task_obj, "steps") else 0
            completed_steps = len(getattr(task_obj, "completed_step_ids", [])) if hasattr(task_obj, "completed_step_ids") else 0
            recoveries = getattr(task_obj, "recovery_count", 0) if hasattr(task_obj, "recovery_count") else 0

            if total_steps > 0:
                parts.append(f"Progress: Completed {completed_steps} of {total_steps} plan steps.")
            if recoveries > 0:
                parts.append(f"Recovery: Required {recoveries} automated adaptation/retry attempts.")

        # 4. Failure details if not success
        if outcome != EpisodicOutcome.SUCCESS and failure_class:
            parts.append(f"Failure Classification: {failure_class}")

        raw_summary = " ".join(parts)
        # Strip <think> tags and scrub sensitive tokens
        cleaned = _scrub_value_defensive(raw_summary)
        # Enforce bounds
        truncated = _truncate_words(cleaned, max_words=MAX_SUMMARY_WORDS)
        if len(truncated) > MAX_SUMMARY_CHARS:
            truncated = truncated[:MAX_SUMMARY_CHARS] + "..."

        return truncated

    def _extract_tags(
        self,
        task_obj: Any,
        meta: Dict[str, Any],
        outcome: EpisodicOutcome,
        project_id: Optional[str],
    ) -> List[str]:
        """Extract and synthesize non-redundant tags."""
        tags: Set[str] = set()

        if task_obj:
            if hasattr(task_obj, "safe_metadata") and isinstance(getattr(task_obj, "safe_metadata"), dict):
                for t in getattr(task_obj, "safe_metadata").get("tags", []):
                    tags.add(str(t).lower().strip())
            elif isinstance(task_obj, dict) and "tags" in task_obj:
                for t in task_obj.get("tags") or []:
                    tags.add(str(t).lower().strip())

        if meta.get("tags"):
            for t in meta["tags"]:
                tags.add(str(t).lower().strip())

        # Synthesize standard categorical tags
        tags.add(outcome.value.lower())
        tags.add("episodic_task")
        if project_id:
            tags.add(f"project:{project_id.lower()}")

        clean_tags = [t for t in tags if t and len(t) <= 40][:MAX_TAGS]
        return _scrub_value_defensive(sorted(clean_tags))

    def _extract_retention_class(self, task_obj: Any, meta: Dict[str, Any]) -> RetentionClass:
        """Extract retention class, defaulting to STANDARD."""
        val = ""
        if task_obj:
            if hasattr(task_obj, "safe_metadata") and isinstance(getattr(task_obj, "safe_metadata"), dict):
                val = getattr(task_obj, "safe_metadata").get("retention_class", "")
            elif isinstance(task_obj, dict):
                val = task_obj.get("retention_class", "")

        if not val and meta.get("retention_class"):
            val = meta["retention_class"]

        val_upper = str(val).upper()
        if val_upper in RetentionClass.__members__:
            return RetentionClass[val_upper]
        return RetentionClass.STANDARD


# Singleton indexer instance
episodic_memory_indexer = EpisodicMemoryIndexer()
