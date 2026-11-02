import { Code, Globe, CheckSquare, Brain, ChevronRight } from "lucide-react";

interface Props {
  onAction: (command: string) => void;
  onOpenMemory?: () => void;
  onOpenAutomation?: () => void;
}

export function QuickActionsCard({
  onAction,
  onOpenMemory,
  onOpenAutomation,
}: Props) {
  const actions = [
    {
      id: "vscode",
      title: "Open VS Code",
      subtitle: "Launch your editor",
      icon: Code,
      handler: () => onAction("Open VS Code"),
    },
    {
      id: "web",
      title: "Browse Web",
      subtitle: "Search the internet",
      icon: Globe,
      handler: () => onAction("Search the internet"),
    },
    {
      id: "task",
      title: "Create Task",
      subtitle: "Add to automation",
      icon: CheckSquare,
      handler: () => {
        if (onOpenAutomation) onOpenAutomation();
        else onAction("Create task");
      },
    },
    {
      id: "memory",
      title: "Check Memory",
      subtitle: "View knowledge base",
      icon: Brain,
      handler: () => {
        if (onOpenMemory) onOpenMemory();
        else onAction("Check memory status");
      },
    },
  ];

  return (
    <div className="glass-panel p-4">
      {/* Header */}
      <div className="pb-3">
        <span className="section-title">
          QUICK ACTIONS
        </span>
      </div>

      {/* Action Items List */}
      <div className="space-y-2">
        {actions.map((act) => {
          const Icon = act.icon;
          return (
            <button
              key={act.id}
              type="button"
              onClick={act.handler}
              className="flex w-full items-center justify-between rounded-xl border border-blue-500/15 bg-slate-900/40 p-2 text-left transition-all hover:border-cyan-500/50 hover:bg-slate-900/80 hover:shadow-[0_0_12px_rgba(0,240,255,0.15)] group cursor-pointer"
            >
              <div className="flex items-center gap-2.5">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-blue-500/30 bg-blue-950/40 text-cyan-400 group-hover:border-cyan-400 group-hover:text-cyan-300 transition-colors">
                  <Icon className="h-4 w-4" />
                </div>
                <div>
                  <div className="text-xs font-semibold text-slate-200 group-hover:text-white transition-colors">
                    {act.title}
                  </div>
                  <div className="text-[10px] text-slate-400">{act.subtitle}</div>
                </div>
              </div>
              <ChevronRight className="h-3.5 w-3.5 text-slate-500 group-hover:text-cyan-400 group-hover:translate-x-0.5 transition-all" />
            </button>
          );
        })}
      </div>
    </div>
  );
}
