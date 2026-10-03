/**
 * RYVEN 3.0 — Agent Control Plane Service
 */

import { RYVEN_API_BASE_URL } from "./jarvis";
import type { AgentState } from "@/types/agent";

export interface AgentTaskSummary {
  task_id: string;
  user_goal: string;
  status: string;
  steps_total: number;
  current_step_index: number;
}

export async function fetchAgentState(taskId?: string): Promise<AgentState> {
  const url = new URL(`${RYVEN_API_BASE_URL}/api/agent/state`);
  if (taskId) {
    url.searchParams.set("task_id", taskId);
  }

  const res = await fetch(url.toString());
  if (!res.ok) {
    throw new Error(`Failed to fetch agent state: ${res.status}`);
  }
  return res.json();
}

export async function fetchAgentTasks(): Promise<AgentTaskSummary[]> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/agent/tasks`);
  if (!res.ok) {
    throw new Error(`Failed to fetch agent tasks: ${res.status}`);
  }
  return res.json();
}

export async function confirmAgentStep(
  token: string,
): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/agent/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!res.ok) {
    throw new Error(`Failed to confirm agent step: ${res.status}`);
  }
  return res.json();
}

export async function cancelAgentTask(
  taskId: string,
): Promise<{ success: boolean; task_id: string }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/agent/cancel`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task_id: taskId }),
  });
  if (!res.ok) {
    throw new Error(`Failed to cancel agent task: ${res.status}`);
  }
  return res.json();
}
