# RYVEN 3.0 — Milestone 16.1 Architecture Audit
## Intelligent Task Decomposition & Planning Engine

### 1. Existing Planners
The repository currently contains three distinct planning implementations created across development milestones:
1. **`app.workflows.planner.WorkflowPlanner`** (M4 / M4.1 / M8 / M8.5):
   - Focus: Procedural, regex-driven deterministic workflows (e.g. `workspace_prep`, `create_project`, `modify_project`).
   - Output: `WorkflowDefinition` containing sequential `WorkflowStep` objects.
   - Execution: Handled by `WorkflowEngine` and `WorkflowExecutor`.
2. **`app.agent.planner.AgentPlanner`** (M15.0 / M15.3):
   - Focus: Single-agent goal decomposition for compound instructions (e.g. composite browser workflows, project modification pipelines).
   - Output: `AgentPlan` containing `TaskStep` items assigned to `CapabilityGroup`.
   - Execution: Handled by `AgentOrchestrator` / `AgentEngine`.
3. **`app.agents.planner.TaskDecomposer`** (M16.0):
   - Focus: Multi-agent DAG construction assigning specialized roles (`RESEARCH`, `DEVELOPER`, `COMPUTER`, `BROWSER`, `WINDOWS`, `VERIFICATION`).
   - Output: `AgentTaskGraph` containing `AgentTask` nodes with deterministic dependency tracking (`depends_on`).
   - Execution: Orchestrated by the central `AgentCoordinator`.

---

### 2. Existing Routers
1. **`app.core.router.IntentRouter`**:
   - Classifies raw chat input into `tool`, `workflow`, `agent`, or `ai`.
   - Uses compiled regex patterns for instant, sub-millisecond dispatch of basic commands (`time`, `system_status`, `open_application`, `open_folder`).
2. **`app.agent.capability_router.CapabilityRouter`**:
   - Classifies user goals into coarse capability groups (`SYSTEM`, `APP`, `FILE`, `INTERNET`, `PROJECT`, `GIT`, `DEPLOY`, `GRAPH`, `HEALTH`).
3. **`app.ai.router.ModelRouter`**:
   - Directs cognitive subtasks (`GENERAL_REASONING`, `PLANNING`, `CODE`, `VISION`, `OCR`) to the optimal local or remote model profile (defaulting to local `qwen2.5:7b` via Ollama).

---

### 3. Existing Decomposition Logic
Currently in `app.agents.planner.TaskDecomposer`:
- Simple heuristics check for single actions:
  - `_is_simple_web_search()` $\rightarrow$ 1 task with `AgentCapability.WEB_SEARCH`
  - `_is_simple_app_launch()` $\rightarrow$ 1 task with `AgentCapability.WINDOWS_APP`
  - `_is_simple_browser_nav()` $\rightarrow$ 1 task with `AgentCapability.BROWSER_ACTION`
  - `_is_simple_health_check()` $\rightarrow$ 1 task with `AgentCapability.HEALTH_CHECK`
- Complex heuristics construct static DAG templates:
  - Research + Dev: `WEB_RESEARCH` $\rightarrow$ `CODE_GENERATION` $\rightarrow$ `CODE_MODIFICATION` $\rightarrow$ `BUILD` $\rightarrow$ `TEST` $\rightarrow$ `QUALITY_GATE` $\rightarrow$ `REPORT`.
  - Diagnostics: `PROJECT_SCAN` $\rightarrow$ `BUILD` $\rightarrow$ `KNOWLEDGE_GRAPH` $\rightarrow$ `REPORT`.
  - Dev Pipeline: `PROJECT_SCAN` $\rightarrow$ `CODE_MODIFICATION` $\rightarrow$ `BUILD` $\rightarrow$ `TEST` $\rightarrow$ `QUALITY_GATE` $\rightarrow$ `GIT` (confirm) $\rightarrow$ `DEPLOY` (confirm) $\rightarrow$ `HEALTH_CHECK`.

**Limitation**: M16.0 decomposition is rigid. It lacks:
- Normalized understanding of messy or abbreviated user phrasing.
- Explicit goal complexity classification (`SIMPLE`, `MODERATE`, `COMPLEX`).
- Dynamic parallelism detection for independent sibling tasks.
- Pre-execution risk classification and confirmation prediction.
- LLM-assisted structured planning for arbitrary complex user goals.
- Deterministic plan validation with bounded repair.

---

### 4. Existing Task Graph (`AgentTaskGraph`)
- Directed Acyclic Graph (DAG) with Kahn's algorithm cycle detection (`validate_graph()`).
- Tracks tasks by ID, dependencies, ready tasks (`get_ready_tasks()`), completed, blocked, and failed tasks.
- Fully supports cascading status updates (failure propagation, atomic cancellation).
- Serializes cleanly to dicts and SQLite checkpoints.
- **Reusability**: 100% reusable; M16.1 will construct validated `AgentTaskGraph` instances as its final output.

---

### 5. Existing Agent Assignment
- `app.agents.capabilities`: Maps `AgentCapability` $\leftrightarrow$ tool names $\leftrightarrow$ `AgentRole`.
- `app.agents.registry.AgentRegistry`: Tracks 8 roles (`COORDINATOR`, `RESEARCH`, `DEVELOPER`, `COMPUTER`, `BROWSER`, `WINDOWS`, `MEMORY`, `VERIFICATION`).
- Assignment validates capability ownership, tool registry presence, and permission guard status.

---

### 6. Existing Context Handling
- `app.runtime.context_budget.ContextBudgetManager`:
  - Enforces priority-based token budgeting (priorities 1 to 6).
  - Trims oversized prompts while preserving user intent, security constraints, and critical task state.
- `app.agents.communication.CommunicationManager`:
  - Isolates context across worker roles via bounded `AgentHandoff` and `AgentMessage`.

---

### 7. Reusable Components
- `AgentCoordinator`: Execution engine for the generated task graph.
- `AgentTaskGraph`: Directed acyclic graph representation.
- `AgentRegistry`: Worker role definitions and capability boundaries.
- `ToolRegistry`: Shared inventory of 88 production tools.
- `ConfirmationManager`: Enforces user approval on destructive/consequential actions.
- `UnifiedAIProvider`: Multi-model provider with fallback to local Qwen 2.5 7B.
- `ModelRouter`: Cognitive routing for planning tasks.
- `ContextBudgetManager`: Token budget optimization for LLM prompts.
- `ConcurrencyController`: Enforces concurrency semaphores (LLM $\le 2$, Build $\le 1$, Browser $\le 2$).
- `ActionEventBus`: Timeline telemetry distribution.

---

### 8. Required Modifications
1. **`app.actions.models.ActionType`**:
   - Add planning event types: `PLAN_CREATED`, `PLAN_VALIDATION_STARTED`, `PLAN_VALIDATED`, `PLAN_INVALID`, `PLAN_REPAIR_STARTED`, `PLAN_REPAIRED`, `PLAN_REPAIR_FAILED`, `PLAN_EXECUTION_READY`, `PLAN_REVISED`.
2. **`app.agents.models`**:
   - Add planning data models: `PlanningMode`, `GoalComplexity`, `PlanRisk`, `PlanStatus`, `PlanningRequest`, `PlanningResult`, `PlanValidationResult`.
3. **`app.agents.planner`**:
   - Upgrade `TaskDecomposer` to use the intelligent planning pipeline.
4. **`app.agents.coordinator`**:
   - Integrate planning engine as the primary decomposition & preflight validation frontend.
5. **`app.api.routes`**:
   - Add `/api/planning/*` preview, validate, and execute endpoints.
6. **`RAVAN/src/services/agents.ts` & `SwarmPanel.tsx`**:
   - Add plan preview and complexity inspection to the Swarm HUD.

---

### 9. New Files Required
1. `backend/app/agents/planning_models.py`: Dedicated models for planning, complexity, risk, validation, and repair.
2. `backend/app/agents/normalizer.py`: Deterministic goal normalizer preserving intent and parameters.
3. `backend/app/agents/complexity.py`: Deterministic and cognitive complexity classifier (`SIMPLE`, `MODERATE`, `COMPLEX`).
4. `backend/app/agents/intent.py`: Structured intent, entity, and capability extraction.
5. `backend/app/agents/llm_planner.py`: Structured Qwen/Ollama planning for complex goals with strict JSON schema parsing.
6. `backend/app/agents/validator.py`: Plan validator (15 safety checks) and bounded repair engine ($\le 3$ attempts).
7. `backend/app/agents/planning_engine.py`: Central facade coordinating the entire M16.1 planning pipeline.
8. `backend/tests/test_m16_1_planning.py`: 60+ comprehensive tests.
9. `backend/scripts/verify_m16_1_live.py`: Safe live verification script.

---

### 10. Potential Conflicts & Architectural Mitigations
- **Conflict**: Calling Qwen for simple commands slows down common tasks.
  - **Mitigation**: Deterministic complexity classification categorizes simple commands as `SIMPLE` and resolves them in $< 15\text{ms}$ with zero LLM calls.
- **Conflict**: Untrusted LLM output introducing forbidden shell execution, unknown tools, or invalid dependencies.
  - **Mitigation**: LLM is treated as an UNTRUSTED generator. All LLM output passes through `PlanValidator` and `SecurityPolicy`. Unknown tools or forbidden shell actions are rejected or repaired.
- **Conflict**: Infinite planning repair loops.
  - **Mitigation**: Hard limit of 3 repair iterations. If repair fails, the plan status is marked `INVALID` and returned with structured diagnostic errors.

---

### 11. Security Boundaries
- **No Arbitrary Shell**: `cmd.exe`, `powershell`, `bash`, and raw OS execution tools remain strictly forbidden.
- **ToolRegistry Boundary**: Only tools in `ToolRegistry` can be scheduled.
- **Confirmation Boundary**: Consequential actions (`git_commit`, `git_push`, `deploy`, `form_submit`, destructive modification) MUST be flagged as requiring confirmation; the planner cannot downgrade their risk.
- **Secret Scrubbing**: All user input, LLM output, and event metadata pass through `redact_secrets()`.

---

### 12. Test Strategy
1. **Goal Normalization & Extraction**: Test whitespace cleanup, entity extraction, parameter preservation, missing info detection.
2. **Complexity Classification**: Test deterministic recognition of SIMPLE vs MODERATE vs COMPLEX.
3. **Intent & Capability Mapping**: Test mapping of natural language verbs to `AgentCapability` and tools.
4. **Dependency & Parallelism**: Test dependency inference, diamond DAGs, independent sibling parallel detection.
5. **Risk & Confirmation Prediction**: Test risk rating and confirmation point detection.
6. **LLM Planning**: Test schema parsing, JSON extraction, fallback to deterministic templates on LLM error.
7. **Plan Validation**: Test all 15 validator rules (invalid IDs, unknown tools, cycles, excessive tasks).
8. **Plan Repair**: Test bounded topological repair and cycle breaking up to 3 attempts.
9. **Coordinator Integration**: Test end-to-end execution of validated plans via `AgentCoordinator`.
10. **ActionEvents**: Test emission and order of `PLAN_*` events on `action_bus`.
11. **Regression**: Full suite execution (`972+` tests PASS).
