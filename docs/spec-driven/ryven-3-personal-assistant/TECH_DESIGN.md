# RYVEN 3.0 Personal Assistant Unification — Technical Design

Status: Frozen  
Based on PRD version/date: Frozen PRD approved 2026-10-03  
Owner: RYVEN project owner  
Approval: Approved by user on 2026-10-03

## Current System State

- `Assistant.process` is the `/api/chat` entry point. It uses `IntentRouter`, `SafetyGuard`, `ToolRegistry`, `WorkflowEngine`, and Ollama, with separate branches for direct tools, workflows, and the agent.
- `AgentEngine` combines `AgentPlanner`, `CapabilityRouter`, and `AgentOrchestrator`. `CapabilityRouter.filter_registry` exists, but planning/execution does not consistently use one injected registry; the agent orchestrator can create its own registry. Generic `AgentState` and confirmation tokens are in-memory only. The current confirmation resume calls `execute_plan` on the original plan, which can replay completed steps.
- M14 `OrchestratorEngine` is a mature developer workflow engine and is also reachable through registered tools. Keep it as a specialized developer-domain capability behind the top-level agent rather than replacing it.
- `ToolRegistry.execute_tool` emits lifecycle events through the existing M14.2 `ActionTracker`/`ActionEventBus`; the API exposes SSE and read-only replay.
- `BrowserEngine` currently uses HTTPX to fetch HTML, parses snapshots, and keeps in-memory state. It does not control the installed Chrome DOM/runtime. HTTPX currently follows redirects after validating the initial URL, so final-hop and subresource policy must be enforced by the real browser adapter.
- `InternetAgent` composes search, research, page reading, forms, authentication pause/resume, downloads/uploads, and verification services.
- `CheckpointStore` persists operational checkpoints in SQLite and redacts metadata, but the generic `AgentOrchestrator` does not currently save/restore its `AgentState`. It is not user memory. `ConversationManager` is in-memory only. The Memory HUD and developer project cards currently use static mock data.
- `SafetyGuard.validate_action` allowlists many tools. `Assistant.process` defaults `auto_confirm=True`; the agent path defaults false. Workflow paths contain implicit auto-confirm defaults, and browser/agent confirmations use separate state. The agent confirmation resume currently risks replaying already-completed steps.
- Current live runtime has 88 registered tools; Ollama and `qwen2.5:7b` report available. Python Playwright is not installed; Chrome and VS Code binaries are present on this Windows host.
- Latest full backend run collected 881 tests and reported 871 passing / 10 failing. Frontend production build passes; ESLint currently reports formatting errors. Preserve the existing dirty worktree.

## Overall Approach

Use `AgentEngine` as the single top-level action control plane. Keep `IntentRouter` deterministic for well-defined single actions and ordinary conversation. For any action, it produces a typed intent/goal; `CapabilityRouter` selects the minimum required domain/tool subset; `AgentPlanner` builds a structured plan; and `AgentOrchestrator` executes and observes steps through the same injected `ToolRegistry`, `SafetyGuard`, confirmation manager, and `ActionEventBus`.

Single-step actions use the same execution/confirmation/event path as composite goals; they must not bypass the agent safety layer. Ordinary Q&A stays on the local-first AI path. LLMs may interpret or propose a structured plan, but tool arguments and every step are validated deterministically before execution. M14 `OrchestratorEngine` remains the specialized implementation for developer lifecycle tasks and is invoked by an adapter/capability rather than as a second top-level assistant.

Retain existing modules and public contracts where practical. Add only the missing personal-memory domain and a real browser runtime adapter. Replace frontend mock results with backend state and honest unavailable/error states.

## Modules and Responsibilities

| Module | Responsibility | Related FRs | Owned contracts |
|---|---|---|---|
| `core/assistant.py` + `core/router.py` | Single request entry; distinguish Q&A from actionable goals; never set implicit approval | FR-001, FR-004, FR-010 | `RouteDecision`, normalized response |
| `agent/engine.py`, `planner.py`, `capability_router.py`, `orchestrator.py` | Top-level plan, minimum capability selection, execution, observation, cancellation, retries, confirmation resume | FR-001, FR-005 | `AgentPlan`, `TaskStep`, `AgentExecutionResult` |
| `tools/registry.py` | One registry instance injected into all domains; action lifecycle wrapper | FR-001, FR-002, FR-005 | `BaseTool` schemas and execution results |
| `browser/engine.py` + `internet/*` | Real browser lifecycle/DOM operations, research, page observation, form and file operations | FR-003, FR-004 | Existing browser/internet models and service APIs |
| `workflows/confirmation.py` + agent/API confirmation endpoints | Single-use confirmation token bound to task, step, and action; reject/cancel/resume exactly the pending step | FR-004 | Confirmation request/decision contract |
| `actions/*` + API SSE | Sole event stream for all action/task/memory state changes; replay reads historical events only | FR-005, FR-009 | `ActionEvent` |
| New `app/personal_memory/*` | Local explicit-save CRUD, secret/sensitive-value rejection, review/forget | FR-006, FR-007 | `PersonalMemory`, store/service API |
| `runtime/checkpoint_store.py` | Continue durable task recovery separately from personal memories | FR-005 | Existing checkpoint schema/versioning |
| RAVAN `useJarvis`/services/HUD | Call backend, display truthful lifecycle and both confirmation surfaces, expose memory controls | FR-009, FR-010 | Chat/agent/memory response types |
| `ai/unified.py` + model router | Local-first reasoning/planning; remote providers remain disabled absent explicit policy | FR-001 | `AIProvider`, typed `TaskType` routing |

## Interface Contracts

- `POST /api/chat` remains the public chat entry point and receives a stable session ID. Its response includes `type`, user-visible message, tool/task identity, status, and safe metadata.
- All action-oriented calls are normalized into a single-action or multi-step `AgentPlan`; direct `Assistant` registry execution is removed from the user request path after compatibility tests prove parity.
- `AgentEngine` receives the app-owned registry, safety guard, confirmation manager, memory service, and domain adapters through constructor injection. No per-call default registry is constructed.
- Confirmation request includes `confirmation_id`, task ID, step ID, action/tool, redacted summary, and expiry. Approve/reject consumes the token once. Approval advances only the pending step; it does not restart completed steps or set a global auto-confirm flag.
- Personal memory API is session/user scoped to the single local profile: create only after explicit save intent, list/search, delete by opaque memory ID, and clear all with explicit confirmation. Values are redacted/rejected before persistence.
- Browser service methods retain their existing parameters/response shape where possible; a session reports actual Chromium-backed state and errors if the browser cannot start. Browser profile/cookies are never persisted.
- Action events carry safe identifiers and summaries only. No credentials, cookies, raw authorization headers, memory payloads, or screenshot bytes enter SSE, telemetry, logs, or replay.

## Data Model and Migration

- Keep `task_checkpoints` and recovery metadata in the current checkpoint database. Extend it to save/restore generic agent plans, step state, current action, and confirmation state. Do not use checkpoints as personal memory.
- Add a schema-versioned local personal-memory table/store, separate from operational checkpoint rows. Proposed fields: opaque memory ID, category, user-provided text, created/updated timestamps, source marker (`explicit_user_save`), and schema version.
- Do not persist ordinary chat transcripts. Store only explicit memory items; reject credentials, tokens, secrets, cookies, private keys, and secret environment values.
- Provide additive SQLite migration; never delete or rewrite existing checkpoints. Memory deletion is explicit and verifiable.
- Encrypt personal-memory payloads at rest using Windows DPAPI scoped to the current Windows account, in addition to explicit-save-only behavior and secret rejection. DPAPI failure must fail closed for writes; deletion remains available.

## State Transitions

- Agent task: `UNDERSTANDING -> PLANNING -> VALIDATING -> EXECUTING/OBSERVING -> VERIFYING -> COMPLETED`; any high-impact step can enter `WAITING_CONFIRMATION`; reject enters `CANCELLED` or a defined blocked state; failures use bounded retry then `FAILED`. Persist transitions and resume only at the recorded current step.
- Confirmation: `PENDING -> APPROVED | REJECTED | EXPIRED`; every transition is single-use and tied to its exact step.
- Browser: `IDLE -> STARTING -> ACTIVE/NAVIGATING -> WAITING_CONFIRMATION | ERROR -> CLOSED`. Session memory is in-process only; cookies/profile are ephemeral.
- Personal memory: `EXPLICIT_SAVE_REQUESTED -> VALIDATED -> STORED`; invalid/sensitive content is rejected without persistence; delete is confirmed and produces an audit-safe event.

## Concurrency, Consistency, and Idempotency

- Reuse existing bounded concurrency, deadlines, and resource manager for expensive model and build/test operations.
- Browser actions within a session are serialized; unrelated tasks may run concurrently within configured limits.
- Confirmation IDs are single-use and idempotency keys prevent duplicate side effects after retries.
- Checkpoint recovery resumes only after verifying current step and confirmation state; never auto-repeat an external side effect whose outcome is unknown. A pending approval survives restart as pending and must be re-presented to the user; approval resumes only that step.

## Authentication, Authorization, Privacy, and Security

- Preserve `SafetyGuard`, `PermissionManager`, `ConfirmationManager`, `ToolRegistry`, `SandboxProcessRunner`, `GitSecurity`, `DeploymentSecurity`, `HealthSecurity`, `ActionEngine`, and M14 developer workflows.
- Default-deny unknown tools. The capability subset is not an authorization grant; every action is validated again at execution.
- `auto_confirm` defaults false across chat, workflows, and agent paths. Confirmation decisions cannot be inferred from unrelated conversational text.
- Use installed Chrome through a Playwright-backed Python adapter with an isolated non-persistent `BrowserContext`; visible mode is default. Headless mode is only used after explicit selection and remains visible in ActionEvent/SSE/HUD activity. Do not use a persistent user-data directory, attach to the user's default Chrome profile, import cookies, or persist browser storage.
- Validate every top-level navigation and subresource request against HTTP/HTTPS-only and private/local-address policy. Revalidate redirects hop-by-hop; block DNS rebinding/private destinations and unsafe schemes. Permit only the task-scoped loopback host/port verified as the approved project's preview server; do not generalize the exception to other localhost ports or private addresses.
- Never extract passwords, cookies, session tokens, authorization headers, keys, `.env` contents, or secret environment values. Apply redaction before storage, logging, telemetry, UI, and event publication.
- Model-generated plans are untrusted; validate schema, capability membership, argument schema, path containment, URL policy, permission, and confirmation before execution.
- Replay remains read-only and consumes stored events only.

## Failures, Retry, Recovery, and Degradation

- Browser startup, navigation, stale element, timeout, and network failures become structured step observations; never fabricate success.
- Retry only idempotent/read-only steps by default. Do not retry uncertain writes, form submits, uploads, purchases, Git push, or deployment without renewed confirmation and outcome verification.
- Ollama unavailable returns a visible local-AI unavailable state. Client fallback must not simulate completed system actions or fake memory/telemetry.
- Resource-manager deferral is a visible `DEFERRED/PAUSED` result, not a task failure mislabeled as an action result.
- Existing recovery state must distinguish pending confirmation from a safe-to-resume read-only step. External side effects require observation/reconciliation before continuation.

## Performance and Capacity

- Initial supported load: one local user, one active browser session by default, bounded concurrent tasks.
- Deterministic operations do not call an LLM per click/type/read/scroll step.
- Existing request/task deadlines and concurrency limits remain in force; browser startup and navigation receive explicit bounded timeouts.
- Performance targets for chat first-token latency and browser action completion remain TBD pending baseline measurement on this Windows host.

## Observability: Logs, Metrics, and Alerts

- Use the existing `ActionEventBus`, SSE, LiveActivityPanel, TaskTimeline, and read-only ReplayPlayer; do not add a second event system.
- Add safe action types/metadata only where required; record task/step/action IDs, domain, duration, status, and confirmation state.
- Verify emitted event count and ordering for start, waiting, approve/reject, completed, failed, and cancelled paths.

## Compatibility

- Preserve existing API endpoints and response compatibility where practical; version any breaking schemas.
- Keep current Windows tools and VS Code behavior, existing sample projects, `.env`, backend dirty changes, and generated project contents intact.
- Add only required dependencies; pin/test the chosen Playwright version and use the installed Chrome channel rather than downloading a second browser where supported.

## Release and Rollback

- Implement vertical slices in PRD order while keeping the app runnable after each slice.
- Use additive DB migrations and preserve existing checkpoint databases.
- Keep legacy entry paths behind compatibility adapters until parity tests pass; then remove duplicate dispatch only in a separately verified change.
- Roll back browser runtime and memory adapters without deleting persisted checkpoints or memories.

## Test Boundaries

- Security unit tests: redirect/private-IP SSRF, unsafe schemes, credentials and secret redaction, token binding/replay, cancellation, capability allowlists.
- Agent integration tests: single-action and composite routing, same injected registry, observation-based continuation, bounded retries, idempotent confirmation resume, read-only replay.
- Browser tests: launch installed Chrome in isolated profile, navigate/read/find/click/type/back/forward/refresh/close, stale page-state detection, startup/network failure, redirect handling.
- Memory tests: explicit-only create, local persistence/restart, list/search/delete, secret rejection, no transcript persistence, no event/log payload leakage.
- Frontend tests: chat and HUD confirmation parity, cancellation, backend offline honest state, real memory/project data instead of placeholders.
- Release gates: all currently collected 881 backend tests plus new tests; frontend type check, lint, production build; Windows smoke checks. Existing suite defects must be fixed or explicitly accepted before final acceptance.

## Alternatives Considered

1. **Recommended — central AgentEngine facade over existing domain engines.** One request/action lifecycle and one confirmation/event path; preserve M14 as the developer-domain engine and reuse ToolRegistry/ActionEventBus. Moderate integration effort; lowest rewrite risk.
2. **Route every operation directly through the LLM.** Rejected: unnecessary latency, harder to bound, and increases prompt-injection/authorization risk.
3. **Keep legacy assistant, workflow, and agent paths independent and add a UI router.** Rejected: leaves duplicate safety/confirmation semantics and does not satisfy one control plane.
4. **Keep HTTPX HTML fetch as the browser implementation.** Rejected for live browser interaction: it does not execute JavaScript or provide real Chrome DOM/action state.

## Blocked Technical Issues

| Issue | Product dependency | Impact | Status |
|---|---|---|---|
| Memory at-rest protection | Privacy requirements for explicit memories | Data-store design and portability | Resolved: use Windows DPAPI |
| Playwright integration | Browser interaction UX | Dependency and isolated profile behavior | Resolved: add Python Playwright and target installed Chrome |
| Task-scoped loopback preview allowance | Security implementation must bind host/port to the process started for the approved project and revoke it at task end | Browser route filter policy | Product choice resolved; implementation proof required |
| Numeric first-token/action latency SLO | User performance expectations | Runtime targets and test thresholds | Proposed assumption: no hard SLA until baseline is measured on this host; keep existing bounded timeouts and report p50/p95 telemetry |

## Technical Decision Log

| Decision ID | Decision | Alternatives | Rationale | Related FRs |
|---|---|---|---|---|
| TD-001 | Use Python Playwright with installed Chrome, an isolated non-persistent `BrowserContext`, visible mode by default, and explicit observable headless mode. | Keep HTTPX-only reader; implement raw Chrome DevTools Protocol. | Playwright provides supported structured DOM and interaction APIs; installed Chrome avoids a second browser binary; non-persistent context avoids personal cookies/storage. | FR-003, FR-004, FR-009 |
| TD-002 | Use `AgentEngine` as the single top-level action coordinator and inject the existing app-owned registry and guard. | Direct LLM tool execution; keep Assistant/Agent/Workflow dispatch independent. | Preserves mature domain implementations while unifying capability selection, validation, confirmation, observation, and events. | FR-001, FR-002, FR-005 |
| TD-003 | Keep personal memories in a separate versioned SQLite store and encrypt payloads with Windows DPAPI. | Reuse task checkpoint rows; plaintext under profile ACL. | Separates retention and deletion from task recovery and scopes readable payloads to the local Windows account. | FR-006, FR-007 |
| TD-004 | Allow only task-scoped verified loopback preview requests through the browser network guard. | Block all private destinations; allow all localhost; allow verified project preview only. | Supports real project preview verification while keeping the exception narrow and tied to the approved task. | FR-003, FR-008 |
