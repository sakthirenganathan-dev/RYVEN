import { Circle, MessageSquare, Cpu, Brain, Code2, Zap } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import type { HudMode } from "@/types/jarvis";
import { cn } from "@/lib/utils";

const ICONS: Record<HudMode, LucideIcon> = {
  core: Circle,
  chat: MessageSquare,
  system: Cpu,
  memory: Brain,
  developer: Code2,
  automation: Zap,
};

const ITEMS: { id: HudMode; label: string }[] = [
  { id: "core", label: "Core" },
  { id: "chat", label: "Chat" },
  { id: "system", label: "System" },
  { id: "memory", label: "Memory" },
  { id: "developer", label: "Developer" },
  { id: "automation", label: "Automation" },
];

export function FloatingNavigation({
  mode,
  onSelect,
}: {
  mode: HudMode;
  onSelect: (mode: HudMode) => void;
}) {
  return (
    <nav className="flex flex-col gap-3 md:gap-4">
      {ITEMS.map(({ id, label }) => {
        const Icon = ICONS[id];
        const activeItem = mode === id;
        return (
          <button
            key={id}
            type="button"
            onClick={() => onSelect(id)}
            className="group flex items-center gap-2"
          >
            <span
              className={cn(
                "grid h-9 w-9 place-items-center rounded-full border transition-all duration-300",
                activeItem
                  ? "border-holo bg-holo/15 text-holo glow-soft"
                  : "border-border text-muted-foreground group-hover:scale-110 group-hover:border-holo/70 group-hover:text-holo",
              )}
            >
              <Icon className="h-4 w-4" />
            </span>
            <span
              className={cn(
                "flex items-center gap-2 overflow-hidden transition-all duration-300",
                activeItem
                  ? "opacity-100"
                  : "w-0 opacity-0 group-hover:w-28 group-hover:opacity-100",
              )}
            >
              <span className="h-px w-4 bg-holo/60" />
              <span className="holo-label text-holo">{label}</span>
            </span>
          </button>
        );
      })}
    </nav>
  );
}
