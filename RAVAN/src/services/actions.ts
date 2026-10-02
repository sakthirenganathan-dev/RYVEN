/**
 * RYVEN M14.2 — Action Engine Service Layer
 * Client for the backend SSE live stream and action REST API.
 */

import type { ActionEvent, ReplayFrame, ReplaySession, TaskSummary } from "@/types/actions";
import { RYVEN_API_BASE_URL } from "@/services/jarvis";

// ─────────────────────────────────────────────────────────────────────────────
// REST API calls
// ─────────────────────────────────────────────────────────────────────────────

export async function fetchRecentEvents(limit = 50): Promise<ActionEvent[]> {
  try {
    const res = await fetch(`${RYVEN_API_BASE_URL}/api/actions/recent?limit=${limit}`);
    if (!res.ok) return [];
    return (await res.json()) as ActionEvent[];
  } catch {
    return [];
  }
}

export async function fetchTaskEvents(taskId: string): Promise<ActionEvent[]> {
  try {
    const res = await fetch(`${RYVEN_API_BASE_URL}/api/actions/task/${encodeURIComponent(taskId)}`);
    if (!res.ok) return [];
    return (await res.json()) as ActionEvent[];
  } catch {
    return [];
  }
}

export async function fetchTaskSummary(
  taskId: string,
  goal = "",
): Promise<TaskSummary | null> {
  try {
    const url = `${RYVEN_API_BASE_URL}/api/actions/task/${encodeURIComponent(taskId)}/summary?goal=${encodeURIComponent(goal)}`;
    const res = await fetch(url);
    if (!res.ok) return null;
    return (await res.json()) as TaskSummary;
  } catch {
    return null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Replay API (read-only)
// ─────────────────────────────────────────────────────────────────────────────

export async function createReplay(taskId: string): Promise<ReplaySession | null> {
  try {
    const res = await fetch(
      `${RYVEN_API_BASE_URL}/api/actions/task/${encodeURIComponent(taskId)}/replay`,
      { method: "POST" },
    );
    if (!res.ok) return null;
    return (await res.json()) as ReplaySession;
  } catch {
    return null;
  }
}

export async function replayNext(taskId: string): Promise<ReplayFrame | null> {
  try {
    const res = await fetch(
      `${RYVEN_API_BASE_URL}/api/actions/task/${encodeURIComponent(taskId)}/replay/next`,
      { method: "POST" },
    );
    if (!res.ok) return null;
    const data = await res.json();
    if ("error" in data) return null;
    return data as ReplayFrame;
  } catch {
    return null;
  }
}

export async function replayPrevious(taskId: string): Promise<ReplayFrame | null> {
  try {
    const res = await fetch(
      `${RYVEN_API_BASE_URL}/api/actions/task/${encodeURIComponent(taskId)}/replay/previous`,
      { method: "POST" },
    );
    if (!res.ok) return null;
    const data = await res.json();
    if ("error" in data) return null;
    return data as ReplayFrame;
  } catch {
    return null;
  }
}

export async function replayRestart(taskId: string): Promise<ReplaySession | null> {
  try {
    const res = await fetch(
      `${RYVEN_API_BASE_URL}/api/actions/task/${encodeURIComponent(taskId)}/replay/restart`,
      { method: "POST" },
    );
    if (!res.ok) return null;
    return (await res.json()) as ReplaySession;
  } catch {
    return null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// SSE subscription factory
// Returns an EventSource and a cleanup function.
// Reconnect-safe: EventSource automatically reconnects.
// Disconnect-safe: cleanup() removes listener without erroring.
// ─────────────────────────────────────────────────────────────────────────────

export function createActionStream(
  onEvent: (event: ActionEvent) => void,
  onConnect?: () => void,
  onError?: () => void,
): () => void {
  const url = `${RYVEN_API_BASE_URL}/api/actions/stream`;
  let es: EventSource | null = null;
  let closed = false;

  function connect() {
    if (closed) return;
    try {
      es = new EventSource(url);
      es.onmessage = (msgEvent) => {
        try {
          const data = JSON.parse(msgEvent.data as string) as Record<string, unknown>;
          // Skip the connection confirmation message
          if (data["type"] === "connected") {
            onConnect?.();
            return;
          }
          onEvent(data as unknown as ActionEvent);
        } catch {
          // Malformed event — ignore
        }
      };
      es.onerror = () => {
        onError?.();
        // EventSource will auto-reconnect; no manual retry needed
      };
    } catch {
      onError?.();
    }
  }

  connect();

  return () => {
    closed = true;
    es?.close();
    es = null;
  };
}
