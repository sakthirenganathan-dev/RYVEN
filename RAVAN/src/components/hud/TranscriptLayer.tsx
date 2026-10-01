import { AnimatePresence, motion } from "motion/react";
import type { TranscriptLine } from "@/types/jarvis";

export function TranscriptLayer({ lines }: { lines: TranscriptLine[] }) {
  const recent = lines.slice(-4);
  return (
    <div className="pointer-events-none flex w-full max-w-xl flex-col gap-2">
      <AnimatePresence initial={false}>
        {recent.map((line) => (
          <motion.div
            key={line.id}
            initial={{ opacity: 0, y: 12, filter: "blur(6px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -8, filter: "blur(4px)" }}
            transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
            className={line.speaker === "user" ? "self-start text-left" : "self-end text-right"}
          >
            <span className="holo-label">{line.speaker === "user" ? "User" : "Ryven"}</span>
            <p
              className={
                line.speaker !== "user"
                  ? "max-w-md text-sm text-holo text-glow"
                  : "max-w-md text-sm text-foreground/85"
              }
              style={{ fontFamily: "var(--font-mono)" }}
            >
              {line.text}
            </p>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}
