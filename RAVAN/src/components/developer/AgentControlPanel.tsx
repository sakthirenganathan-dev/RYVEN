import { useEffect, useState } from "react";
import { StatusIndicator } from "@/components/hud/StatusIndicator";
import { fetchAgentState, confirmAgentStep, cancelAgentTask } from "@/services/agent";
import type { AgentState, AgentStatus } from "@/types/agent";

export function AgentControlPanel() {
  const [state, setState] = useState<AgentState | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    const poll = async () => {
      try {
        const data = await fetchAgentState();
        if (mounted) setState(data);
      } catch {
        /* backend offline or idle */
      }
    };

    poll();
    const interval = setInterval(poll, 2000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const handleConfirm = async () => {
    if (!state?.confirmation_token) return;
    setConfirming(true);
    setActionMsg(null);
    try {
      const res = await confirmAgentStep(state.confirmation_token);
      setActionMsg(res.message);
      const updated = await fetchAgentState(state.task_id);
      setState(updated);
    } catch (err) {
      setActionMsg(`Confirmation failed: ${String(err)}`);
    } finally {
      setConfirming(false);
    }
  };

  const handleCancel = async () => {
    if (!state?.task_id) return;
    try {
      await cancelAgentTask(state.task_id);
      const updated = await fetchAgentState(state.task_id);
      setState(updated);
    } catch (err) {
      setActionMsg(`Cancel failed: ${String(err)}`);
    }
  };

  const statusTone = (status?: AgentStatus) => {
    switch (status) {
      case "EXECUTING":
      case "OBSERVING":
      case "PLANNING":
      case "VERIFYING":
        return "holo";
      case "WAITING_CONFIRMATION":
        return "signal";
      case "RETRYING":
        return "violet";
      case "COMPLETED":
        return "holo";
      case "FAILED":
      case "CANCELLED":
        return "alert";
      default:
        return "muted";
    }
  };

  const steps = state?.current_plan?.steps || [];
  const currentStep = steps[state?.current_step_index ?? -1] || null;
  const progressPercent =
    steps.length > 0
      ? Math.round(((state?.current_step_index ?? 0) / steps.length) * 100)
      : state?.status === "COMPLETED"
        ? 100
        : 0;

  return (
    <div className="space-y-3">
      {/* Task & Status Header */}
      <div className="holo-corners relative border border-border/70 p-3 space-y-2">
        <div className="flex items-center justify-between text-xs tracking-wider">
          <span
            className="text-muted-foreground uppercase"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            AGENT TASK
          </span>
          <StatusIndicator
            label={state?.status || "IDLE"}
            tone={statusTone(state?.status)}
            pulse={
              state?.status === "EXECUTING" ||
              state?.status === "OBSERVING" ||
              state?.status === "WAITING_CONFIRMATION" ||
              state?.status === "RETRYING"
            }
          />
        </div>

        {state?.task_id && (
          <div className="text-[10px] text-muted-foreground font-mono truncate">
            ID: {state.task_id}
          </div>
        )}

        {/* Current Goal */}
        <div className="border-t border-border/60 pt-2">
          <div
            className="text-[9px] uppercase tracking-widest text-muted-foreground"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            CURRENT GOAL
          </div>
          <div className="text-xs text-foreground font-medium mt-0.5 line-clamp-2">
            {state?.user_goal || "No active autonomous task"}
          </div>
        </div>

        {/* Progress Bar */}
        {steps.length > 0 && (
          <div className="space-y-1 pt-1">
            <div className="flex justify-between text-[10px] font-mono text-muted-foreground">
              <span>PROGRESS</span>
              <span>
                {Math.min((state?.current_step_index ?? 0) + 1, steps.length)} / {steps.length} (
                {progressPercent}%)
              </span>
            </div>
            <div className="h-1 w-full bg-border/70 rounded overflow-hidden">
              <div
                className="h-full bg-holo transition-all duration-500"
                style={{
                  width: `${progressPercent}%`,
                  boxShadow: "0 0 8px var(--color-holo)",
                }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Active Step & Tool */}
      {currentStep && state?.status !== "COMPLETED" && state?.status !== "IDLE" && (
        <div className="holo-corners relative border border-border/70 p-3 space-y-2">
          <div
            className="text-[9px] uppercase tracking-widest text-muted-foreground"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            ACTIVE STEP & TOOL
          </div>
          <div className="flex items-center justify-between text-xs">
            <span className="font-semibold text-holo">{currentStep.name}</span>
            <span className="text-[10px] font-mono px-1.5 py-0.5 bg-holo/10 text-holo border border-holo/30 rounded">
              {currentStep.tool_name}
            </span>
          </div>
          <div className="text-[11px] text-muted-foreground line-clamp-2">
            {currentStep.description}
          </div>
          {(state?.retry_count ?? 0) > 0 && (
            <div className="text-[10px] font-mono text-violet">
              RETRY ATTEMPT: {state?.retry_count} / {currentStep.max_retries}
            </div>
          )}
        </div>
      )}

      {/* Current Observation */}
      {state?.current_observation && (
        <div className="holo-corners relative border border-border/70 p-3 space-y-1">
          <div
            className="flex items-center justify-between text-[9px] uppercase tracking-widest text-muted-foreground"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            <span>LATEST OBSERVATION</span>
            <span className={state.current_observation.success ? "text-holo" : "text-destructive"}>
              {state.current_observation.success ? "VERIFIED" : "ATTENTION"}
            </span>
          </div>
          <div className="text-[11px] font-mono text-foreground/90 bg-black/30 p-2 rounded border border-border/40 max-h-20 overflow-y-auto">
            {state.current_observation.summary}
          </div>
        </div>
      )}

      {/* Confirmation Boundary */}
      {state?.status === "WAITING_CONFIRMATION" && (
        <div className="holo-corners relative border border-signal/60 bg-signal/5 p-3 space-y-2">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-signal">
            <span className="animate-pulse">⚠</span>
            CONFIRMATION REQUIRED
          </div>
          <div className="text-[11px] text-foreground">
            {state.confirmation_message ||
              "A sensitive action requires explicit human authorization."}
          </div>
          <div className="flex gap-2 pt-1">
            <button
              type="button"
              disabled={confirming}
              onClick={handleConfirm}
              className="flex-1 px-2 py-1 text-xs font-mono font-bold bg-signal text-black rounded hover:bg-signal/90 transition-colors disabled:opacity-50"
            >
              {confirming ? "APPROVING..." : "APPROVE ACTION"}
            </button>
            <button
              type="button"
              onClick={handleCancel}
              className="px-2 py-1 text-xs font-mono border border-border/70 text-muted-foreground rounded hover:text-foreground transition-colors"
            >
              CANCEL
            </button>
          </div>
        </div>
      )}

      {/* Action Message feedback */}
      {actionMsg && (
        <div className="text-[10px] font-mono text-holo border border-holo/30 bg-holo/5 p-2 rounded">
          {actionMsg}
        </div>
      )}

      {/* Execution Plan Step Breakdown */}
      {steps.length > 0 && (
        <div className="border border-border/60 rounded p-2 space-y-1.5 max-h-48 overflow-y-auto">
          <div
            className="text-[9px] uppercase tracking-widest text-muted-foreground"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            PLAN EXECUTION STEPS
          </div>
          {steps.map((st, idx) => (
            <div
              key={st.step_id || idx}
              className={`flex items-center justify-between text-[10px] font-mono px-2 py-1 rounded ${
                st.status === "COMPLETED"
                  ? "text-holo/80 bg-holo/5"
                  : st.status === "RUNNING"
                    ? "text-foreground font-bold bg-holo/20 border border-holo/40"
                    : st.status === "FAILED"
                      ? "text-destructive bg-destructive/10"
                      : "text-muted-foreground"
              }`}
            >
              <div className="flex items-center gap-1.5 truncate max-w-[200px]">
                <span className="text-[9px] text-muted-foreground">{idx + 1}.</span>
                <span className="truncate">{st.name}</span>
              </div>
              <span className="text-[9px] uppercase tracking-wider">{st.status}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
