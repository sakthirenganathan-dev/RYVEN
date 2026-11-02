import { useEffect, useState, useRef } from "react";
import { fetchSystemStatus, type SystemStatusData } from "@/services/jarvis";
import { ChevronRight } from "lucide-react";

interface Props {
  onClick?: () => void;
}

export function SystemOverviewCard({ onClick }: Props) {
  const [data, setData] = useState<SystemStatusData | null>(null);
  const lastSuccessTimeRef = useRef<number>(0);

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const telemetry = await fetchSystemStatus();
        if (!mounted) return;
        setData(telemetry);
        lastSuccessTimeRef.current = Date.now();
      } catch {
        // keep previous data if recent
      }
    };

    poll();
    const interval = setInterval(poll, 4000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const cpu = data ? Math.round(data.cpu_percent) : null;
  const ram = data ? Math.round(data.ram_percent) : null;
  const disk = data ? Math.round(data.disk.percent) : null;
  const battery =
    data?.battery.available && data.battery.percent !== null
      ? Math.round(data.battery.percent)
      : null;

  return (
    <div
      onClick={onClick}
      role={onClick ? "button" : undefined}
      className={`glass-panel p-3.5 transition-all ${
        onClick ? "cursor-pointer hover:border-cyan-400/60" : ""
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between pb-2.5">
        <span className="flex items-center gap-1.5 section-title">
          <span className="text-cyan-400">▸</span>
          SYSTEM OVERVIEW
        </span>
        <ChevronRight className="h-3.5 w-3.5 text-slate-400" />
      </div>

      {/* 4 Circular Progress Gauges in 1 Row */}
      <div className="grid grid-cols-4 gap-2 pt-1">
        <CircleGauge value={cpu} label="CPU" tone="cyan" id="cpu-gauge" />
        <CircleGauge value={ram} label="RAM" tone="cyan" id="ram-gauge" />
        <CircleGauge value={disk} label="DISK" tone="cyan" id="disk-gauge" />
        <CircleGauge value={battery} label="BATTERY" tone="green" id="bat-gauge" />
      </div>
    </div>
  );
}

function CircleGauge({
  value,
  label,
  tone = "cyan",
  id,
}: {
  value: number | null;
  label: string;
  tone?: "cyan" | "green";
  id: string;
}) {
  const r = 20;
  const c = 2 * Math.PI * r;
  const clamped = value !== null ? Math.max(0, Math.min(100, value)) : 0;
  const gradId = `gaugeGrad-${id}`;

  return (
    <div className="flex flex-col items-center">
      <div className="relative h-13 w-13">
        <svg viewBox="0 0 52 52" className="h-full w-full -rotate-90">
          <defs>
            <linearGradient id={gradId} x1="0%" y1="0%" x2="100%" y2="100%">
              {tone === "green" ? (
                <>
                  <stop offset="0%" stopColor="#22C55E" />
                  <stop offset="100%" stopColor="#10B981" />
                </>
              ) : (
                <>
                  <stop offset="0%" stopColor="#22D3EE" />
                  <stop offset="100%" stopColor="#3B82F6" />
                </>
              )}
            </linearGradient>
          </defs>
          {/* Track */}
          <circle
            cx="26"
            cy="26"
            r={r}
            fill="none"
            stroke="rgba(30, 58, 138, 0.35)"
            strokeWidth="3.5"
          />
          {/* Progress with gradient stroke arc */}
          {value !== null && (
            <circle
              cx="26"
              cy="26"
              r={r}
              fill="none"
              stroke={`url(#${gradId})`}
              strokeWidth="3.5"
              strokeLinecap="round"
              strokeDasharray={`${(clamped / 100) * c} ${c}`}
              className="transition-all duration-700"
              filter={tone === "green" ? "drop-shadow(0 0 5px rgba(34,197,94,0.6))" : "drop-shadow(0 0 5px rgba(34,211,238,0.6))"}
            />
          )}
        </svg>
        <span
          className="absolute inset-0 grid place-items-center text-[10px] font-mono font-bold text-white"
        >
          {value !== null ? `${clamped}%` : "N/A"}
        </span>
      </div>
      <span className="mt-1 text-[9px] font-mono tracking-wider text-slate-400">
        {label}
      </span>
    </div>
  );
}
