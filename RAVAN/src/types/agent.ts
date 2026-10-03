export type AgentStatus =
  | "IDLE"
  | "UNDERSTANDING"
  | "PLANNING"
  | "VALIDATING"
  | "EXECUTING"
  | "OBSERVING"
  | "WAITING_CONFIRMATION"
  | "RETRYING"
  | "VERIFYING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED"
  | "PAUSED";

export type StepStatus =
  "PENDING" | "RUNNING" | "OBSERVING" | "WAITING_CONFIRMATION" | "COMPLETED" | "FAILED" | "SKIPPED";

export interface TaskStep {
  step_id: string;
  order: number;
  name: string;
  capability: string;
  tool_name: string;
  arguments: Record<string, unknown>;
  description: string;
  requires_confirmation: boolean;
  status: StepStatus;
  retry_count: number;
  max_retries: number;
  observation?: {
    summary: string;
    success: boolean;
  };
  error?: string;
}

export interface AgentPlan {
  plan_id: string;
  goal: string;
  capabilities_required: string[];
  steps: TaskStep[];
}

export interface AgentState {
  task_id: string;
  user_goal: string;
  status: AgentStatus;
  current_plan?: AgentPlan | null;
  current_step_index: number;
  current_observation?: {
    summary: string;
    success: boolean;
    timestamp: string;
  } | null;
  retry_count: number;
  confirmation_token?: string | null;
  confirmation_message?: string | null;
  created_at: string;
  updated_at: string;
}
