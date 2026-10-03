import { useEffect, useState } from "react";
import {
  fetchRuntimeStatus,
  type RuntimeStatusResponse,
} from "@/services/runtime";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

export function RuntimePanel() {
  const [data, setData] = useState<RuntimeStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    async function load() {
      try {
        const res = await fetchRuntimeStatus();
        if (mounted) setData(res);
      } catch (err) {
        console.warn("Failed to load runtime telemetry:", err);
      } finally {
        if (mounted) setLoading(false);
      }
    }
    load();
    const timer = setInterval(load, 5000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  const resources = data?.host_resources;
  const perf = data?.performance;

  const ramPct = resources?.ram_used_pct ?? 0;
  const cpuPct = resources?.cpu_pct ?? 0;
  const state = data?.runtime_state ?? "NORMAL";

  const getTone = (status?: string): "holo" | "signal" | "alert" | "violet" => {
    if (status === "CRITICAL" || status === "RESOURCE_CRITICAL") return "signal";
    if (status === "WARNING" || status === "RESOURCE_WARNING") return "alert";
    if (status === "RECOVERING") return "violet";
    return "holo";
  };

  return (
    <div className="space-y-2 text-[10px]" style={{ fontFamily: "var(--font-mono)" }}>
      {/* Runtime State Banner */}
      <div className="holo-corners border border-holo/40 bg-holo/10 p-2">
        <div className="flex items-center justify-between">
          <span className="text-foreground tracking-widest text-[9px]">RUNTIME RELIABILITY</span>
          <StatusIndicator
            label={state}
            tone={getTone(state)}
            pulse={state !== "NORMAL"}
          />
        </div>
        <div className="mt-1 flex items-baseline justify-between">
          <span className="text-sm font-bold text-holo" style={{ fontFamily: "var(--font-display)" }}>
            RYVEN RUNTIME
          </span>
          <span className="text-[9px] text-muted-foreground">M15.3.9</span>
        </div>
      </div>

      {/* Warning Banner if High Pressure */}
      {resources?.status === "WARNING" && (
        <div className="border border-yellow-500/40 bg-yellow-500/10 px-2 py-1 text-yellow-300">
          ELEVATED RESOURCE PRESSURE DETECTED
        </div>
      )}
      {resources?.status === "CRITICAL" && (
        <div className="border border-red-500/40 bg-red-500/10 px-2 py-1 text-red-300">
          CRITICAL PRESSURE: HEAVY OPERATIONS DEFERRED
        </div>
      )}

      {/* Host Hardware Utilization */}
      <div className="holo-corners border border-border/70 p-2 space-y-2">
        <span className="text-[9px] tracking-wider text-muted-foreground">HOST HARDWARE</span>

        {/* CPU */}
        <div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">CPU</span>
            <span className="text-foreground">{cpuPct}%</span>
          </div>
          <div className="mt-1 h-1 w-full bg-border/60">
            <div
              className={`h-full transition-all duration-500 ${
                cpuPct >= 85 ? "bg-red-400" : cpuPct >= 70 ? "bg-yellow-400" : "bg-holo"
              }`}
              style={{ width: `${Math.min(100, cpuPct)}%` }}
            />
          </div>
        </div>

        {/* RAM */}
        <div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">RAM</span>
            <span className="text-foreground">
              {ramPct}% ({resources?.ram_used_gb ?? 0} / {resources?.ram_total_gb ?? 0} GB)
            </span>
          </div>
          <div className="mt-1 h-1 w-full bg-border/60">
            <div
              className={`h-full transition-all duration-500 ${
                ramPct >= 90 ? "bg-red-400" : ramPct >= 75 ? "bg-yellow-400" : "bg-holo"
              }`}
              style={{ width: `${Math.min(100, ramPct)}%` }}
            />
          </div>
        </div>

        {/* Process Memory */}
        {resources?.process_memory_mb !== undefined && resources.process_memory_mb > 0 && (
          <div className="flex justify-between text-[9px] text-muted-foreground border-t border-border/40 pt-1">
            <span>PROCESS RSS</span>
            <span>{resources.process_memory_mb} MB</span>
          </div>
        )}
      </div>

      {/* Performance Latency & Operations */}
      <div className="holo-corners border border-border/70 p-2 space-y-1">
        <span className="text-[9px] tracking-wider text-muted-foreground">PERFORMANCE PROFILER</span>

        <div className="grid grid-cols-2 gap-1 pt-1">
          <div className="border border-border/40 p-1">
            <span className="text-[8px] text-muted-foreground block">AVG LATENCY</span>
            <span className="text-holo font-bold text-xs">{perf?.avg_latency_ms ?? 0} ms</span>
          </div>
          <div className="border border-border/40 p-1">
            <span className="text-[8px] text-muted-foreground block">P95 LATENCY</span>
            <span className="text-holo font-bold text-xs">{perf?.p95_latency_ms ?? 0} ms</span>
          </div>
        </div>

        <div className="flex justify-between pt-1 border-t border-border/40 text-[9px]">
          <span className="text-muted-foreground">OPERATIONS</span>
          <span className="text-foreground">
            {perf?.total_operations ?? 0} ({perf?.successful_operations ?? 0} ok / {perf?.failed_operations ?? 0} err)
          </span>
        </div>

        <div className="flex justify-between text-[9px]">
          <span className="text-muted-foreground">ACTIVE / INCOMPLETE</span>
          <span className="text-foreground">{data?.active_or_incomplete_tasks_count ?? 0}</span>
        </div>
      </div>
    </div>
  );
}
