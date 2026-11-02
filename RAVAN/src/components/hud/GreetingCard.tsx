import { ChevronRight } from "lucide-react";

interface Props {
  onPromptClick?: () => void;
}

export function GreetingCard({ onPromptClick }: Props) {
  // Determine greeting based on current local hour
  const hour = new Date().getHours();
  const greeting =
    hour < 12 ? "GOOD MORNING" : hour < 17 ? "GOOD AFTERNOON" : "GOOD EVENING";

  return (
    <div className="glass-panel p-4">
      {/* Top Tag & Audio Icon */}
      <div className="flex items-center justify-between pb-1">
        <span className="text-[10px] font-mono tracking-widest text-slate-400 uppercase">
          {greeting}
        </span>
        {/* Equalizer mini wave */}
        <div className="flex items-center gap-[2px] h-3.5">
          <span className="w-[1.5px] h-2 bg-cyan-400 rounded-full animate-pulse" />
          <span className="w-[1.5px] h-3.5 bg-cyan-400 rounded-full" />
          <span className="w-[1.5px] h-1.5 bg-cyan-400 rounded-full animate-pulse" />
          <span className="w-[1.5px] h-2.5 bg-cyan-400 rounded-full" />
        </div>
      </div>

      {/* Main Brand Title */}
      <div className="mt-1">
        <h2
          className="text-xl font-bold tracking-[0.18em] text-white"
          style={{ fontFamily: "var(--font-display)" }}
        >
          R Y V E N <span className="text-cyan-400">3 . 0</span>
        </h2>
        <p className="mt-1 text-xs text-slate-300 font-medium">
          Your local AI assistant is online.
        </p>
        <p className="mt-2 text-[11px] leading-relaxed text-slate-400">
          Ready to help, automate, and build your ideas — securely on your device.
        </p>
      </div>

      {/* Interactive Prompt Action Pill */}
      <button
        type="button"
        onClick={onPromptClick}
        className="mt-3.5 flex w-full items-center justify-between rounded-xl border border-blue-500/30 bg-slate-900/60 px-3 py-2 text-xs text-slate-400 transition-all hover:border-cyan-400/60 hover:text-slate-200 hover:bg-slate-900/90"
      >
        <span className="truncate">What would you like to do today?</span>
        <ChevronRight className="h-3.5 w-3.5 shrink-0 text-cyan-400" />
      </button>
    </div>
  );
}
