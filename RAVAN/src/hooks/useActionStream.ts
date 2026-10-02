/**
 * RYVEN M14.2 — useActionStream hook
 * Manages a live SSE connection to the RYVEN action event bus.
 * Automatically reconnects. Cleans up on unmount.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { createActionStream, fetchRecentEvents } from "@/services/actions";
import type { ActionEvent } from "@/types/actions";

const MAX_LOCAL_EVENTS = 100; // keep last 100 events in component state

export interface UseActionStreamResult {
  events: ActionEvent[];
  latestEvent: ActionEvent | null;
  connected: boolean;
  clear: () => void;
}

export function useActionStream(): UseActionStreamResult {
  const [events, setEvents] = useState<ActionEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const cleanupRef = useRef<(() => void) | null>(null);

  const addEvent = useCallback((event: ActionEvent) => {
    setEvents((prev) => {
      const next = [...prev, event];
      // Keep bounded — drop oldest when over limit
      return next.length > MAX_LOCAL_EVENTS
        ? next.slice(next.length - MAX_LOCAL_EVENTS)
        : next;
    });
  }, []);

  const clear = useCallback(() => setEvents([]), []);

  useEffect(() => {
    let mounted = true;

    // Pre-load recent events from REST endpoint on mount
    fetchRecentEvents(50).then((initial) => {
      if (mounted && initial.length > 0) {
        setEvents(initial.slice(-MAX_LOCAL_EVENTS));
      }
    });

    // Subscribe to live SSE stream
    const cleanup = createActionStream(
      (event) => {
        if (mounted) addEvent(event);
      },
      () => {
        if (mounted) setConnected(true);
      },
      () => {
        if (mounted) setConnected(false);
      },
    );

    cleanupRef.current = cleanup;

    return () => {
      mounted = false;
      cleanup();
    };
  }, [addEvent]);

  const latestEvent = events.length > 0 ? (events[events.length - 1] ?? null) : null;

  return { events, latestEvent, connected, clear };
}
