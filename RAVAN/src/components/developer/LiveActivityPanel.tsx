/**
 * RYVEN M14.2 — Live Activity Panel
 * Displays real-time action events from the SSE stream.
 * Shows: current action, status, progress, confirmation waiting, failures.
 */

import { useActionStream } from "@/hooks/useActionStream";
import type { ActionEvent, ActionStatus } from "@/types/actions";

const STATUS_ICON: Record<ActionStatus, string> = {
  PENDING: "○",
  STARTED: "●",
  PROGRESS: "◎",
  WAITING_CONFIRMATION: "⚠",
  COMPLETED: "✓",
  FAILED: "✗",
  CANCELLED: "⊘",
};

const STATUS_COLOR: Record<ActionStatus, string> = {
  PENDING: "text-muted-foreground",
  STARTED: "text-holo animate-pulse",
  PROGRESS: "text-holo animate-pulse",
  WAITING_CONFIRMATION: "text-amber-400 animate-pulse",
  COMPLETED: "text-signal",
  FAILED: "text-red-400",
  CANCELLED: "text-muted-foreground",
};

function formatDuration(ms: number | null): string {
  if (ms === null) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function formatTime(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "";
  }
}

interface ActionRowProps {
  event: ActionEvent;
  isCurrent: boolean;
}

function ActionRow({ event, isCurrent }: ActionRowProps) {
  const icon = STATUS_ICON[event.status] ?? "○";
  const color = STATUS_COLOR[event.status] ?? "text-muted-foreground";

  return (
    <div
      className={`flex items-start gap-2 px-1 py-0.5 rounded transition-all duration-300 ${
        isCurrent ? "bg-holo/10 border border-holo/30" : "border border-transparent"
      }`}
    >
      <span className={`mt-0.5 font-mono text-xs ${color} w-3 shrink-0`}>{icon}</span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center justify-between gap-1">
          <span
            className="truncate text-xs text-foreground"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            {event.title}
          </span>
          {event.duration_ms !== null && (
            <span className="shrink-0 text-[9px] text-muted-foreground">
              {formatDuration(event.duration_ms)}
            </span>
          )}
        </div>
        {event.confirmation_required && event.status === "WAITING_CONFIRMATION" && (
          <div className="mt-0.5 text-[9px] text-amber-400 tracking-widest animate-pulse">
            ⚠ AWAITING CONFIRMATION
          </div>
        )}
        {event.status === "FAILED" && event.error_code && (
          <div className="mt-0.5 text-[9px] text-red-400">{event.error_code}</div>
        )}
        {event.progress !== null && event.progress > 0 && (
          <div className="mt-1 h-px w-full bg-border/40">
            <div
              className="h-px bg-holo transition-all duration-500"
              style={{ width: `${Math.round(event.progress * 100)}%` }}
            />
          </div>
        )}
      </div>
      <span className="shrink-0 text-[9px] text-muted-foreground">
        {formatTime(event.started_at)}
      </span>
    </div>
  );
}

export function LiveActivityPanel() {
  const { events, latestEvent, connected } = useActionStream();

  // Show most recent 10 events for the activity panel
  const visibleEvents = events.slice(-10);
  const currentEvent =
    latestEvent?.status === "STARTED" || latestEvent?.status === "PROGRESS"
      ? latestEvent
      : null;

  return (
    <div className="flex flex-col gap-2">
      {/* Header */}
      <div className="flex items-center justify-between">
        <span
          className="text-[10px] tracking-[0.15em] text-muted-foreground"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          RYVEN · {connected ? "LIVE" : "OFFLINE"}
        </span>
        {connected && (
          <span className="flex items-center gap-1 text-[9px] text-signal">
            <span className="inline-block h-1.5 w-1.5 rounded-full bg-signal animate-pulse" />
            ACTIVE
          </span>
        )}
      </div>

      {/* Current active action callout */}
      {currentEvent && (
        <div className="rounded border border-holo/40 bg-holo/5 px-2 py-1.5">
          <div
            className="text-[10px] text-holo tracking-[0.12em] animate-pulse"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            ● {currentEvent.title}
          </div>
          {currentEvent.step_index !== null && currentEvent.total_steps !== null && (
            <div className="mt-0.5 text-[9px] text-muted-foreground">
              Step {(currentEvent.step_index ?? 0) + 1} of {currentEvent.total_steps}
            </div>
          )}
        </div>
      )}

      {/* Event list */}
      <div className="flex flex-col gap-0.5 max-h-44 overflow-y-auto scrollbar-thin">
        {visibleEvents.length === 0 ? (
          <div className="text-[10px] text-muted-foreground px-1 py-2">
            No activity yet. Execute a command to see live events.
          </div>
        ) : (
          visibleEvents.map((e) => (
            <ActionRow
              key={e.event_id}
              event={e}
              isCurrent={e.event_id === latestEvent?.event_id}
            />
          ))
        )}
      </div>
    </div>
  );
}
