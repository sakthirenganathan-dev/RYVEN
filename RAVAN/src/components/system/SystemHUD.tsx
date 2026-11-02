import { useEffect, useState, useRef } from "react";
import {
  fetchSystemStatus,
  type SystemStatusData,
} from "@/services/jarvis";
import { HUDPanel } from "@/components/hud/HUDPanel";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

function Gauge({
  value,
  label,
  display,
  unit,
  tone = "holo",
}: {
  value: number;
  label: string;
  display: string;
  unit?: string | undefined;
  tone?: "holo" | "signal" | "alert";
}) {
  const r = 24;
  const c = 2 * Math.PI * r;
  const clamped = Math.max(0, Math.min(100, value));

  const strokeColor =
    tone === "alert"
      ? "var(--color-destructive, #ff4d4f)"
      : tone === "signal"
        ? "var(--color-signal, #52c41a)"
        : "var(--color-holo, #00f0ff)";

  return (
    <div className="flex items-center gap-3">
      <div className="relative h-14 w-14 shrink-0">
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
            stroke={strokeColor}
            strokeWidth="2"
            strokeLinecap="round"
            strokeDasharray={`${(clamped / 100) * c} ${c}`}
            className="transition-all duration-700"
          />
          <circle
            cx="32"
            cy="32"
            r={17}
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
          {Math.round(clamped)}%
        </span>
      </div>
      <div className="min-w-0 flex-1">
        <span className="holo-label block truncate">{label}</span>
        <p
          className="text-lg font-semibold leading-tight text-foreground text-glow truncate"
          style={{ fontFamily: "var(--font-display)" }}
        >
          {display}
          {unit && <span className="ml-1 text-xs text-holo-dim">{unit}</span>}
        </p>
      </div>
    </div>
  );
}

export function SystemHUD({ className }: { className?: string }) {
  const [data, setData] = useState<SystemStatusData | null>(null);
  const [status, setStatus] = useState<"loading" | "live" | "stale" | "offline">("loading");
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const lastSuccessTimeRef = useRef<number>(0);

  useEffect(() => {
    let mounted = true;
    let timer: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const telemetry = await fetchSystemStatus();
        if (!mounted) return;
        setData(telemetry);
        const now = Date.now();
        lastSuccessTimeRef.current = now;
        setLastUpdated(now);
        setStatus("live");
      } catch {
        if (!mounted) return;
        if (lastSuccessTimeRef.current > 0 && Date.now() - lastSuccessTimeRef.current < 20000) {
          setStatus("stale");
        } else {
          setStatus("offline");
        }
      }
    };

    poll();
    timer = setInterval(poll, 4500);

    const handleVisibility = () => {
      if (!document.hidden) {
        poll();
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);

    return () => {
      mounted = false;
      if (timer) clearInterval(timer);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);

  const timeString = lastUpdated
    ? new Date(lastUpdated).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : null;

  return (
    <HUDPanel title="Hardware Telemetry" code="SYS.HW" className={className ?? "w-[330px]"} tilt={-4}>
      <div className="space-y-3.5">
        {status === "loading" && !data && (
          <div className="py-8 text-center space-y-2">
            <span className="holo-label animate-pulse text-holo">SYNCING LOCAL SENSORS...</span>
            <div className="mx-auto h-1 w-24 overflow-hidden rounded bg-border">
              <div className="h-full w-full bg-holo animate-[holo-scan_1.5s_infinite_linear]" />
            </div>
          </div>
        )}

        {status === "offline" && !data && (
          <div className="py-6 text-center space-y-2">
            <StatusIndicator label="SENSOR LINK SEVERED" tone="alert" pulse />
            <p className="holo-label opacity-70">Backend unavailable on 127.0.0.1:8000</p>
          </div>
        )}

        {data && (
          <>
            {/* CPU */}
            <Gauge
              value={data.cpu_percent}
              label="CPU Utilization"
              display={data.cpu_percent.toFixed(1)}
              unit="%"
              tone={data.cpu_percent > 85 ? "alert" : "holo"}
            />

            <span className="block h-px w-full bg-border/50" />

            {/* RAM */}
            <Gauge
              value={data.ram_percent}
              label="Memory Load"
              display={`${data.ram_used_gb.toFixed(1)} / ${data.ram_total_gb.toFixed(1)}`}
              unit="GB"
              tone={data.ram_percent > 90 ? "alert" : "holo"}
            />

            <span className="block h-px w-full bg-border/50" />

            {/* Disk */}
            <Gauge
              value={data.disk.percent}
              label="Primary Storage"
              display={`${data.disk.used_gb.toFixed(1)} / ${data.disk.total_gb.toFixed(1)}`}
              unit="GB"
            />

            <span className="block h-px w-full bg-border/50" />

            {/* Power / Battery */}
            {data.battery.available && data.battery.percent !== null ? (
              <Gauge
                value={data.battery.percent}
                label={data.battery.power_plugged ? "Battery (A/C Connected)" : "Battery Discharge"}
                display={`${Math.round(data.battery.percent)}`}
                unit="%"
                tone={data.battery.percent < 20 && !data.battery.power_plugged ? "alert" : "signal"}
              />
            ) : (
              <div className="flex items-center justify-between px-1 py-1">
                <span className="holo-label">Power Source</span>
                <span className="holo-label text-signal font-semibold">MAINS A/C (PLUGGED)</span>
              </div>
            )}

            <div className="flex items-center justify-between border-t border-border/60 pt-2 text-[10px]">
              <StatusIndicator
                label={status === "stale" ? "STALE TELEMETRY" : status === "offline" ? "OFFLINE" : "SENSORS ONLINE"}
                tone={status === "live" ? "signal" : status === "stale" ? "holo" : "alert"}
              />
              {timeString && <span className="holo-label opacity-60">{timeString}</span>}
            </div>
          </>
        )}
      </div>
    </HUDPanel>
  );
}
