import { useEffect, useState } from "react";
import {
  fetchAgentsList,
  submitAgentGoal,
  confirmAgentTask,
  cancelAgentGraph,
  type AgentDescriptor,
  type TaskGraphData,
} from "@/services/agents";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

export function SwarmPanel() {
  const [agents, setAgents] = useState<AgentDescriptor[]>([]);
  const [activeGraph, setActiveGraph] = useState<TaskGraphData | null>(null);
  const [goalInput, setGoalInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Poll agents list
  useEffect(() => {
    let mounted = true;
    async function loadAgents() {
      try {
        const res = await fetchAgentsList();
        if (mounted) setAgents(res.agents);
      } catch (err) {
        console.warn("Failed to load agents list:", err);
      }
    }
    loadAgents();
    const timer = setInterval(loadAgents, 5000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  const handleLaunch = async () => {
    if (!goalInput.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const graph = await submitAgentGoal(goalInput.trim());
      setActiveGraph(graph);
      setGoalInput("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to coordinate task");
    } finally {
      setLoading(false);
    }
  };

  const handleConfirm = async (taskId: string) => {
    if (!activeGraph) return;
    try {
      const updated = await confirmAgentTask(taskId, activeGraph.graph_id);
      setActiveGraph(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to confirm task");
    }
  };

  const handleCancel = async () => {
    if (!activeGraph) return;
    try {
      const updated = await cancelAgentGraph(activeGraph.graph_id);
      setActiveGraph(updated);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to cancel graph");
    }
  };

  const getStatusTone = (status: string): "holo" | "signal" | "alert" | "violet" => {
    if (status === "RUNNING") return "signal";
    if (status === "REQUIRES_CONFIRMATION" || status === "WAITING") return "alert";
    if (status === "FAILED" || status === "BLOCKED") return "violet";
    return "holo";
  };

  const progressPct = activeGraph?.progress?.progress_pct ?? 0;

  return (
    <div className="space-y-3 text-[10px]" style={{ fontFamily: "var(--font-mono)" }}>
      {/* Banner */}
      <div className="holo-corners border border-holo/40 bg-holo/10 p-2">
        <div className="flex items-center justify-between">
          <span className="text-foreground tracking-widest text-[9px]">AGENT COORDINATION HUD</span>
          <StatusIndicator
            label={activeGraph?.status ?? "READY"}
            tone={getStatusTone(activeGraph?.status ?? "READY")}
            pulse={activeGraph?.status === "RUNNING"}
          />
        </div>
        <div className="mt-1 flex items-baseline justify-between">
          <span className="text-sm font-bold text-holo" style={{ fontFamily: "var(--font-display)" }}>
            RYVEN SWARM
          </span>
          <span className="text-[9px] text-muted-foreground">M16.0 FOUNDATION</span>
        </div>
      </div>

      {/* Goal Dispatcher */}
      <div className="border border-border/70 p-2 space-y-1.5">
        <span className="text-[9px] tracking-wider text-muted-foreground">SUBMIT COORDINATION GOAL</span>
        <div className="flex gap-1.5">
          <input
            type="text"
            value={goalInput}
            onChange={(e) => setGoalInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleLaunch()}
            placeholder="e.g. Research React Three Fiber and build demo..."
            className="flex-1 bg-background/80 border border-border/60 px-2 py-1 text-[9px] rounded text-foreground focus:outline-none focus:border-holo"
          />
          <button
            type="button"
            onClick={handleLaunch}
            disabled={loading || !goalInput.trim()}
            className="px-2.5 py-1 bg-holo/20 hover:bg-holo/30 text-holo border border-holo/50 rounded text-[9px] font-bold tracking-wider disabled:opacity-40"
          >
            {loading ? "PLANNING..." : "DISPATCH"}
          </button>
        </div>
        {error && <div className="text-red-400 text-[9px]">{error}</div>}
      </div>

      {/* Active Graph View */}
      {activeGraph && (
        <div className="holo-corners border border-border/70 p-2 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[9px] text-muted-foreground truncate max-w-[200px]">
              {activeGraph.goal}
            </span>
            <button
              type="button"
              onClick={handleCancel}
              className="text-[8px] text-red-400 hover:text-red-300 underline"
            >
              CANCEL
            </button>
          </div>

          {/* Progress Bar */}
          <div>
            <div className="flex justify-between text-[9px] text-muted-foreground">
              <span>PROGRESS</span>
              <span>{progressPct}% ({activeGraph.progress.completed}/{activeGraph.progress.total_tasks})</span>
            </div>
            <div className="mt-1 h-1.5 w-full bg-border/60 rounded overflow-hidden">
              <div
                className="h-full bg-holo transition-all duration-500"
                style={{ width: `${progressPct}%`, boxShadow: "0 0 8px var(--color-holo)" }}
              />
            </div>
          </div>

          {/* Tasks List */}
          <div className="space-y-1.5 max-h-[140px] overflow-y-auto pr-1">
            {Object.values(activeGraph.tasks).map((t) => (
              <div
                key={t.task_id}
                className="border border-border/50 bg-background/50 p-1.5 rounded flex items-center justify-between gap-1"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[8px] px-1 py-0.2 bg-holo/20 text-holo rounded">
                      {t.role}
                    </span>
                    <span className="truncate text-[9px] text-foreground font-medium">
                      {t.objective}
                    </span>
                  </div>
                  {t.dependencies.length > 0 && (
                    <div className="text-[8px] text-muted-foreground mt-0.5">
                      deps: {t.dependencies.length}
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-1.5">
                  <StatusIndicator
                    label={t.status}
                    tone={getStatusTone(t.status)}
                    pulse={t.status === "RUNNING"}
                  />
                  {t.status === "REQUIRES_CONFIRMATION" && (
                    <button
                      type="button"
                      onClick={() => handleConfirm(t.task_id)}
                      className="px-1.5 py-0.5 bg-yellow-500/20 text-yellow-300 border border-yellow-500/40 rounded text-[8px] hover:bg-yellow-500/30 font-bold"
                    >
                      CONFIRM
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Registered Worker Roles */}
      <div className="border border-border/70 p-2 space-y-1.5">
        <span className="text-[9px] tracking-wider text-muted-foreground">SPECIALIZED WORKER ROLES</span>
        <div className="grid grid-cols-2 gap-1.5">
          {agents.map((a) => (
            <div
              key={a.role}
              className="border border-border/40 p-1 rounded bg-background/40 flex items-center justify-between"
            >
              <div>
                <div className="text-[9px] text-foreground font-semibold tracking-wide">
                  {a.role}
                </div>
                <div className="text-[8px] text-muted-foreground">
                  {a.capabilities.length} capabilities
                </div>
              </div>
              <StatusIndicator
                label={a.status}
                tone={getStatusTone(a.status)}
                pulse={a.status === "RUNNING"}
              />
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
