/**
 * RYVEN M14.2 — Action Event TypeScript types
 * Mirrors the Python ActionEvent model.
 * No credentials, tokens, or secrets are ever stored in events.
 */

export type ActionStatus =
  | "PENDING"
  | "STARTED"
  | "PROGRESS"
  | "WAITING_CONFIRMATION"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type ActionType =
  | "TASK_STARTED"
  | "TASK_COMPLETED"
  | "TASK_FAILED"
  | "TASK_CANCELLED"
  | "TASK_PAUSED"
  | "TASK_RESUMED"
  | "TASK_PLANNING"
  | "TOOL_EXECUTE"
  | "SCAN_PROJECT"
  | "GRAPH_QUERY"
  | "PLAN_MODIFICATION"
  | "APPLY_MODIFICATION"
  | "BUILD"
  | "TEST"
  | "QUALITY_GATE"
  | "GIT_DIFF"
  | "GIT_COMMIT"
  | "GIT_PUSH"
  | "DEPLOY"
  | "HEALTH_CHECK"
  | "FINAL_REPORT"
  | "CONFIRMATION_REQUESTED"
  | "CONFIRMATION_RECEIVED"
  | "RETRY"
  | "UNKNOWN";

export interface ActionEvent {
  event_id: string;
  task_id: string | null;
  action_id: string;
  parent_action_id: string | null;
  action_type: ActionType;
  status: ActionStatus;
  title: string;
  description: string;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  progress: number | null;
  step_index: number | null;
  total_steps: number | null;
  confirmation_required: boolean;
  confirmation_status: string | null;
  safe_metadata: Record<string, unknown>;
  error_code: string | null;
}

export interface TaskSummary {
  task_id: string;
  goal: string;
  status: ActionStatus;
  started_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
  total_actions: number;
  successful_actions: number;
  failed_actions: number;
  cancelled_actions: number;
  confirmations_required: number;
  build_result: string | null;
  test_result: string | null;
  quality_gate_result: string | null;
  deployment_result: string | null;
  health_result: string | null;
}

export interface ReplayFrame {
  frame_index: number;
  total_frames: number;
  event: ActionEvent;
}

export interface ReplaySession {
  task_id: string;
  total_frames: number;
  current_index: number;
  is_playing: boolean;
  current_frame: ReplayFrame | null;
  replay_mode: "READ_ONLY";
  safety_note: string;
}
