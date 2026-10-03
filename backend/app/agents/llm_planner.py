"""RYVEN 3.0 — Milestone 16.1 LLM-Assisted Planner.

Provides structured Qwen 2.5 7B goal decomposition for open-ended or composite workflows.
Treats all model output as untrusted and parses into validated PlanningTaskDraft items.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from app.agents.models import AgentCapability, AgentRole, redact_secrets
from app.agents.planning_models import PlanRisk, PlanningTaskDraft
from app.ai.models import TaskType
from app.ai.unified import UnifiedAIProvider, unified_ai_provider
from app.core.logging_config import logger
from app.runtime.context_budget import ContextBudgetManager


PLANNING_SYSTEM_PROMPT = """You are the RYVEN 3.0 Central Planning Engine.
Your task is to decompose a complex user request into a minimal, ordered sequence of execution tasks.

Rules:
1. ONLY use valid capabilities from: WEB_SEARCH, WEB_RESEARCH, PAGE_READING, BROWSER_ACTION, WINDOWS_APP, WINDOWS_FILES, CLIPBOARD, SYSTEM_STATUS, PROJECT_SCAN, CODE_GENERATION, CODE_MODIFICATION, BUILD, TEST, GIT, DEPLOY, HEALTH_CHECK.
2. Assign each task to the appropriate role: RESEARCH, DEVELOPER, COMPUTER, BROWSER, WINDOWS, VERIFICATION, or COORDINATOR.
3. Express task dependencies using previous task IDs in 'depends_on'. NEVER introduce cycles.
4. Output MUST BE valid JSON adhering strictly to this schema:
{
  "tasks": [
    {
      "id": "step_1",
      "objective": "Clear description of action",
      "capability": "VALID_CAPABILITY",
      "preferred_role": "VALID_ROLE",
      "tool_name": "registered_tool_name_or_null",
      "arguments": {},
      "depends_on": []
    }
  ]
}
Do not include any conversational preamble or markdown formatting around the JSON. Output raw JSON only."""


class LLMPlanner:
    """Invokes local-first LLM (Qwen 2.5 7B) to decompose complex workflows."""

    def __init__(self, ai_provider: Optional[UnifiedAIProvider] = None) -> None:
        self.ai_provider = ai_provider or unified_ai_provider
        self.ai = self.ai_provider

    async def plan(self, request: PlanningRequest) -> List[PlanningTaskDraft]:
        """Convenience method accepting PlanningRequest with deterministic fallback."""
        tasks = await self.plan_goal(
            normalized_goal=request.user_goal,
            context=request.context,
            project_name=request.project_name,
        )
        if not tasks:
            from app.agents.normalizer import GoalNormalizer
            from app.agents.intent import IntentAnalyzer
            from app.agents.planning_engine import planning_engine
            normalized, params, _ = GoalNormalizer.normalize(request.user_goal)
            intent = IntentAnalyzer.analyze(normalized, params)
            return planning_engine._plan_deterministic(normalized, intent, params)
        return tasks

    async def plan_goal(
        self,
        normalized_goal: str,
        context: Optional[Dict[str, Any]] = None,
        project_name: Optional[str] = None,
    ) -> List[PlanningTaskDraft]:
        """Call LLM provider and parse structured task plan."""
        logger.info(f"[LLM_PLANNER] Generating cognitive plan for: {normalized_goal!r}")

        ctx_summary = ""
        if context:
            ctx_summary = f"\nContext: {json.dumps(redact_secrets(context))}"

        prompt = f"Decompose this goal into executable tasks:\nGoal: {normalized_goal}{ctx_summary}"
        if project_name:
            prompt += f"\nTarget Project Name: {project_name}"

        try:
            if hasattr(self.ai, "chat") and callable(getattr(self.ai, "chat")):
                res = await self.ai.chat(prompt=prompt, system_prompt=PLANNING_SYSTEM_PROMPT)
                raw_text = res if isinstance(res, str) else getattr(res, "content", str(res))
            else:
                response = await self.ai_provider.generate(
                    prompt=prompt,
                    system_prompt=PLANNING_SYSTEM_PROMPT,
                    task_type=TaskType.PLANNING,
                )
                raw_text = response.content.strip()
            return self._parse_llm_json(raw_text, normalized_goal)
        except Exception as exc:
            logger.warning(f"[LLM_PLANNER] LLM generation failed or timed out: {exc}. Falling back to deterministic plan.")
            return []


    def _parse_llm_json(self, raw_text: str, default_goal: str) -> List[PlanningTaskDraft]:
        """Safely extract and parse JSON task list from model response."""
        # Find JSON block
        json_match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", raw_text)
        if not json_match:
            logger.warning("[LLM_PLANNER] No JSON structure located in LLM response.")
            return []

        payload_str = json_match.group(1)
        try:
            data = json.loads(payload_str)
        except json.JSONDecodeError as err:
            logger.warning(f"[LLM_PLANNER] JSON decode error: {err}")
            return []

        raw_tasks = data.get("tasks", []) if isinstance(data, dict) else data
        if not isinstance(raw_tasks, list):
            return []

        drafts: List[PlanningTaskDraft] = []
        id_map: Dict[str, str] = {}  # Map model's step_1 to UUID ptask-...

        # 1. Map IDs
        for i, item in enumerate(raw_tasks):
            if not isinstance(item, dict):
                continue
            orig_id = str(item.get("id") or f"step_{i+1}")
            clean_id = orig_id if re.match(r"^[a-zA-Z0-9_\-]+$", orig_id) else f"ptask-{uuid.uuid4().hex[:8]}"
            id_map[orig_id] = clean_id

        from app.agents.security import FORBIDDEN_SHELL_TOOLS

        # 2. Build PlanningTaskDraft items
        for i, item in enumerate(raw_tasks):
            if not isinstance(item, dict):
                continue

            orig_id = str(item.get("id") or f"step_{i+1}")
            assigned_id = id_map[orig_id]
            title = str(item.get("title") or item.get("objective") or default_goal)
            objective = str(item.get("objective") or default_goal)
            cap_str = str(item.get("capability") or "TASK_COORDINATION").upper()
            role_str = str(item.get("preferred_role") or item.get("role") or "COORDINATOR").upper()

            # Validate capability against enum
            try:
                capability = AgentCapability[cap_str]
            except KeyError:
                capability = AgentCapability.TASK_COORDINATION

            # Validate role against enum
            try:
                preferred_role = AgentRole[role_str]
            except KeyError:
                preferred_role = AgentRole.COORDINATOR

            tool_name = item.get("tool_name")
            tools = list(item.get("tools") or [])
            if tool_name and tool_name not in tools:
                tools.append(tool_name)
            # Filter forbidden tools
            tools = [t for t in tools if t.lower() not in FORBIDDEN_SHELL_TOOLS]
            if tool_name and tool_name.lower() in FORBIDDEN_SHELL_TOOLS:
                tool_name = None
            if not tool_name and tools:
                tool_name = tools[0]

            arguments = item.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}

            # Map dependencies
            raw_deps = item.get("depends_on") or item.get("dependencies") or []
            if not isinstance(raw_deps, list):
                raw_deps = []
            mapped_deps = [id_map.get(d, d) for d in raw_deps if id_map.get(d, d) != assigned_id]

            drafts.append(
                PlanningTaskDraft(
                    task_id=assigned_id,
                    title=title,
                    objective=objective,
                    capability=capability,
                    preferred_role=preferred_role,
                    tool_name=tool_name,
                    tools=tools,
                    arguments=redact_secrets(arguments),
                    depends_on=mapped_deps,
                )
            )

        return drafts


llm_planner = LLMPlanner()

