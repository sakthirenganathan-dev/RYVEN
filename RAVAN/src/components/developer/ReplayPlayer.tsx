/**
 * RYVEN M14.2 — Read-Only Replay Player
 *
 * SAFETY CONTRACT:
 * Replay is STRICTLY READ-ONLY. It only reads historical ActionEvent data.
 * It NEVER calls any tool, shell command, Git operation, file modification,
 * deployment command, or any other system action.
 * The "play" button animates through stored events — it does NOT re-execute them.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  createReplay,
  fetchTaskEvents,
  replayNext,
  replayPrevious,
  replayRestart,
} from "@/services/actions";
import { TaskTimeline } from "@/components/developer/TaskTimeline";
import type { ActionEvent, ReplayFrame } from "@/types/actions";

interface ReplayPlayerProps {
  taskId: string;
  goal?: string;
}

export function ReplayPlayer({ taskId, goal }: ReplayPlayerProps) {
  const [allEvents, setAllEvents] = useState<ActionEvent[]>([]);
  const [visibleEvents, setVisibleEvents] = useState<ActionEvent[]>([]);
  const [currentFrame, setCurrentFrame] = useState<ReplayFrame | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  const [isLoaded, setIsLoaded] = useState(false);
  const [totalFrames, setTotalFrames] = useState(0);
  const playIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPlayback = useCallback(() => {
    if (playIntervalRef.current) {
      clearInterval(playIntervalRef.current);
      playIntervalRef.current = null;
    }
    setIsPlaying(false);
  }, []);

  // Load replay session from backend (read-only)
  const loadReplay = useCallback(async () => {
    const [events, session] = await Promise.all([fetchTaskEvents(taskId), createReplay(taskId)]);

    if (!session) {
      setIsLoaded(false);
      return;
    }

    setAllEvents(events);
    setTotalFrames(session.total_frames);
    setVisibleEvents([]);
    setCurrentFrame(session.current_frame);
    setIsLoaded(true);
    setIsPlaying(false);
  }, [taskId]);

  useEffect(() => {
    loadReplay();
    return () => stopPlayback();
  }, [loadReplay, stopPlayback]);

  // Advance one frame forward (read-only backend call)
  const advanceFrame = useCallback(async () => {
    const frame = await replayNext(taskId);
    if (!frame) {
      stopPlayback();
      return;
    }
    setCurrentFrame(frame);
    setVisibleEvents((prev) => {
      const already = prev.some((e) => e.event_id === frame.event.event_id);
      if (already) return prev;
      return [...prev, frame.event];
    });
  }, [taskId, stopPlayback]);

  // ▶ Play — auto-advances frames at 600ms intervals (read-only animation)
  const handlePlay = useCallback(() => {
    if (isPlaying) return;
    setIsPlaying(true);
    playIntervalRef.current = setInterval(() => {
      advanceFrame();
    }, 600);
  }, [isPlaying, advanceFrame]);

  // ⏸ Pause
  const handlePause = useCallback(() => {
    stopPlayback();
  }, [stopPlayback]);

  // ⏭ Next
  const handleNext = useCallback(async () => {
    stopPlayback();
    await advanceFrame();
  }, [stopPlayback, advanceFrame]);

  // ⏮ Previous
  const handlePrevious = useCallback(async () => {
    stopPlayback();
    const frame = await replayPrevious(taskId);
    if (!frame) return;
    setCurrentFrame(frame);
    // Show events up to this frame
    const idx = allEvents.findIndex((e) => e.event_id === frame.event.event_id);
    setVisibleEvents(idx >= 0 ? allEvents.slice(0, idx + 1) : []);
  }, [taskId, stopPlayback, allEvents]);

  // ↻ Restart
  const handleRestart = useCallback(async () => {
    stopPlayback();
    const session = await replayRestart(taskId);
    if (!session) return;
    setVisibleEvents([]);
    setCurrentFrame(session.current_frame);
  }, [taskId, stopPlayback]);

  if (!isLoaded) {
    return <div className="text-[10px] text-muted-foreground px-1 py-2">Loading replay…</div>;
  }

  const frameIndex = currentFrame?.frame_index ?? 0;
  const progress = totalFrames > 0 ? frameIndex / totalFrames : 0;

  return (
    <div className="flex flex-col gap-2">
      {/* Safety badge */}
      <div
        className="flex items-center gap-1 text-[9px] text-muted-foreground"
        style={{ fontFamily: "var(--font-mono)" }}
      >
        <span className="text-amber-400">⚠</span>
        READ-ONLY REPLAY — no system actions executed
      </div>

      {/* Progress bar */}
      <div className="h-0.5 w-full rounded-full bg-border/50">
        <div
          className="h-0.5 rounded-full bg-holo transition-all duration-300"
          style={{ width: `${Math.round(progress * 100)}%` }}
        />
      </div>

      {/* Frame counter */}
      <div className="text-[9px] text-muted-foreground" style={{ fontFamily: "var(--font-mono)" }}>
        Frame {frameIndex + 1} / {totalFrames}
      </div>

      {/* Controls */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleRestart}
          className="rounded border border-border/60 px-2 py-0.5 text-[10px] text-muted-foreground hover:text-foreground hover:border-holo/50 transition-colors"
          title="Restart"
        >
          ↻
        </button>
        <button
          type="button"
          onClick={handlePrevious}
          className="rounded border border-border/60 px-2 py-0.5 text-[10px] text-muted-foreground hover:text-foreground hover:border-holo/50 transition-colors"
          title="Previous"
        >
          ⏮
        </button>
        {isPlaying ? (
          <button
            type="button"
            onClick={handlePause}
            className="rounded border border-holo/60 px-3 py-0.5 text-[10px] text-holo hover:bg-holo/10 transition-colors"
            title="Pause"
          >
            ⏸ Pause
          </button>
        ) : (
          <button
            type="button"
            onClick={handlePlay}
            className="rounded border border-holo/60 px-3 py-0.5 text-[10px] text-holo hover:bg-holo/10 transition-colors"
            title="Play"
          >
            ▶ Play
          </button>
        )}
        <button
          type="button"
          onClick={handleNext}
          className="rounded border border-border/60 px-2 py-0.5 text-[10px] text-muted-foreground hover:text-foreground hover:border-holo/50 transition-colors"
          title="Next"
        >
          ⏭
        </button>
      </div>

      {/* Timeline showing visible replay frames */}
      <div className="border-t border-border/40 pt-2">
        <TaskTimeline events={visibleEvents} taskId={taskId} goal={goal} />
      </div>
    </div>
  );
}
