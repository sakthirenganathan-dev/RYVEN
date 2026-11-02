import { useEffect, useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { HoloBackground } from "@/components/hud/HoloBackground";
import { TopHeaderBar } from "@/components/hud/TopHeaderBar";
import { SidebarNav } from "@/components/navigation/SidebarNav";
import { GreetingCard } from "@/components/hud/GreetingCard";
import { QuickActionsCard } from "@/components/hud/QuickActionsCard";
import { AIOrb } from "@/components/core/AIOrb";
import { SystemOverviewCard } from "@/components/system/SystemOverviewCard";
import { ActiveServicesCard } from "@/components/hud/ActiveServicesCard";
import { MemoryLatticeWidget } from "@/components/memory/MemoryLatticeWidget";
import { RecentActivityCard } from "@/components/hud/RecentActivityCard";
import { CommandDeck } from "@/components/hud/CommandDeck";
import { VoiceReadyWidget } from "@/components/voice/VoiceReadyWidget";
import { TranscriptLayer } from "@/components/hud/TranscriptLayer";
import { SystemHUD } from "@/components/system/SystemHUD";
import { DeveloperHUD } from "@/components/developer/DeveloperHUD";
import { AutomationHUD } from "@/components/automation/AutomationHUD";
import { MemoryHUD } from "@/components/MemoryHUD";
import { useJarvis } from "@/hooks/useJarvis";
import { useMicLevel } from "@/hooks/useMicLevel";
import { checkBackendHealth, type BackendHealthInfo } from "@/services/jarvis";
import { logActivityEvent } from "@/services/activity";
import { X } from "lucide-react";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "RYVEN 3.0 — Holographic Personal AI" },
      {
        name: "description",
        content:
          "RYVEN 3.0 — High-fidelity holographic personal AI operating interface featuring planetary volumetric core, real hardware telemetry, and local neural intelligence.",
      },
      { property: "og:site_name", content: "RYVEN" },
      { property: "og:title", content: "RYVEN 3.0 — Holographic Personal AI" },
      {
        property: "og:description",
        content:
          "A cinematic holographic desktop cockpit: living AI core, voice interaction, and spatial HUD panels.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
    ],
  }),
  component: CommandCenter,
});

function CommandCenter() {
  const {
    state,
    mode,
    setMode,
    transcript,
    submit,
    toggleListening,
  } = useJarvis();

  const level = useMicLevel(state === "listening");
  const speakLevel = state === "speaking" ? 0.4 + Math.random() * 0.2 : level;
  const [health, setHealth] = useState<BackendHealthInfo | null>(null);

  useEffect(() => {
    let mounted = true;
    const probe = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
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

  // Keyboard shortcut: ESC returns from subsystem view to core cockpit
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && mode !== "core" && mode !== "chat") {
        setMode("core");
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [mode, setMode]);

  const handleCommandSubmit = async (cmd: string) => {
    logActivityEvent(`Command: "${cmd.length > 28 ? cmd.substring(0, 28) + "..." : cmd}"`, "cyan");
    await submit(cmd);
  };

  return (
    <main className="relative flex h-screen w-screen flex-col overflow-hidden bg-slate-950 font-sans text-slate-100 select-none">
      {/* 1. Deep Space Cosmic Canvas with Stars, Mountains & Perspective Floor */}
      <HoloBackground pull={state === "thinking"} />

      {/* 2. Top Header Telemetry & Profile Bar */}
      <div className="relative z-30 shrink-0 h-12 w-full">
        <TopHeaderBar health={health} />
      </div>

      {/* 3. Main Desktop Cockpit Workspace */}
      <div className="relative z-10 flex flex-1 w-full min-h-0 px-6 pt-1 pb-4">
        {/* Left Section: Glass Navigation Sidebar */}
        <div className="mr-5 flex h-full flex-col justify-between pb-12">
          <SidebarNav currentMode={mode} onSelectMode={setMode} />

          {/* Inspirational Quote below sidebar */}
          <div className="mt-3 max-w-[172px] text-[10px] italic leading-tight text-slate-400/80">
            &ldquo;The best time to build the future is now.&rdquo;
            <span className="block not-italic text-slate-500 font-mono mt-0.5">
              — RYVEN
            </span>
          </div>
        </div>

        {/* Center-Left Section: Greeting Card + Quick Actions */}
        <div className="flex w-[280px] shrink-0 flex-col gap-3 pb-20">
          <GreetingCard
            onPromptClick={() => handleCommandSubmit("What can you do for me today?")}
          />
          <QuickActionsCard
            onAction={handleCommandSubmit}
            onOpenMemory={() => setMode("memory")}
            onOpenAutomation={() => setMode("automation")}
          />
        </div>

        {/* Center Section: Planetary Holographic AI Core (Perfect Dead-Center) */}
        <div className="flex flex-1 flex-col items-center justify-center pb-8 pt-0">
          <AIOrb
            state={state}
            level={speakLevel}
            size={480}
            onClick={toggleListening}
          />
        </div>

        {/* Right Section: 4 Holographic Telemetry Cards */}
        <div className="flex w-[360px] shrink-0 flex-col gap-3 pb-20 overflow-y-auto no-scrollbar">
          {/* Card 1: System Overview (4 circular gauges) */}
          <SystemOverviewCard onClick={() => setMode("system")} />

          {/* Card 2: Active Services */}
          <ActiveServicesCard />

          {/* Card 3: Memory Lattice */}
          <MemoryLatticeWidget onExplore={() => setMode("memory")} />

          {/* Card 4: Recent Activity */}
          <RecentActivityCard />
        </div>
      </div>

      {/* 4. Bottom Command Deck & Voice Control Bar */}
      <div className="pointer-events-none absolute inset-x-0 bottom-3 z-30 flex flex-col items-center justify-end px-6">
        {/* Transcript Floating Layer for Assistant Responses */}
        <div className="pointer-events-auto mb-2 w-full max-w-2xl">
          <TranscriptLayer lines={transcript} />
        </div>

        {/* Floating Command Console and Voice Ready Widget — Perfectly Balanced */}
        <div className="pointer-events-auto flex w-full items-end justify-between">
          {/* Left Spacer to align command deck symmetrically with right voice widget */}
          <div className="w-[230px] shrink-0 hidden lg:block" />

          {/* Center Command Deck (Dead Centered) */}
          <div className="flex flex-1 justify-center px-4">
            <CommandDeck
              onSubmit={handleCommandSubmit}
              onToggleMic={toggleListening}
              isListening={state === "listening"}
              isLoading={state === "thinking" || state === "executing"}
              activeMode={mode}
              onSelectMode={setMode}
            />
          </div>

          {/* Right Voice Ready Widget */}
          <div className="w-[230px] shrink-0">
            <VoiceReadyWidget
              state={state}
              level={speakLevel}
              onToggle={toggleListening}
            />
          </div>
        </div>
      </div>

      {/* 5. Subsystem Modal Overlay when specialized navigation is active */}
      {mode !== "core" && mode !== "chat" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 backdrop-blur-md p-6">
          <div className="relative w-full max-w-4xl max-h-[85vh] overflow-y-auto rounded-3xl border border-cyan-500/40 bg-slate-950/90 p-6 shadow-[0_0_40px_rgba(0,240,255,0.25)] no-scrollbar">
            <div className="flex items-center justify-between border-b border-cyan-500/20 pb-4 mb-4">
              <span className="text-xs font-mono font-bold tracking-widest text-cyan-400 uppercase">
                RYVEN SUBSYSTEM :: {mode.toUpperCase()}
              </span>
              <button
                type="button"
                onClick={() => setMode("core")}
                className="flex items-center gap-1.5 rounded-lg border border-cyan-500/30 px-3 py-1 text-xs text-slate-300 hover:border-cyan-400 hover:text-white transition-all cursor-pointer"
              >
                <X className="h-4 w-4" />
                <span>CLOSE [ESC]</span>
              </button>
            </div>

            {mode === "system" && <SystemHUD className="w-full" />}
            {mode === "memory" && <MemoryHUD />}
            {mode === "developer" && <DeveloperHUD />}
            {mode === "automation" && <AutomationHUD />}
          </div>
        </div>
      )}
    </main>
  );
}
