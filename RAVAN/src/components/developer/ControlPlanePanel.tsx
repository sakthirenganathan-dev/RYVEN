import { useEffect, useState } from "react";
import {
  executeControlGoal,
  fetchControlStatus,
  confirmControl,
  cancelControl,
  fetchRunningApplications,
  type ControlResultData,
} from "@/services/agents";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

export function ControlPlanePanel() {
  const [goalInput, setGoalInput] = useState("");
  const [autoConfirm, setAutoConfirm] = useState(false);
  const [activeControl, setActiveControl] = useState<ControlResultData | null>(null);
  const [runningApps, setRunningApps] = useState<Array<{ name: string; pid: number; title: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  // Poll running applications periodically
  useEffect(() => {
    let mounted = true;
    const loadApps = async () => {
      try {
        const data = await fetchRunningApplications();
        if (mounted) setRunningApps(data.processes);
      } catch {
        // quiet ignore
      }
    };
    loadApps();
    const timer = setInterval(loadApps, 8000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  // Poll active control if running or waiting
  useEffect(() => {
    if (!activeControl || !["PLANNING", "OBSERVING", "AUTHORIZING", "EXECUTING", "WAITING_CONFIRMATION", "RECOVERING", "VERIFYING"].includes(activeControl.status)) {
      return;
    }
    let mounted = true;
    const interval = setInterval(async () => {
      try {
        const updated = await fetchControlStatus(activeControl.control_id);
        if (mounted) setActiveControl(updated);
      } catch (err) {
        console.warn("Failed to poll control status:", err);
      }
    }, 2000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [activeControl]);

  const handleExecute = async () => {
    if (!goalInput.trim() || loading) return;
    setLoading(true);
    setActionMsg(null);
    try {
      const res = await executeControlGoal(goalInput.trim(), undefined, autoConfirm);
      setActiveControl(res);
      setGoalInput("");
    } catch (err) {
      setActionMsg(`Dispatch failed: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  const handleConfirmAction = async () => {
    if (!activeControl || !activeControl.confirmation_token) return;
    setLoading(true);
    setActionMsg(null);
    try {
      const res = await confirmControl(activeControl.control_id, activeControl.confirmation_token);
      setActiveControl(res);
      setActionMsg("Action confirmed and execution resumed.");
    } catch (err) {
      setActionMsg(`Confirm failed: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  const handleCancelAction = async () => {
    if (!activeControl) return;
    setLoading(true);
    setActionMsg(null);
    try {
      const res = await cancelControl(activeControl.control_id, "User requested cancellation");
      setActiveControl(res);
      setActionMsg("Control operation cancelled.");
    } catch (err) {
      setActionMsg(`Cancel failed: ${String(err)}`);
    } finally {
      setLoading(false);
    }
  };

  const statusTone = (status?: string) => {
    switch (status) {
      case "EXECUTING":
      case "OBSERVING":
      case "PLANNING":
        return "signal";
      case "WAITING_CONFIRMATION":
      case "RECOVERING":
        return "alert";
      case "COMPLETED":
        return "holo";
      case "FAILED":
      case "CANCELLED":
        return "alert";
      default:
        return "muted";
    }
  };

  return (
    <div className="space-y-3 text-xs" style={{ fontFamily: "var(--font-mono)" }}>
      {/* Title & Badge */}
      <div className="flex items-center justify-between border-b border-border/40 pb-1.5">
        <span className="text-[10px] tracking-wider text-muted-foreground uppercase">
          M17.0 Unified Control Plane
        </span>
        <span className="text-[9px] px-1.5 py-0.5 rounded bg-holo/10 text-holo border border-holo/30">
          OS + WEB LIVE
        </span>
      </div>

      {/* Goal Dispatcher */}
      <div className="space-y-1.5">
        <textarea
          value={goalInput}
          onChange={(e) => setGoalInput(e.target.value)}
          placeholder="Enter natural language PC or web goal (e.g., 'Inspect Chrome and summarize latest git log')..."
          rows={2}
          className="w-full text-[11px] p-2 rounded bg-background/50 border border-border/40 text-foreground placeholder:text-muted-foreground/60 resize-none focus:outline-none focus:border-holo/60 transition-colors"
        />
        <div className="flex items-center justify-between gap-2">
          <label className="flex items-center gap-1.5 text-[10px] text-muted-foreground cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoConfirm}
              onChange={(e) => setAutoConfirm(e.target.checked)}
              className="rounded border-border/60 bg-background/50 text-holo focus:ring-0"
            />
            <span>Auto-confirm non-destructive</span>
          </label>
          <button
            type="button"
            onClick={handleExecute}
            disabled={loading || !goalInput.trim()}
            className="px-2.5 py-1 text-[10px] rounded bg-holo text-black font-semibold hover:bg-holo/80 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            {loading ? "DISPATCHING..." : "RUN CONTROL"}
          </button>
        </div>
      </div>

      {/* Action Notification Message */}
      {actionMsg && (
        <div className="p-1.5 text-[10px] rounded bg-border/30 border border-border/50 text-foreground">
          {actionMsg}
        </div>
      )}

      {/* Active Control Details */}
      {activeControl && (
        <div className="p-2 rounded bg-background/40 border border-border/40 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <StatusIndicator tone={statusTone(activeControl.status)} pulse={["EXECUTING", "PLANNING", "WAITING_CONFIRMATION"].includes(activeControl.status)} />
              <span className="font-semibold text-[11px]">{activeControl.status}</span>
            </div>
            <span className="text-[9px] text-muted-foreground">{activeControl.control_id}</span>
          </div>

          <div className="text-[10px] text-foreground font-mono truncate">
            {activeControl.goal}
          </div>

          {/* Active Context Row */}
          <div className="grid grid-cols-2 gap-1 text-[9px] text-muted-foreground pt-1 border-t border-border/20">
            <div>
              <span className="text-muted-foreground/60">APP: </span>
              <span className="text-foreground">{activeControl.current_application || "None"}</span>
            </div>
            <div>
              <span className="text-muted-foreground/60">WEB: </span>
              <span className="text-foreground truncate">{activeControl.current_website ? new URL(activeControl.current_website).hostname : "None"}</span>
            </div>
            <div>
              <span className="text-muted-foreground/60">AGENT: </span>
              <span className="text-holo">{activeControl.active_agent || "Coordinator"}</span>
            </div>
            <div>
              <span className="text-muted-foreground/60">STEPS: </span>
              <span className="text-foreground">{activeControl.steps_completed} / {activeControl.steps_total}</span>
            </div>
          </div>

          {activeControl.current_action && (
            <div className="text-[10px] p-1.5 rounded bg-holo/5 border border-holo/20 text-holo">
              » {activeControl.current_action}
            </div>
          )}

          {/* Confirmation Alert Gate */}
          {activeControl.confirmation_required && (
            <div className="p-2 rounded bg-amber-500/10 border border-amber-500/40 space-y-1.5">
              <div className="flex items-center justify-between text-amber-400 font-semibold text-[10px]">
                <span>CONFIRMATION REQUIRED</span>
                <span className="text-[9px]">{activeControl.confirmation_type || "SYSTEM"}</span>
              </div>
              <p className="text-[10px] text-muted-foreground">{activeControl.message}</p>
              <div className="flex gap-2 pt-1">
                <button
                  type="button"
                  onClick={handleConfirmAction}
                  disabled={loading}
                  className="flex-1 py-1 text-[10px] rounded bg-amber-500 text-black font-semibold hover:bg-amber-400 transition-colors"
                >
                  CONFIRM & PROCEED
                </button>
                <button
                  type="button"
                  onClick={handleCancelAction}
                  disabled={loading}
                  className="px-2 py-1 text-[10px] rounded bg-red-500/20 text-red-400 border border-red-500/40 hover:bg-red-500/30 transition-colors"
                >
                  ABORT
                </button>
              </div>
            </div>
          )}

          {/* Scope Protection Items */}
          {activeControl.discovered_scope_items.length > 0 && (
            <div className="p-1.5 rounded bg-violet-500/10 border border-violet-500/30 space-y-1">
              <div className="text-[9px] font-semibold text-violet-400 tracking-wider">
                SCOPE BOUNDARY ITEMS ({activeControl.discovered_scope_items.length})
              </div>
              {activeControl.discovered_scope_items.slice(0, 2).map((item) => (
                <div key={item.item_id} className="text-[9px] text-muted-foreground truncate">
                  [{item.boundary}] {item.description}
                </div>
              ))}
            </div>
          )}

          {/* Observations Stream */}
          {activeControl.observations.length > 0 && (
            <div className="space-y-1 pt-1 border-t border-border/20">
              <div className="text-[9px] text-muted-foreground uppercase tracking-wider">
                Live Observations ({activeControl.observations.length})
              </div>
              <div className="max-h-24 overflow-y-auto space-y-1 pr-1">
                {activeControl.observations.slice(-3).map((obs) => (
                  <div key={obs.observation_id} className="text-[9px] p-1 rounded bg-background/60 border border-border/20">
                    <span className="text-holo uppercase">[{obs.source}]</span> {obs.title}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Running Applications Quick Peek */}
      <div className="pt-1.5 border-t border-border/30">
        <div className="flex items-center justify-between text-[9px] text-muted-foreground uppercase tracking-wider pb-1">
          <span>Active Desktop Apps</span>
          <span>{runningApps.length} FOUND</span>
        </div>
        <div className="flex flex-wrap gap-1 max-h-16 overflow-y-auto">
          {runningApps.length === 0 ? (
            <span className="text-[9px] text-muted-foreground/60 italic">No allowlisted desktop apps detected</span>
          ) : (
            runningApps.map((p) => (
              <span
                key={`${p.name}-${p.pid}`}
                className="text-[9px] px-1.5 py-0.5 rounded bg-border/40 text-muted-foreground border border-border/60 truncate max-w-[150px]"
                title={`${p.name} (PID: ${p.pid}) - ${p.title}`}
              >
                {p.name.replace(".exe", "")}
              </span>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
