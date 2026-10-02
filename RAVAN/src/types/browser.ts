/**
 * TypeScript types for M14.3 Controlled Computer & Browser Control Engine.
 */

export type BrowserStatus =
  | "IDLE"
  | "INITIALIZING"
  | "ACTIVE"
  | "NAVIGATING"
  | "WAITING_CONFIRMATION"
  | "ERROR"
  | "CLOSED";

export type BrowserActionType =
  | "OPEN"
  | "NAVIGATE"
  | "GET_PAGE"
  | "READ_PAGE"
  | "FIND_ELEMENT"
  | "CLICK"
  | "TYPE_TEXT"
  | "PRESS_KEY"
  | "SCROLL"
  | "GO_BACK"
  | "GO_FORWARD"
  | "REFRESH"
  | "SNAPSHOT"
  | "SUBMIT_FORM"
  | "DOWNLOAD"
  | "CLOSE";

export interface BrowserSnapshot {
  url: string;
  title: string;
  captured_at: string;
  text_content: string;
  headings: string[];
  links: Array<{ href: string; text: string }>;
  interactive_elements_count: number;
}

export interface BrowserActionRecord {
  action_type: BrowserActionType;
  target?: string;
  status: string;
  duration_ms: number;
  timestamp: string;
  requires_confirmation?: boolean;
  details?: Record<string, unknown>;
}

export interface BrowserState {
  session_id: string;
  current_url: string;
  page_title: string;
  tabs: string[];
  active_tab_index: number;
  browser_status: BrowserStatus;
  last_action?: BrowserActionRecord | null;
  last_snapshot?: BrowserSnapshot | null;
  history: string[];
  history_index: number;
  created_at: string;
  updated_at: string;
}
