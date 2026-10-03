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
