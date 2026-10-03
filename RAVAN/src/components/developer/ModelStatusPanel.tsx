import { useEffect, useState } from "react";
import {
  fetchProvidersHealth,
  fetchVisionStatus,
  type UnifiedProvidersStatus,
  type VisionStatus,
} from "@/services/models";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

export function ModelStatusPanel() {
  const [data, setData] = useState<UnifiedProvidersStatus | null>(null);
  const [vision, setVision] = useState<VisionStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    async function load() {
      try {
        const [res, vis] = await Promise.allSettled([
          fetchProvidersHealth(),
          fetchVisionStatus(),
        ]);
        if (mounted) {
          if (res.status === "fulfilled") setData(res.value);
          if (vis.status === "fulfilled") setVision(vis.value);
        }
      } catch (err) {
        console.warn("Failed to load providers health:", err);
      } finally {
        if (mounted) setLoading(false);
      }
    }
    load();
    const timer = setInterval(load, 10000);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, []);

  const ollama = data?.providers?.ollama;
  const grok = data?.providers?.grok;
  const hfRemote = data?.providers?.huggingface_remote;
  const hfLocal = data?.providers?.huggingface_local;

  return (
    <div className="space-y-2 text-[10px]" style={{ fontFamily: "var(--font-mono)" }}>
      {/* Active Model Engine Banner */}
      <div className="holo-corners border border-holo/40 bg-holo/10 p-2">
        <div className="flex items-center justify-between">
          <span className="text-foreground tracking-widest text-[9px]">ACTIVE MODEL ENGINE</span>
          <StatusIndicator label="LOCAL PRIMARY" tone="holo" pulse />
        </div>
        <div className="mt-1 flex items-baseline justify-between">
          <span
            className="text-sm font-bold text-holo"
            style={{ fontFamily: "var(--font-display)" }}
          >
            {data?.active_default_model || "qwen2.5:7b"}
          </span>
          <span className="text-[9px] text-muted-foreground">PROVIDER · OLLAMA</span>
        </div>
        <div className="mt-1 text-[9px] text-muted-foreground flex justify-between">
          <span>BOUNDARY: LOCAL-FIRST</span>
          <span className="text-signal">ZERO TELEMETRY LEAK</span>
        </div>
      </div>

      {/* Provider Matrix */}
      <div className="space-y-1.5 pt-1">
        <span className="text-[9px] text-muted-foreground tracking-widest">
          PROVIDER ROUTING MATRIX
        </span>

        {/* 1. Ollama Local */}
        <div className="flex items-center justify-between border border-border/60 px-2 py-1 bg-background/50">
          <div>
            <div className="text-foreground font-semibold">OLLAMA (LOCAL)</div>
            <div className="text-[9px] text-muted-foreground">{ollama?.model || "qwen2.5:7b"}</div>
          </div>
          <StatusIndicator
            label={ollama?.available ? "ONLINE · DEFAULT" : "STANDBY"}
            tone={ollama?.available ? "holo" : "signal"}
          />
        </div>

        {/* 2. Hugging Face Local */}
        <div className="flex items-center justify-between border border-border/60 px-2 py-1 bg-background/50">
          <div>
            <div className="text-foreground">HUGGING FACE (LOCAL)</div>
            <div className="text-[9px] text-muted-foreground">LAZY LOADING ENVELOPE</div>
          </div>
          <span className="text-[9px] text-muted-foreground px-1.5 py-0.5 border border-border/40 rounded">
            {hfLocal?.available ? "CACHED" : "UNLOADED"}
          </span>
        </div>

        {/* 3. Grok API Remote */}
        <div className="flex items-center justify-between border border-border/40 px-2 py-1 opacity-75 bg-background/30">
          <div>
            <div className="text-muted-foreground">GROK API (REMOTE)</div>
            <div className="text-[9px] text-muted-foreground">{grok?.model || "grok-2-latest"}</div>
          </div>
          <span className="text-[9px] text-amber-500/80 px-1.5 py-0.5 border border-amber-500/30 rounded">
            {grok?.configured ? "OPT-IN" : "DISABLED"}
          </span>
        </div>

        {/* 4. Hugging Face Remote */}
        <div className="flex items-center justify-between border border-border/40 px-2 py-1 opacity-75 bg-background/30">
          <div>
            <div className="text-muted-foreground">HF INFERENCE (REMOTE)</div>
            <div className="text-[9px] text-muted-foreground">
              {hfRemote?.model || "Qwen2.5-Coder-32B"}
            </div>
          </div>
          <span className="text-[9px] text-amber-500/80 px-1.5 py-0.5 border border-amber-500/30 rounded">
            {hfRemote?.remote_allowed ? "ALLOWED" : "DISABLED"}
          </span>
        </div>
      </div>

      {/* Vision & OCR Subsystem (Local-Only) */}
      <div className="space-y-1.5 pt-1">
        <span className="text-[9px] text-muted-foreground tracking-widest">
          VISION & OCR PIPELINE (LOCAL-ONLY)
        </span>

        <div className="flex items-center justify-between border border-border/60 px-2 py-1 bg-background/50">
          <div>
            <div className="text-foreground font-semibold">VISION PERCEPTION</div>
            <div className="text-[9px] text-muted-foreground">
              MODEL · {vision?.default_model || "moondream"}
            </div>
          </div>
          <StatusIndicator
            label={vision?.installed_vision_models?.length ? "READY" : "AVAILABLE"}
            tone={vision?.installed_vision_models?.length ? "holo" : "muted"}
          />
        </div>

        <div className="flex items-center justify-between border border-border/60 px-2 py-1 bg-background/50">
          <div>
            <div className="text-foreground">OCR ENGINE</div>
            <div className="text-[9px] text-muted-foreground">
              {vision?.ocr_engine === "tesseract" ? "PYTESSERACT (LOCAL)" : "VISION MODEL FALLBACK"}
            </div>
          </div>
          <span className="text-[9px] text-signal px-1.5 py-0.5 border border-border/40 rounded">
            {vision?.pytesseract_available ? "ACCELERATED" : "FALLBACK"}
          </span>
        </div>
      </div>

      {/* Safety Policy Tag */}
      <div className="border-t border-border/60 pt-1.5 text-[9px] text-muted-foreground flex items-center justify-between">
        <span>SECURITY: LOCAL ISOLATION</span>
        <span className="text-signal">SECRETS BLOCKED</span>
      </div>
    </div>
  );
}
