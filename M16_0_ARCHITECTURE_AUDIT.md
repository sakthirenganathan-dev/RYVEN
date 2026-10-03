# RYVEN 3.0 — M16.0 Multi-Agent Coordination Foundation
## Architecture Audit & Implementation Plan

**Milestone**: M16.0 — Multi-Agent Coordination Foundation  
**System**: RYVEN 3.0 Local-First Personal AI Control Plane  
**Status**: IN PROGRESS — PHASE 0 ARCHITECTURE AUDIT  
**Baseline Verified**: M15.3.10 (900/900 tests passing, 0 regressions)

---

## 1. Executive Summary

RYVEN 3.0 is evolving from a single-agent orchestrator into a **controlled multi-agent coordination system**. 
In strict alignment with the core non-negotiable architectural rule:
- There is **ONE** central RYVEN Control Plane (`AgentCoordinator`).
- Specialized agents are **role-based execution workers** operating strictly under the central control plane.
- There are **NO independent mini-RYVEN systems**: no separate tool registries, no separate orchestrator engines, no separate security or confirmation managers, no separate checkpoint stores, and no independent AI provider instances.
- All specialized roles share the single, battle-tested core infrastructure.

---

## 2. Audit of Existing Reusable Subsystems

| Subsystem | Location | Existing Capabilities & Reusability |
| :--- | :--- | :--- |
| **Tool Registry** | `backend/app/tools/registry.py` | Centralized registry of 88 registered tools. Strongly typed argument schemas, automatic action event instrumentation, and strict execution isolation. Tools remain the single execution boundary. |
| **Orchestrator Engine** | `backend/app/orchestrator/engine.py` | Step-by-step deterministic orchestration with state tracking, error handling, and recovery hook points. |
| **Dependency Graph / DAG** | `backend/app/orchestrator/dependency.py` | Kahn's algorithm DAG validator, cycle detection, topological sort, and ready step resolution. Reusable directly for `AgentTaskGraph`. |
| **Confirmation Manager** | `backend/app/workflows/confirmation.py` | Evaluates safe vs. consequential actions (`git_commit`, `git_push`, `deploy`, file mutations). Enforces pause for user confirmation. |
| **Action Event Bus & Tracker** | `backend/app/actions/event_bus.py`, `action_tracker.py` | Async in-process bounded ring buffer (1000 events max). Automatic recursion-safe secret redaction (`_redact_dict`). Event streams over SSE. |
| **Runtime & Reliability** | `backend/app/runtime/` | Singleton managers: `ResourceManager` (CPU/RAM telemetry & pressure gating), `RuntimePerformanceService` (latencies & metrics), `CheckpointStore` (SQLite persistence), `RuntimeRecoveryService` (safe auto-resume & confirmation gates), `DeadlineManager` (time-to-live timeouts), `ConcurrencyController` (bounded semaphores), `ResourceLifecycleManager` (resource cleanup), `ContextBudgetManager` (token budget control). |
| **AI Provider & Model Router** | `backend/app/ai/` | Local-first Qwen 2.5 7B via Ollama. Strict enforcement that remote inference remains disabled unless explicitly configured. |
| **Internet & Browser** | `backend/app/internet/`, `backend/app/browser/` | Read-only web search, page reading, browser session automation with SSRF protection and domain boundary validation. |
| **Developer HUD** | `RAVAN/src/components/developer/DeveloperHUD.tsx` | Front-end HUD with tabs for `projects`, `activity`, `browser`, `agent`, `models`, `runtime`. Can be cleanly extended with a dedicated `SWARM` / `AGENTS` coordination tab. |

---

## 3. Architecture Blueprint: Controlled Multi-Agent Coordination

```
                                  RYVEN CONTROL PLANE
                                           │
                                  ┌─────────────────┐
                                  │AgentCoordinator │
                                  └────────┬────────┘
                                           │
                                  ┌────────▼────────┐
                                  │ TaskDecomposer  │
                                  └────────┬────────┘
                                           │
                                  ┌────────▼────────┐
                                  │ AgentTaskGraph  │ (DAG Engine)
                                  └────────┬────────┘
                                           │
                  ┌────────────────────────┼────────────────────────┐
                  │                        │                        │
         ┌────────▼────────┐      ┌────────▼────────┐      ┌────────▼────────┐
         │  Research Role  │      │ Developer Role  │      │  Computer Role  │
         │ (Web / Sources) │      │ (Code / Build)  │      │ (Windows/Files) │
         └────────┬────────┘      └────────┬────────┘      └────────┬────────┘
                  │                        │                        │
                  └────────────────────────┼────────────────────────┘
                                           │
                                     SHARED CORE
                                           │
            ┌──────────────────────────────┼──────────────────────────────┐
            │                              │                              │
    ┌───────▼────────┐             ┌───────▼────────┐             ┌───────▼────────┐
    │  ToolRegistry  │             │ Orchestrator   │             │    Runtime     │
    │   (88 Tools)   │             │     Engine     │             │  (Reliability) │
    └───────┬────────┘             └───────┬────────┘             └───────┬────────┘
            │                              │                              │
    ┌───────▼────────┐             ┌───────▼────────┐             ┌───────▼────────┐
    │  Confirmation  │             │  ActionEvents  │             │  Checkpoints   │
    │    Manager     │             │     & SSE      │             │  (SQLite DB)   │
    └───────┬────────┘             └───────┬────────┘             └───────┬────────┘
            │                              │                              │
            └──────────────────────────────┼──────────────────────────────┘
                                           │
                                  ┌────────▼────────┐
                                  │UnifiedAIProvider│
                                  └────────┬────────┘
                                           │
                                  ┌────────▼────────┐
                                  │   ModelRouter   │
                                  └────────┬────────┘
                                           │
                                  ┌────────▼────────┐
                                  │ Local Qwen 2.5  │
                                  └─────────────────┘
```

---

## 4. Required Extensions & Component Breakdown

### A. New Module: `backend/app/agents/`
To maintain complete modularity and prevent mixing with the legacy M8.5 single-agent package (`backend/app/agent/`), we create `backend/app/agents/`:

1. **`models.py`**:
   - `AgentRole` enum: `COORDINATOR`, `RESEARCH`, `DEVELOPER`, `COMPUTER`, `BROWSER`, `WINDOWS`, `MEMORY`, `VERIFICATION`.
   - `AgentStatus` enum: `CREATED`, `READY`, `PLANNING`, `WAITING`, `RUNNING`, `BLOCKED`, `REQUIRES_CONFIRMATION`, `COMPLETED`, `FAILED`, `CANCELLED`, `RECOVERING`.
   - `AgentCapability` enum: Mapped strictly to existing `ToolRegistry` tool categories (e.g., `WEB_SEARCH`, `WEB_RESEARCH`, `PAGE_READING`, `PROJECT_SCAN`, `CODE_GENERATION`, `CODE_MODIFICATION`, `BUILD`, `TEST`, `GIT`, `DEPLOY`, `HEALTH_CHECK`, `KNOWLEDGE_GRAPH`, `VISION`, `OCR`, `SYSTEM_STATUS`, `CLIPBOARD`, `WINDOWS_APP`, `WINDOWS_FILES`).
   - `AgentTask`: Task definition containing `task_id`, `parent_task_id`, `graph_id`, `role`, `capability`, `objective`, `input_context`, `dependencies`, `status`, `result`, `error`, `retry_count`, `checkpoint_id`, `requires_confirmation`, `confirmation_type`.
   - `AgentMessage`: Structured inter-agent communication (`TASK_REQUEST`, `TASK_RESULT`, `FINDING`, `ARTIFACT`, `STATUS`, `BLOCKED`, `HANDOFF`, `VERIFICATION`, `ERROR`, `CONFIRMATION_REQUIRED`).
   - `AgentContext`: Budgeted, structured context payload avoiding global conversation transcript pollution.
   - `AgentHandoff`: Explicit structured handoff payload between specialized roles.
   - Spawn limits configuration constants (`MAX_ACTIVE_AGENTS`, `MAX_TASKS_PER_GRAPH`, `MAX_AGENT_DEPTH`, `MAX_HANDOFFS`, `MAX_RETRIES`, `MAX_GRAPH_RUNTIME_SEC`).

2. **`capabilities.py`**:
   - Explicit capability mappings linking `AgentCapability` to registered `ToolRegistry` tool names.
   - Validation logic verifying that tools exist in `ToolRegistry`.

3. **`registry.py`**:
   - `AgentRegistry`: Singleton registry of specialized agent roles, their declared capabilities, and current status.
   - Registration validation preventing duplicate or conflicting agent roles.

4. **`task_graph.py`**:
   - `AgentTaskGraph`: Directed Acyclic Graph (DAG) for agent tasks.
   - Reuses Kahn's algorithm for cycle detection, topological ordering, and `get_ready_tasks()`.
   - Supports task completion propagation, failure cascade blocking, and cancellation.

5. **`planner.py` (TaskDecomposer)**:
   - Evaluates natural language user goal.
   - Emits structured `AgentTaskGraph`.
   - Implements rule: Simple goals (e.g. "Open Chrome", "Search for X") yield **1 task**, not complex graphs.
   - Multi-step requests (e.g. "Research X, build demo, test it, deploy it") decompose into sequential/parallel dependency DAGs.

6. **`security.py`**:
   - Security validator verifying capability ownership, tool registry presence, path/SSRF validation, confirmation requirements, and secret redaction.
   - Strict prohibition of arbitrary shell execution.

7. **`communication.py`**:
   - Handoff validation, structured message serialization, secret scrubbing, and depth limits.

8. **`coordinator.py`**:
   - `AgentCoordinator`: Central engine coordinating decomposition, DAG validation, task assignment, concurrency-controlled parallel execution, observation, checkpointing, cancellation, and final reporting.

9. **`lifecycle.py`**:
   - Agent execution lifecycle states and transitions.

10. **`__init__.py`**:
    - Centralized clean exports for all agent models and services.

---

### B. Modifications to Existing Files

1. **`backend/app/actions/models.py`**:
   - Add new `ActionType` enum values for agent coordination:
     - `AGENT_CREATED`
     - `AGENT_READY`
     - `AGENT_STARTED`
     - `AGENT_TASK_CREATED`
     - `AGENT_TASK_ASSIGNED`
     - `AGENT_TASK_STARTED`
     - `AGENT_TASK_COMPLETED`
     - `AGENT_TASK_FAILED`
     - `AGENT_TASK_BLOCKED`
     - `AGENT_HANDOFF`
     - `AGENT_MESSAGE`
     - `AGENT_RETRY`
     - `AGENT_CANCELLED`
     - `AGENT_RECOVERED`
     - `AGENT_CONFIRMATION_REQUIRED`
     - `AGENT_GRAPH_STARTED`
     - `AGENT_GRAPH_COMPLETED`
     - `AGENT_GRAPH_FAILED`

2. **`backend/app/api/routes.py`**:
   - Add `/api/agents/...` endpoints:
     - `POST /api/agents/task`: Submit a high-level goal to the Agent Coordinator.
     - `GET /api/agents`: List all registered agent roles and their capabilities.
     - `GET /api/agents/{agent_id}`: Retrieve agent status.
     - `GET /api/agents/tasks/{task_id}`: Retrieve agent task details.
     - `GET /api/agents/graph/{graph_id}`: Retrieve active task graph structure and progression.
     - `POST /api/agents/tasks/{task_id}/cancel`: Cancel task graph.
     - `POST /api/agents/tasks/{task_id}/confirm`: Provide confirmation for gated action.
     - `GET /api/agents/events`: Stream recent agent action events.

3. **Frontend Extension**:
   - Create `RAVAN/src/components/developer/SwarmPanel.tsx` (Agent Coordination HUD).
   - Update `RAVAN/src/components/developer/DeveloperHUD.tsx` to add the `SWARM` view button.

---

## 5. Security Invariants & Boundaries

1. **ToolRegistry as Sole Execution Boundary**: Specialized agents do not have direct system access; all actions execute through `ToolRegistry.execute_tool()`.
2. **Confirmation Boundary**: Consequential actions (`GIT_COMMIT`, `GIT_PUSH`, `DEPLOY`, file overwrites) pause in `REQUIRES_CONFIRMATION` and are NEVER auto-resumed without explicit user confirmation.
3. **No Arbitrary Shell**: Shell execution is strictly prohibited.
4. **Secret Redaction**: All inter-agent messages, context budgets, task parameters, and action events are sanitized via `_redact_dict` / regex scrubbing.
5. **Local-First Invariant**: Default AI routing remains local Qwen 2.5 7B via Ollama. Remote inference is blocked.
6. **Bounded Concurrency & Spawn Ceilings**:
   - `MAX_ACTIVE_AGENTS = 5`
   - `MAX_TASKS_PER_GRAPH = 20`
   - `MAX_AGENT_DEPTH = 3`
   - `MAX_HANDOFFS = 5`
   - `MAX_RETRIES = 2`
   - `MAX_GRAPH_RUNTIME_SEC = 300.0`
   - ConcurrencyController semaphores enforced: LLM (configured), Build (1), Browser (configured), Knowledge Graph (1).

---

## 6. Migration & Backward Compatibility Strategy

- All 900 existing backend tests must pass unchanged.
- All existing endpoints (`/api/chat`, `/api/health`, `/api/orchestrator/*`, `/api/runtime/*`) remain 100% backward compatible.
- The single-agent fallback (`backend/app/agent/`) remains untouched to avoid breaking legacy unit tests.
- Multi-agent coordination (`backend/app/agents/`) will serve as the higher-level foundation.

Audit Phase 0 is complete. Proceeding to implementation.
