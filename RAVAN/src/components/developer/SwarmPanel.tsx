import { useEffect, useState } from "react";
import {
  fetchAgentsList,
  submitAgentGoal,
  confirmAgentTask,
  cancelAgentGraph,
  fetchPlanPreview,
  executePlan,
  type AgentDescriptor,
  type TaskGraphData,
  type PlanningResultData,
  type PlanningDraftTask,
} from "@/services/agents";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

export function SwarmPanel() {
  const [agents, setAgents] = useState<AgentDescriptor[]>([]);
  const [activeGraph, setActiveGraph] = useState<TaskGraphData | null>(null);
  const [planPreview, setPlanPreview] = useState<PlanningResultData | null>(null);
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
      setPlanPreview(null);
      setGoalInput("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to coordinate task");
    } finally {
      setLoading(false);
    }
  };

  const handlePreview = async () => {
    if (!goalInput.trim() || loading) return;
    setLoading(true);
    setError(null);
    try {
      const preview = await fetchPlanPreview(goalInput.trim());
      setPlanPreview(preview);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to generate plan preview");
    } finally {
      setLoading(false);
    }
  };

  const handleExecutePlan = async () => {
    if (!planPreview || loading) return;
    setLoading(true);
    setError(null);
    try {
      const graph = await executePlan(planPreview.plan_id);
      setActiveGraph(graph);
      setPlanPreview(null);
      setGoalInput("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to execute plan");
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
            label={activeGraph?.status ?? (planPreview ? "PLAN_READY" : "READY")}
            tone={getStatusTone(activeGraph?.status ?? "READY")}
            pulse={activeGraph?.status === "RUNNING"}
          />
        </div>
        <div className="mt-1 flex items-baseline justify-between">
          <span className="text-sm font-bold text-holo" style={{ fontFamily: "var(--font-display)" }}>
            RYVEN SWARM
          </span>
          <span className="text-[9px] text-muted-foreground">M16.1 PLANNING ENGINE</span>
        </div>
      </div>

      {/* Goal Dispatcher */}
      <div className="border border-border/70 p-2 space-y-1.5">
        <span className="text-[9px] tracking-wider text-muted-foreground">SUBMIT OR PLAN GOAL</span>
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
            onClick={handlePreview}
            disabled={loading || !goalInput.trim()}
            className="px-2 py-1 bg-border/40 hover:bg-border/60 text-muted-foreground border border-border/60 rounded text-[9px] font-semibold disabled:opacity-40"
          >
            PREVIEW
          </button>
          <button
            type="button"
            onClick={handleLaunch}
            disabled={loading || !goalInput.trim()}
            className="px-2.5 py-1 bg-holo/20 hover:bg-holo/30 text-holo border border-holo/50 rounded text-[9px] font-bold tracking-wider disabled:opacity-40"
          >
            {loading ? "PROCESSING..." : "DISPATCH"}
          </button>
        </div>
        {error && <div className="text-red-400 text-[9px]">{error}</div>}
      </div>

      {/* Plan Preview Section (M16.1) */}
      {planPreview && (
        <div className="holo-corners border border-holo/50 bg-background/60 p-2.5 space-y-2">
          <div className="flex items-center justify-between border-b border-border/50 pb-1.5">
            <span className="font-bold text-holo text-[10px] tracking-wider">PLAN PREVIEW</span>
            <div className="flex items-center gap-1.5">
              <span className="px-1.5 py-0.5 bg-holo/15 text-holo rounded text-[8px] font-bold">
                {planPreview.complexity}
              </span>
              <span className="px-1.5 py-0.5 bg-foreground/10 text-muted-foreground rounded text-[8px]">
                {planPreview.planning_mode}
              </span>
              <span className="px-1.5 py-0.5 bg-yellow-500/15 text-yellow-300 rounded text-[8px]">
                {planPreview.overall_risk}
              </span>
            </div>
          </div>

          <div className="text-[9px] text-muted-foreground">
            <span className="text-foreground font-medium">Goal: </span>
            {planPreview.normalized_goal}
          </div>

          {/* Task Steps */}
          <div className="space-y-1 max-h-[140px] overflow-y-auto pr-1">
            {planPreview.tasks.map((task: PlanningDraftTask, idx: number) => (
              <div
                key={task.task_id}
                className="flex items-center justify-between gap-1 p-1 bg-background/40 border border-border/40 rounded text-[8px]"
              >
                <div className="flex items-center gap-1.5 truncate flex-1">
                  <span className="text-muted-foreground font-mono">{idx + 1}.</span>
                  <span className="px-1 py-0.2 bg-holo/15 text-holo rounded text-[7px]">
                    {task.preferred_role ?? "WORKER"}
                  </span>
                  <span className="truncate text-foreground font-medium">
                    {task.objective}
                  </span>
                </div>
                {task.requires_confirmation && (
                  <span className="text-[7px] text-yellow-400 px-1 py-0.2 bg-yellow-500/10 border border-yellow-500/30 rounded">
                    CONFIRM
                  </span>
                )}
              </div>
            ))}
          </div>

          <div className="flex items-center justify-between text-[8px] text-muted-foreground pt-1 border-t border-border/40">
            <span>
              Parallelism: {planPreview.estimated_parallelism} | Steps: {planPreview.estimated_steps}
            </span>
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => setPlanPreview(null)}
                className="px-2 py-0.5 text-muted-foreground hover:text-foreground text-[8px]"
              >
                DISCARD
              </button>
              <button
                type="button"
                onClick={handleExecutePlan}
                disabled={loading}
                className="px-2.5 py-0.5 bg-holo text-background font-bold rounded text-[8px] hover:bg-holo/90"
              >
                EXECUTE PLAN
              </button>
            </div>
          </div>
        </div>
      )}

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
