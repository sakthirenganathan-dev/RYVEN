# RYVEN 3.0 Personal Assistant Unification — Acceptance Contract

Status: Frozen  
Based on PRD/Tech Design version/date: PRD and TECH_DESIGN frozen 2026-10-03  
Approval: Approved by user on 2026-10-03

## Scope Boundaries

### In Scope

- One request/control plane across Windows, browser/research, personal memory, and developer workflows.
- Real Playwright-backed Chrome interaction with isolated non-persistent browser contexts.
- Local explicit-save personal memory encrypted with Windows DPAPI.
- Unified confirmation, action events, task checkpointing/recovery, and truthful RAVAN UI state.
- Regression fixes and tests needed to meet these criteria.

### Out of Scope

- Cloud memory sync, multi-user hosting, arbitrary shell/executable access, credential extraction, hidden automation, and actions outside approved tools.
- Automatic purchase, publishing, message sending, destructive actions, or unconfirmed deployments.
- Rebuilding the project or replacing the existing tool/event/runtime architecture.

### Non-goals

- Treating a passing unit suite alone as proof of real Windows/browser behavior.
- Reporting actions, sources, telemetry, or memory as successful without observed results.
- Removing or rewriting existing user worktree modifications.

### Deferred

- Any numeric performance SLA; capture p50/p95 metrics on the Windows host and propose targets after measurement.
- Cloud synchronization and multi-user memory.

### Assumptions

- One local Windows user; Ollama is the default AI provider; remote inference remains disabled unless separately authorized.
- Ordinary chat history remains ephemeral. Task checkpoints and explicit memories have separate retention and deletion behavior.
- Headless browsing is explicit and observable; visible Chrome is the default.

### Release Blockers

- No consequential action can run without action-bound explicit confirmation.
- No SSRF, credential, privacy, prompt-injection, or replay side-effect regression.
- All baseline and new backend tests pass; frontend checks and Windows smoke tests pass.
- All original uncommitted user changes remain preserved.

### Definition of Done

- All blocking criteria below pass with the requested evidence.
- The app remains runnable after each vertical slice and is verified as one integrated release.
- Audit report records actual test totals, real Windows results, limitations, and any approved caveats.

## Acceptance Criteria

### AC-001 — One action control plane
- Related FRs: FR-001, FR-002, FR-005, FR-008
- Blocking: Yes
- Scenario: A user submits ordinary Q&A, a single tool request, and a composite goal across the four domains.
- Preconditions: Backend and Ollama are available; required tools are registered.
- Action/event: Submit each request through `/api/chat`.
- Expected result: Action-oriented requests are normalized through the same `AgentEngine`/`CapabilityRouter`/`AgentOrchestrator` path, use the app-owned `ToolRegistry`, and retain domain-specialized engines as adapters. Q&A remains on the local AI path. No duplicate registry or event bus is created.
- Required evidence: Unit/integration tests for all request classes; registry identity assertion; live API examples with tool/task IDs.

### AC-002 — Safe Windows actions
- Related FRs: FR-002, FR-004
- Blocking: Yes
- Scenario: Open Chrome, VS Code, Notepad, Calculator, Windows Terminal, File Explorer; open an approved folder/file; read/write clipboard; query system status.
- Preconditions: Supported Windows executables/tools exist and permissions are active.
- Action/event: Submit each natural-language request through the assistant.
- Expected result: Only allowlisted tools/apps execute. Results report observed OS/tool outcomes. Arbitrary executables, shell commands, protected/system paths, and prohibited clipboard reads are blocked. Existing VS Code behavior remains intact.
- Required evidence: Unit tests with actual process arguments; Windows smoke tests verifying `chrome.exe` and `Code.exe`; API response and action event per operation.

### AC-003 — Real browser interaction
- Related FRs: FR-003, FR-004, FR-009
- Blocking: Yes
- Scenario: Open browser, navigate, inspect page, find an element, click/type/press/scroll/back/forward/refresh, capture a snapshot, and close.
- Preconditions: Playwright dependency installed, Chrome detected, isolated non-persistent browser context created.
- Action/event: Exercise all browser methods against a deterministic local test page and an approved public page.
- Expected result: Operations use real Chromium DOM/page state, not HTTPX-only HTML parsing or fabricated state. Visible Chrome is default. Headless mode works only when explicitly selected and emits the same observable events. Browser context state/cookies are not persisted or imported from the user's default Chrome profile.
- Required evidence: Focused Playwright tests for each operation; Windows smoke test screenshot/logs with URL/title/result; startup failure and stale-page-state tests.

### AC-004 — URL and network security
- Related FRs: FR-003, FR-004
- Blocking: Yes
- Scenario: Navigate to HTTP/HTTPS public pages, unsafe schemes, redirect chains, DNS-rebinding/private destinations, metadata IPs, and local previews.
- Preconditions: Browser request interception and existing health/browser validators are active.
- Action/event: Attempt each destination, including redirects and subresource requests.
- Expected result: Only HTTP/HTTPS is allowed. Every redirect/subresource is validated. Private, LAN, and metadata destinations are blocked except the verified loopback host/port bound to the active approved project preview task. No general localhost or private-network allowance is added.
- Required evidence: Parametrized URL/redirect tests, local mock preview test, and security tests showing blocked requests never reach their destination.

### AC-005 — Confirmation and cancellation
- Related FRs: FR-004, FR-005, FR-009
- Blocking: Yes
- Scenario: Request form submission, upload, external message, destructive content change, Git commit/push, install, purchase/payment, publish, or deployment.
- Preconditions: Chat and relevant HUD panel are connected to one pending action state.
- Action/event: Request action, approve or reject/cancel, then retry a consumed token.
- Expected result: Action pauses before side effect; confirmation is explicit, action/task/step-bound, single-use, and shown in chat and HUD. Reject/cancel causes no side effect. Unrelated “yes” text cannot approve. Approval resumes only the pending step. No entry path defaults to `auto_confirm=True` for consequential actions.
- Required evidence: API/component tests for approval, rejection, cancellation, expired/reused token, unrelated conversational “yes”, and side-effect counters remaining unchanged until approval.

### AC-006 — Unified action events and read-only replay
- Related FRs: FR-005, FR-009
- Blocking: Yes
- Scenario: Run a safe action, a confirmation-gated action, a failure, a cancellation, and a completed multi-step task; open the activity stream and replay.
- Preconditions: Existing ActionEventBus, SSE, LiveActivityPanel, TaskTimeline, and ReplayPlayer are used.
- Action/event: Observe lifecycle over SSE and create/advance/restart replay.
- Expected result: One event system reports started, waiting, approved/cancelled, completed/failed events. Events contain only safe metadata. Replay reads stored events and cannot invoke tools, filesystem writes, process launches, network mutation, or form submission.
- Required evidence: Event ordering and SSE integration tests; replay side-effect spies asserting zero execution calls.

### AC-007 — Research evidence and truthful results
- Related FRs: FR-003, FR-010
- Blocking: Yes
- Scenario: Research a technical topic and request a sourced summary; simulate no results, network failure, and unverified source content.
- Preconditions: InternetAgent and verification service are active.
- Action/event: Ask RYVEN to research and summarize retrieved sources.
- Expected result: Output cites only sources actually fetched and read, distinguishes failed/unverified sources, and never fabricates page state or research completion.
- Required evidence: Mocked-source tests, public-network smoke test with captured URLs, and failure-path tests.

### AC-008 — Local explicit personal memory
- Related FRs: FR-006, FR-007
- Blocking: Yes
- Scenario: Explicitly save a preference, list/search it, retrieve it after restart, forget it, and attempt to save a credential/secret.
- Preconditions: Personal-memory store uses additive schema migration and Windows DPAPI.
- Action/event: Exercise save/list/search/forget through the assistant and memory HUD.
- Expected result: Only explicitly requested non-secret memory is stored locally, encrypted for the current Windows account, survives restart, is reviewable/deletable, and is never emitted in logs/events/replay. Credential-like content is rejected. Ordinary chat transcripts do not survive restart. DPAPI failure fails closed for writes.
- Required evidence: Store/API/UI tests, restart persistence test on Windows, DPAPI encrypt/decrypt test, secret rejection/redaction tests, and deletion verification.

### AC-009 — Developer-agent continuity
- Related FRs: FR-005, FR-008
- Blocking: Yes
- Scenario: Plan and execute project inspection, modification, build/test, Git, deployment preview, and health verification tasks.
- Preconditions: Existing sandbox, GitSecurity, DeploymentSecurity, HealthSecurity, ActionEngine, and M14 domain engine remain enabled.
- Action/event: Run a deterministic multi-step task with dependencies, one step failure, pause/resume, and a consequential step.
- Expected result: One top-level plan uses existing specialist engines; every step is validated and observed; retries are bounded and limited to safe/idempotent cases; protections/confirmation remain in force; state appears in timeline and status UI.
- Required evidence: Orchestrator integration tests, dependency/failure/retry tests, and disposable-project Windows smoke test.

### AC-010 — Restart recovery without duplicate side effects
- Related FRs: FR-005, FR-006, FR-008
- Blocking: Yes
- Scenario: Terminate/restart backend during a safe read-only task, a pending confirmation, and an uncertain external side effect.
- Preconditions: Generic agent state is persisted through the existing checkpoint infrastructure or a compatible additive extension.
- Action/event: Restart backend and run recovery scan/resume.
- Expected result: Safe idempotent work resumes from its current step. Pending approval is re-presented. External side effects are not automatically replayed when outcome is uncertain; user confirmation/reconciliation is required. Memories and checkpoints remain separate.
- Required evidence: SQLite integration tests across service reconstruction/restart and spies proving no duplicate external side effects.

### AC-011 — Truthful frontend and failure states
- Related FRs: FR-009, FR-010
- Blocking: Yes
- Scenario: Use chat, voice input where supported, memory, system, browser, developer, and activity views with backend online and offline.
- Preconditions: RAVAN API service and real backend routes are wired.
- Action/event: Execute operations and simulate backend/model unavailable responses.
- Expected result: Live data replaces mock memory/project/telemetry/action success claims. If backend or model is offline, UI reports unavailable/error and offers retry; it never reports a fake action as completed. Confirmation is visible in chat and HUD.
- Required evidence: Frontend component/integration tests, API contract tests, and desktop/mobile viewport smoke checks.

### AC-012 — Regression and security suite
- Related FRs: FR-001–FR-010
- Blocking: Yes
- Scenario: Run all existing and new backend tests after implementation.
- Preconditions: Baseline failures are recorded and classified; dependencies are installed in the project environment.
- Action/event: Run complete test collection and suite, including original security tests.
- Expected result: All 881 original tests and all new unification tests pass; no existing test is removed, skipped, or weakened to obtain a green result. Security regressions are zero.
- Required evidence: Full pytest command/output, test count, failure count, and security-specific test report.

### AC-013 — Frontend quality gates
- Related FRs: FR-009, FR-010
- Blocking: Yes
- Scenario: Run frontend static checks and build after implementation.
- Preconditions: RAVAN dependencies are installed.
- Action/event: Run lint, TypeScript no-emit check, and production build.
- Expected result: All checks exit zero; no new warnings conceal type errors.
- Required evidence: Exact commands and successful output.

### AC-014 — Windows real-world verification
- Related FRs: FR-002–FR-010
- Blocking: Yes
- Scenario: Verify app launch, visible Chrome search/read, VS Code launch, verified local preview, confirmation cancel, memory save/restart/forget, and replay.
- Preconditions: Windows host with installed Chrome, VS Code, Ollama model; isolated disposable project/test page.
- Action/event: Run controlled smoke tests using the user-facing API/UI; never use production sites for side-effecting tests.
- Expected result: Actual `chrome.exe` and `Code.exe` are observed; browser results match actual page contents; confirmation cancel produces `ACTION_CANCELLED` without submitting; memory persists only as designed; replay triggers no action.
- Required evidence: Timestamped command/test output, HTTP responses, captured event IDs, and screenshots/logs that contain no secrets.

### AC-015 — Performance and bounded execution
- Related FRs: FR-001, FR-005, FR-010
- Blocking: Yes
- Scenario: Execute deterministic browser primitives and multi-step tasks under ordinary local load and simulated resource pressure.
- Preconditions: Existing deadline/concurrency/resource managers enabled.
- Action/event: Record operation latency and model-call counts.
- Expected result: Click/type/read/scroll/back/forward/refresh do not call the LLM per action; all operations have bounded timeouts; resource deferral is truthful. Capture p50/p95 without asserting an unapproved numeric SLA.
- Required evidence: Performance telemetry and deterministic tests asserting bounded calls/timeouts.

## Coverage Map

| FR ID | AC IDs | Coverage notes |
|---|---|---|
| FR-001 | AC-001, AC-012, AC-015 | Routing/control-plane integration |
| FR-002 | AC-002, AC-012, AC-014 | Controlled Windows operations |
| FR-003 | AC-003, AC-004, AC-007, AC-014 | Real browser, research, SSRF and local preview boundaries |
| FR-004 | AC-002, AC-003, AC-005, AC-006, AC-014 | Confirmation, cancellation, observability |
| FR-005 | AC-001, AC-006, AC-009, AC-010, AC-015 | Planning, events, retries, recovery |
| FR-006 | AC-008, AC-010, AC-014 | Explicit local memory and checkpoint separation |
| FR-007 | AC-008, AC-012 | Privacy, DPAPI, secret handling, deletion |
| FR-008 | AC-001, AC-009, AC-010, AC-014 | Existing developer/tool protections |
| FR-009 | AC-005, AC-006, AC-008, AC-011, AC-014 | HUD/chat truthful state and confirmations |
| FR-010 | AC-007, AC-011, AC-015 | Honest degradation, timeouts, measured performance |
