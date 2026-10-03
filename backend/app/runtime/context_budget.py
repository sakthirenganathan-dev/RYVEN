"""RYVEN 3.0 — AI Context & Token Budget Manager (M15.3.9).

Enforces prompt context budgeting and priority-based pruning to avoid oversized prompts,
reduce model latency, and prevent token exhaustion.

Preserves critical task state, user intent, security constraints, and tool schemas.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.actions.event_bus import action_bus
from app.actions.models import ActionEvent, ActionStatus, ActionType
from app.core.logging_config import logger
from app.runtime.models import ContextBudgetReport


# Priority Levels (1 = highest priority, must be retained; 6 = lowest priority, trimmed first)
PRIORITY_USER_INTENT = 1
PRIORITY_TASK_STATE_AND_SECURITY = 2
PRIORITY_RELEVANT_FILES = 3
PRIORITY_TOOL_RESULTS = 4
PRIORITY_PROJECT_KNOWLEDGE = 5
PRIORITY_HISTORY_CONTEXT = 6

CATEGORY_NAMES = {
    PRIORITY_USER_INTENT: "user_intent",
    PRIORITY_TASK_STATE_AND_SECURITY: "task_state_and_security",
    PRIORITY_RELEVANT_FILES: "relevant_files",
    PRIORITY_TOOL_RESULTS: "tool_results",
    PRIORITY_PROJECT_KNOWLEDGE: "project_knowledge",
    PRIORITY_HISTORY_CONTEXT: "conversation_history",
}


class ContextBudgetManager:
    """Calculates, enforces, and prunes prompt context within token limits."""

    DEFAULT_BUDGET_TOKENS = 4096

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count using the standard RYVEN 4 chars-per-token rule."""
        if not text:
            return 0
        return max(1, len(text) // 4)

    @classmethod
    def optimize_context(
        cls,
        task_goal: str,
        user_intent: str,
        task_state_and_security: str,
        relevant_files: Dict[str, str],
        tool_results: Dict[str, Any],
        project_knowledge: Optional[str] = None,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    ) -> Tuple[Dict[str, Any], ContextBudgetReport]:
        """Prune and construct context strictly adhering to priority order.
        
        Returns:
            (optimized_context_dict, report)
        """
        # 1. Format individual sections
        sections: Dict[int, str] = {}
        sections[PRIORITY_USER_INTENT] = user_intent or task_goal or ""
        sections[PRIORITY_TASK_STATE_AND_SECURITY] = task_state_and_security or ""

        # Format files
        file_texts = [f"--- File: {k} ---\n{v}" for k, v in relevant_files.items()]
        sections[PRIORITY_RELEVANT_FILES] = "\n\n".join(file_texts)

        # Format tool results
        tool_texts = [f"Tool {k}: {v}" for k, v in tool_results.items()]
        sections[PRIORITY_TOOL_RESULTS] = "\n".join(tool_texts)

        # Format project knowledge
        sections[PRIORITY_PROJECT_KNOWLEDGE] = project_knowledge or ""

        # Format history
        hist_texts = []
        if conversation_history:
            for msg in conversation_history:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                hist_texts.append(f"{role.upper()}: {content}")
        sections[PRIORITY_HISTORY_CONTEXT] = "\n".join(hist_texts)

        # 2. Measure baseline
        baseline_text = "\n\n".join([v for v in sections.values() if v])
        baseline_chars = len(baseline_text)
        baseline_tokens = cls.estimate_tokens(baseline_text)

        # 3. If within budget, retain everything
        if baseline_tokens <= max_budget_tokens:
            report = ContextBudgetReport(
                task_goal=task_goal,
                estimated_input_chars=baseline_chars,
                estimated_tokens=baseline_tokens,
                max_context_budget_tokens=max_budget_tokens,
                selected_context_tokens=baseline_tokens,
                optimization_ratio=0.0,
                omitted_categories=[],
                retained_categories=[CATEGORY_NAMES[p] for p in sorted(sections.keys()) if sections[p]],
                security_invariants_preserved=True,
            )
            return (
                {
                    "user_intent": sections[PRIORITY_USER_INTENT],
                    "task_state": sections[PRIORITY_TASK_STATE_AND_SECURITY],
                    "files": relevant_files,
                    "tool_results": tool_results,
                    "knowledge": project_knowledge or "",
                    "history": conversation_history or [],
                },
                report,
            )

        # 4. Pruning from lowest priority (6) upwards to (3)
        # Invariants: PRIORITY_USER_INTENT and PRIORITY_TASK_STATE_AND_SECURITY are NEVER omitted.
        omitted: List[str] = []
        retained: List[str] = []
        optimized_sections = dict(sections)

        for p in (PRIORITY_HISTORY_CONTEXT, PRIORITY_PROJECT_KNOWLEDGE, PRIORITY_TOOL_RESULTS, PRIORITY_RELEVANT_FILES):
            curr_text = "\n\n".join([v for v in optimized_sections.values() if v])
            curr_tokens = cls.estimate_tokens(curr_text)
            if curr_tokens <= max_budget_tokens:
                break

            # Need to trim priority p
            cat_name = CATEGORY_NAMES[p]
            omitted.append(cat_name)
            optimized_sections[p] = ""

        # Check final tokens after pruning
        final_text = "\n\n".join([v for v in optimized_sections.values() if v])
        final_tokens = cls.estimate_tokens(final_text)

        retained = [CATEGORY_NAMES[p] for p in sorted(optimized_sections.keys()) if optimized_sections[p]]

        reduction_tokens = max(0, baseline_tokens - final_tokens)
        ratio = round(reduction_tokens / baseline_tokens, 4) if baseline_tokens > 0 else 0.0

        report = ContextBudgetReport(
            task_goal=task_goal,
            estimated_input_chars=len(final_text),
            estimated_tokens=final_tokens,
            max_context_budget_tokens=max_budget_tokens,
            selected_context_tokens=final_tokens,
            optimization_ratio=ratio,
            omitted_categories=omitted,
            retained_categories=retained,
            security_invariants_preserved=True,
        )

        # Emit optimization event
        try:
            action_bus.emit(
                ActionEvent(
                    action_type=ActionType.RUNTIME_CONTEXT_OPTIMIZED,
                    status=ActionStatus.COMPLETED,
                    title=f"Context Optimized: {ratio * 100:.1f}% reduction",
                    description=f"Pruned {len(omitted)} categories to satisfy {max_budget_tokens} token budget.",
                    safe_metadata={
                        "baseline_tokens": baseline_tokens,
                        "optimized_tokens": final_tokens,
                        "omitted_categories": omitted,
                        "optimization_ratio": ratio,
                    },
                )
            )
        except Exception as exc:
            logger.debug(f"[CONTEXT_BUDGET] Failed to emit optimization event: {exc}")

        return (
            {
                "user_intent": optimized_sections[PRIORITY_USER_INTENT],
                "task_state": optimized_sections[PRIORITY_TASK_STATE_AND_SECURITY],
                "files": relevant_files if PRIORITY_RELEVANT_FILES not in [6, 5, 4, 3] or CATEGORY_NAMES[PRIORITY_RELEVANT_FILES] not in omitted else {},
                "tool_results": tool_results if CATEGORY_NAMES[PRIORITY_TOOL_RESULTS] not in omitted else {},
                "knowledge": project_knowledge if CATEGORY_NAMES[PRIORITY_PROJECT_KNOWLEDGE] not in omitted else "",
                "history": conversation_history if CATEGORY_NAMES[PRIORITY_HISTORY_CONTEXT] not in omitted else [],
            },
            report,
        )
