import { useEffect, useState, useCallback } from "react";
import {
  Play,
  Pause,
  XCircle,
  RotateCcw,
  Sliders,
  Cpu,
  Layers,
  AlertTriangle,
  CheckCircle2,
  Clock,
  ShieldAlert,
  RefreshCw,
  Lock,
} from "lucide-react";
import { HUDPanel } from "@/components/hud/HUDPanel";
import { StatusIndicator } from "@/components/hud/StatusIndicator";
import {
  getSchedulerStatus,
  getSchedulerQueue,
  getSchedulerResources,
  getTask,
  pauseTask,
  resumeTask,
  cancelTask,
  retryTask,
  updateTaskPriority,
  type SchedulerStatusData,
  type QueuedTaskItem,
  type SchedulerResourcesData,
  type SchedulerTaskDetail,
} from "@/services/agents";

const TONE_MAP: Record<string, "signal" | "holo" | "violet"> = {
  RUNNING: "signal",
  READY: "holo",
  QUEUED: "holo",
  PAUSED: "violet",
  BLOCKED_DEPENDENCY: "violet",
  BLOCKED_RESOURCE: "signal",
  COMPLETED: "holo",
  CANCELLED: "violet",
  FAILED: "signal",
  RECOVERY_REQUIRED: "signal",
};

export function MultiTaskSchedulerHUD() {
  const [statusData, setStatusData] = useState<SchedulerStatusData | null>(null);
  const [queueItems, setQueueItems] = useState<QueuedTaskItem[]>([]);
  const [resourceData, setResourceData] = useState<SchedulerResourcesData | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [taskDetail, setTaskDetail] = useState<SchedulerTaskDetail | null>(null);

  const [loading, setLoading] = useState<boolean>(true);
  const [actionPending, setActionPending] = useState<boolean>(false);
  const [alertMessage, setAlertMessage] = useState<{ text: string; isError: boolean } | null>(null);
  const [priorityModalOpen, setPriorityModalOpen] = useState<boolean>(false);
  const [targetPriority, setTargetPriority] = useState<number>(50);

  // Fetch scheduler plane telemetry from authoritative backend
  const refreshScheduler = useCallback(async () => {
    try {
      const [sStatus, sQueue, sResources] = await Promise.all([
        getSchedulerStatus(),
        getSchedulerQueue(),
        getSchedulerResources(),
      ]);
      setStatusData(sStatus);
      setQueueItems(sQueue);
      setResourceData(sResources);

      if (selectedTaskId) {
        try {
          const detail = await getTask(selectedTaskId);
          setTaskDetail(detail);
        } catch {
          // Task might have terminated or transitioned
        }
      } else if (sQueue.length > 0 && !taskDetail) {
        setSelectedTaskId(sQueue[0].task_id);
        const detail = await getTask(sQueue[0].task_id);
        setTaskDetail(detail);
      }
    } catch (err) {
      console.warn("Scheduler refresh error:", err);
    } finally {
      setLoading(false);
    }
  }, [selectedTaskId, taskDetail]);

  // Bounded polling loop (3000ms), cleanly destroyed on unmount
  useEffect(() => {
    let mounted = true;
    refreshScheduler();

    const interval = setInterval(() => {
      if (mounted) {
        refreshScheduler();
      }
    }, 3000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [refreshScheduler]);

  // Handle task selection
  const handleSelectTask = async (taskId: string) => {
    setSelectedTaskId(taskId);
    setAlertMessage(null);
    try {
      const detail = await getTask(taskId);
      setTaskDetail(detail);
    } catch (err) {
      setAlertMessage({ text: `Failed to load details for ${taskId}`, isError: true });
    }
  };

  // Task Control Handlers
  const handlePause = async () => {
    if (!selectedTaskId || actionPending) return;
    setActionPending(true);
    setAlertMessage(null);
    try {
      await pauseTask(selectedTaskId, "Paused via MultiTaskSchedulerHUD");
      setAlertMessage({ text: `Task ${selectedTaskId} paused.`, isError: false });
      await refreshScheduler();
    } catch (err) {
      setAlertMessage({ text: `Pause failed: ${String(err)}`, isError: true });
    } finally {
      setActionPending(false);
    }
  };

  const handleResume = async () => {
    if (!selectedTaskId || actionPending) return;
    setActionPending(true);
    setAlertMessage(null);
    try {
      await resumeTask(selectedTaskId, "Resumed via MultiTaskSchedulerHUD");
      setAlertMessage({ text: `Task ${selectedTaskId} resumed into queue.`, isError: false });
      await refreshScheduler();
    } catch (err) {
      setAlertMessage({ text: `Resume failed: ${String(err)}`, isError: true });
    } finally {
      setActionPending(false);
    }
  };

  const handleCancel = async () => {
    if (!selectedTaskId || actionPending) return;
    setActionPending(true);
    setAlertMessage(null);
    try {
      await cancelTask(selectedTaskId, "Cancelled via MultiTaskSchedulerHUD");
      setAlertMessage({ text: `Task ${selectedTaskId} cancelled.`, isError: false });
      await refreshScheduler();
    } catch (err) {
      setAlertMessage({ text: `Cancel failed: ${String(err)}`, isError: true });
    } finally {
      setActionPending(false);
    }
  };

  const handleRetry = async () => {
    if (!selectedTaskId || actionPending) return;
    setActionPending(true);
    setAlertMessage(null);
    try {
      await retryTask(selectedTaskId, "Retried via MultiTaskSchedulerHUD");
      setAlertMessage({ text: `Task ${selectedTaskId} re-enqueued for retry.`, isError: false });
      await refreshScheduler();
    } catch (err) {
      setAlertMessage({ text: `Retry failed: ${String(err)}`, isError: true });
    } finally {
      setActionPending(false);
    }
  };

  const handleUpdatePriority = async (p: number) => {
    if (!selectedTaskId || actionPending) return;
    setActionPending(true);
    setAlertMessage(null);
    try {
      await updateTaskPriority(selectedTaskId, p);
      setAlertMessage({ text: `Task ${selectedTaskId} priority updated to ${p}.`, isError: false });
      setPriorityModalOpen(false);
      await refreshScheduler();
    } catch (err) {
      setAlertMessage({ text: `Priority update failed: ${String(err)}`, isError: true });
    } finally {
      setActionPending(false);
    }
  };

  return (
    <HUDPanel
      title="Multi-task scheduler"
      code="SCHED.M17.8"
      className="w-[420px] max-h-[88vh] overflow-y-auto"
      tilt={-3}
    >
      <div className="space-y-3.5 text-xs">
        {/* ============================================================ */}
        {/* 1. SCHEDULER OVERVIEW                                        */}
        {/* ============================================================ */}
        <div className="holo-corners border border-border/70 p-2.5 bg-background/60">
          <div className="flex items-center justify-between pb-2 border-b border-border/40">
            <div className="flex items-center gap-2">
              <span
                className="text-[11px] font-semibold tracking-wider uppercase text-foreground"
                style={{ fontFamily: "var(--font-display)" }}
              >
                Control Plane
              </span>
              <StatusIndicator
                label={statusData?.scheduler_state || (loading ? "CONNECTING" : "OFFLINE")}
                tone={TONE_MAP[statusData?.scheduler_state || ""] || "holo"}
                pulse={statusData?.scheduler_state === "RUNNING"}
              />
            </div>
            <button
              type="button"
              onClick={refreshScheduler}
              disabled={loading}
              className="text-[10px] text-muted-foreground hover:text-holo flex items-center gap-1 transition-colors"
              title="Refresh scheduler telemetry"
            >
              <RefreshCw className={`h-3 w-3 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>

          <div className="grid grid-cols-4 gap-1.5 pt-2 text-[10px]" style={{ fontFamily: "var(--font-mono)" }}>
            <div className="bg-holo/5 border border-border/50 p-1.5 rounded-sm">
              <span className="text-muted-foreground block text-[9px]">RUNNING</span>
              <span className="text-holo font-bold text-sm">
                {statusData?.running_task_count ?? 0}
              </span>
            </div>
            <div className="bg-holo/5 border border-border/50 p-1.5 rounded-sm">
              <span className="text-muted-foreground block text-[9px]">QUEUED</span>
              <span className="text-foreground font-bold text-sm">
                {statusData?.queued_task_count ?? 0}
              </span>
            </div>
            <div className="bg-holo/5 border border-border/50 p-1.5 rounded-sm">
              <span className="text-muted-foreground block text-[9px]">BLOCKED</span>
              <span className={`font-bold text-sm ${(statusData?.blocked_task_count ?? 0) > 0 ? "text-amber-400" : "text-muted-foreground"}`}>
                {statusData?.blocked_task_count ?? 0}
              </span>
            </div>
            <div className="bg-holo/5 border border-border/50 p-1.5 rounded-sm">
              <span className="text-muted-foreground block text-[9px]">PAUSED</span>
              <span className="text-muted-foreground font-bold text-sm">
                {statusData?.paused_task_count ?? 0}
              </span>
            </div>
          </div>

          <div className="flex items-center justify-between pt-2 text-[10px] text-muted-foreground" style={{ fontFamily: "var(--font-mono)" }}>
            <div className="flex items-center gap-1.5">
              <Layers className="h-3 w-3 text-holo" />
              <span>
                Concurrency: {statusData?.active_execution_slots ?? 0} / {statusData?.max_concurrency ?? 1} slots
              </span>
            </div>
            {(statusData?.recovery_required_count ?? 0) > 0 && (
              <span className="text-rose-400 flex items-center gap-1">
                <AlertTriangle className="h-3 w-3" />
                Recovery req: {statusData?.recovery_required_count}
              </span>
            )}
          </div>
        </div>

        {/* Action Alert Banner */}
        {alertMessage && (
          <div
            className={`p-2 rounded-sm text-[10px] border flex items-center gap-2 ${
              alertMessage.isError
                ? "bg-rose-950/40 border-rose-800/80 text-rose-300"
                : "bg-emerald-950/40 border-emerald-800/80 text-emerald-300"
            }`}
            style={{ fontFamily: "var(--font-mono)" }}
          >
            {alertMessage.isError ? <AlertTriangle className="h-3.5 w-3.5 shrink-0" /> : <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />}
            <span className="truncate">{alertMessage.text}</span>
          </div>
        )}

        {/* ============================================================ */}
        {/* 2. TASK QUEUE & SELECTION                                    */}
        {/* ============================================================ */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-[11px] text-foreground font-medium">
            <span style={{ fontFamily: "var(--font-display)" }}>Authoritative Queue</span>
            <span className="text-[10px] text-muted-foreground" style={{ fontFamily: "var(--font-mono)" }}>
              {queueItems.length} tasks
            </span>
          </div>

          <div className="max-h-40 overflow-y-auto space-y-1 pr-1">
            {queueItems.length === 0 ? (
              <div className="p-3 text-center text-[10px] text-muted-foreground border border-dashed border-border/60">
                Queue is empty. No tasks awaiting admission.
              </div>
            ) : (
              queueItems.map((item) => {
                const isSelected = item.task_id === selectedTaskId;
                return (
                  <div
                    key={item.task_id}
                    onClick={() => handleSelectTask(item.task_id)}
                    className={`p-2 cursor-pointer border rounded-sm transition-all text-[10px] ${
                      isSelected
                        ? "bg-holo/15 border-holo text-foreground"
                        : "bg-background/40 border-border/60 text-muted-foreground hover:border-border hover:text-foreground"
                    }`}
                    style={{ fontFamily: "var(--font-mono)" }}
                  >
                    <div className="flex items-center justify-between gap-1.5">
                      <span className="font-semibold truncate text-foreground">
                        {item.goal || item.summary || item.task_id}
                      </span>
                      <StatusIndicator
                        label={item.state}
                        tone={TONE_MAP[item.state] || "holo"}
                      />
                    </div>
                    <div className="flex items-center justify-between mt-1 text-[9px] text-muted-foreground">
                      <span>ID: {item.task_id.slice(0, 14)}...</span>
                      <span>
                        P: {item.priority} (Eff: {item.effective_priority.toFixed(1)})
                      </span>
                    </div>
                    {item.blocked_reason && (
                      <div className="mt-1 text-[9px] text-amber-400/90 truncate flex items-center gap-1">
                        <Lock className="h-2.5 w-2.5 shrink-0" />
                        <span>{item.blocked_reason}</span>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* ============================================================ */}
        {/* 3. SELECTED TASK DETAIL & CONTROLS                           */}
        {/* ============================================================ */}
        {taskDetail && (
          <div className="holo-corners border border-border/80 p-2.5 bg-background/80 space-y-2">
            <div className="flex items-center justify-between border-b border-border/40 pb-1.5">
              <span
                className="text-[11px] font-semibold text-foreground truncate max-w-[220px]"
                style={{ fontFamily: "var(--font-display)" }}
              >
                {taskDetail.goal || taskDetail.summary || taskDetail.task_id}
              </span>
              <span className="text-[9px] text-muted-foreground font-mono">
                {taskDetail.task_id}
              </span>
            </div>

            <div className="grid grid-cols-2 gap-2 text-[10px]" style={{ fontFamily: "var(--font-mono)" }}>
              <div>
                <span className="text-muted-foreground text-[9px] block">State</span>
                <span className="text-holo font-semibold">{taskDetail.state}</span>
              </div>
              <div>
                <span className="text-muted-foreground text-[9px] block">Priority</span>
                <span className="text-foreground">
                  Base: {taskDetail.priority} | Eff: {taskDetail.effective_priority.toFixed(1)}
                </span>
              </div>
              <div>
                <span className="text-muted-foreground text-[9px] block">Progress</span>
                <div className="w-full bg-border/60 h-1.5 rounded-full mt-1 overflow-hidden">
                  <div
                    className="bg-holo h-full transition-all"
                    style={{ width: `${Math.min(100, Math.max(0, taskDetail.progress))}%` }}
                  />
                </div>
              </div>
              <div>
                <span className="text-muted-foreground text-[9px] block">Retries / Checkpoint</span>
                <span>
                  {taskDetail.retry_count} / {taskDetail.checkpoint_available ?? taskDetail.checkpoint_availability ? "Available" : "None"}
                </span>
              </div>
            </div>

            {/* Task Controls */}
            <div className="flex flex-wrap items-center gap-1.5 pt-2 border-t border-border/40">
              <button
                type="button"
                onClick={handlePause}
                disabled={actionPending || ["PAUSED", "COMPLETED", "CANCELLED"].includes(taskDetail.state)}
                className="px-2 py-1 bg-border/40 hover:bg-border disabled:opacity-30 rounded text-[9px] flex items-center gap-1 transition-colors"
                title="Pause active execution safely at checkpoint"
              >
                <Pause className="h-3 w-3" />
                Pause
              </button>
              <button
                type="button"
                onClick={handleResume}
                disabled={actionPending || ["RUNNING", "COMPLETED", "CANCELLED"].includes(taskDetail.state)}
                className="px-2 py-1 bg-holo/20 text-holo border border-holo/40 hover:bg-holo/30 disabled:opacity-30 rounded text-[9px] flex items-center gap-1 transition-colors"
                title="Resume task into scheduler queue"
              >
                <Play className="h-3 w-3" />
                Resume
              </button>
              <button
                type="button"
                onClick={handleCancel}
                disabled={actionPending || ["COMPLETED", "CANCELLED"].includes(taskDetail.state)}
                className="px-2 py-1 bg-rose-950/40 text-rose-300 border border-rose-800/60 hover:bg-rose-900/40 disabled:opacity-30 rounded text-[9px] flex items-center gap-1 transition-colors"
                title="Cancel task and release all held resources"
              >
                <XCircle className="h-3 w-3" />
                Cancel
              </button>
              <button
                type="button"
                onClick={handleRetry}
                disabled={actionPending || taskDetail.state === "RUNNING" || taskDetail.retry_count >= 3}
                className="px-2 py-1 bg-border/40 hover:bg-border disabled:opacity-30 rounded text-[9px] flex items-center gap-1 transition-colors"
                title="Reset failed steps and re-enqueue"
              >
                <RotateCcw className="h-3 w-3" />
                Retry
              </button>
              <button
                type="button"
                onClick={() => setPriorityModalOpen(!priorityModalOpen)}
                disabled={actionPending}
                className="px-2 py-1 bg-border/40 hover:bg-border rounded text-[9px] flex items-center gap-1 transition-colors ml-auto"
                title="Adjust base priority"
              >
                <Sliders className="h-3 w-3" />
                Priority
              </button>
            </div>

            {/* Priority Slider / Tier Picker */}
            {priorityModalOpen && (
              <div className="p-2 bg-background border border-holo/50 rounded text-[10px] space-y-2 mt-1 font-mono">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Select Priority Tier</span>
                  <span className="text-holo font-bold">{targetPriority}</span>
                </div>
                <div className="grid grid-cols-5 gap-1">
                  {[10, 25, 50, 75, 100].map((tier) => (
                    <button
                      key={tier}
                      type="button"
                      onClick={() => handleUpdatePriority(tier)}
                      className={`p-1 text-[9px] border rounded ${
                        taskDetail.priority === tier
                          ? "bg-holo/30 border-holo text-holo"
                          : "border-border/60 hover:border-border text-foreground"
                      }`}
                    >
                      {tier === 100 ? "CRIT" : tier === 75 ? "HIGH" : tier === 50 ? "NORM" : tier === 25 ? "LOW" : "BG"}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ============================================================ */}
        {/* 4. RESOURCE ALLOCATION PANEL                                */}
        {/* ============================================================ */}
        <div className="holo-corners border border-border/70 p-2.5 bg-background/60 space-y-2">
          <div className="flex items-center justify-between text-[11px] font-medium text-foreground">
            <span style={{ fontFamily: "var(--font-display)" }}>Resource Plane</span>
            <span className="text-[10px] text-muted-foreground font-mono">
              {resourceData?.active_leases_count ?? 0} active leases
            </span>
          </div>

          <div className="grid grid-cols-3 gap-1.5 text-[9px]" style={{ fontFamily: "var(--font-mono)" }}>
            {(resourceData?.resources || []).map((res) => {
              const isMutex = ["COMPUTER_INTERACTION", "CLIPBOARD"].includes(res.resource_type);
              const isOccupied = res.active_usage > 0;
              return (
                <div
                  key={res.resource_type}
                  className={`p-1.5 border rounded-sm ${
                    isOccupied
                      ? isMutex
                        ? "border-amber-500/80 bg-amber-950/20 text-amber-200"
                        : "border-holo/70 bg-holo/10 text-foreground"
                      : "border-border/40 bg-background/40 text-muted-foreground"
                  }`}
                >
                  <div className="font-semibold truncate text-[8.5px]" title={res.resource_type}>
                    {res.resource_type.replace("_", " ")}
                  </div>
                  <div className="flex items-center justify-between mt-1 text-[8px]">
                    <span>
                      {res.active_usage} / {res.capacity}
                    </span>
                    {isMutex && isOccupied && <Lock className="h-2 w-2 text-amber-400" />}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </HUDPanel>
  );
}
