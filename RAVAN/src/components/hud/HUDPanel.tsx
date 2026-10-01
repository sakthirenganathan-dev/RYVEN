import { motion } from "motion/react";
import { cn } from "@/lib/utils";

/**
 * Floating holographic panel with a materialization sequence:
 * scan line -> outline draws -> markers -> content -> glow stabilizes.
 */
export function HUDPanel({
  title,
  code,
  children,
  className,
  delay = 0,
  tilt = 0,
}: {
  title?: string;
  code?: string;
  children: React.ReactNode;
  className?: string;
  delay?: number;
  tilt?: number;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scaleY: 0.82, filter: "blur(6px)" }}
      animate={{ opacity: 1, scaleY: 1, filter: "blur(0px)" }}
      exit={{ opacity: 0, scaleY: 0.86, filter: "blur(5px)" }}
      transition={{ duration: 0.55, delay, ease: [0.16, 1, 0.3, 1] }}
      style={{ transform: tilt ? `perspective(1200px) rotateY(${tilt}deg)` : undefined }}
      className={cn("holo-panel holo-corners relative overflow-hidden rounded-sm", className)}
    >
      <motion.span
        className="pointer-events-none absolute inset-x-0 top-0 h-px"
        style={{ background: "linear-gradient(90deg,transparent,var(--color-holo),transparent)" }}
        initial={{ y: 0, opacity: 1 }}
        animate={{ y: ["0%", "1000%"], opacity: [1, 1, 0] }}
        transition={{ duration: 0.75, delay, ease: "linear" }}
      />
      {(title || code) && (
        <header className="flex items-center justify-between gap-4 border-b border-border/70 px-3 py-2">
          <span className="holo-label text-holo">{title}</span>
          {code && <span className="holo-label opacity-60">{code}</span>}
        </header>
      )}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: delay + 0.28 }}
        className="px-3 py-3"
      >
        {children}
      </motion.div>
    </motion.div>
  );
}
