import { useEffect, useState } from "react";
import { getSystemMetrics } from "@/services/jarvis";
import { HUDPanel } from "@/components/hud/HUDPanel";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

function Gauge({
  value,
  label,
  display,
  unit,
}: {
  value: number;
  label: string;
  display: string;
  unit?: string | undefined;
}) {
  const r = 26;
  const c = 2 * Math.PI * r;
  return (
    <div className="flex items-center gap-3">
      <div className="relative h-16 w-16 shrink-0">
        <svg viewBox="0 0 64 64" className="h-full w-full -rotate-90">
          <circle
            cx="32"
            cy="32"
            r={r}
            fill="none"
            stroke="var(--color-holo)"
            strokeOpacity="0.15"
            strokeWidth="2"
          />
          <circle
            cx="32"
            cy="32"
            r={r}
            fill="none"
            stroke="var(--color-holo)"
            strokeWidth="2"
            strokeLinecap="round"
            strokeDasharray={`${(value / 100) * c} ${c}`}
            className="transition-all duration-700"
          />
          <circle
            cx="32"
            cy="32"
            r="18"
            fill="none"
            stroke="var(--color-holo)"
            strokeOpacity="0.2"
            strokeWidth="0.6"
            strokeDasharray="2 6"
          />
        </svg>
        <span
          className="absolute inset-0 grid place-items-center text-[10px] text-holo"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          {Math.round(value)}%
        </span>
      </div>
      <div className="min-w-0">
        <span className="holo-label">{label}</span>
        <p
          className="text-xl leading-tight text-foreground text-glow"
          style={{ fontFamily: "var(--font-display)" }}
        >
          {display}
          {unit && <span className="ml-1 text-xs text-holo-dim">{unit}</span>}
        </p>
      </div>
    </div>
  );
}

export function SystemHUD() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick((t) => t + 1), 1400);
    return () => clearInterval(id);
  }, []);

  const metrics = getSystemMetrics(tick);

  return (
    <HUDPanel title="System diagnostics" code="SYS.01" className="w-[320px]">
      <div className="space-y-4">
        {metrics.map((m, i) => (
          <div key={m.key} className="relative">
            {i > 0 && <span className="absolute -top-2 left-0 h-px w-full bg-border/60" />}
            <Gauge value={m.value} label={m.label} display={m.display} unit={m.unit} />
          </div>
        ))}
        <div className="flex items-center justify-between border-t border-border/60 pt-2">
          <StatusIndicator label="All subsystems nominal" tone="signal" />
          <span className="holo-label opacity-60">{`T+${(tick * 1.4).toFixed(1)}s`}</span>
        </div>
      </div>
    </HUDPanel>
  );
}
