import { useEffect, useState } from "react";
import {
  fetchProvidersHealth,
  fetchVisionStatus,
  fetchTelemetrySummary,
  type UnifiedProvidersStatus,
  type VisionStatus,
  type ModelTelemetrySummary,
} from "@/services/models";
import { StatusIndicator } from "@/components/hud/StatusIndicator";

export function ModelStatusPanel() {
  const [data, setData] = useState<UnifiedProvidersStatus | null>(null);
  const [vision, setVision] = useState<VisionStatus | null>(null);
  const [telemetry, setTelemetry] = useState<ModelTelemetrySummary | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    async function load() {
      try {
        const [res, vis, tel] = await Promise.allSettled([
          fetchProvidersHealth(),
          fetchVisionStatus(),
          fetchTelemetrySummary(),
        ]);
        if (mounted) {
          if (res.status === "fulfilled") setData(res.value);
          if (vis.status === "fulfilled") setVision(vis.value);
          if (tel.status === "fulfilled") setTelemetry(tel.value);
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

      {/* Model Intelligence Telemetry & Observability (M17.10 Phase 5) */}
      <div className="space-y-1.5 pt-1">
        <span className="text-[9px] text-muted-foreground tracking-widest">
          OPERATIONAL OBSERVABILITY & GOVERNANCE
        </span>

        <div className="border border-border/60 p-2 bg-background/40 space-y-1.5">
          <div className="flex items-center justify-between text-[9px]">
            <span className="text-muted-foreground">LOGICAL INFERENCE REQUESTS</span>
            <span className="text-foreground font-semibold">
              {telemetry?.logical_requests ? `${telemetry.logical_requests.success} / ${telemetry.logical_requests.total} OK` : "N/A"}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-2 text-[9px] pt-0.5">
            <div className="border border-border/40 p-1.5 bg-background/60">
              <div className="text-muted-foreground text-[8px]">LOCAL / REMOTE</div>
              <div className="font-semibold text-holo mt-0.5">
                {telemetry?.logical_requests
                  ? `${telemetry.logical_requests.local_count} L · ${telemetry.logical_requests.remote_count} R`
                  : "0 L · 0 R"}
              </div>
            </div>

            <div className="border border-border/40 p-1.5 bg-background/60">
              <div className="text-muted-foreground text-[8px]">FALLBACK RATE</div>
              <div className="font-semibold text-foreground mt-0.5">
                {telemetry?.logical_requests?.total
                  ? `${Math.round(telemetry.logical_requests.fallback_rate * 100)}% (${telemetry.logical_requests.fallback_count})`
                  : "0%"}
              </div>
            </div>
          </div>

          {/* Latency & TTFT */}
          <div className="grid grid-cols-2 gap-2 text-[9px]">
            <div className="border border-border/40 p-1.5 bg-background/60">
              <div className="text-muted-foreground text-[8px]">PRIMARY LATENCY (AVG/P95)</div>
              <div className="font-semibold text-foreground mt-0.5">
                {telemetry?.providers?.ollama?.latency?.avg_ms != null
                  ? `${telemetry.providers.ollama.latency.avg_ms}ms · ${telemetry.providers.ollama.latency.p95_ms}ms`
                  : "NO SAMPLES"}
              </div>
            </div>

            <div className="border border-border/40 p-1.5 bg-background/60">
              <div className="text-muted-foreground text-[8px]">STREAM TTFT (FIRST TOKEN)</div>
              <div className="font-semibold text-foreground mt-0.5">
                {telemetry?.providers?.ollama?.first_token_latency?.avg_ms != null
                  ? `${telemetry.providers.ollama.first_token_latency.avg_ms}ms`
                  : "NO STREAM DATA"}
              </div>
            </div>
          </div>

          <div className="flex justify-between items-center text-[8px] text-muted-foreground pt-0.5 border-t border-border/30">
            <span>BUFFER: {telemetry?.buffer_status ? `${telemetry.buffer_status.events_retained}/${telemetry.buffer_status.max_events} EVTS` : "BOUNDED"}</span>
            <span className="text-signal">PROMPTS & SECRETS: ZERO-STORE</span>
          </div>
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
