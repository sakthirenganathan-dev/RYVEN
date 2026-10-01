import { automationRoutines } from "@/services/jarvis";
import { HUDPanel } from "@/components/hud/HUDPanel";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

const tone = { RUNNING: "signal", READY: "holo", SCHEDULED: "violet" } as const;

export function AutomationHUD() {
  return (
    <HUDPanel title="Automation protocols" code="AUTO.05" className="w-[340px]" tilt={-4}>
      <div className="space-y-3">
        {automationRoutines.map((routine, i) => (
          <div key={routine.id} className="holo-corners relative border border-border/70 px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span
                className="truncate text-sm tracking-[0.12em] text-foreground"
                style={{ fontFamily: "var(--font-display)" }}
              >
                {routine.name}
              </span>
              <StatusIndicator
                label={routine.status}
                tone={tone[routine.status]}
                pulse={routine.status === "RUNNING"}
              />
            </div>
            <div
              className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              <span>{routine.protocol}</span>
              <span>{routine.trigger}</span>
            </div>
            <div className="mt-2 h-px w-full bg-border/70">
              <div
                className="h-px bg-holo transition-all duration-700"
                style={{
                  width: routine.status === "RUNNING" ? "100%" : "40%",
                  boxShadow: "0 0 8px var(--color-holo)",
                }}
              />
            </div>
            {i === 0 && <span className="scan-line top-0" />}
          </div>
        ))}
        <div
          className="grid grid-cols-3 gap-2 border-t border-border/60 pt-2 text-[10px] text-muted-foreground"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          <span>ENGINE · ACTIVE</span>
          <span>ROUTINES · 4/4</span>
          <span>DISPATCH · ZERO LAG</span>
        </div>
      </div>
    </HUDPanel>
  );
}
