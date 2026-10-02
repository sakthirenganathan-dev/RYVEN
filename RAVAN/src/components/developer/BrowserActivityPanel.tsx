import { useEffect, useState } from "react";
import { StatusIndicator } from "@/components/hud/StatusIndicator";
import { fetchBrowserState, confirmBrowserAction } from "@/services/browser";
import type { BrowserState } from "@/types/browser";

interface BrowserActivityPanelProps {
  sessionId?: string;
}

export function BrowserActivityPanel({ sessionId }: BrowserActivityPanelProps) {
  const [state, setState] = useState<BrowserState | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmMsg, setConfirmMsg] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    const poll = async () => {
      try {
        const data = await fetchBrowserState(sessionId);
        if (mounted) setState(data);
      } catch {
        /* backend offline or polling idle */
      }
    };

    poll();
    const interval = setInterval(poll, 2500);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [sessionId]);

  const handleConfirm = async () => {
    const token = (state?.last_action?.details as Record<string, unknown> | undefined)?.["token"];
    if (typeof token !== "string") return;

    setConfirming(true);
    try {
      const res = await confirmBrowserAction(token);
      setConfirmMsg(res.message);
      const updated = await fetchBrowserState(sessionId);
      setState(updated);
    } catch (err) {
      setConfirmMsg(`Confirmation error: ${String(err)}`);
    } finally {
      setConfirming(false);
    }
  };

  const statusTone =
    state?.browser_status === "ACTIVE" || state?.browser_status === "NAVIGATING"
      ? "holo"
      : state?.browser_status === "WAITING_CONFIRMATION"
      ? "signal"
      : state?.browser_status === "ERROR"
      ? "alert"
      : "muted";

  return (
    <div className="space-y-3">
      {/* Header specs */}
      <div className="holo-corners relative border border-border/70 p-3 space-y-2">
          <div className="flex items-center justify-between text-xs tracking-wider">
            <span className="text-muted-foreground uppercase">Browser State</span>
            <StatusIndicator
              label={state?.browser_status || "IDLE"}
              tone={statusTone}
              pulse={state?.browser_status === "NAVIGATING" || state?.browser_status === "WAITING_CONFIRMATION"}
            />
          </div>

          <div className="flex flex-col gap-1 border-t border-border/60 pt-2 text-xs">
            <span className="text-[10px] text-muted-foreground uppercase" style={{ fontFamily: "var(--font-mono)" }}>
              Active URL
            </span>
            <span
              className="text-foreground truncate font-mono text-[11px] select-all"
              title={state?.current_url || "about:blank"}
            >
              {state?.current_url || "about:blank"}
            </span>
          </div>

          {state?.page_title && (
            <div className="flex flex-col gap-1 text-xs">
              <span className="text-[10px] text-muted-foreground uppercase" style={{ fontFamily: "var(--font-mono)" }}>
                Page Title
              </span>
              <span className="text-foreground truncate text-[11px]">
                {state.page_title}
              </span>
            </div>
          )}
        </div>

        {/* Human Confirmation Alert */}
        {state?.browser_status === "WAITING_CONFIRMATION" && (
          <div className="border border-signal/60 bg-signal/10 p-2.5 rounded-sm space-y-2">
            <div className="flex items-center gap-1.5 text-xs text-signal font-semibold">
              <span>⚠ Safety Boundary</span>
            </div>
            <p className="text-[11px] text-foreground/90 font-mono">
              Action targets sensitive or stateful element. Authorization required.
            </p>
            <button
              onClick={handleConfirm}
              disabled={confirming}
              className="w-full py-1 text-xs bg-signal/20 hover:bg-signal/30 text-signal border border-signal/40 rounded transition-colors font-mono tracking-wider uppercase"
            >
              {confirming ? "Authorizing..." : "Authorize Action"}
            </button>
            {confirmMsg && (
              <p className="text-[10px] text-muted-foreground">{confirmMsg}</p>
            )}
          </div>
        )}

        {/* Last Action Details */}
        {state?.last_action && (
          <div className="border border-border/60 p-2.5 space-y-1.5 text-[11px] font-mono">
            <div className="flex items-center justify-between text-[10px] text-muted-foreground uppercase">
              <span>Last Operation</span>
              <span className={state.last_action.status === "SUCCESS" ? "text-holo" : "text-signal"}>
                {state.last_action.status}
              </span>
            </div>
            <div className="text-foreground font-semibold">
              {state.last_action.action_type}
              {state.last_action.target ? ` → ${state.last_action.target}` : ""}
            </div>
            <div className="text-[10px] text-muted-foreground">
              Latency: {state.last_action.duration_ms.toFixed(1)}ms
            </div>
          </div>
        )}

        {/* Snapshot Summary */}
        {state?.last_snapshot && (
          <div className="border border-border/40 p-2 text-[10px] text-muted-foreground space-y-1 font-mono">
            <div className="flex justify-between">
              <span>Headings: {state.last_snapshot.headings.length}</span>
              <span>Links: {state.last_snapshot.links.length}</span>
              <span>Chars: {state.last_snapshot.text_content.length}</span>
            </div>
          </div>
        )}
      </div>
  );
}
