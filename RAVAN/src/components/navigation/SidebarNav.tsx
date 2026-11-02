import {
  Home,
  MessageSquare,
  Brain,
  Monitor,
  Code2,
  Workflow,
  Settings,
  ChevronRight,
} from "lucide-react";
import type { HudMode } from "@/types/jarvis";

interface Props {
  currentMode: HudMode;
  onSelectMode: (mode: HudMode) => void;
}

export function SidebarNav({ currentMode, onSelectMode }: Props) {
  const navItems = [
    { id: "core" as HudMode, label: "Home", icon: Home },
    { id: "chat" as HudMode, label: "Chat", icon: MessageSquare },
    { id: "memory" as HudMode, label: "Memory", icon: Brain },
    { id: "system" as HudMode, label: "System", icon: Monitor },
    { id: "developer" as HudMode, label: "Developer", icon: Code2 },
    { id: "automation" as HudMode, label: "Automation", icon: Workflow },
  ];

  return (
    <aside className="glass-panel flex h-full w-[172px] flex-col justify-between p-3 shadow-[0_0_24px_rgba(34,211,238,0.18),inset_0_0_30px_rgba(59,130,246,0.08)]">
      {/* Top Nav Items */}
      <div className="space-y-1.5">
        {navItems.map((item) => {
          const Icon = item.icon;
          const isActive =
            item.id === "core"
              ? currentMode === "core" || currentMode === "chat" // Home covers base cockpit
              : currentMode === item.id;

          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onSelectMode(item.id)}
              className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-xs font-medium transition-all ${
                isActive
                  ? "border border-cyan-400 bg-gradient-to-r from-cyan-500/30 to-blue-600/40 text-white shadow-[0_0_18px_rgba(34,211,238,0.45)]"
                  : "text-slate-400 hover:bg-slate-900/50 hover:text-slate-200"
              }`}
            >
              <div className="flex items-center gap-2.5">
                <Icon
                  className={`h-4 w-4 ${
                    isActive ? "text-cyan-300" : "text-slate-400"
                  }`}
                />
                <span>{item.label}</span>
              </div>
              {isActive && (
                <ChevronRight className="h-3.5 w-3.5 text-cyan-300" />
              )}
            </button>
          );
        })}
      </div>

      {/* Bottom Section */}
      <div className="space-y-4 pt-4 border-t border-blue-500/15">
        <button
          type="button"
          onClick={() => onSelectMode("system")}
          className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2 text-xs font-medium text-slate-400 transition-colors hover:bg-slate-900/50 hover:text-slate-200"
        >
          <Settings className="h-4 w-4 text-slate-400" />
          <span>Settings</span>
        </button>

        <div className="px-2 text-[10px] leading-tight text-slate-500">
          Built for privacy, powered by your machine.
        </div>
      </div>
    </aside>
  );
}
