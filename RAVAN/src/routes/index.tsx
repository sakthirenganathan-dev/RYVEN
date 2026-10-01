import { useState } from "react";
import { createFileRoute } from "@tanstack/react-router";
import { AnimatePresence, motion } from "motion/react";
import { AIOrb } from "@/components/core/AIOrb";
import { HoloBackground } from "@/components/hud/HoloBackground";
import { HUDPanel } from "@/components/hud/HUDPanel";
import { CommandFlow } from "@/components/hud/CommandFlow";
import { HolographicText } from "@/components/hud/HolographicText";
import { StatusIndicator } from "@/components/hud/StatusIndicator";
import { TranscriptLayer } from "@/components/hud/TranscriptLayer";
import { SystemHUD } from "@/components/system/SystemHUD";
import { MemoryGraph } from "@/components/memory/MemoryGraph";
import { DeveloperHUD } from "@/components/developer/DeveloperHUD";
import { AutomationHUD } from "@/components/automation/AutomationHUD";
import { FloatingNavigation } from "@/components/navigation/FloatingNavigation";
import { VoiceControl } from "@/components/voice/VoiceControl";
import { useJarvis } from "@/hooks/useJarvis";
import { useMicLevel } from "@/hooks/useMicLevel";
import { suggestedCommands } from "@/services/jarvis";

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "RYVEN — Personal AI Assistant" },
      {
        name: "description",
        content:
          "RYVEN — Advanced Personal AI Assistant and Holographic Command Environment featuring spatial system HUDs, neural memory lattice, and voice intelligence.",
      },
      { property: "og:site_name", content: "RYVEN" },
      { property: "og:title", content: "RYVEN — Personal AI Assistant" },
      {
        property: "og:description",
        content:
          "A cinematic holographic command environment: living AI core, voice interaction, and spatial HUD panels.",
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
    steps,
    activeStep,
    transcript,
    statusText,
    submit,
    toggleListening,
  } = useJarvis();
  const level = useMicLevel(state === "listening");
  const speakLevel = state === "speaking" ? 0.35 + Math.random() * 0.2 : level;
  const [draft, setDraft] = useState("");

  return (
    <main className="relative h-screen w-screen overflow-hidden bg-background">
      <HoloBackground pull={state === "thinking"} />

      {/* top frame */}
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between px-6 py-5">
        <div className="pointer-events-auto">
          <HolographicText size="sm">Ryven</HolographicText>
          <span className="holo-label mt-1 block opacity-70">Personal AI Assistant</span>
        </div>
        <div className="pointer-events-auto flex items-center gap-5">
          <StatusIndicator label={statusText} tone={state === "idle" ? "holo" : "signal"} />
          <span className="holo-label opacity-60">NODE · LOCAL</span>
        </div>
      </div>

      {/* left navigation */}
      <div className="absolute left-6 top-1/2 z-20 -translate-y-1/2">
        <FloatingNavigation mode={mode} onSelect={setMode} />
      </div>

      {/* central core */}
      <div className="absolute inset-0 flex flex-col items-center justify-center pb-24">
        <div className="relative -mt-16 flex items-center justify-center">
          <AIOrb state={state} level={speakLevel} size={440} />
          <div className="pointer-events-none absolute -bottom-6 flex flex-col items-center gap-1">
            <HolographicText size="lg">Ryven</HolographicText>
            <AnimatePresence mode="wait">
              <motion.span
                key={statusText}
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -6 }}
                className="holo-label text-holo"
              >
                {statusText}
              </motion.span>
            </AnimatePresence>
          </div>
        </div>
      </div>

      {/* contextual panels */}
      <div className="pointer-events-none absolute inset-0 hidden items-center justify-between px-24 lg:flex">
        <div className="pointer-events-auto w-[340px] space-y-4">
          <AnimatePresence mode="popLayout">
            {steps.length > 0 && (
              <HUDPanel key="flow" title="Command sequence" code="CMD.02" tilt={6}>
                <CommandFlow steps={steps} activeStep={activeStep} />
              </HUDPanel>
            )}
            {mode === "memory" && <MemoryGraph key="mem" />}
          </AnimatePresence>
        </div>

        <div className="pointer-events-auto flex w-[340px] flex-col items-end space-y-4">
          <AnimatePresence mode="popLayout">
            {mode === "system" && <SystemHUD key="sys" />}
            {mode === "developer" && <DeveloperHUD key="dev" />}
            {mode === "automation" && <AutomationHUD key="auto" />}
          </AnimatePresence>
        </div>
      </div>

      {/* transcript + voice */}
      <div className="absolute inset-x-0 bottom-0 flex flex-col items-center gap-4 px-6 pb-8">
        <TranscriptLayer lines={transcript} />

        {mode === "chat" && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submit(draft);
              setDraft("");
            }}
            className="holo-panel holo-corners flex w-full max-w-lg items-center gap-2 rounded-sm px-3 py-2"
          >
            <span className="holo-label text-holo">CMD</span>
            <input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="Type an instruction…"
              className="w-full bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
              style={{ fontFamily: "var(--font-mono)" }}
            />
          </form>
        )}

        <VoiceControl state={state} level={speakLevel} onToggle={toggleListening} />

        <div className="flex flex-wrap items-center justify-center gap-2">
          {suggestedCommands.slice(0, 5).map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => submit(c)}
              className="holo-label rounded-full border border-border px-3 py-1 transition-colors hover:border-holo/70 hover:text-holo"
            >
              {c}
            </button>
          ))}
        </div>
      </div>
    </main>
  );
}
