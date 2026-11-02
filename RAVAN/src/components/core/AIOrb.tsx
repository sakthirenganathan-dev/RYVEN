import { useState, useEffect } from "react";
import type { CoreState } from "@/types/jarvis";
import { QuantumSingularityCore } from "./QuantumSingularityCore";

interface Props {
  state: CoreState;
  level: number;
  size?: number;
  onClick?: () => void;
}

/**
 * RYVEN 3.0 — Signature Quantum Singularity AI Energy Core
 * 
 * Replaces the traditional flat sphere with an original, cinematic quantum phenomenon:
 * - Dark translucent singularity core (event horizon void) with gravitational lensing rim
 * - Intensely luminous centered RYVEN Delta emblem
 * - Curved, flowing organic cyan, electric-blue & violet energy filaments
 * - 3D-projected accretion disk of 400+ luminous particles with Keplerian velocity
 * - Thin, broken high-tech telemetry arcs with traveling orbital energy nodes
 * - Ethereal, translucent vertical quantum beam fading seamlessly into cosmic space
 * - Refined holographic floor platform with counter-rotating concentric rings
 * - Pure transparent alpha canvas — zero rectangular bounding boxes or clipping
 * - 60fps GPU-accelerated rendering with tab visibility pause & reduced-motion support
 */
export function AIOrb({ state, level, onClick }: Props) {
  const listening = state === "listening";
  const thinking = state === "thinking";
  const executing = state === "executing";
  const speaking = state === "speaking";
  const isError = state === "error";

  const statusLabel =
    state === "idle" || state === "boot"
      ? "IDLE"
      : state === "listening"
      ? "LISTENING"
      : state === "thinking"
      ? "THINKING"
      : state === "speaking"
      ? "RESPONDING"
      : state === "executing"
      ? "EXECUTING"
      : "ERROR";

  const statusTone =
    isError
      ? "bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.8)]"
      : state === "idle" || state === "boot"
      ? "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.8)]"
      : state === "speaking"
      ? "bg-cyan-400 shadow-[0_0_8px_rgba(34,211,238,0.8)]"
      : state === "thinking"
      ? "bg-purple-400 shadow-[0_0_8px_rgba(168,85,247,0.8)]"
      : "bg-blue-400 shadow-[0_0_8px_rgba(59,130,246,0.8)]";

  return (
    <div className="relative flex flex-col items-center justify-center select-none w-full max-w-[580px]">
      {/* 1. Status Capsule Badge above the Singularity */}
      <div className="mb-1 z-20 flex items-center gap-2 rounded-full border border-cyan-500/40 bg-slate-950/80 px-3.5 py-1 text-[11px] font-mono tracking-wider text-cyan-300 backdrop-blur-md shadow-[0_0_16px_rgba(0,240,255,0.25)]">
        <span>AI CORE</span>
        <span className={`h-2 w-2 rounded-full ${statusTone} animate-pulse`} />
        <span className="font-bold">{statusLabel}</span>
      </div>

      {/* 2. Quantum Singularity Core Canvas */}
      <QuantumSingularityCore
        state={state}
        level={level}
        onClick={onClick}
      />

      {/* 3. Audio Equalizer & Security Footer */}
      <div className="flex flex-col items-center gap-1.5 mt-[-16px] z-20">
        <div className="flex items-center gap-[3px] h-5 px-4">
          {Array.from({ length: 32 }, (_, i) => {
            const centerDist = Math.abs(i - 15.5) / 16;
            const falloff = 1 - centerDist * 0.75;
            const heightMultiplier = listening
              ? Math.max(3, level * 26 * falloff)
              : speaking
              ? Math.max(3, (level * 20 + Math.sin(i * 0.8) * 6 + 8) * falloff)
              : thinking
              ? Math.max(3, (Math.sin(i * 1.2 + Date.now() * 0.006) * 6 + 8) * falloff)
              : Math.max(2, (Math.sin(i * 0.6 + Date.now() * 0.002) * 4 + 5) * falloff);

            return (
              <span
                key={i}
                className="w-[2px] rounded-full transition-all duration-150"
                style={{
                  height: `${heightMultiplier}px`,
                  backgroundColor:
                    i % 2 === 0
                      ? isError
                        ? "rgb(239, 68, 68)"
                        : "rgb(56, 189, 248)"
                      : isError
                      ? "rgb(252, 165, 165)"
                      : "rgb(192, 132, 252)",
                }}
              />
            );
          })}
        </div>
        <span className="text-[10px] font-mono tracking-[0.25em] text-cyan-400/90 font-semibold drop-shadow-[0_0_8px_rgba(0,240,255,0.5)]">
          LOCAL AI · PRIVATE · SECURE
        </span>
      </div>
    </div>
  );
}
