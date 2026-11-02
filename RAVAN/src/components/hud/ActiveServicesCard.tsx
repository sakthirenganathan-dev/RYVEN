import { useEffect, useState } from "react";
import { checkBackendHealth, type BackendHealthInfo } from "@/services/jarvis";
import { Zap, Bot, Globe } from "lucide-react";

export function ActiveServicesCard() {
  const [health, setHealth] = useState<BackendHealthInfo | null>(null);

  useEffect(() => {
    let mounted = true;
    const probe = async () => {
      const h = await checkBackendHealth();
      if (mounted) setHealth(h);
    };
    probe();
    const interval = setInterval(probe, 8000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  const backendOnline = health?.backend === "online";
  const ollamaOnline = health?.ollama === "online";
  const modelName = health?.model || "qwen2.5:7b";

  return (
    <div className="glass-panel p-3.5">
      {/* Header */}
      <div className="flex items-center gap-1.5 pb-2.5">
        <span className="text-[10px] text-cyan-400">▲</span>
        <span className="section-title">
          ACTIVE SERVICES
        </span>
      </div>

      {/* Services List */}
      <div className="space-y-2 text-[11px]">
        {/* Service 1: FastAPI */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="flex h-5 w-5 items-center justify-center rounded bg-emerald-950/50 text-emerald-400 border border-emerald-500/30">
              <Zap className="h-3 w-3" />
            </div>
            <span className="font-medium text-slate-200">FastAPI Backend</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1 text-[10px] text-emerald-400">
              <span className={`h-1.5 w-1.5 rounded-full ${backendOnline ? "bg-emerald-400 animate-pulse" : "bg-red-400"}`} />
              {backendOnline ? "Online" : "Offline"}
            </span>
            <span className="font-mono text-[10px] text-slate-400">:8000</span>
          </div>
        </div>

        {/* Service 2: Ollama */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="flex h-5 w-5 items-center justify-center rounded bg-cyan-950/50 text-cyan-400 border border-cyan-500/30">
              <Bot className="h-3 w-3" />
            </div>
            <span className="font-medium text-slate-200">Ollama ({modelName})</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1 text-[10px] text-emerald-400">
              <span className={`h-1.5 w-1.5 rounded-full ${ollamaOnline ? "bg-emerald-400 animate-pulse" : "bg-amber-400"}`} />
              {ollamaOnline ? "Online" : "Connecting"}
            </span>
            <span className="font-mono text-[10px] text-slate-400">:11434</span>
          </div>
        </div>

        {/* Service 3: Frontend (Vite) */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="flex h-5 w-5 items-center justify-center rounded bg-purple-950/50 text-purple-400 border border-purple-500/30">
              <Globe className="h-3 w-3" />
            </div>
            <span className="font-medium text-slate-200">Frontend (Vite)</span>
          </div>
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1 text-[10px] text-emerald-400">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Online
            </span>
            <span className="font-mono text-[10px] text-slate-400">:8080</span>
          </div>
        </div>
      </div>
    </div>
  );
}
