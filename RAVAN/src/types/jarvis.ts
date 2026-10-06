export type CoreState =
  | "boot"
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "executing"
  | "speaking"
  | "interrupted"
  | "error";

/**
 * Spatial HUD viewport modes.
 */
export type HudMode = "core" | "system" | "memory" | "developer" | "automation" | "chat";

/**
 * Conversational transcript line between user and RYVEN.
 */
export interface TranscriptLine {
  id: string;
  speaker: "user" | "ryven" | "jarvis";
  text: string;
  timestamp?: number;
}

/**
 * Granular step within an executed command sequence.
 */
export interface CommandStep {
  label: string;
  detail?: string;
}

/**
 * Standard command execution result.
 */
export interface CommandResult {
  reply: string;
  mode: HudMode;
  steps: CommandStep[];
  executionTimeMs?: number;
}

/**
 * Real-time system telemetry metric.
 */
export interface SystemMetric {
  key: string;
  label: string;
  value: number;
  display: string;
  unit?: string;
}

/**
 * Node within the neural memory lattice graph.
 */
export interface MemoryNode {
  id: string;
  label: string;
  x: number;
  y: number;
  items: string[];
}

/**
 * Developer project workspace status.
 */
export interface DevProject {
  name: string;
  status: "RUNNING" | "READY" | "BUILDING";
  stack: string;
  branch: string;
  load: number;
}

/**
 * Autonomous background routine or protocol.
 */
export interface AutomationRoutine {
  id: string;
  name: string;
  trigger: string;
  status: "RUNNING" | "READY" | "SCHEDULED";
  protocol: string;
  interval?: string;
  lastExecution?: string;
}

/* =========================================================================
 * Antigravity Python Backend Integration Contracts
 * =========================================================================
 * Clean interfaces prepared for future communication with the Antigravity
 * Python RYVEN backend (REST / WebSocket / Server-Sent Events).
 * ========================================================================= */

export interface AntigravityBridgeConfig {
  backendUrl: string;
  wsUrl: string;
  status: "connected" | "connecting" | "offline" | "mock";
  apiVersion: string;
  latencyMs?: number;
}

export interface VoiceStreamPayload {
  sampleRate: number;
  format: "pcm16" | "wav" | "webm";
  audioChunk?: ArrayBuffer | string;
  isFinal: boolean;
}

export interface AntigravityCommandRequest {
  query: string;
  mode?: HudMode;
  context?: {
    currentMode: HudMode;
    clientTimestamp: number;
    activeWorkspace?: string;
  };
}

export interface AntigravityCommandResponse {
  reply: string;
  mode: HudMode;
  steps: CommandStep[];
  telemetryUpdate?: SystemMetric[];
  memoryDelta?: Partial<MemoryNode>[];
  ttsAudioUrl?: string;
}
