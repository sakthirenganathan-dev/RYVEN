import { devProjects } from "@/services/jarvis";
import { HUDPanel } from "@/components/hud/HUDPanel";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

const tone = { RUNNING: "signal", READY: "holo", BUILDING: "violet" } as const;

export function DeveloperHUD() {
  return (
    <HUDPanel title="Developer environment" code="DEV.04" className="w-[340px]" tilt={-4}>
      <div className="space-y-3">
        {devProjects.map((p, i) => (
          <div key={p.name} className="holo-corners relative border border-border/70 px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span
                className="truncate text-sm tracking-[0.12em] text-foreground"
                style={{ fontFamily: "var(--font-display)" }}
              >
                {p.name}
              </span>
              <StatusIndicator
                label={p.status}
                tone={tone[p.status]}
                pulse={p.status !== "READY"}
              />
            </div>
            <div
              className="mt-1 flex items-center justify-between text-[10px] text-muted-foreground"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              <span>{p.stack}</span>
              <span>{p.branch}</span>
            </div>
            <div className="mt-2 h-px w-full bg-border/70">
              <div
                className="h-px bg-holo transition-all duration-700"
                style={{ width: `${p.load}%`, boxShadow: "0 0 8px var(--color-holo)" }}
              />
            </div>
            {i === 0 && <span className="scan-line top-0" />}
          </div>
        ))}
        <div
          className="grid grid-cols-2 gap-2 border-t border-border/60 pt-2 text-[10px] text-muted-foreground"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          <span>GIT · CLEAN</span>
          <span>DEPLOY · EDGE</span>
          <span className="text-holo">GRAPH · INDEXED</span>
          <span className="text-holo">CTX · OPTIMIZED</span>
          <span className="text-signal col-span-2 flex items-center justify-between">
            <span>HEALTH · HEALTHY</span>
            <span>48 MS</span>
          </span>
        </div>
      </div>
    </HUDPanel>
  );
}
