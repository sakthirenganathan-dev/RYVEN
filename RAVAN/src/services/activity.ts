export interface ActivityItem {
  id: string;
  text: string;
  timestamp: number;
  dotColor: string;
}

const STORAGE_KEY = "ryven_session_activity_log";

export function getActivityLog(): ActivityItem[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) {
      // Default session initialization events if new tab
      const initEvents: ActivityItem[] = [
        {
          id: "init-1",
          text: "System diagnostics online",
          timestamp: Date.now() - 1000 * 60 * 3,
          dotColor: "bg-emerald-400",
        },
        {
          id: "init-2",
          text: "Ollama neural core linked",
          timestamp: Date.now() - 1000 * 60 * 2,
          dotColor: "bg-cyan-400",
        },
      ];
      sessionStorage.setItem(STORAGE_KEY, JSON.stringify(initEvents));
      return initEvents;
    }
    return JSON.parse(raw) as ActivityItem[];
  } catch {
    return [];
  }
}

export function logActivityEvent(
  text: string,
  tone: "green" | "cyan" | "amber" | "fuchsia" = "cyan"
) {
  if (typeof window === "undefined") return;
  try {
    const events = getActivityLog();
    const dotColor =
      tone === "green"
        ? "bg-emerald-400"
        : tone === "amber"
        ? "bg-amber-400"
        : tone === "fuchsia"
        ? "bg-fuchsia-400"
        : "bg-cyan-400";

    const newEvent: ActivityItem = {
      id: `${Date.now()}-${Math.random().toString(36).substring(2, 6)}`,
      text,
      timestamp: Date.now(),
      dotColor,
    };

    const updated = [newEvent, ...events].slice(0, 8); // Keep latest 8 items
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(updated));
    window.dispatchEvent(new CustomEvent("ryven_activity_updated"));
  } catch {
    // Ignore storage issues
  }
}

export function formatTimeAgo(timestamp: number): string {
  const diffSec = Math.floor((Date.now() - timestamp) / 1000);
  if (diffSec < 45) return "Just now";
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHours = Math.floor(diffMin / 60);
  if (diffHours < 24) return `${diffHours}h ago`;
  return `${Math.floor(diffHours / 24)}d ago`;
}
