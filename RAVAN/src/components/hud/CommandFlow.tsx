import { AnimatePresence, motion } from "motion/react";
import type { CommandStep } from "@/types/jarvis";
import { cn } from "@/lib/utils";

export function CommandFlow({ steps, activeStep }: { steps: CommandStep[]; activeStep: number }) {
  return (
    <AnimatePresence>
      {steps.length > 0 && (
        <motion.ol
          initial={{ opacity: 0, x: -14 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: -10 }}
          transition={{ duration: 0.4 }}
          className="space-y-2"
        >
          {steps.map((step, i) => {
            const done = i < activeStep;
            const current = i === activeStep;
            return (
              <li key={step.label} className="relative pl-5">
                <span
                  className={cn(
                    "absolute left-0 top-[6px] h-1.5 w-1.5 rotate-45 border",
                    current
                      ? "border-holo bg-holo"
                      : done
                        ? "border-holo/60 bg-holo/40"
                        : "border-holo/25",
                  )}
                />
                {i < steps.length - 1 && (
                  <span
                    className={cn(
                      "absolute left-[3px] top-4 h-[calc(100%-6px)] w-px",
                      done ? "bg-holo/50" : "bg-holo/15",
                    )}
                  />
                )}
                <span
                  className={cn(
                    "holo-label block transition-colors",
                    current
                      ? "text-holo"
                      : done
                        ? "text-foreground/70"
                        : "text-muted-foreground/60",
                  )}
                >
                  {step.label}
                </span>
                {step.detail && (
                  <span
                    className="block truncate text-xs text-foreground/80"
                    style={{ fontFamily: "var(--font-mono)" }}
                  >
                    {step.detail}
                  </span>
                )}
                {current && (
                  <motion.span
                    layoutId="flow-scan"
                    className="pointer-events-none absolute inset-y-0 left-0 w-full"
                    style={{
                      background:
                        "linear-gradient(90deg, oklch(0.85 0.14 205 / 0.12), transparent 70%)",
                    }}
                  />
                )}
              </li>
            );
          })}
        </motion.ol>
      )}
    </AnimatePresence>
  );
}
