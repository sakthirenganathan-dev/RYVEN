import { useState } from "react";
import { devProjects } from "@/services/jarvis";
import { HUDPanel } from "@/components/hud/HUDPanel";
import { StatusIndicator } from "@/components/hud/StatusIndicator";
import { LiveActivityPanel } from "@/components/developer/LiveActivityPanel";
import { BrowserActivityPanel } from "@/components/developer/BrowserActivityPanel";

const tone = { RUNNING: "signal", READY: "holo", BUILDING: "violet" } as const;

type DeveloperView = "projects" | "activity" | "browser";

export function DeveloperHUD() {
  const [view, setView] = useState<DeveloperView>("projects");

  return (
    <HUDPanel title="Developer environment" code="DEV.04" className="w-[340px]" tilt={-4}>
      <div className="space-y-3">
        {/* View toggle */}
        <div className="flex gap-1 border-b border-border/40 pb-2">
          <button
            type="button"
            onClick={() => setView("projects")}
            className={`px-2 py-0.5 text-[9px] rounded transition-colors tracking-widest ${
              view === "projects"
                ? "bg-holo/20 text-holo border border-holo/40"
                : "text-muted-foreground hover:text-foreground"
            }`}
            style={{ fontFamily: "var(--font-mono)" }}
          >
            PROJECTS
          </button>
          <button
            type="button"
            onClick={() => setView("activity")}
            className={`px-2 py-0.5 text-[9px] rounded transition-colors tracking-widest ${
              view === "activity"
                ? "bg-holo/20 text-holo border border-holo/40"
                : "text-muted-foreground hover:text-foreground"
            }`}
            style={{ fontFamily: "var(--font-mono)" }}
          >
            LIVE ACTIVITY
          </button>
          <button
            type="button"
            onClick={() => setView("browser")}
            className={`px-2 py-0.5 text-[9px] rounded transition-colors tracking-widest ${
              view === "browser"
                ? "bg-holo/20 text-holo border border-holo/40"
                : "text-muted-foreground hover:text-foreground"
            }`}
            style={{ fontFamily: "var(--font-mono)" }}
          >
            BROWSER
          </button>
        </div>

        {/* Projects view */}
        {view === "projects" && (
          <>
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
              <span className="text-holo col-span-2 flex items-center justify-between border-t border-border/40 pt-1">
                <span>ORCH · READY</span>
                <span>DAG · ACTIVE</span>
              </span>
            </div>
          </>
        )}

        {/* Live Activity view */}
        {view === "activity" && <LiveActivityPanel />}

        {/* Browser Activity view */}
        {view === "browser" && <BrowserActivityPanel />}
      </div>
    </HUDPanel>
  );
}
