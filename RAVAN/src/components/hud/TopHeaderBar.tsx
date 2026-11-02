import { useState, useEffect } from "react";
import { Sparkles, Sun } from "lucide-react";
import { checkBackendHealth, type BackendHealthInfo } from "@/services/jarvis";

interface Props {
  health: BackendHealthInfo | null;
}

export function TopHeaderBar({ health: initialHealth }: Props) {
  const [health, setHealth] = useState<BackendHealthInfo | null>(initialHealth);
  const [timeStr, setTimeStr] = useState("00:24");
  const [dateStr, setDateStr] = useState("Oct 11, 2026");

  useEffect(() => {
    if (initialHealth) {
      setHealth(initialHealth);
    }
  }, [initialHealth]);

  useEffect(() => {
    let mounted = true;
    const probe = async () => {
      const h = await checkBackendHealth();
      if (mounted) setHealth(h);
    };
    probe();
    const interval = setInterval(probe, 6000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    const updateTime = () => {
      const now = new Date();
      setTimeStr(
        now.toLocaleTimeString([], {
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        })
      );
      setDateStr(
        now.toLocaleDateString([], {
          month: "short",
          day: "numeric",
          year: "numeric",
        })
      );
    };
    updateTime();
    const interval = setInterval(updateTime, 1000);
    return () => clearInterval(interval);
  }, []);

  const isChecking = health === null;
  const backendOnline = health?.backend === "online";
  const ollamaOnline = health?.ollama === "online";
  const modelName = health?.model?.toUpperCase() || "QWEN2.5:7B";

  return (
    <header className="flex h-12 w-full items-center justify-between px-6 select-none z-30">
      {/* Left Brand */}
      <div className="flex items-center gap-3">
        {/* Delta Logo Icon */}
        <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-cyan-400/40 bg-cyan-950/40 shadow-[0_0_14px_rgba(34,211,238,0.4)]">
          <svg viewBox="0 0 24 24" className="h-5 w-5 fill-cyan-400">
            <path d="M12 3L2 21h4.5l5.5-10.5L17.5 21H22L12 3z" />
          </svg>
        </div>
        <div>
          <div
            className="text-sm font-bold tracking-[0.25em] text-white"
            style={{ fontFamily: "var(--font-display)" }}
          >
            RYVEN 3.0
          </div>
          <div className="text-[9px] font-mono tracking-wider text-cyan-400/80">
            YOUR AI OPERATING SYSTEM
          </div>
        </div>
      </div>

      {/* Center Status Pills — Dead Centered */}
      <div className="absolute left-1/2 -translate-x-1/2 flex items-center gap-2.5">
        {/* Pill 1: Backend */}
        <div
          className={`flex items-center gap-2 rounded-full border px-3 py-1 text-[11px] font-mono font-semibold transition-colors ${
            backendOnline
              ? "border-emerald-500/40 bg-emerald-950/30 text-emerald-400 shadow-[0_0_12px_rgba(34,197,94,0.3)]"
              : isChecking
              ? "border-cyan-500/40 bg-cyan-950/30 text-cyan-400"
              : "border-red-500/40 bg-red-950/30 text-red-400"
          }`}
        >
          <span
            className={`h-2 w-2 rounded-full ${
              backendOnline
                ? "bg-emerald-400 animate-pulse"
                : isChecking
                ? "bg-cyan-400 animate-pulse"
                : "bg-red-400"
            }`}
          />
          <span>BACKEND: {backendOnline ? "ONLINE" : isChecking ? "CHECKING..." : "OFFLINE"}</span>
        </div>

        {/* Pill 2: Ollama */}
        <div
          className={`flex items-center gap-2 rounded-full border px-3 py-1 text-[11px] font-mono font-semibold transition-colors ${
            ollamaOnline
              ? "border-cyan-500/40 bg-cyan-950/30 text-cyan-300 shadow-[0_0_12px_rgba(34,211,238,0.25)]"
              : isChecking
              ? "border-cyan-500/40 bg-cyan-950/30 text-cyan-400"
              : "border-red-500/40 bg-red-950/30 text-red-400"
          }`}
        >
          <Sparkles className="h-3 w-3 text-cyan-400" />
          <span>OLLAMA: {ollamaOnline ? modelName : isChecking ? "LINKING..." : "OFFLINE"}</span>
        </div>

        {/* Pill 3: Node */}
        <div className="flex items-center gap-2 rounded-full border border-blue-500/30 bg-slate-900/40 px-3 py-1 text-[11px] font-mono text-slate-300">
          <span className="text-[10px] text-cyan-400">⬡</span>
          <span>NODE: 127.0.0.1:8000</span>
        </div>
      </div>

      {/* Right Clock & Date Badge */}
      <div className="flex items-center gap-2 rounded-xl border border-slate-800/80 bg-slate-900/50 px-3.5 py-1.5 backdrop-blur-md shadow-[0_0_12px_rgba(0,0,0,0.5)]">
        <Sun className="h-3.5 w-3.5 text-amber-400 shrink-0" />
        <div className="flex items-baseline gap-2 font-mono">
          <span className="text-xs font-bold text-white tracking-wide">{timeStr}</span>
          <span className="text-[10px] text-slate-400">{dateStr}</span>
        </div>
      </div>
    </header>
  );
}
