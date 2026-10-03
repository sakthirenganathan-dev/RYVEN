/**
 * RYVEN 3.0 — TypeScript types for the Unified Internet Agent Engine.
 */

export type InternetStatus =
  | "IDLE"
  | "PLANNING"
  | "EXECUTING"
  | "WAITING_CONFIRMATION"
  | "WAITING_AUTHENTICATION"
  | "VERIFYING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export interface SearchResultItem {
  title: string;
  url: string;
  snippet: string;
  domain: string;
  relevance_score: number;
}

export interface WebSourceItem {
  title: string;
  url: string;
  domain: string;
  headings: string[];
  excerpt: string;
}

export interface WebResearchReport {
  topic: string;
  summary: string;
  key_findings: string[];
  sources: WebSourceItem[];
  created_at: string;
}

export interface AuthSessionInfo {
  session_id: string;
  task_id: string;
  target_service: string;
  login_url: string;
  auth_status: "REQUIRED" | "AUTHENTICATING" | "AUTHENTICATED" | "FAILED";
  prompt_message: string;
  resume_token: string;
  detected_at: string;
}

export interface InternetAgentState {
  status: InternetStatus;
  current_task_id?: string | null;
  current_goal: string;
  current_url: string;
  page_title: string;
  active_action?: string | null;
  last_search_query?: string | null;
  last_research_result?: WebResearchReport | null;
  active_auth_session?: AuthSessionInfo | null;
  pending_confirmation_token?: string | null;
  confirmation_message?: string | null;
  history_urls: string[];
  task_progress_pct: number;
  updated_at: string;
}
