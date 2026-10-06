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
