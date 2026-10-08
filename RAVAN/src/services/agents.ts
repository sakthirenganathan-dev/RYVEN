/**
 * RYVEN 3.0 — M16.0 Multi-Agent Coordination Service
 */

import { RYVEN_API_BASE_URL } from "./jarvis";

export interface AgentDescriptor {
  agent_id: string;
  role: string;
  name: string;
  description: string;
  capabilities: string[];
  status: string;
  active_tasks: string[];
}

export interface AgentTaskItem {
  task_id: string;
  role: string;
  objective: string;
  capability: string;
  status: string;
  tool_name?: string;
  dependencies: string[];
  requires_confirmation: boolean;
  confirmation_type?: string;
  confirmed: boolean;
  result?: Record<string, unknown>;
  error?: string;
}

export interface TaskGraphProgress {
  graph_id: string;
  goal: string;
  status: string;
  total_tasks: number;
  completed: number;
  failed: number;
  blocked: number;
  running: number;
  pending: number;
  progress_pct: number;
}

export interface TaskGraphData {
  graph_id: string;
  goal: string;
  status: string;
  created_at: string;
  updated_at: string;
  completed_at?: string;
  error?: string;
  tasks: Record<string, AgentTaskItem>;
  dependencies: Record<string, string[]>;
  progress: TaskGraphProgress;
}

export async function fetchAgentsList(): Promise<{ total_agents: number; agents: AgentDescriptor[] }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/agents`);
  if (!res.ok) {
    throw new Error(`Failed to fetch agents: ${res.status}`);
  }
  return res.json();
}

export async function fetchAgentGraph(graphId: string): Promise<TaskGraphData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/agents/graph/${graphId}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch task graph: ${res.status}`);
  }
  return res.json();
}

export async function submitAgentGoal(
  goal: string,
  projectName?: string,
  autoConfirm: boolean = false
): Promise<TaskGraphData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/agents/task`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal, project_name: projectName, auto_confirm: autoConfirm }),
  });
  if (!res.ok) {
    throw new Error(`Failed to submit agent goal: ${res.status}`);
  }
  return res.json();
}

export async function confirmAgentTask(
  taskId: string,
  graphId?: string
): Promise<TaskGraphData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/agents/tasks/${taskId}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ graph_id: graphId }),
  });
  if (!res.ok) {
    throw new Error(`Failed to confirm task: ${res.status}`);
  }
  return res.json();
}

export async function cancelAgentGraph(
  taskIdOrGraphId: string,
  reason: string = "User cancelled"
): Promise<TaskGraphData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/agents/tasks/${taskIdOrGraphId}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) {
    throw new Error(`Failed to cancel agent task: ${res.status}`);
  }
  return res.json();
}

export interface PlanningDraftTask {
  task_id: string;
  objective: string;
  capability: string;
  preferred_role?: string;
  tool_name?: string;
  arguments: Record<string, unknown>;
  depends_on: string[];
  requires_confirmation: boolean;
  confirmation_type?: string;
  risk: string;
}

export interface PlanningResultData {
  plan_id: string;
  raw_goal: string;
  normalized_goal: string;
  complexity: "SIMPLE" | "MODERATE" | "COMPLEX";
  planning_mode: "DIRECT" | "DETERMINISTIC" | "LLM_ASSISTED" | "HYBRID";
  tasks: PlanningDraftTask[];
  dependencies: Record<string, string[]>;
  parallel_groups: string[][];
  overall_risk: "SAFE" | "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
  requires_confirmation: boolean;
  confirmation_points: string[];
  estimated_steps: number;
  estimated_parallelism: number;
  explanation: string;
  warnings: string[];
  status: string;
}

export async function fetchPlanPreview(
  goal: string,
  projectName?: string
): Promise<PlanningResultData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/planning/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal, project_name: projectName }),
  });
  if (!res.ok) {
    throw new Error(`Failed to generate plan preview: ${res.status}`);
  }
  return res.json();
}

export async function executePlan(
  planId: string,
  autoConfirm: boolean = false
): Promise<TaskGraphData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/planning/${planId}/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_confirm: autoConfirm }),
  });
  if (!res.ok) {
    throw new Error(`Failed to execute plan: ${res.status}`);
  }
  return res.json();
}

// ---------------------------------------------------------------------------
// M17.0 Full Computer & Internet Control Plane Service
// ---------------------------------------------------------------------------

export interface ObservationRecordData {
  observation_id: string;
  timestamp: string;
  source: string;
  title: string;
  details: Record<string, unknown>;
  summary: string;
  url?: string;
  app_name?: string;
  project_name?: string;
  success: boolean;
  error?: string;
}

export interface DiscoveredScopeItemData {
  item_id: string;
  description: string;
  boundary: "CORE" | "DISCOVERED" | "OPTIONAL";
  source_task_id?: string;
  requires_user_approval: boolean;
  approved: boolean;
  details: Record<string, unknown>;
}

export interface ControlResultData {
  control_id: string;
  goal: string;
  status:
    | "IDLE"
    | "OBSERVING"
    | "PLANNING"
    | "AUTHORIZING"
    | "WAITING_CONFIRMATION"
    | "EXECUTING"
    | "RECOVERING"
    | "VERIFYING"
    | "COMPLETED"
    | "FAILED"
    | "CANCELLED";
  success: boolean;
  plan_id?: string;
  graph_id?: string;
  steps_total: number;
  steps_completed: number;
  steps_failed: number;
  active_agent?: string;
  current_action?: string;
  current_application?: string;
  current_website?: string;
  observations: ObservationRecordData[];
  discovered_scope_items: DiscoveredScopeItemData[];
  recovery_attempts: number;
  confirmation_required: boolean;
  confirmation_token?: string;
  confirmation_type?: string;
  message: string;
  error?: string;
  final_output: Record<string, unknown>;
  duration_ms: number;
}

export async function executeControlGoal(
  goal: string,
  projectName?: string,
  autoConfirm: boolean = false
): Promise<ControlResultData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/control/execute`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ goal, project_name: projectName, auto_confirm: autoConfirm }),
  });
  if (!res.ok) {
    throw new Error(`Failed to execute control goal: ${res.status}`);
  }
  return res.json();
}

export async function fetchControlStatus(controlId: string): Promise<ControlResultData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/control/${controlId}`);
  if (!res.ok) {
    throw new Error(`Failed to fetch control status: ${res.status}`);
  }
  return res.json();
}

export async function confirmControl(
  controlId: string,
  confirmationToken: string
): Promise<ControlResultData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/control/${controlId}/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirmation_token: confirmationToken }),
  });
  if (!res.ok) {
    throw new Error(`Failed to confirm control: ${res.status}`);
  }
  return res.json();
}

export async function cancelControl(
  controlId: string,
  reason: string = "User cancelled"
): Promise<ControlResultData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/control/${controlId}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) {
    throw new Error(`Failed to cancel control: ${res.status}`);
  }
  return res.json();
}

export async function fetchRunningApplications(): Promise<{
  count: number;
  processes: Array<{ name: string; pid: number; title: string }>;
}> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/control/applications/running`);
  if (!res.ok) {
    throw new Error(`Failed to fetch running applications: ${res.status}`);
  }
  return res.json();
}

export interface LongHorizonProgress {
  task_id: string;
  state: string;
  total_steps: number;
  completed_steps: number;
  failed_steps: number;
  pending_steps: number;
  current_step_id?: string;
  current_step_name?: string;
  progress_percent: number;
  elapsed_ms: number;
  estimated_remaining_ms?: number;
  last_milestone: string;
  next_action: string;
  waiting_for_user: boolean;
  requires_confirmation: boolean;
  confirmation_token?: string;
  failure_class?: string;
  recovery_attempts: number;
  safe_metadata: Record<string, unknown>;
}

export async function fetchLongTasksList(): Promise<{ tasks: Array<Record<string, unknown>>; count: number }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/tasks`);
  if (!res.ok) throw new Error(`Failed to fetch tasks: ${res.status}`);
  return res.json();
}

export async function fetchLongTaskProgress(taskId: string): Promise<LongHorizonProgress> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/tasks/${taskId}/progress`);
  if (!res.ok) throw new Error(`Failed to fetch task progress: ${res.status}`);
  return res.json();
}

export async function pauseLongTask(taskId: string): Promise<{ status: string; progress: LongHorizonProgress }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/tasks/${taskId}/pause`, { method: "POST" });
  if (!res.ok) throw new Error(`Failed to pause task: ${res.status}`);
  return res.json();
}

export async function resumeLongTask(taskId: string, autoConfirm: boolean = false): Promise<{ status: string; progress: LongHorizonProgress }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/tasks/${taskId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_confirm: autoConfirm }),
  });
  if (!res.ok) throw new Error(`Failed to resume task: ${res.status}`);
  return res.json();
}

export async function cancelLongTask(taskId: string, reason: string = "User cancelled"): Promise<{ status: string; progress: LongHorizonProgress }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/tasks/${taskId}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) throw new Error(`Failed to cancel task: ${res.status}`);
  return res.json();
}

// ---------------------------------------------------------------------------
// M17.8 Multi-Task Scheduler Types & Service API
// ---------------------------------------------------------------------------

export interface SchedulerResourceSummary {
  total_resources?: number;
  occupied_resources: number;
  available_resources?: number;
  active_leases: number;
  waiting_tasks?: number;
}

export interface SchedulerStatusData {
  scheduler_state: string;
  running_task_count: number;
  queued_task_count: number;
  paused_task_count: number;
  blocked_task_count: number;
  recovery_required_count: number;
  active_execution_slots: number;
  max_concurrency: number;
  resource_usage_summary: SchedulerResourceSummary;
  preemption_count: number;
  uptime_seconds: number;
}

export interface QueuedTaskItem {
  task_id: string;
  goal?: string;
  summary?: string;
  state: string;
  priority: number;
  effective_priority: number;
  wait_bonus?: number;
  enqueue_time?: number;
  enqueued_at?: number;
  enqueue_time_iso?: string;
  enqueued_at_iso?: string;
  dependencies: string[];
  requested_resources: string[];
  retry_count: number;
  preemption_count: number;
  recovery_count: number;
  blocked_reason?: string | null;
  blocked_resources?: string[];
  progress?: number;
}

export interface ResourceItemData {
  resource_type: string;
  capacity: number;
  active_usage: number;
  available_capacity: number;
  safe_owner_task_ids: string[];
  blocked_tasks: string[];
  conflicts: Array<{
    resource?: string;
    requested_by?: string;
    owned_by?: string;
    reason?: string;
  }>;
}

export interface SchedulerResourcesData {
  resources: ResourceItemData[];
  total_capacity: number;
  total_active_usage: number;
  total_available: number;
  active_leases_count: number;
}

export interface SchedulerTaskDetail {
  task_id: string;
  goal?: string;
  summary?: string;
  state: string;
  priority: number;
  effective_priority: number;
  progress: number;
  dependencies: string[];
  resources: string[];
  retry_count: number;
  preemption_count: number;
  recovery_state?: string | null;
  recovery_attempts?: number;
  checkpoint_available?: boolean;
  checkpoint_availability?: boolean;
  last_milestone?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface TaskProgressData {
  task_id: string;
  progress_percent: number;
  current_step_index: number;
  total_steps: number;
  completed_steps: number;
  state: string;
}

export async function getSchedulerStatus(): Promise<SchedulerStatusData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/scheduler/status`);
  if (!res.ok) throw new Error(`Failed to fetch scheduler status: ${res.status}`);
  return res.json();
}

export async function getSchedulerQueue(): Promise<QueuedTaskItem[]> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/scheduler/queue`);
  if (!res.ok) throw new Error(`Failed to fetch scheduler queue: ${res.status}`);
  const data = await res.json();
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.queue)) return data.queue;
  return [];
}

export async function getSchedulerResources(): Promise<SchedulerResourcesData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/scheduler/resources`);
  if (!res.ok) throw new Error(`Failed to fetch scheduler resources: ${res.status}`);
  const data = await res.json();
  const list: ResourceItemData[] = Array.isArray(data) ? data : (data.resources || []);
  return {
    resources: list,
    total_capacity: list.reduce((acc, r) => acc + (r.capacity || 0), 0),
    total_active_usage: list.reduce((acc, r) => acc + (r.active_usage || 0), 0),
    total_available: list.reduce((acc, r) => acc + (r.available_capacity || 0), 0),
    active_leases_count: data.occupied_count || list.filter((r) => r.active_usage > 0).length,
  };
}

export async function getTask(taskId: string): Promise<SchedulerTaskDetail> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/scheduler/tasks/${encodeURIComponent(taskId)}`);
  if (!res.ok) throw new Error(`Failed to fetch task '${taskId}': ${res.status}`);
  return res.json();
}

export async function getTaskProgress(taskId: string): Promise<TaskProgressData> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/scheduler/tasks/${encodeURIComponent(taskId)}/progress`);
  if (!res.ok) throw new Error(`Failed to fetch progress for task '${taskId}': ${res.status}`);
  return res.json();
}

export async function pauseTask(taskId: string, reason?: string): Promise<{ status: string; task_id: string; message: string }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/scheduler/tasks/${encodeURIComponent(taskId)}/pause`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) throw new Error(`Failed to pause task: ${res.status}`);
  return res.json();
}

export async function resumeTask(taskId: string, reason?: string): Promise<{ status: string; task_id: string; message: string }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/scheduler/tasks/${encodeURIComponent(taskId)}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) throw new Error(`Failed to resume task: ${res.status}`);
  return res.json();
}

export async function cancelTask(taskId: string, reason?: string): Promise<{ status: string; task_id: string; message: string }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/scheduler/tasks/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) throw new Error(`Failed to cancel task: ${res.status}`);
  return res.json();
}

export async function retryTask(taskId: string, reason?: string): Promise<{ status: string; task_id: string; message: string }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/scheduler/tasks/${encodeURIComponent(taskId)}/retry`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
  if (!res.ok) throw new Error(`Failed to retry task: ${res.status}`);
  return res.json();
}

export async function updateTaskPriority(
  taskId: string,
  priority: number | string,
): Promise<{ status: string; task_id: string; priority: number; effective_priority: number }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/scheduler/tasks/${encodeURIComponent(taskId)}/priority`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ priority }),
  });
  if (!res.ok) throw new Error(`Failed to update priority for task '${taskId}': ${res.status}`);
  return res.json();
}

// ============================================================
// M17.9 PERSISTENT MEMORY & USER MEMORY MANAGEMENT REST API
// ============================================================

export interface MemoryItem {
  memory_id: string;
  memory_type: string;
  source: string;
  project_id?: string | null;
  task_id?: string | null;
  trust_level: string;
  taint_status?: string | null;
  content: string;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MemorySearchResult {
  query: string;
  total_found: number;
  results: MemoryItem[];
}

export interface UserPreference {
  key: string;
  value: string;
  project_id?: string | null;
  updated_at?: string | null;
}

export interface UserPreferencesList {
  project_id?: string | null;
  count: number;
  preferences: UserPreference[];
}

export interface MemoryDeleteResult {
  deleted: boolean;
  count: number;
  target: string;
}

export interface MemoryExportResult {
  exported_at: string;
  total_exported: number;
  memories: MemoryItem[];
}

export interface MemoryStats {
  total_memories: number;
  semantic_count: number;
  episodic_count: number;
  preference_count: number;
  project_scoped_count: number;
  global_count: number;
  project_id?: string | null;
}

export async function searchMemories(
  query: string,
  projectId?: string,
  memoryType?: string,
  limit: number = 5
): Promise<MemorySearchResult> {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (projectId) params.set("project_id", projectId);
  if (memoryType && memoryType !== "ALL") params.set("memory_type", memoryType);
  params.set("limit", String(Math.min(limit, 5)));

  const res = await fetch(`${RYVEN_API_BASE_URL}/api/memory/search?${params.toString()}`);
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errorBody.detail || `Search failed with status: ${res.status}`);
  }
  return res.json();
}

export async function getMemory(memoryId: string): Promise<MemoryItem> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/memory/${encodeURIComponent(memoryId)}`);
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errorBody.detail || `Failed to fetch memory: ${res.status}`);
  }
  return res.json();
}

export async function getPreferences(projectId?: string): Promise<UserPreferencesList> {
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  const queryStr = params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/memory/preferences${queryStr}`);
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errorBody.detail || `Failed to fetch preferences: ${res.status}`);
  }
  return res.json();
}

export async function savePreference(
  key: string,
  value: string,
  projectId?: string
): Promise<UserPreference> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/memory/preferences`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ key, value, project_id: projectId || null }),
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errorBody.detail || `Failed to save preference: ${res.status}`);
  }
  return res.json();
}

export async function deletePreference(key: string, projectId?: string): Promise<MemoryDeleteResult> {
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  const queryStr = params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/memory/preferences/${encodeURIComponent(key)}${queryStr}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errorBody.detail || `Failed to delete preference: ${res.status}`);
  }
  return res.json();
}

export async function deleteMemory(memoryId: string): Promise<MemoryDeleteResult> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/memory/${encodeURIComponent(memoryId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errorBody.detail || `Failed to delete memory: ${res.status}`);
  }
  return res.json();
}

export async function deleteTaskMemories(taskId: string): Promise<MemoryDeleteResult> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/memory/task/${encodeURIComponent(taskId)}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errorBody.detail || `Failed to delete task memories: ${res.status}`);
  }
  return res.json();
}

export async function deleteProjectMemories(
  projectId: string,
  confirmationToken?: string
): Promise<MemoryDeleteResult> {
  const headers: Record<string, string> = {};
  if (confirmationToken) {
    headers["X-Confirmation-Token"] = confirmationToken;
  }
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/memory/project/${encodeURIComponent(projectId)}`, {
    method: "DELETE",
    headers,
  });
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    const error = new Error(errorBody.detail || `Failed to delete project memories: ${res.status}`) as Error & { status?: number };
    error.status = res.status;
    throw error;
  }
  return res.json();
}

export async function exportMemories(projectId?: string, limit: number = 100): Promise<MemoryExportResult> {
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  params.set("limit", String(Math.min(limit, 100)));

  const res = await fetch(`${RYVEN_API_BASE_URL}/api/memory/export?${params.toString()}`);
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errorBody.detail || `Failed to export memories: ${res.status}`);
  }
  return res.json();
}

export async function getMemoryStats(projectId?: string): Promise<MemoryStats> {
  const params = new URLSearchParams();
  if (projectId) params.set("project_id", projectId);
  const queryStr = params.toString() ? `?${params.toString()}` : "";
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/memory/stats${queryStr}`);
  if (!res.ok) {
    const errorBody = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errorBody.detail || `Failed to fetch memory stats: ${res.status}`);
  }
  return res.json();
}
