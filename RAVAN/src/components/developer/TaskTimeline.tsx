/**
 * RYVEN M14.2 — Task Timeline Component
 * Compact, expandable timeline of completed task action events.
 * Never exposes secrets.
 */

import { useState } from "react";
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
  STARTED: "text-holo",
  PROGRESS: "text-holo",
  WAITING_CONFIRMATION: "text-amber-400",
  COMPLETED: "text-signal",
  FAILED: "text-red-400",
  CANCELLED: "text-muted-foreground",
};

function formatTime(iso: string | null): string {
  if (!iso) return "--:--:--";
  try {
    return new Date(iso).toLocaleTimeString("en-US", {
      hour12: false,
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return "--:--:--";
  }
}

function formatDuration(ms: number | null): string {
  if (ms === null) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

interface TimelineRowProps {
  event: ActionEvent;
  isExpanded: boolean;
  onToggle: () => void;
}

function TimelineRow({ event, isExpanded, onToggle }: TimelineRowProps) {
  const icon = STATUS_ICON[event.status] ?? "○";
  const color = STATUS_COLOR[event.status] ?? "text-muted-foreground";
  const hasMeta =
    Object.keys(event.safe_metadata).length > 0 || event.error_code || event.confirmation_status;

  return (
    <div className="group">
      <button
        type="button"
        className="flex w-full items-center gap-2 px-1 py-0.5 rounded hover:bg-border/30 transition-colors text-left"
        onClick={hasMeta ? onToggle : undefined}
      >
        {/* Time */}
        <span className="w-14 shrink-0 font-mono text-[9px] text-muted-foreground">
          {formatTime(event.started_at)}
        </span>
        {/* Status icon */}
        <span className={`font-mono text-xs ${color} w-3 shrink-0`}>{icon}</span>
        {/* Title */}
        <span
          className={`flex-1 truncate text-xs ${color}`}
          style={{ fontFamily: "var(--font-mono)" }}
        >
          {event.title}
        </span>
        {/* Duration */}
        {event.duration_ms !== null && (
          <span className="shrink-0 text-[9px] text-muted-foreground">
            {formatDuration(event.duration_ms)}
          </span>
        )}
        {/* Expand chevron */}
        {hasMeta && (
          <span className="shrink-0 text-[9px] text-muted-foreground">
            {isExpanded ? "▲" : "▼"}
          </span>
        )}
      </button>

      {/* Expanded details */}
      {isExpanded && hasMeta && (
        <div className="ml-5 mt-0.5 mb-1 rounded border border-border/40 px-2 py-1 text-[9px] text-muted-foreground font-mono space-y-0.5">
          {event.step_index !== null && event.total_steps !== null && (
            <div>
              Step {(event.step_index ?? 0) + 1} / {event.total_steps}
            </div>
          )}
          {event.confirmation_status && <div>Confirmation: {event.confirmation_status}</div>}
          {event.error_code && <div className="text-red-400">Error: {event.error_code}</div>}
          {event.description && <div className="text-foreground/60">{event.description}</div>}
          {Object.entries(event.safe_metadata)
            .filter(([, v]) => typeof v !== "object" && v !== null && v !== undefined)
            .slice(0, 5)
            .map(([k, v]) => (
              <div key={k}>
                {k}: <span className="text-foreground/80">{String(v)}</span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}

interface TaskTimelineProps {
  events: ActionEvent[];
  taskId?: string;
  goal?: string | undefined;
}

export function TaskTimeline({ events, taskId, goal }: TaskTimelineProps) {
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  if (events.length === 0) {
    return (
      <div className="text-[10px] text-muted-foreground px-1 py-2">
        No events recorded for this task.
      </div>
    );
  }

  const completed = events.filter((e) => e.status === "COMPLETED").length;
  const failed = events.filter((e) => e.status === "FAILED").length;
  const confirmations = events.filter((e) => e.confirmation_required).length;

  return (
    <div className="flex flex-col gap-1">
      {/* Task summary bar */}
      {taskId && (
        <div
          className="flex gap-3 text-[9px] text-muted-foreground pb-1 border-b border-border/40"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          {goal && <span className="truncate flex-1">{goal}</span>}
          <span className="text-signal shrink-0">✓ {completed}</span>
          {failed > 0 && <span className="text-red-400 shrink-0">✗ {failed}</span>}
          {confirmations > 0 && <span className="text-amber-400 shrink-0">⚠ {confirmations}</span>}
        </div>
      )}

      {/* Event rows */}
      <div className="flex flex-col max-h-48 overflow-y-auto scrollbar-thin">
        {events.map((e) => (
          <TimelineRow
            key={e.event_id}
            event={e}
            isExpanded={expandedId === e.event_id}
            onToggle={() => toggleExpand(e.event_id)}
          />
        ))}
      </div>
    </div>
  );
}
