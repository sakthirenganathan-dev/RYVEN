import { HUDPanel } from "@/components/hud/HUDPanel";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

export interface DeploymentPanelProps {
  provider?: string;
  framework?: string;
  buildStatus?: "PASS" | "FAIL" | "PENDING";
  testStatus?: "PASS" | "FAIL" | "PENDING";
  qualityGate?: "PASS" | "BLOCKED" | "PENDING";
  gitStatus?: "CLEAN" | "DIRTY";
  deploymentStatus?: "READY" | "PREFLIGHT" | "DEPLOYING" | "DEPLOYED" | "FAILED" | "NOT_CONFIGURED";
  liveUrl?: string;
  onDeploy?: () => void;
}

export function DeploymentPanel({
  provider = "Vercel",
  framework = "React + Vite",
  buildStatus = "PASS",
  testStatus = "PASS",
  qualityGate = "PASS",
  gitStatus = "CLEAN",
  deploymentStatus = "READY",
  liveUrl,
  onDeploy,
}: DeploymentPanelProps) {
  const toneMap = {
    DEPLOYED: "holo",
    READY: "holo",
    DEPLOYING: "violet",
    PREFLIGHT: "violet",
    FAILED: "alert",
    NOT_CONFIGURED: "signal",
  } as const;

  return (
    <HUDPanel title="Cloud Deployment" code="DPL.12" className="w-[340px]" tilt={-2}>
      <div className="space-y-3">
        {/* Header specs */}
        <div className="holo-corners relative border border-border/70 p-3 space-y-2">
          <div className="flex items-center justify-between text-xs tracking-wider">
            <span className="text-muted-foreground uppercase">Target Provider</span>
            <span
              className="text-foreground font-semibold"
              style={{ fontFamily: "var(--font-display)" }}
            >
              {provider}
            </span>
          </div>

          <div className="flex items-center justify-between text-xs tracking-wider">
            <span className="text-muted-foreground uppercase">Framework</span>
            <span className="text-foreground" style={{ fontFamily: "var(--font-mono)" }}>
              {framework}
            </span>
          </div>

          <div
            className="grid grid-cols-4 gap-1 border-t border-border/60 pt-2 text-[10px] text-muted-foreground text-center"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            <div>
              <div className="text-[9px] uppercase">Build</div>
              <span className={buildStatus === "PASS" ? "text-emerald-400" : "text-amber-400"}>
                ✓
              </span>
            </div>
            <div>
              <div className="text-[9px] uppercase">Tests</div>
              <span className={testStatus === "PASS" ? "text-emerald-400" : "text-amber-400"}>
                ✓
              </span>
            </div>
            <div>
              <div className="text-[9px] uppercase">Quality</div>
              <span className={qualityGate === "PASS" ? "text-emerald-400" : "text-rose-400"}>
                ✓
              </span>
            </div>
            <div>
              <div className="text-[9px] uppercase">Git</div>
              <span className={gitStatus === "CLEAN" ? "text-emerald-400" : "text-amber-400"}>
                ✓
              </span>
            </div>
          </div>
        </div>

        {/* Status indicator and action */}
        <div className="flex items-center justify-between border-t border-border/60 pt-2">
          <div className="flex items-center gap-2">
            <span
              className="text-[10px] text-muted-foreground uppercase"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              Status
            </span>
            <StatusIndicator
              label={deploymentStatus}
              tone={toneMap[deploymentStatus] || "holo"}
              pulse={deploymentStatus === "DEPLOYING" || deploymentStatus === "PREFLIGHT"}
            />
          </div>

          {liveUrl ? (
            <a
              href={liveUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-holo hover:underline flex items-center gap-1"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              Open URL ↗
            </a>
          ) : (
            onDeploy && (
              <button
                onClick={onDeploy}
                className="px-2 py-1 text-xs border border-border/70 hover:border-holo hover:text-holo transition-colors"
                style={{ fontFamily: "var(--font-mono)" }}
              >
                DEPLOY
              </button>
            )
          )}
        </div>
      </div>
    </HUDPanel>
  );
}
