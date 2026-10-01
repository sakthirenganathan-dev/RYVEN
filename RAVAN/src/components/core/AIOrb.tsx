import { motion } from "motion/react";
import type { CoreState } from "@/types/jarvis";

interface Props {
  state: CoreState;
  level: number;
  size?: number;
}

const markers = Array.from({ length: 48 }, (_, i) => i);
const radials = Array.from({ length: 24 }, (_, i) => i);

/** Layered holographic intelligence core: energy centre, rings, segments, waveform. */
export function AIOrb({ state, level, size = 420 }: Props) {
  const listening = state === "listening";
  const thinking = state === "thinking";
  const executing = state === "executing";
  const speaking = state === "speaking";
  const active = listening || thinking || executing || speaking;

  const scale = 1 + (listening ? level * 0.07 + 0.03 : speaking ? level * 0.02 + 0.02 : 0);

  return (
    <motion.div
      className="relative"
      style={{ width: size, height: size }}
      initial={{ opacity: 0, scale: 0.3, filter: "blur(18px)" }}
      animate={{ opacity: 1, scale, filter: "blur(0px)" }}
      transition={{ duration: state === "boot" ? 1.6 : 0.5, ease: [0.16, 1, 0.3, 1] }}
    >
      {/* ambient glow */}
      <div
        className="absolute inset-0 rounded-full"
        style={{
          background: `radial-gradient(circle, oklch(0.85 0.14 205 / ${active ? 0.3 : 0.18}) 0%, transparent 62%)`,
          filter: "blur(18px)",
        }}
      />

      {/* expanding sound waves while speaking */}
      {speaking &&
        [0, 1, 2].map((i) => (
          <span
            key={i}
            className="absolute inset-[18%] rounded-full border border-holo/40"
            style={{ animation: `holo-pulse-ring 2.4s ease-out ${i * 0.8}s infinite` }}
          />
        ))}

      <svg viewBox="0 0 400 400" className="absolute inset-0 h-full w-full">
        <defs>
          <radialGradient id="coreGrad">
            <stop offset="0%" stopColor="var(--color-foreground)" stopOpacity="0.95" />
            <stop offset="35%" stopColor="var(--color-holo)" stopOpacity="0.85" />
            <stop offset="100%" stopColor="var(--color-holo-deep)" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* outer orbital ring, slow */}
        <g className="anim-spin-slow" style={{ transformOrigin: "200px 200px" }}>
          <circle
            cx="200"
            cy="200"
            r="188"
            fill="none"
            stroke="var(--color-holo)"
            strokeOpacity="0.35"
            strokeWidth="0.8"
            strokeDasharray="2 14"
          />
          <circle
            cx="200"
            cy="200"
            r="170"
            fill="none"
            stroke="var(--color-holo)"
            strokeOpacity="0.5"
            strokeWidth="1"
            strokeDasharray="90 260"
          />
        </g>

        {/* mid technical segments, reverse */}
        <g
          className={active ? "anim-spin-fast" : "anim-spin-med"}
          style={{ transformOrigin: "200px 200px" }}
        >
          <circle
            cx="200"
            cy="200"
            r="146"
            fill="none"
            stroke="var(--color-holo)"
            strokeOpacity="0.45"
            strokeWidth="1.2"
            strokeDasharray="40 22 6 22"
          />
          {markers.map((i) => {
            const a = (i / markers.length) * Math.PI * 2;
            const inner = i % 6 === 0 ? 128 : 136;
            return (
              <line
                key={i}
                x1={200 + Math.cos(a) * inner}
                y1={200 + Math.sin(a) * inner}
                x2={200 + Math.cos(a) * 142}
                y2={200 + Math.sin(a) * 142}
                stroke="var(--color-holo)"
                strokeOpacity={i % 6 === 0 ? 0.7 : 0.3}
                strokeWidth="1"
              />
            );
          })}
        </g>

        {/* fine radial lines */}
        <g className="anim-spin-slow" style={{ transformOrigin: "200px 200px" }}>
          {radials.map((i) => {
            const a = (i / radials.length) * Math.PI * 2;
            return (
              <line
                key={i}
                x1={200 + Math.cos(a) * 96}
                y1={200 + Math.sin(a) * 96}
                x2={200 + Math.cos(a) * 118}
                y2={200 + Math.sin(a) * 118}
                stroke="var(--color-holo-dim)"
                strokeOpacity="0.4"
                strokeWidth="0.7"
              />
            );
          })}
        </g>

        {/* scanning ring */}
        <g
          style={{
            transformOrigin: "200px 200px",
            animation: `holo-spin ${thinking ? 1.6 : 4.5}s linear infinite`,
          }}
        >
          <circle
            cx="200"
            cy="200"
            r="112"
            fill="none"
            stroke="var(--color-holo)"
            strokeOpacity="0.85"
            strokeWidth="1.6"
            strokeDasharray="60 644"
            strokeLinecap="round"
          />
        </g>

        {/* transparent shells */}
        <circle
          cx="200"
          cy="200"
          r="92"
          fill="oklch(0.85 0.14 205 / 0.04)"
          stroke="var(--color-holo)"
          strokeOpacity="0.28"
          strokeWidth="0.8"
        />
        <circle
          cx="200"
          cy="200"
          r="68"
          fill="oklch(0.85 0.14 205 / 0.06)"
          stroke="var(--color-holo)"
          strokeOpacity="0.4"
          strokeWidth="0.8"
        />

        {/* inner luminous core */}
        <g className="anim-breathe" style={{ transformOrigin: "200px 200px" }}>
          <circle cx="200" cy="200" r={44 + level * 12} fill="url(#coreGrad)" />
        </g>

        {/* audio reactive waveform across the core */}
        {(listening || speaking) && (
          <g>
            {Array.from({ length: 34 }, (_, i) => {
              const x = 132 + i * 4;
              const falloff = 1 - Math.abs(i - 16.5) / 18;
              const h = Math.max(
                2,
                (level * 46 + 6) * falloff * (0.55 + Math.abs(Math.sin(i * 1.7 + level * 9)) * 0.8),
              );
              return (
                <line
                  key={i}
                  x1={x}
                  y1={200 - h / 2}
                  x2={x}
                  y2={200 + h / 2}
                  stroke="var(--color-foreground)"
                  strokeOpacity="0.75"
                  strokeWidth="1.4"
                  strokeLinecap="round"
                />
              );
            })}
          </g>
        )}

        {/* data fragments travelling inward while thinking */}
        {thinking &&
          Array.from({ length: 10 }, (_, i) => {
            const a = (i / 10) * Math.PI * 2;
            return (
              <circle key={i} r="2" fill="var(--color-holo)">
                <animate
                  attributeName="cx"
                  values={`${200 + Math.cos(a) * 170};${200 + Math.cos(a) * 60}`}
                  dur="1.4s"
                  begin={`${i * 0.13}s`}
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="cy"
                  values={`${200 + Math.sin(a) * 170};${200 + Math.sin(a) * 60}`}
                  dur="1.4s"
                  begin={`${i * 0.13}s`}
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="opacity"
                  values="0;1;0"
                  dur="1.4s"
                  begin={`${i * 0.13}s`}
                  repeatCount="indefinite"
                />
              </circle>
            );
          })}
      </svg>
    </motion.div>
  );
}
