import { Mic } from "lucide-react";
import type { CoreState } from "@/types/jarvis";

interface Props {
  state: CoreState;
  level: number;
  onToggle: () => void;
}

export function VoiceReadyWidget({ state, level, onToggle }: Props) {
  const isListening = state === "listening";
  const isSpeaking = state === "speaking";

  const title = isListening
    ? "LISTENING"
    : isSpeaking
    ? "SPEAKING"
    : "VOICE READY";

  const subtitle = isListening
    ? "Receiving voice stream..."
    : isSpeaking
    ? "Audio playback active..."
    : "Listening for your command...";

  return (
    <div
      onClick={onToggle}
      role="button"
      tabIndex={0}
      className="glass-panel flex cursor-pointer flex-col p-3.5 shadow-[0_0_24px_rgba(34,211,238,0.2),inset_0_0_30px_rgba(59,130,246,0.08)] group hover:border-cyan-400/60 transition-all"
    >
      <div className="flex items-center gap-3">
        {/* Glowing Mic Icon Circle */}
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full border transition-all ${
            isListening || isSpeaking
              ? "border-cyan-400 bg-cyan-500/25 text-cyan-300 shadow-[0_0_16px_rgba(34,211,238,0.6)] animate-pulse"
              : "border-cyan-500/40 bg-slate-900/80 text-cyan-400 shadow-[0_0_10px_rgba(34,211,238,0.3)] group-hover:border-cyan-300"
          }`}
        >
          <Mic className="h-4 w-4" />
        </div>

        {/* Text Details */}
        <div>
          <div className="text-xs font-bold tracking-wider text-white">
            {title}
          </div>
          <div className="text-[10px] text-slate-400">
            {subtitle}
          </div>
        </div>
      </div>

      {/* Animated Waveform Bars */}
      <div className="mt-2.5 flex items-center justify-between gap-[3px] h-4 px-1">
        {Array.from({ length: 18 }, (_, i) => {
          const centerDist = Math.abs(i - 8.5) / 9;
          const falloff = 1 - centerDist * 0.6;
          const heightMultiplier = isListening
            ? Math.max(3, level * 16 * falloff)
            : isSpeaking
            ? Math.max(3, (level * 12 + Math.sin(i * 0.7) * 4 + 6) * falloff)
            : Math.max(2, (Math.sin(i * 0.6) * 2.5 + 4) * falloff);

          return (
            <span
              key={i}
              className="w-[2px] rounded-full transition-all duration-150"
              style={{
                height: `${heightMultiplier}px`,
                backgroundColor:
                  i % 2 === 0 ? "rgb(56, 189, 248)" : "rgb(192, 132, 252)",
              }}
            />
          );
        })}
      </div>
    </div>
  );
}
