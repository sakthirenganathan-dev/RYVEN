import { useState, useRef, type FormEvent } from "react";
import {
  Plus,
  Send,
  Mic,
  MessageSquare,
  Wrench,
  Brain,
  Code2,
  Globe,
  Radio,
  Loader2,
} from "lucide-react";
import type { HudMode } from "@/types/jarvis";

interface Props {
  onSubmit: (command: string) => void;
  onToggleMic: () => void;
  isListening: boolean;
  isLoading?: boolean;
  activeMode: HudMode;
  onSelectMode: (mode: HudMode) => void;
}

export function CommandDeck({
  onSubmit,
  onToggleMic,
  isListening,
  isLoading = false,
  activeMode,
  onSelectMode,
}: Props) {
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  const handleSubmit = (e?: FormEvent) => {
    if (e) e.preventDefault();
    if (!draft.trim() || isLoading) return;
    onSubmit(draft.trim());
    setDraft("");
    if (inputRef.current) {
      inputRef.current.style.height = "auto";
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.key === "Enter" && !e.shiftKey) || (e.key === "Enter" && (e.ctrlKey || e.metaKey))) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setDraft(e.target.value);
    // Auto-adjust height up to 120px
    e.target.style.height = "auto";
    e.target.style.height = `${Math.min(e.target.scrollHeight, 120)}px`;
  };

  const modeButtons = [
    { id: "chat" as HudMode, label: "Chat", icon: MessageSquare },
    { id: "core" as HudMode, label: "Tools", icon: Wrench },
    { id: "memory" as HudMode, label: "Memory", icon: Brain },
    { id: "developer" as HudMode, label: "Code", icon: Code2 },
    { id: "automation" as HudMode, label: "Web", icon: Globe },
    { id: "voice" as HudMode, label: "Voice", icon: Radio },
  ];

  return (
    <div className="glass-panel w-full max-w-2xl p-2.5 shadow-[0_0_24px_rgba(34,211,238,0.22),inset_0_0_30px_rgba(59,130,246,0.08)] focus-within:border-cyan-400/70 focus-within:shadow-[0_0_30px_rgba(34,211,238,0.35)] transition-all">
      {/* Top Input Row */}
      <form onSubmit={handleSubmit} className="flex items-center gap-2">
        {/* Plus Button */}
        <button
          type="button"
          onClick={() => {
            if (inputRef.current) {
              setDraft((prev) => (prev ? `${prev}\n/` : "/"));
              inputRef.current.focus();
            }
          }}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-blue-500/30 bg-slate-900/60 text-slate-300 transition-all hover:border-cyan-400 hover:text-white cursor-pointer"
          title="Add context or file instruction"
        >
          <Plus className="h-4 w-4" />
        </button>

        {/* Multi-line or Single line Text Area */}
        <textarea
          ref={inputRef}
          rows={1}
          value={draft}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          placeholder="Ask me anything... (Shift+Enter for newline)"
          className="flex-1 bg-transparent px-2 py-1.5 text-sm text-white placeholder-slate-400 outline-none resize-none leading-tight disabled:opacity-50"
          style={{ minHeight: "28px", maxHeight: "120px" }}
        />

        {/* Send Button */}
        <button
          type="submit"
          disabled={!draft.trim() || isLoading}
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition-all cursor-pointer ${
            isLoading
              ? "border-cyan-500/40 bg-cyan-950/40 text-cyan-400 animate-spin"
              : draft.trim()
              ? "border-cyan-400 bg-cyan-500/20 text-cyan-300 shadow-[0_0_12px_rgba(34,211,238,0.5)] hover:bg-cyan-500/30"
              : "border-blue-500/20 bg-slate-900/40 text-slate-500"
          }`}
          title="Send instruction (Ctrl+Enter or Enter)"
        >
          {isLoading ? (
            <Loader2 className="h-3.5 w-3.5" />
          ) : (
            <Send className="h-3.5 w-3.5" />
          )}
        </button>

        {/* Mic Button */}
        <button
          type="button"
          onClick={onToggleMic}
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full border transition-all cursor-pointer ${
            isListening
              ? "border-cyan-400 bg-cyan-500/30 text-cyan-300 shadow-[0_0_15px_rgba(34,211,238,0.6)] animate-pulse"
              : "border-blue-500/30 bg-slate-900/60 text-slate-300 hover:border-cyan-400 hover:text-white"
          }`}
          title={isListening ? "Listening... click to pause" : "Activate voice"}
        >
          <Mic className="h-4 w-4" />
        </button>
      </form>

      {/* Bottom Mode Chips Row */}
      <div className="mt-2 flex items-center justify-between border-t border-blue-500/15 pt-2">
        <div className="flex items-center gap-1.5 no-scrollbar">
          {modeButtons.map((item) => {
            const Icon = item.icon;
            const isSelected = activeMode === item.id;
            return (
              <button
                key={item.label}
                type="button"
                onClick={() => onSelectMode(item.id)}
                className={`relative flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-[11px] font-medium transition-all cursor-pointer ${
                  isSelected
                    ? "border border-cyan-400/50 bg-cyan-950/40 text-cyan-300 shadow-[0_0_10px_rgba(34,211,238,0.25)] after:absolute after:-bottom-2 after:inset-x-1.5 after:h-[2px] after:bg-cyan-400 after:shadow-[0_0_6px_#22d3ee]"
                    : "text-slate-400 hover:bg-slate-900/50 hover:text-slate-200"
                }`}
              >
                <Icon className="h-3 w-3" />
                <span>{item.label}</span>
              </button>
            );
          })}
        </div>

        {/* Shortcut Hint */}
        <div className="hidden sm:block shrink-0 pl-3 text-[10px] font-mono text-slate-400">
          Ctrl + Enter
        </div>
      </div>
    </div>
  );
}
