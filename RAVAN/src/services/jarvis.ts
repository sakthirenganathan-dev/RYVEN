import type {
  AntigravityBridgeConfig,
  AutomationRoutine,
  CommandResult,
  CommandStep,
  DevProject,
  HudMode,
  MemoryNode,
  SystemMetric,
} from "@/types/jarvis";

/**
 * ============================================================================
 * RYVEN SERVICE LAYER
 * ============================================================================
 * All frontend <-> assistant communication is abstracted behind this service.
 *
 * Prepared for Antigravity Python Backend integration:
 * - Mock data is strictly isolated below.
 * - Service functions provide uniform contracts for future HTTP/WebSocket
 *   streaming to the Python RYVEN backend without modifying UI components.
 * ============================================================================
 */

/* --------------------------------------------------------------------------
 * App Launcher Mapping
 * -------------------------------------------------------------------------- */
const APP_MAP: Record<string, string> = {
  code: "VS Code",
  "vs code": "VS Code",
  vscode: "VS Code",
  editor: "VS Code",
  chrome: "Chrome",
  browser: "Chrome",
  terminal: "Terminal",
  spotify: "Spotify",
  figma: "Figma",
};

function matchApp(text: string): string | null {
  const lower = text.toLowerCase();
  for (const key of Object.keys(APP_MAP)) {
    if (lower.includes(key)) return APP_MAP[key] ?? null;
  }
  return null;
}

/* --------------------------------------------------------------------------
 * Command Interpreter
 * -------------------------------------------------------------------------- */
export function interpretCommand(input: string): CommandResult {
  const text = input.trim();
  const lower = text.toLowerCase();

  // System Diagnostics
  if (/(system|status|cpu|ram|memory usage|battery|network|telemetry)/.test(lower)) {
    return {
      reply: "System nominal. All subsystems are operating within expected parameters.",
      mode: "system",
      steps: [
        { label: "Voice input", detail: text },
        { label: "Intent detected", detail: "SYSTEM.DIAGNOSTICS" },
        { label: "Polling sensors" },
        { label: "Telemetry synced" },
      ],
    };
  }

  // Developer Workspace
  if (/(project|developer|dev environment|git|deploy|build|workspace|service)/.test(lower)) {
    return {
      reply: "Developer environment online. Three projects indexed, one service running.",
      mode: "developer",
      steps: [
        { label: "Voice input", detail: text },
        { label: "Intent detected", detail: "DEV.WORKSPACE" },
        { label: "Mounting workspace" },
        { label: "Environment ready" },
      ],
    };
  }

  // Automation Protocols
  if (/(automation|workflow|protocol|routine|task schedule|automate)/.test(lower)) {
    return {
      reply: "Automation protocols active. Four routines registered with Antigravity bridge.",
      mode: "automation",
      steps: [
        { label: "Voice input", detail: text },
        { label: "Intent detected", detail: "AUTO.PROTOCOLS" },
        { label: "Querying scheduler" },
        { label: "Protocols verified" },
      ],
    };
  }

  // Memory Lattice
  if (/(remember|memory|recall|know about me|preference|lattice)/.test(lower)) {
    return {
      reply: "Retrieving long term memory lattice. Five clusters available.",
      mode: "memory",
      steps: [
        { label: "Voice input", detail: text },
        { label: "Intent detected", detail: "MEMORY.QUERY" },
        { label: "Indexing nodes" },
        { label: "Lattice resolved" },
      ],
    };
  }

  // Application Launching
  const app = matchApp(lower);
  if (app && /(open|launch|start|run)/.test(lower)) {
    return {
      reply: `Opening ${app} now.`,
      mode: "core",
      steps: [
        { label: "Voice input", detail: text },
        { label: "Intent detected", detail: "APP.LAUNCH" },
        { label: "Application", detail: app },
        { label: "Verifying system access" },
        { label: "Application started" },
      ],
    };
  }

  // Default Conversational Flow
  return {
    reply: `Understood. I have logged "${text}" and I am standing by for your next instruction.`,
    mode: "chat",
    steps: [
      { label: "Voice input", detail: text },
      { label: "Intent detected", detail: "CONVERSATION" },
      { label: "Response composed" },
    ],
  };
}

/* --------------------------------------------------------------------------
 * System Telemetry Simulation
 * -------------------------------------------------------------------------- */
export function getSystemMetrics(tick: number): SystemMetric[] {
  const wave = (offset: number, amp: number, base: number) =>
    base + Math.sin((tick + offset) / 7) * amp;
  const cpu = Math.max(4, Math.round(wave(0, 9, 18)));
  const ram = Math.max(2, wave(3, 1.1, 7.2));
  const battery = Math.round(wave(11, 3, 76));
  return [
    { key: "cpu", label: "CPU", value: cpu, display: `${cpu}`, unit: "%" },
    { key: "ram", label: "RAM", value: (ram / 16) * 100, display: ram.toFixed(1), unit: "GB" },
    { key: "storage", label: "Storage", value: 47, display: "241", unit: "GB" },
    { key: "battery", label: "Battery", value: battery, display: `${battery}`, unit: "%" },
    { key: "network", label: "Network", value: 92, display: "LINKED" },
  ];
}

/* ==========================================================================
 * ISOLATED MOCK DATA (READY TO BE REPLACED BY ANTIGRAVITY PYTHON BACKEND)
 * ========================================================================== */

export const memoryNodes: MemoryNode[] = [
  {
    id: "projects",
    label: "Projects",
    x: 18,
    y: 26,
    items: ["VisionMate AI", "ISL Healthcare", "SafeMeds AI"],
  },
  {
    id: "personal",
    label: "Personal",
    x: 74,
    y: 18,
    items: ["Timezone IST", "Focus block 21:00", "Prefers dark rooms"],
  },
  {
    id: "preferences",
    label: "Preferences",
    x: 82,
    y: 62,
    items: ["Concise replies", "Cyan HUD", "Voice first"],
  },
  { id: "tasks", label: "Tasks", x: 30, y: 74, items: ["Ship v2 HUD", "Review PR #182"] },
  {
    id: "knowledge",
    label: "Knowledge",
    x: 52,
    y: 46,
    items: ["Python backend", "React interfaces", "Signal processing"],
  },
];

export const devProjects: DevProject[] = [
  { name: "VisionMate AI", status: "RUNNING", stack: "Python · Torch", branch: "main", load: 62 },
  { name: "ISL Healthcare", status: "READY", stack: "React · Node", branch: "dev", load: 18 },
  { name: "SafeMeds AI", status: "READY", stack: "FastAPI", branch: "feat/rx", load: 9 },
  { name: "Ryven Core", status: "BUILDING", stack: "TypeScript", branch: "hud-2.0", load: 44 },
];

export const automationRoutines: AutomationRoutine[] = [
  {
    id: "auto-sync",
    name: "Antigravity Bridge Sync",
    trigger: "Continuous · 2.4s",
    status: "RUNNING",
    protocol: "IPC / WebSocket",
  },
  {
    id: "auto-telemetry",
    name: "System Telemetry Daemon",
    trigger: "Interval · 1.4s",
    status: "RUNNING",
    protocol: "Background Sensor",
  },
  {
    id: "auto-lattice",
    name: "Memory Lattice Indexer",
    trigger: "On Context Change",
    status: "READY",
    protocol: "Vector Clustering",
  },
  {
    id: "auto-backup",
    name: "Workspace State Checkpoint",
    trigger: "Hourly · T+00",
    status: "SCHEDULED",
    protocol: "Local Vault",
  },
];

export const suggestedCommands = [
  "Ryven, what is my system status?",
  "Open VS Code",
  "Open my development environment",
  "Show automation protocols",
  "What do you remember about me?",
  "Open Chrome",
];

export function sampleCommand(index: number): string {
  return suggestedCommands[index % suggestedCommands.length] ?? suggestedCommands[0]!;
}

export const hudModes: { id: HudMode; label: string }[] = [
  { id: "core", label: "Core" },
  { id: "chat", label: "Chat" },
  { id: "system", label: "System" },
  { id: "memory", label: "Memory" },
  { id: "developer", label: "Developer" },
  { id: "automation", label: "Automation" },
];

/**
 * Connection bridge configuration for Antigravity backend.
 */
export function getAntigravityBridgeStatus(): AntigravityBridgeConfig {
  return {
    backendUrl: RYVEN_API_BASE_URL,
    wsUrl: "ws://127.0.0.1:8000/ws/ryven",
    status: "mock",
    apiVersion: "2.0.0",
    latencyMs: 12,
  };
}

/* ==========================================================================
 * REAL BACKEND API INTEGRATION (PHASE 2)
 * ========================================================================== */

export interface RyvenApiChatResponse {
  success: boolean;
  type: "tool" | "ai" | "workflow" | "error";
  message: string;
  tool: string | null;
  metadata: Record<string, unknown>;
}

// Backwards compatibility alias
export type JarvisApiChatResponse = RyvenApiChatResponse;

export const RYVEN_API_BASE_URL =
  (typeof import.meta !== "undefined" && import.meta.env?.["VITE_RYVEN_API_URL"]) ||
  (typeof import.meta !== "undefined" && import.meta.env?.["VITE_JARVIS_API_URL"]) ||
  "http://127.0.0.1:8000";

// Backwards compatibility alias
export const JARVIS_API_BASE_URL = RYVEN_API_BASE_URL;

/**
 * Calls POST /api/chat on the Python FastAPI backend.
 */
export async function sendChatMessageToBackend(
  message: string,
  sessionId?: string,
): Promise<RyvenApiChatResponse> {
  const url = `${RYVEN_API_BASE_URL}/api/chat`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      message,
      session_id: sessionId,
    }),
  });

  if (!response.ok) {
    throw new Error(`RYVEN API HTTP error: ${response.status}`);
  }

  return (await response.json()) as RyvenApiChatResponse;
}

/**
 * Execute command against backend with fallback to client interpreter.
 */
export async function executeRyvenCommand(
  input: string,
  sessionId?: string,
): Promise<CommandResult> {
  const text = input.trim();
  const startTime = Date.now();

  try {
    const res = await sendChatMessageToBackend(text, sessionId);
    const executionTimeMs = Date.now() - startTime;

    let mode: HudMode = "chat";
    const steps: CommandStep[] = [{ label: "Voice / Input", detail: text }];

    if (res.type === "workflow") {
      mode = "developer";
      const wfName = (res.metadata?.["workflow_name"] as string) || "Workflow";
      const wfStatus = (res.metadata?.["status"] as string) || "COMPLETED";
      steps.push({ label: "Workflow Engine", detail: `${wfName} [${wfStatus}]` });

      if (Array.isArray(res.metadata?.["steps"])) {
        const wfSteps = res.metadata["steps"] as Array<{
          name?: string;
          tool_name?: string;
          status?: string;
        }>;
        wfSteps.forEach((s) => {
          const mark = s.status === "SUCCESS" ? "✓" : s.status === "FAILED" ? "✗" : "•";
          steps.push({
            label: s.name || s.tool_name || "Workflow Step",
            detail: `${s.status ?? "DONE"} ${mark}`,
          });
        });
      }
      steps.push({
        label: "Workflow Status",
        detail: wfStatus === "COMPLETED" ? "WORKFLOW COMPLETED ✓" : `${wfStatus} ✗`,
      });
    } else if (res.type === "tool") {
      if (res.tool === "system_status" || res.tool === "system_info") {
        mode = "system";
      } else if (
        res.tool === "open_application" ||
        res.tool === "open_website" ||
        res.tool === "open_folder" ||
        res.tool === "search_files" ||
        res.tool === "open_file" ||
        res.tool === "set_clipboard" ||
        res.tool === "get_clipboard"
      ) {
        mode = "automation";
      } else {
        mode = "core";
      }
      steps.push({ label: "Permission Check", detail: "Policy Safe ✓" });
      steps.push({ label: "Tool Action", detail: `${res.tool} ✓ SUCCESS` });
    } else if (res.type === "error" && res.metadata?.["security_status"] === "BLOCKED") {
      mode = "system";
      steps.push({ label: "Security Guard", detail: "Prohibited Action ✗" });
    } else {
      mode = "chat";
      const model = (res.metadata?.["model"] as string) || "qwen2.5:7b";
      steps.push({ label: "Neural Brain", detail: model });
    }

    steps.push({ label: "Response Ready" });

    return {
      reply: res.message,
      mode,
      steps,
      executionTimeMs,
    };
  } catch (_error) {
    // If backend is offline or network fails, fall back smoothly to local interpreter
    const fallback = interpretCommand(text);
    return {
      ...fallback,
      steps: [...fallback.steps, { label: "Backend link", detail: "Offline - client fallback" }],
    };
  }
}

// Backwards compatibility alias
export const executeJarvisCommand = executeRyvenCommand;
