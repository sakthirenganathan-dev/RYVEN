import { useState } from "react";
import { motion } from "motion/react";
import { memoryNodes } from "@/services/jarvis";
import { HUDPanel } from "@/components/hud/HUDPanel";

export function MemoryGraph() {
  const [selected, setSelected] = useState(memoryNodes[4]!.id);
  const active = memoryNodes.find((n) => n.id === selected) ?? memoryNodes[0]!;
  const hub = memoryNodes[4]!;

  return (
    <HUDPanel title="Memory lattice" code="MEM.03" className="w-[340px]" tilt={4}>
      <div className="relative h-[180px] w-full">
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          className="absolute inset-0 h-full w-full"
        >
          {memoryNodes.map((n) =>
            n.id === hub.id ? null : (
              <line
                key={n.id}
                x1={hub.x}
                y1={hub.y}
                x2={n.x}
                y2={n.y}
                stroke="var(--color-holo)"
                strokeOpacity={selected === n.id ? 0.8 : 0.28}
                strokeWidth="0.35"
                strokeDasharray="2 2"
              >
                <animate
                  attributeName="stroke-dashoffset"
                  values="8;0"
                  dur="2.4s"
                  repeatCount="indefinite"
                />
              </line>
            ),
          )}
        </svg>
        {memoryNodes.map((n) => (
          <button
            key={n.id}
            type="button"
            onClick={() => setSelected(n.id)}
            className="absolute -translate-x-1/2 -translate-y-1/2"
            style={{ left: `${n.x}%`, top: `${n.y}%` }}
          >
            <motion.span
              animate={{ scale: selected === n.id ? 1.15 : 1 }}
              className={`block rounded-full border px-2 py-1 text-[9px] uppercase tracking-[0.18em] transition-colors ${
                selected === n.id
                  ? "border-holo bg-holo/20 text-holo glow-soft"
                  : "border-holo/30 bg-background/40 text-muted-foreground hover:border-holo/60"
              }`}
              style={{ fontFamily: "var(--font-mono)" }}
            >
              {n.label}
            </motion.span>
          </button>
        ))}
      </div>
      <div className="mt-2 border-t border-border/60 pt-2">
        <span className="holo-label text-holo">{active.label}</span>
        <ul
          className="mt-1 space-y-1 text-xs text-foreground/85"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          {active.items.map((item) => (
            <li key={item} className="flex items-center gap-2">
              <span className="h-px w-3 bg-holo/70" />
              {item}
            </li>
          ))}
        </ul>
      </div>
    </HUDPanel>
  );
}
