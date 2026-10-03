# RYVEN 3.0 Personal Assistant Unification — Agent Plan

Status: Frozen  
Specification approval reference: PRD, TECH_DESIGN, and ACCEPTANCE approved by user on 2026-10-03  
Shared contracts frozen at: 2026-10-03 draft design  
Plan owner: Main agent

## Frozen Shared Contracts

| Contract/type/schema | Location | Owner | Change approval rule |
|---|---|---|---|
| Chat request/response | `backend/app/schemas/messages.py`, `backend/app/api/routes.py` | Main agent | Preserve existing fields; additions are backward compatible; confirm breaking changes with user. |
| Top-level action engine | `backend/app/agent/engine.py` | Main agent | `execute_goal(goal, session_id, auto_confirm=False)` remains the user-goal entry; approval is never inferred. |
| Agent plan and state | `backend/app/agent/models.py` | Main agent | Keep typed capability, dependency, state, and observation models; additive checkpoint fields only. |
| Registry injection | `backend/app/tools/registry.py`, `backend/app/agent/orchestrator.py` | Main agent | Assistant-owned `ToolRegistry` is the only registry for user-requested actions. |
| Confirmation | `backend/app/workflows/confirmation.py`, `backend/app/agent/orchestrator.py`, API routes | Main agent | Action/task/step-bound single-use decision; UI mirrors one backend state. |
| Browser state | `backend/app/browser/models.py`, `backend/app/browser/engine.py` | Main agent | Preserve public request/response shapes where possible; real runtime owns observed state. |
| Action event | `backend/app/actions/models.py`, `backend/app/actions/event_bus.py` | Main agent | Existing ActionEventBus/SSE remains the only event stream; safe metadata only. |
| Personal memory | New `backend/app/personal_memory/` module and additive local SQLite table | Main agent | Local-only, explicit-save-only, DPAPI encrypted; never store chat transcripts or secrets. |
| Checkpoint/recovery | `backend/app/runtime/checkpoint_store.py`, `backend/app/runtime/recovery.py` | Main agent | Separate task recovery from memory; no automatic duplicate external side effects. |
| Frontend API contracts | `RAVAN/src/services/`, `RAVAN/src/types/` | Main agent | Typed responses; no fake success fallback. |

## Dependency Graph

```text
TASK-001 unified confirmation + injected registry
    ├── TASK-002 daily Windows path through control plane
    ├── TASK-003 Playwright browser + SSRF policy
    ├── TASK-004 local personal memory
    └── TASK-005 developer-agent persistence/recovery
              └── TASK-006 truthful RAVAN integration
                        └── TASK-007 full regression + Windows acceptance
```

Tasks are sequential where they share assistant/API contracts. No subagents are assigned.

## Ownership Map

| Task ID | Allowed paths | Forbidden paths | Overlap check |
|---|---|---|---|
| TASK-001 | `backend/app/core/assistant.py`, `backend/app/core/router.py`, `backend/app/agent/**`, `backend/app/workflows/confirmation.py`, `backend/app/workflows/engine.py`, related backend tests | `RAVAN/projects/**`, `.env`, `backend/.ryven/**`, `backend/scratch/**` | First slice; review existing user diffs before every touched file. |
| TASK-002 | `backend/app/tools/**`, `backend/app/core/**`, `backend/app/agent/**`, `backend/app/workflows/**`, `backend/app/api/routes.py`, relevant backend tests | `RAVAN/projects/**`, `.env`, `backend/.ryven/**`, `backend/scratch/**` | Runs after TASK-001; single actions and legacy workflow plans must use one Assistant-owned AgentEngine/ToolRegistry. |
| TASK-003 | `backend/app/browser/**`, `backend/app/internet/**`, `backend/requirements.txt`, browser tests, browser API service/types/components | `RAVAN/projects/**`, `.env`, `backend/.ryven/**`, `backend/scratch/**` | Browser/API contracts from TASK-001 are frozen first. |
| TASK-004 | New `backend/app/personal_memory/**`, `backend/app/api/routes.py`, registry/permissions/system prompt, memory tests, RAVAN memory service/types/panel | `RAVAN/projects/**`, `.env`, checkpoint database contents, `backend/scratch/**` | Memory table/store is separate from task checkpoint rows. |
| TASK-005 | `backend/app/agent/**`, `backend/app/runtime/**`, `backend/app/orchestrator/**`, recovery tests | `RAVAN/projects/**`, `.env`, `backend/.ryven/**`, `backend/scratch/**` | Checkpoint migration is additive; protect existing M15 work. |
| TASK-006 | `RAVAN/src/hooks/**`, `RAVAN/src/routes/**`, `RAVAN/src/services/**`, `RAVAN/src/types/**`, `RAVAN/src/components/**`, frontend tests | `RAVAN/projects/**`, `.env` | Uses frozen API/event contracts; no mock-success fallback. |
| TASK-007 | Relevant tests, audit document under `docs/spec-driven/ryven-3-personal-assistant/` | Existing sample projects, secrets, unrelated M15 scratch outputs | Validation/documentation only except approved focused test fixes. |

## Tasks

### TASK-001 — Unify Action Entry and Confirmation
- Objective: Route actionable chat through AgentEngine with the app-owned registry and enforce action-bound, non-implicit confirmation.
- Related FRs: FR-001, FR-004, FR-005.
- Related ACs: AC-001, AC-005, AC-006.
- Inputs: Frozen PRD/TECH_DESIGN/ACCEPTANCE; current dirty changes in assistant/router/agent/workflow files.
- Dependencies: None.
- Allowed files/directories: Ownership Map TASK-001.
- Forbidden files/directories: All listed forbidden paths; do not overwrite unrelated existing diffs.
- Required outputs: Regression tests proving default confirmation is false, exact-step resume does not replay completed steps, one registry instance is injected, and action events cover approval/rejection.
- Required checks: Focused tests first (RED/GREEN), then M14 orchestrator/action tests.
- Required evidence: Test output and diff review for touched paths.
- Stop and report when: A protected workflow cannot be routed without bypassing a security module, or user-owned changes conflict with the required contract.

### TASK-002 — Grounded Daily Windows Assistant and Unified Request Entry
- Objective: Route deterministic single-tool actions and validated legacy workflow plans through the Assistant-owned AgentEngine and registry; report real outcomes, with client fallback never claiming mock actions succeeded.
- Related FRs: FR-001, FR-002, FR-005, FR-008, FR-010.
- Related ACs: AC-001, AC-002, AC-005, AC-006, AC-009, AC-011.
- Inputs: TASK-001 action/response contract.
- Dependencies: TASK-001.
- Allowed files/directories: Ownership Map TASK-002.
- Forbidden files/directories: All listed forbidden paths.
- Required outputs: Real tool status/error mapping, complete capability map, workflow-plan adapter, same-engine task endpoints, and tests for app/folder/file/clipboard/system-status routing.
- Required checks: Focused routing/adapter/API tests; controlled Windows launch smoke tests.
- Required evidence: HTTP responses and process identity for Chrome/Code; blocked-operation tests.
- Stop and report when: A task needs unrestricted process, system-root, credential, or destructive access.

### TASK-003 — Real Browser and Research Runtime
- Objective: Replace HTTPX-only interaction with Playwright-backed Chrome DOM operations, enforce redirect/subresource SSRF policy, and preserve research/source verification.
- Related FRs: FR-003, FR-004, FR-010.
- Related ACs: AC-003, AC-004, AC-007, AC-014.
- Inputs: TASK-001 confirmation contract; installed Chrome verified; Playwright approved.
- Dependencies: TASK-001 and TASK-002.
- Allowed files/directories: Ownership Map TASK-003.
- Forbidden files/directories: All listed forbidden paths; never attach to the user's default Chrome profile.
- Required outputs: Browser adapter, isolated non-persistent contexts, explicit visible/headless mode, deterministic fixtures, safe redirect/resource routing, live state.
- Required checks: Browser tests including navigation, page state, find/click/type/scroll/history/refresh/close, prompt injection, cancellation, and failure recovery.
- Required evidence: Browser test report and real Windows Chrome smoke evidence.
- Stop and report when: Network guard cannot safely validate a target/redirect or a feature would require saved user cookies.

### TASK-004 — Explicit Local Personal Memory
- Objective: Add local explicit-save/list/search/forget memory encrypted with Windows DPAPI and keep it separate from chat and checkpoints.
- Related FRs: FR-006, FR-007.
- Related ACs: AC-008, AC-010, AC-011.
- Inputs: DPAPI and retention choices confirmed; checkpoint store is operational state only.
- Dependencies: TASK-001.
- Allowed files/directories: Ownership Map TASK-004.
- Forbidden files/directories: `.env`, existing checkpoint DB contents, `backend/.ryven/**`, sample projects, scratch.
- Required outputs: Additive versioned memory store, explicit intent/tool/API, review/forget UI, secret rejection, data export-free default.
- Required checks: Unit/API tests, DPAPI failure tests, restart persistence and deletion tests on Windows.
- Required evidence: Encrypted persisted bytes, decrypted expected result under current account, no plaintext in logs/events, deleted item absent.
- Stop and report when: DPAPI cannot initialize or persistent plaintext is required for the proposed design.

### TASK-005 — Restart-Safe Developer Agent
- Objective: Persist generic agent plans/state and recover at the current step without replaying uncertain side effects; retain M14 specialist workflows.
- Related FRs: FR-005, FR-008.
- Related ACs: AC-009, AC-010.
- Inputs: Frozen AgentPlan/TaskStep contracts; existing CheckpointStore and recovery service.
- Dependencies: TASK-001 and TASK-004 data-boundary agreement.
- Allowed files/directories: Ownership Map TASK-005.
- Forbidden files/directories: All listed forbidden paths; no destructive DB migration.
- Required outputs: Additive serialization/recovery for task type `agent`, restart-pending confirmation state, correct exact-step resume.
- Required checks: Reconstruction/restart tests for safe read-only, pending confirmation, and uncertain side-effect cases.
- Required evidence: Persisted checkpoint state and side-effect spies proving no duplicate writes/deployments.
- Stop and report when: The recovery decision cannot prove idempotency of the current step.

### TASK-006 — Live RAVAN Assistant Experience
- Objective: Wire chat, memory, domain state, and shared confirmation/event state to live APIs; remove UI mock-success behavior.
- Related FRs: FR-001, FR-006, FR-009, FR-010.
- Related ACs: AC-005, AC-008, AC-011.
- Inputs: Frozen backend API contracts and ActionEvent/SSE contract.
- Dependencies: TASK-001 through TASK-005.
- Allowed files/directories: Ownership Map TASK-006.
- Forbidden files/directories: `RAVAN/projects/**`, `.env`, unrelated styling redesign.
- Required outputs: Chat/HUD confirmation parity, truthful loading/error/offline states, live memory and agent state, local/session context behavior.
- Required checks: TypeScript check, lint, frontend unit/integration checks, production build, viewport smoke tests.
- Required evidence: HTTP/UI result traces and screenshots without secrets.
- Stop and report when: Existing design system or routes require unrelated redesign to fit the integration.

### TASK-007 — Integrated Release Verification
- Objective: Verify all frozen acceptance criteria and document actual results.
- Related FRs: FR-001–FR-010.
- Related ACs: AC-001–AC-015.
- Inputs: Completed slices and preserved pre-existing worktree.
- Dependencies: TASK-001 through TASK-006.
- Allowed files/directories: Tests and the milestone audit/spec directory only, except user-approved focused repairs.
- Forbidden files/directories: Existing sample projects, secrets, destructive cleanup, git history rewrites.
- Required outputs: Full regression/security report, frontend checks, Windows smoke results, limitations and residual risks.
- Required checks: Full pytest, frontend lint/typecheck/build, real Windows checks, docs guard, diff/status preservation check.
- Required evidence: Commands and actual outputs; no claim based solely on an earlier audit.
- Stop and report when: Any blocking AC fails after three focused repair attempts or a safety regression appears.

## Integration Order

1. TASK-001 shared control and confirmation foundation.
2. TASK-002 daily Windows actions.
3. TASK-003 browser/research.
4. TASK-004 persistent memory.
5. TASK-005 developer task recovery.
6. TASK-006 unified RAVAN experience.
7. TASK-007 full acceptance judgment and audit.

## Main-Agent Validation Plan

- Re-run failing/related tests on untouched baseline slices before editing and record the exact failure signature.
- Use RED/GREEN for each defect/behavior; validate the smallest relevant test immediately after each first code edit.
- Run the original 881 tests and new acceptance tests; do not remove, skip, or weaken tests to manufacture success.
- Run frontend lint, explicit TypeScript no-emit check, and production build.
- Verify browser/Windows behavior through actual local process and browser state; no network mutations in live smoke tests.
- Check uncommitted user changes remain intact and audit all modified paths before final acceptance.
