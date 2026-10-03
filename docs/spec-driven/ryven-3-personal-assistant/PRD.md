# RYVEN 3.0 Personal Assistant Unification — Product Requirements

Status: Frozen  
Owner: RYVEN project owner  
Last updated: 2026-10-03  
Approval: Approved by user on 2026-10-03

## Problem and Context

RYVEN already has a Windows assistant, browser and InternetAgent capabilities, developer workflows, a ToolRegistry, ActionEventBus, orchestrators, and SQLite task checkpoints. These capabilities are exposed through overlapping assistant, workflow, and agent paths. The UI still contains mock memory and project data, while conversation history exists only in process memory. RYVEN needs one coherent personal-assistant experience without rebuilding the mature codebase or weakening existing security controls.

## Users and Stakeholders

- Primary user: one person using RYVEN locally on Windows.
- Maintainer: RYVEN project owner.

## Product Goals

- Provide one natural-language entry point for daily Windows tasks, browser/research tasks, personal memory, and development workflows.
- Route requests through one observable control plane that reuses the existing registry, permissions, confirmation system, action bus, orchestrator, and runtime protections.
- Return observed tool results rather than simulated or fabricated state.
- Provide local, user-controlled persistent memory distinct from execution checkpoints.
- Preserve existing security and working project behavior.

## Success Metrics

| Metric | Baseline | Target | Measurement window/source |
|---|---|---|---|
| Registered safe tools available to the assistant | 88 reported by live `/api/health` on 2026-10-03 | All supported tools remain routable through the unified control plane | API integration tests and live health response |
| Existing backend suite | 881 tests collected; latest run 871 passed, 10 failed | 100% of baseline tests pass; new tests pass | Full pytest run in the project environment |
| Frontend validation | Production build passes; ESLint reports 8 formatting errors | Type check, lint, and production build pass | Frontend CI/local checks |
| Personal memory persistence | No personal-memory store; Memory HUD uses static mock data | Explicitly saved local memories survive restart and can be listed/deleted | Store and API integration tests |
| Confirmation safety | Assistant chat defaults `auto_confirm=True`; agent path defaults false | No consequential action executes without a valid, action-bound confirmation | Negative and positive security tests |

## User Flows

1. User asks RYVEN to perform an ordinary Windows task; RYVEN selects an allowlisted tool, executes it, observes the result, and reports the result.
2. User asks RYVEN to search or research a topic; RYVEN uses the controlled InternetAgent, returns findings with source URLs, and distinguishes unavailable/unverified results.
3. User explicitly asks RYVEN to remember a preference or fact; RYVEN saves it locally and can later list or forget it.
4. User requests a multi-step developer task; RYVEN plans dependency-aware steps, executes safe steps, pauses at confirmation boundaries, reports observations, and exposes the same lifecycle in the HUD/timeline.
5. User rejects a confirmation; the action is cancelled and no side effect occurs.

## Functional Requirements

| FR ID | Requirement | Priority | Status | Notes |
|---|---|---|---|---|
| FR-001 | A single user-facing assistant entry point classifies natural-language requests across the four domains and routes them through one capability/control plane. | Must | Draft | Reuse existing modules; do not add a parallel registry or event bus. |
| FR-002 | Daily Windows actions use explicitly registered, allowlisted capabilities and report observed execution results. | Must | Draft | No arbitrary executable or shell access. |
| FR-003 | Browser and research tasks use the controlled InternetAgent, validate URLs/targets, observe page state, and cite actual retrieved sources when researching. | Must | Draft | Visible Chrome is default; headless mode is an explicit user choice and remains fully observable. Keep SSRF protections and secret redaction. |
| FR-004 | Consequential or externally visible actions pause until the user confirms the specific action; cancellation prevents execution. | Must | Draft | Confirmation must be bound to an action and single-use. |
| FR-005 | Multi-step work uses dependency-aware plans, observe/verify steps, bounded retries, and existing ActionEventBus/SSE surfaces. | Must | Draft | Replay remains read-only. |
| FR-006 | Personal memory is stored locally only and is written only on explicit user instruction. | Must | Draft | User confirmed local-only and explicit-save behavior on 2026-10-03. |
| FR-007 | Users can inspect and delete saved personal memories; credentials, cookies, tokens, private keys, and secret environment values are never stored. | Must | Draft | Retention and data categories need technical design. |
| FR-008 | Developer workflows continue to use existing project sandbox, Git, deployment, health, runtime, and recovery capabilities. | Must | Draft | Preserve APIs and existing safe behavior. |
| FR-009 | The HUD displays real assistant state, active domain/action, confirmation state, errors, and memory status without placeholder values presented as live facts. | Must | Draft | Extend existing DeveloperHUD/MemoryGraph patterns. |
| FR-010 | The system degrades clearly when Ollama, browser, network, or a tool is unavailable; it does not silently substitute mock execution results. | Must | Draft | Offline conversational fallback behavior to be specified. |

## Business Rules

- Read-only, low-risk operations may run without confirmation only where existing policy explicitly permits them.
- Form submission, publishing, sending messages, downloads/uploads with risk, Git commit/push, deployment, file modification, deletion, installation, purchases, and other irreversible/external actions require explicit confirmation.
- A model may propose structured actions but cannot invoke operating-system or browser APIs directly.
- Browser and tool output is untrusted data and cannot override RYVEN safety policy.
- The browser may access only a task-scoped, verified loopback preview server started for the approved project; all other private, LAN, and metadata destinations remain blocked.
- The sole action event stream is the existing ActionEventBus and its consumers.

## Data Lifecycle

- Conversation context remains ephemeral unless a separate approved product decision changes it.
- Task checkpoints remain operational recovery records and are not personal memory.
- Personal memory is local-only, explicit-save-only, inspectable, and deletable by the user.
- Secrets and authentication material are excluded from memory, telemetry, and replay.

## In Scope

- Unification of daily Windows, browser/research, personal-memory, and developer-agent requests.
- Confirmation and cancellation correctness across all entry paths.
- Local personal-memory persistence and user controls.
- Real UI state and results wired to existing backend capabilities.
- Regression repair needed to achieve the acceptance criteria.

## Out of Scope

- Cloud memory synchronization or multi-user hosting.
- Unrestricted desktop control, arbitrary shell commands, credential extraction, or hidden automation.
- Replacing the existing FastAPI/React/Ollama stack or rebuilding the tool, event, workflow, or runtime architecture.
- Automatic purchases, publishing, messaging, or other consequential side effects.

## Non-goals

- Claiming all milestone tests or real Windows actions passed without captured evidence.
- Starting an unrelated future milestone.
- Treating a task checkpoint as user memory.

## Assumptions

| ID | Assumption | Risk | Validation plan | Status |
|---|---|---|---|---|
| A-001 | The first supported deployment is one local Windows user with the existing FastAPI backend and RAVAN frontend. | Low | Verify against current launch configuration and owner review. | Proposed |
| A-002 | Ollama/Qwen is the default reasoning provider; internet access is used only for requested browser/research tasks. | Medium | Verify model routing and provider defaults. | Proposed |
| A-003 | Personal memory is opt-in by explicit instruction, local-only, encrypted at rest with Windows DPAPI, and users can review and forget saved items. | Medium | Owner confirmed local-only, explicit-save, and DPAPI encryption behavior. | Confirmed |
| A-004 | Existing uncommitted M15 and sample-project modifications are user-owned and must be preserved. | High | Keep changes scoped; review status before each edit. | Confirmed by worktree state |

## Open Product Decisions

| Decision ID | Dependency | Options | Recommendation | Impacted FRs | Status |
|---|---|---|---|---|---|
| PD-001 | Memory behavior | Explicit save only; automatic inference of non-sensitive preferences | Explicit save only | FR-006, FR-007 | Resolved by user |
| PD-002 | Confirmation interaction | Inline chat confirmation; HUD confirmation panel; both | Both surfaces backed by one confirmation token/state | FR-004, FR-009 | Resolved by user |
| PD-003 | Conversation retention | In-memory only; local persistent transcript; user-configurable | Keep ephemeral; persist only explicit memories and operational checkpoints | FR-006, FR-007 | Resolved by user |
| PD-004 | First release acceptance | All four domains complete together; vertical releases in declared order | Stage vertical slices in the declared order, keep the app usable at each stage, and accept only the integrated final release | FR-001–FR-010 | Resolved by user |

## Decision Log

| Decision ID | Decision | Rationale | Decider/date | Impacted FRs |
|---|---|---|---|---|
| D-001 | Include all four domains: daily Windows, browser/research, persistent personal memory, developer agent. | User requested all four domains. | User, 2026-10-03 | FR-001–FR-010 |
| D-002 | Preserve controlled access; consequential actions require explicit confirmation. | User confirmed controlled access and existing safety architecture must remain intact. | User, 2026-10-03 | FR-002, FR-004, FR-005, FR-008 |
| D-003 | Personal memory is local-only and explicit-save-only. | Privacy by default; user confirmed local and explicit save. | User, 2026-10-03 | FR-006, FR-007 |
| D-004 | Do not overwrite existing dirty worktree changes. | Repository contains extensive pre-existing M15 and sample-project modifications. | Repository evidence, 2026-10-03 | All |
| D-005 | Show consequential-action approvals in both chat and the relevant HUD panel, backed by one pending approval. | Keeps the interaction visible in the conversation and domain-specific activity view without duplicating confirmation state. | User, 2026-10-03 | FR-004, FR-009 |
| D-006 | Do not persist ordinary chat transcripts across backend restarts. | Limits retained personal data; operational checkpoints and explicitly saved memories remain separate local records. | User, 2026-10-03 | FR-006, FR-007 |
| D-007 | Deliver vertical slices in the declared domain order; require an integrated final release across all four domains. | Allows meaningful validation without leaving the running app broken between implementation stages. | User, 2026-10-03 | FR-001–FR-010 |
| D-008 | Encrypt local personal-memory payloads at rest using Windows DPAPI. | Protects intentionally saved personal data while keeping it local to the current Windows account. | User, 2026-10-03 | FR-006, FR-007 |
| D-009 | Support both visible and headless browser modes; default to visible Chrome and require explicit selection for headless mode. | Preserves user supervision by default while allowing an observable headless option for deliberate use. | User, 2026-10-03 | FR-003, FR-004, FR-009 |
| D-010 | Permit browser access only to the developer agent's task-scoped, verified loopback preview port; keep all other private/LAN/metadata destinations blocked. | Enables developer preview verification without granting general private-network access. | User, 2026-10-03 | FR-003, FR-008 |
