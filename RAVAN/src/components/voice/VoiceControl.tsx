import { Mic } from "lucide-react";
import type { CoreState } from "@/types/jarvis";
import { cn } from "@/lib/utils";

const LABEL: Record<CoreState, string> = {
  boot: "STANDBY",
  idle: "MIC",
  listening: "LISTENING",
  thinking: "PROCESSING",
  executing: "EXECUTING",
  speaking: "SPEAKING",
};

export function VoiceControl({
  state,
  level,
  onToggle,
}: {
  state: CoreState;
  level: number;
  onToggle: () => void;
}) {
  const active = state !== "idle" && state !== "boot";

  return (
    <div className="flex flex-col items-center gap-3">
      <button
        type="button"
        onClick={onToggle}
        aria-label="Toggle voice interaction"
        className={cn(
          "group relative grid h-16 w-16 place-items-center rounded-full border transition-all duration-300",
          active
            ? "border-holo/80 bg-holo/15 glow-core"
            : "border-holo/35 bg-holo/5 hover:border-holo/70 hover:bg-holo/10 glow-soft",
        )}
        style={{ transform: `scale(${1 + level * 0.09})` }}
      >
        {active &&
          [0, 1].map((i) => (
            <span
              key={i}
              className="absolute inset-0 rounded-full border border-holo/50"
              style={{ animation: `holo-pulse-ring 1.8s ease-out ${i * 0.9}s infinite` }}
            />
          ))}
        <span
          className="absolute inset-[-10px] rounded-full border border-dashed border-holo/25"
          style={{ animation: "holo-spin 18s linear infinite" }}
        />
        <Mic className="h-5 w-5 text-holo transition-transform group-hover:scale-110" />
      </button>

      <div className="flex h-5 items-end gap-[3px]">
        {Array.from({ length: 13 }, (_, i) => {
          const falloff = 1 - Math.abs(i - 6) / 8;
          const h = active
            ? Math.max(3, level * 20 * falloff * (0.6 + Math.abs(Math.sin(i * 2.1 + level * 11))))
            : 3;
          return (
            <span
              key={i}
              className="w-[2px] rounded-full bg-holo/80 transition-[height] duration-100"
              style={{ height: `${h}px` }}
            />
          );
        })}
      </div>

      <span className="holo-label text-holo">{LABEL[state]}</span>
    </div>
  );
}
