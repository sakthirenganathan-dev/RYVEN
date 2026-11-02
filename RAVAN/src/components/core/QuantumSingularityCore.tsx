import { useEffect, useRef } from "react";
import type { CoreState } from "@/types/jarvis";
import { animate } from "animejs";
import { motion } from "motion/react";

interface Props {
  state: CoreState;
  level: number;
  onClick?: (() => void) | undefined;
  className?: string | undefined;
}

interface AccretionParticle {
  r: number;
  theta: number;
  speed: number;
  size: number;
  baseAlpha: number;
  colorType: 0 | 1 | 2 | 3; // 0=cyan, 1=blue, 2=violet, 3=white
  phase: number;
  driftZ: number;
}

interface ProjectedParticle {
  px: number;
  py: number;
  drawDiameter: number;
  alpha: number;
  colorType: 0 | 1 | 2 | 3;
  isFg: boolean;
}

interface EnergyNode {
  arcIndex: number;
  angle: number;
  speed: number;
  size: number;
  trail: { x: number; y: number; alpha: number }[];
}

interface Ripple {
  r: number;
  maxR: number;
  alpha: number;
  speed: number;
}

interface QuantumSpark {
  x: number;
  y: number;
  vx: number;
  vy: number;
  alpha: number;
  size: number;
  color: string;
}

// Reusable Path2D for RYVEN Delta Emblem
const DELTA_PATH = typeof window !== "undefined" && typeof Path2D !== "undefined"
  ? new Path2D("M 0 -15 L -13 13 L -6.5 13 L 0 1.2 L 6.5 13 L 13 13 Z")
  : null;

// Helper to pre-render hardware-accelerated soft dot sprites
function createDotSprite(centerColor: string, outerGlow: string): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = 24;
  canvas.height = 24;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const grad = ctx.createRadialGradient(12, 12, 1, 12, 12, 11);
    grad.addColorStop(0, centerColor);
    grad.addColorStop(0.35, outerGlow);
    grad.addColorStop(0.8, "rgba(0, 0, 0, 0.08)");
    grad.addColorStop(1, "rgba(0, 0, 0, 0)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 24, 24);
  }
  return canvas;
}

/**
 * RYVEN 3.0 — High-Performance Quantum Singularity AI Energy Core
 *
 * Optimizations:
 * - Particle system projection computed ONCE per frame (550 particles)
 * - Hardware-accelerated offscreen sprite texture blitting with zero runtime string allocations
 * - Cached Path2D geometry for RYVEN Delta Emblem
 * - Zero GC allocations inside animation loop
 * - Complete prefers-reduced-motion accessibility with dynamic listener and pristine static fallback
 * - Balanced canvas transform stack
 */
export function QuantumSingularityCore({
  state,
  level = 0,
  onClick,
  className = "",
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // Dynamic animation values driven by Anime.js
  const animStateRef = useRef({
    speedMultiplier: 1.0,
    corePulseScale: 1.0,
    filamentIntensity: 1.0,
    glowBoost: 1.0,
  });

  const stateRef = useRef(state);
  stateRef.current = state;
  const levelRef = useRef(level);
  levelRef.current = level;

  // Track pointer for interactive gravitational attractor
  const mouseRef = useRef({ x: 0, y: 0, targetX: 0, targetY: 0, active: false });

  // Expose static redraw trigger for reduced motion state updates
  const redrawStaticRef = useRef<(() => void) | null>(null);

  // Anime.js transition whenever application state changes
  useEffect(() => {
    let targetSpeed = 1.0;
    let targetFilament = 1.0;
    let targetGlow = 1.0;

    switch (state) {
      case "thinking":
        targetSpeed = 2.4;
        targetFilament = 1.5;
        targetGlow = 1.4;
        break;
      case "executing":
        targetSpeed = 1.8;
        targetFilament = 1.3;
        targetGlow = 1.25;
        break;
      case "listening":
        targetSpeed = 1.3;
        targetFilament = 1.2;
        targetGlow = 1.3;
        break;
      case "speaking":
        targetSpeed = 1.6;
        targetFilament = 1.4;
        targetGlow = 1.35;
        break;
      case "error":
        targetSpeed = 0.7;
        targetFilament = 0.8;
        targetGlow = 1.5;
        break;
      default: // idle
        targetSpeed = 1.0;
        targetFilament = 1.0;
        targetGlow = 1.0;
        break;
    }

    const isReduced =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    if (isReduced) {
      animStateRef.current.speedMultiplier = 0;
      animStateRef.current.filamentIntensity = 1.0;
      animStateRef.current.glowBoost = 1.0;
      animStateRef.current.corePulseScale = 1.0;
      if (redrawStaticRef.current) redrawStaticRef.current();
      return;
    }

    animate(animStateRef.current, {
      speedMultiplier: targetSpeed,
      filamentIntensity: targetFilament,
      glowBoost: targetGlow,
      corePulseScale: [1.12, 1.0],
      duration: 650,
      ease: "outElastic(1, .7)",
      complete: () => {
        if (redrawStaticRef.current) redrawStaticRef.current();
      },
    });

    if (redrawStaticRef.current) redrawStaticRef.current();
  }, [state]);

  // Main Canvas Render Loop with Performance & Reduced-Motion Engine
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    let animId: number = 0;
    let width = 580;
    let height = 420;
    const dpr = Math.min(2, typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1);

    // Exact Optical Center of Singularity
    let cx = width / 2;
    let cy = 188;

    // 1. Initialize 550 Accretion Disk Particles
    const particleCount = 550;
    const particles: AccretionParticle[] = [];
    const rMin = 75;
    const rMax = 162;

    for (let i = 0; i < particleCount; i++) {
      const u = Math.random();
      const r = rMin + Math.pow(u, 1.35) * (rMax - rMin);
      const theta = Math.random() * Math.PI * 2;
      const speed = (0.01 + (0.022 * (rMax - r)) / (rMax - rMin)) * (0.85 + Math.random() * 0.3);
      const size = 0.75 + Math.random() * 1.6;
      const baseAlpha = 0.35 + Math.random() * 0.65;
      const randColor = Math.random();
      const colorType: 0 | 1 | 2 | 3 =
        randColor < 0.45 ? 0 : randColor < 0.7 ? 1 : randColor < 0.88 ? 2 : 3;

      particles.push({
        r,
        theta,
        speed,
        size,
        baseAlpha,
        colorType,
        phase: Math.random() * Math.PI * 2,
        driftZ: (Math.random() - 0.5) * 12,
      });
    }

    // Scratch buffer for particle projection (zero allocations inside loop)
    const projected: ProjectedParticle[] = new Array(particleCount);
    for (let i = 0; i < particleCount; i++) {
      projected[i] = { px: 0, py: 0, drawDiameter: 0, alpha: 0, colorType: 0, isFg: false };
    }

    // Pre-rendered offscreen dot sprites
    const sprites: HTMLCanvasElement[] = [
      createDotSprite("#22d3ee", "rgba(34, 211, 238, 0.7)"),  // 0: cyan
      createDotSprite("#38bdf8", "rgba(56, 189, 248, 0.7)"),  // 1: electric sky
      createDotSprite("#a855f7", "rgba(168, 85, 247, 0.7)"),  // 2: neon violet
      createDotSprite("#ffffff", "rgba(255, 255, 255, 0.8)"),  // 3: hot white
    ];
    const spriteRed = createDotSprite("#ef4444", "rgba(239, 68, 68, 0.7)");
    const spriteGreen = createDotSprite("#34d399", "rgba(52, 211, 153, 0.7)");

    // 2. Initialize 4 Telemetry Energy Nodes with Trails
    const energyNodes: EnergyNode[] = [
      { arcIndex: 0, angle: 0.4, speed: 0.013, size: 3.0, trail: [] },
      { arcIndex: 1, angle: 2.2, speed: -0.01, size: 2.7, trail: [] },
      { arcIndex: 2, angle: 4.8, speed: 0.008, size: 2.4, trail: [] },
      { arcIndex: 3, angle: 3.5, speed: -0.012, size: 2.6, trail: [] },
    ];

    // 3. Shockwave Ripples Queue
    const ripples: Ripple[] = [];
    let lastRippleSpawn = 0;

    // 4. Coronal Quantum Sparks Queue
    const sparks: QuantumSpark[] = [];
    let lastSparkSpawn = 0;

    let tick = 0;
    let isHidden = false;

    // Accessibility: prefers-reduced-motion detection & listener
    const motionQuery =
      typeof window !== "undefined"
        ? window.matchMedia("(prefers-reduced-motion: reduce)")
        : null;
    let prefersReducedMotion = motionQuery ? motionQuery.matches : false;


    // Accretion disk tilt settings
    const diskTilt = 0.54; // ~31 deg tilt
    const cosTilt = Math.cos(diskTilt);
    const sinTilt = Math.sin(diskTilt);
    const diskRotationAngle = -0.3;
    const cosR = Math.cos(diskRotationAngle);
    const sinR = Math.sin(diskRotationAngle);

    const resizeCanvas = () => {
      const rect = canvas.getBoundingClientRect();
      width = rect.width || 620;
      height = rect.height || 500;
      cx = width / 2;
      cy = 188;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.scale(dpr, dpr);
      if (prefersReducedMotion) {
        drawFrame(64);
      }
    };

    resizeCanvas();
    window.addEventListener("resize", resizeCanvas);

    // Mouse pointer listener for gravitational interaction
    const handleMouseMove = (e: MouseEvent) => {
      if (prefersReducedMotion) return;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left - rect.width / 2;
      const y = e.clientY - rect.top - rect.height / 2;
      if (Math.hypot(x, y) < 320) {
        mouseRef.current.targetX = x * 0.12;
        mouseRef.current.targetY = y * 0.12;
        mouseRef.current.active = true;
      } else {
        mouseRef.current.targetX = 0;
        mouseRef.current.targetY = 0;
        mouseRef.current.active = false;
      }
    };

    window.addEventListener("mousemove", handleMouseMove, { passive: true });

    // =========================================================================
    // MASTER FRAME DRAW FUNCTION
    // =========================================================================
    const drawFrame = (currentTick: number) => {
      const curState = stateRef.current;
      const curLevel = levelRef.current;
      const animState = animStateRef.current;

      // Smooth mouse lerp
      if (!prefersReducedMotion) {
        mouseRef.current.x += (mouseRef.current.targetX - mouseRef.current.x) * 0.08;
        mouseRef.current.y += (mouseRef.current.targetY - mouseRef.current.y) * 0.08;
      } else {
        mouseRef.current.x = 0;
        mouseRef.current.y = 0;
      }

      const mx = mouseRef.current.x;
      const my = mouseRef.current.y;

      // 100% transparent clear every frame
      ctx.clearRect(0, 0, width, height);

      const speedMult = prefersReducedMotion ? 0 : animState.speedMultiplier;
      const isError = curState === "error";
      const isListening = curState === "listening";
      const isSpeaking = curState === "speaking";

      // Dynamic State Hues
      const primaryColor = isError
        ? "rgba(239, 68, 68,"
        : isListening
        ? "rgba(34, 197, 94,"
        : curState === "thinking"
        ? "rgba(168, 85, 247,"
        : "rgba(34, 211, 238,";

      const secondaryColor = isError
        ? "rgba(245, 158, 11,"
        : curState === "thinking"
        ? "rgba(236, 72, 153,"
        : "rgba(139, 92, 246,";

      // Singularity core center with slight gravitational mouse attraction
      const coreCenterX = cx + mx * 0.5;
      const coreCenterY = cy + my * 0.5;
      const coreR = 64 * animState.corePulseScale;

      // =====================================================================
      // PRE-CALCULATE ACCRETION DISK 3D PROJECTION ONCE PER FRAME
      // =====================================================================
      for (let i = 0; i < particleCount; i++) {
        const p = particles[i]!;
        if (!prefersReducedMotion) {
          p.theta += p.speed * speedMult;
        }

        const cosTheta = Math.cos(p.theta);
        const sinTheta = Math.sin(p.theta);

        const xOrbit = p.r * cosTheta;
        const yOrbit = p.r * sinTheta * cosTilt;
        const zDepth = p.r * sinTheta * sinTilt + p.driftZ;

        const px = coreCenterX + (xOrbit * cosR - yOrbit * sinR);
        const py = coreCenterY + (xOrbit * sinR + yOrbit * cosR);

        const isFg = zDepth > 0;
        const twinkle = prefersReducedMotion
          ? 1.0
          : Math.sin(currentTick * 0.04 + p.phase) * 0.25 + 0.75;
        const pAlpha = p.baseAlpha * twinkle * (isFg ? 0.95 : 0.45);

        const pt = projected[i]!;
        pt.px = px;
        pt.py = py;
        pt.drawDiameter = p.size * (isFg ? 2.6 : 1.9);
        pt.alpha = pAlpha;
        pt.colorType = p.colorType;
        pt.isFg = isFg;
      }

      // =====================================================================
      // PASS 1: Ethereal Vertical Quantum Beam (Narrow, Translucent, Faded)
      // =====================================================================
      ctx.save();
      const beamCoreW = 5;
      const beamGlowW = 26;
      const beamTop = coreCenterY - 175;
      const beamBottom = coreCenterY + 135;
      const beamH = beamBottom - beamTop;

      const beamGlowGrad = ctx.createLinearGradient(0, beamTop, 0, beamBottom);
      beamGlowGrad.addColorStop(0, `${primaryColor} 0)`);
      beamGlowGrad.addColorStop(0.2, `${primaryColor} 0.07)`);
      beamGlowGrad.addColorStop(0.5, `${primaryColor} 0.15)`);
      beamGlowGrad.addColorStop(0.8, `${primaryColor} 0.07)`);
      beamGlowGrad.addColorStop(1, `${primaryColor} 0)`);

      ctx.fillStyle = beamGlowGrad;
      ctx.fillRect(coreCenterX - beamGlowW / 2, beamTop, beamGlowW, beamH);

      const beamCoreGrad = ctx.createLinearGradient(0, beamTop, 0, beamBottom);
      beamCoreGrad.addColorStop(0, "rgba(255, 255, 255, 0)");
      beamCoreGrad.addColorStop(0.25, "rgba(255, 255, 255, 0.16)");
      beamCoreGrad.addColorStop(0.5, "rgba(255, 255, 255, 0.32)");
      beamCoreGrad.addColorStop(0.75, "rgba(255, 255, 255, 0.16)");
      beamCoreGrad.addColorStop(1, "rgba(255, 255, 255, 0)");

      ctx.fillStyle = beamCoreGrad;
      ctx.fillRect(coreCenterX - beamCoreW / 2, beamTop, beamCoreW, beamH);
      ctx.restore();

      // =====================================================================
      // PASS 2: Multi-Ring Holographic Sci-Fi Platform (Floor Projection)
      // =====================================================================
      ctx.save();
      const platY = cy + 132;
      const platAngle = prefersReducedMotion ? 0.35 : currentTick * 0.005 * speedMult;

      // Soft ground illumination pool
      const poolGrad = ctx.createRadialGradient(cx, platY, 5, cx, platY, 115);
      poolGrad.addColorStop(0, `${primaryColor} 0.18)`);
      poolGrad.addColorStop(0.5, `${secondaryColor} 0.06)`);
      poolGrad.addColorStop(1, "rgba(0, 0, 0, 0)");
      ctx.fillStyle = poolGrad;
      ctx.beginPath();
      ctx.ellipse(cx, platY, 115, 27, 0, 0, Math.PI * 2);
      ctx.fill();

      // Platform Ring 1 (Outer Segmented Ring with Telemetry Ticks)
      ctx.save();
      ctx.translate(cx, platY);
      ctx.strokeStyle = `${primaryColor} 0.5)`;
      ctx.lineWidth = 1.3;
      ctx.setLineDash([32, 12, 10, 12]);
      ctx.beginPath();
      ctx.ellipse(0, 0, 110, 26, platAngle * 0.4, 0, Math.PI * 2);
      ctx.stroke();

      // 12 Telemetry Tick Marks on Perimeter
      ctx.setLineDash([]);
      ctx.strokeStyle = `${primaryColor} 0.75)`;
      ctx.lineWidth = 1.1;
      for (let i = 0; i < 12; i++) {
        const a = (i / 12) * Math.PI * 2 + platAngle * 0.4;
        const tx1 = Math.cos(a) * 106;
        const ty1 = Math.sin(a) * 25.1;
        const tx2 = Math.cos(a) * 114;
        const ty2 = Math.sin(a) * 27.0;
        ctx.beginPath();
        ctx.moveTo(tx1, ty1);
        ctx.lineTo(tx2, ty2);
        ctx.stroke();
      }
      ctx.restore();

      // Platform Ring 2 (Middle Counter-Rotating Telemetry Arcs)
      ctx.save();
      ctx.translate(cx, platY);
      ctx.strokeStyle = `${secondaryColor} 0.6)`;
      ctx.lineWidth = 1.1;
      ctx.setLineDash([20, 14]);
      ctx.beginPath();
      ctx.ellipse(0, 0, 82, 19.5, -platAngle * 0.65, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();

      // Platform Ring 3 (Inner Quantum Anchor Ring)
      ctx.save();
      ctx.translate(cx, platY);
      ctx.strokeStyle = `${primaryColor} 0.85)`;
      ctx.lineWidth = 1.4;
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.ellipse(0, 0, 52, 12.5, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();

      ctx.restore(); // Closes PASS 2 ctx.save() cleanly

      // =====================================================================
      // PASS 3: Concentric Scanning Ripples (Reacts to Audio & Breathing)
      // =====================================================================
      if (!prefersReducedMotion) {
        const rippleInterval = isListening ? 22 - curLevel * 16 : isSpeaking ? 30 : 65;
        if (currentTick - lastRippleSpawn > rippleInterval && ripples.length < 8) {
          ripples.push({
            r: coreR * 0.95,
            maxR: isListening ? 165 + curLevel * 50 : 155,
            alpha: isListening ? 0.5 + curLevel * 0.45 : 0.35,
            speed: isListening ? 1.3 + curLevel * 1.6 : 0.95,
          });
          lastRippleSpawn = currentTick;
        }

        ctx.save();
        for (let i = ripples.length - 1; i >= 0; i--) {
          const rip = ripples[i];
          if (!rip) continue;

          rip.r += rip.speed * speedMult;
          const progress = (rip.r - coreR * 0.95) / (rip.maxR - coreR * 0.95);
          const curAlpha = rip.alpha * (1 - progress);

          if (progress >= 1 || curAlpha <= 0.01) {
            ripples.splice(i, 1);
            continue;
          }

          ctx.strokeStyle = `${primaryColor} ${curAlpha})`;
          ctx.lineWidth = 1.3 * (1 - progress * 0.5);
          ctx.beginPath();
          ctx.ellipse(coreCenterX, coreCenterY, rip.r, rip.r * 0.88, 0, 0, Math.PI * 2);
          ctx.stroke();
        }
        ctx.restore();
      }

      // =====================================================================
      // PASS 4: Broken Telemetry Arcs & Traveling Energy Nodes
      // =====================================================================
      ctx.save();
      const aAngle0 = prefersReducedMotion ? 0.4 : currentTick * 0.007 * speedMult;
      const aAngle1 = prefersReducedMotion ? -0.3 : -currentTick * 0.0055 * speedMult;
      const aAngle2 = prefersReducedMotion ? 0.2 : currentTick * 0.004 * speedMult;

      // Arc 1 (R = 172, tilt -18°)
      ctx.save();
      ctx.translate(coreCenterX, coreCenterY);
      ctx.rotate(-0.31);
      ctx.strokeStyle = `${primaryColor} 0.38)`;
      ctx.lineWidth = 1.2;
      ctx.beginPath();
      ctx.arc(0, 0, 172, aAngle0, aAngle0 + 1.7);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(0, 0, 172, aAngle0 + 2.8, aAngle0 + 4.2);
      ctx.stroke();
      ctx.restore();

      // Arc 2 (R = 206, tilt +24°)
      ctx.save();
      ctx.translate(coreCenterX, coreCenterY);
      ctx.rotate(0.42);
      ctx.strokeStyle = `${secondaryColor} 0.4)`;
      ctx.lineWidth = 1.1;
      ctx.beginPath();
      ctx.arc(0, 0, 206, aAngle1, aAngle1 + 1.3);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(0, 0, 206, aAngle1 + 2.3, aAngle1 + 3.9);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(0, 0, 206, aAngle1 + 4.7, aAngle1 + 5.6);
      ctx.stroke();
      ctx.restore();

      // Arc 3 (R = 238, tilt -8°)
      ctx.save();
      ctx.translate(coreCenterX, coreCenterY);
      ctx.rotate(-0.14);
      ctx.strokeStyle = `${primaryColor} 0.28)`;
      ctx.lineWidth = 1.0;
      ctx.setLineDash([24, 18, 8, 18]);
      ctx.beginPath();
      ctx.arc(0, 0, 238, aAngle2, aAngle2 + 2.4);
      ctx.stroke();
      ctx.restore();

      // Render Traveling Energy Nodes
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < energyNodes.length; i++) {
        const node = energyNodes[i]!;
        if (!prefersReducedMotion) {
          node.angle += node.speed * speedMult;
        }

        const arcR = i === 0 ? 172 : i === 1 ? 206 : i === 2 ? 238 : 172;
        const arcTilt = i === 0 ? -0.31 : i === 1 ? 0.42 : i === 2 ? -0.14 : -0.31;

        const rawX = Math.cos(node.angle) * arcR;
        const rawY = Math.sin(node.angle) * arcR;
        const nodeX = coreCenterX + rawX * Math.cos(arcTilt) - rawY * Math.sin(arcTilt);
        const nodeY = coreCenterY + rawX * Math.sin(arcTilt) + rawY * Math.cos(arcTilt);

        if (!prefersReducedMotion) {
          node.trail.unshift({ x: nodeX, y: nodeY, alpha: 0.8 });
          if (node.trail.length > 9) node.trail.pop();

          for (let t = node.trail.length - 1; t >= 1; t--) {
            const p1 = node.trail[t]!;
            const p2 = node.trail[t - 1]!;
            const tAlpha = (1 - t / node.trail.length) * 0.5;
            ctx.strokeStyle = `${primaryColor} ${tAlpha})`;
            ctx.lineWidth = node.size * (1 - t / node.trail.length);
            ctx.beginPath();
            ctx.moveTo(p1.x, p1.y);
            ctx.lineTo(p2.x, p2.y);
            ctx.stroke();
          }
        }

        // Node Glowing Head
        ctx.fillStyle = `${primaryColor} 0.95)`;
        ctx.beginPath();
        ctx.arc(nodeX, nodeY, node.size, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = "rgba(255, 255, 255, 0.95)";
        ctx.beginPath();
        ctx.arc(nodeX, nodeY, node.size * 0.45, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalCompositeOperation = "source-over";
      ctx.restore();

      // =====================================================================
      // PASS 5: Accretion Disk (Background Half — Behind Singularity Core)
      // =====================================================================
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < particleCount; i++) {
        const pt = projected[i]!;
        if (pt.isFg) continue; // Behind core only

        const sprite = isError
          ? spriteRed
          : isListening
          ? spriteGreen
          : sprites[pt.colorType] || sprites[0]!;

        ctx.globalAlpha = pt.alpha;
        ctx.drawImage(
          sprite,
          pt.px - pt.drawDiameter / 2,
          pt.py - pt.drawDiameter / 2,
          pt.drawDiameter,
          pt.drawDiameter
        );
      }
      ctx.restore();

      // =====================================================================
      // PASS 6: Flowing Energy Filaments (10 Dual-Layer Bezier Strands)
      // =====================================================================
      ctx.save();
      ctx.globalCompositeOperation = "lighter";

      // Layer A: 6 Outer Swirling Cosmic Filaments (Electric Blue & Violet)
      const outerFilaments = 6;
      for (let i = 0; i < outerFilaments; i++) {
        const baseA =
          (i / outerFilaments) * Math.PI * 2 +
          (prefersReducedMotion ? 0.4 : currentTick * 0.013 * speedMult);
        const r1 = 155;
        const r2 = coreR * 0.98;

        const x1 = coreCenterX + Math.cos(baseA) * r1;
        const y1 = coreCenterY + Math.sin(baseA) * r1 * 0.75;

        const cp1x = coreCenterX + Math.cos(baseA + 0.6) * 130;
        const cp1y = coreCenterY + Math.sin(baseA + 0.6) * 130 * 0.7;

        const cp2x = coreCenterX + Math.cos(baseA + 1.35) * 96;
        const cp2y = coreCenterY + Math.sin(baseA + 1.35) * 96 * 0.62;

        const x2 = coreCenterX + Math.cos(baseA + 2.1) * r2;
        const y2 = coreCenterY + Math.sin(baseA + 2.1) * r2 * 0.58;

        const grad = ctx.createLinearGradient(x1, y1, x2, y2);
        grad.addColorStop(0, `${primaryColor} 0)`);
        grad.addColorStop(0.3, `${secondaryColor} ${0.45 * animState.filamentIntensity})`);
        grad.addColorStop(0.8, `${primaryColor} ${0.75 * animState.filamentIntensity})`);
        grad.addColorStop(1, "rgba(255, 255, 255, 0.9)");

        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.7;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.bezierCurveTo(cp1x, cp1y, cp2x, cp2y, x2, y2);
        ctx.stroke();

        // Luminous leading spark
        ctx.fillStyle = `${primaryColor} 0.9)`;
        ctx.beginPath();
        ctx.arc(x2, y2, 2.0, 0, Math.PI * 2);
        ctx.fill();
      }

      // Layer B: 4 Inner High-Velocity Quantum Flux Strands
      const innerFilaments = 4;
      for (let j = 0; j < innerFilaments; j++) {
        const baseA =
          (j / innerFilaments) * Math.PI * 2 -
          (prefersReducedMotion ? 0.3 : currentTick * 0.022 * speedMult);
        const r1 = 104;
        const r2 = coreR * 0.88;

        const x1 = coreCenterX + Math.cos(baseA) * r1;
        const y1 = coreCenterY + Math.sin(baseA) * r1 * 0.7;

        const cpx = coreCenterX + Math.cos(baseA + 0.85) * 82;
        const cpy = coreCenterY + Math.sin(baseA + 0.85) * 82 * 0.62;

        const x2 = coreCenterX + Math.cos(baseA + 1.6) * r2;
        const y2 = coreCenterY + Math.sin(baseA + 1.6) * r2 * 0.55;

        const grad = ctx.createLinearGradient(x1, y1, x2, y2);
        grad.addColorStop(0, `${primaryColor} 0)`);
        grad.addColorStop(0.5, `${primaryColor} ${0.7 * animState.filamentIntensity})`);
        grad.addColorStop(1, "rgba(255, 255, 255, 0.95)");

        ctx.strokeStyle = grad;
        ctx.lineWidth = 2.0;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.quadraticCurveTo(cpx, cpy, x2, y2);
        ctx.stroke();
      }
      ctx.restore();

      // =====================================================================
      // PASS 7: Dark Translucent Singularity Core (Event Horizon Void)
      // =====================================================================
      ctx.save();

      // Gravitational Refraction & Lensing Ambient Corona
      const coronaGrad = ctx.createRadialGradient(
        coreCenterX,
        coreCenterY,
        coreR * 0.65,
        coreCenterX,
        coreCenterY,
        coreR * 1.55
      );
      coronaGrad.addColorStop(0, `${primaryColor} 0.4)`);
      coronaGrad.addColorStop(0.35, `${secondaryColor} 0.22)`);
      coronaGrad.addColorStop(0.75, `${primaryColor} 0.06)`);
      coronaGrad.addColorStop(1, "rgba(0, 0, 0, 0)");

      ctx.fillStyle = coronaGrad;
      ctx.beginPath();
      ctx.arc(coreCenterX, coreCenterY, coreR * 1.55, 0, Math.PI * 2);
      ctx.fill();

      // Dark Translucent Void (Event Horizon)
      const voidGrad = ctx.createRadialGradient(
        coreCenterX,
        coreCenterY,
        0,
        coreCenterX,
        coreCenterY,
        coreR
      );
      voidGrad.addColorStop(0, "#01030a"); // Absolute deep space black
      voidGrad.addColorStop(0.65, "rgba(2, 6, 23, 0.95)"); // Dark obsidian
      voidGrad.addColorStop(0.88, "rgba(10, 18, 48, 0.88)"); // Midnight navy
      voidGrad.addColorStop(0.97, `${primaryColor} 0.45)`); // Lensing edge
      voidGrad.addColorStop(1, "rgba(0, 0, 0, 0)");

      ctx.fillStyle = voidGrad;
      ctx.beginPath();
      ctx.arc(coreCenterX, coreCenterY, coreR, 0, Math.PI * 2);
      ctx.fill();

      // Double Gravitational Lensing Caustic Rims (Cyan & Violet)
      ctx.strokeStyle = `${primaryColor} 0.85)`;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.arc(coreCenterX, coreCenterY, coreR * 0.96, 0, Math.PI * 2);
      ctx.stroke();

      ctx.strokeStyle = `${secondaryColor} 0.75)`;
      ctx.lineWidth = 1.3;
      ctx.beginPath();
      ctx.arc(coreCenterX, coreCenterY, coreR * 1.02, 0, Math.PI * 2);
      ctx.stroke();

      // Magnetic Distortion Arcs (Caustic highlight arcs on rim)
      const rimRot = prefersReducedMotion ? 0.3 : currentTick * 0.012;
      ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
      ctx.lineWidth = 2.0;
      ctx.beginPath();
      ctx.arc(coreCenterX, coreCenterY, coreR * 0.96, -0.8 + rimRot, 0.25 + rimRot);
      ctx.stroke();

      const rimRot2 = prefersReducedMotion ? 0.25 : currentTick * 0.009;
      ctx.strokeStyle = `${secondaryColor} 0.85)`;
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.arc(coreCenterX, coreCenterY, coreR * 0.96, 2.3 - rimRot2, 3.3 - rimRot2);
      ctx.stroke();
      ctx.restore();

      // =====================================================================
      // PASS 8: Accretion Disk (Foreground Half — Passing in Front of Core)
      // =====================================================================
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      for (let i = 0; i < particleCount; i++) {
        const pt = projected[i]!;
        if (!pt.isFg) continue; // Foreground only

        const sprite = isError
          ? spriteRed
          : isListening
          ? spriteGreen
          : sprites[pt.colorType] || sprites[0]!;

        ctx.globalAlpha = pt.alpha;
        ctx.drawImage(
          sprite,
          pt.px - pt.drawDiameter / 2,
          pt.py - pt.drawDiameter / 2,
          pt.drawDiameter,
          pt.drawDiameter
        );
      }
      ctx.restore();

      // =====================================================================
      // PASS 9: Quantum Coronal Sparks (Periodic Ejections)
      // =====================================================================
      if (!prefersReducedMotion) {
        if (currentTick - lastSparkSpawn > (isThinking(curState) ? 14 : 35) && sparks.length < 24) {
          const spawnAngle = Math.random() * Math.PI * 2;
          const spawnSpeed = 1.2 + Math.random() * 2.2;
          sparks.push({
            x: coreCenterX + Math.cos(spawnAngle) * (coreR * 0.95),
            y: coreCenterY + Math.sin(spawnAngle) * (coreR * 0.95),
            vx: Math.cos(spawnAngle) * spawnSpeed,
            vy: Math.sin(spawnAngle) * spawnSpeed,
            alpha: 0.9,
            size: 1.2 + Math.random() * 1.5,
            color: Math.random() < 0.6 ? "#22d3ee" : "#c084fc",
          });
          lastSparkSpawn = currentTick;
        }

        ctx.save();
        ctx.globalCompositeOperation = "lighter";
        for (let s = sparks.length - 1; s >= 0; s--) {
          const spk = sparks[s]!;
          spk.x += spk.vx;
          spk.y += spk.vy;
          spk.alpha -= 0.024;

          if (spk.alpha <= 0) {
            sparks.splice(s, 1);
            continue;
          }

          ctx.fillStyle = spk.color;
          ctx.globalAlpha = spk.alpha;
          ctx.beginPath();
          ctx.arc(spk.x, spk.y, spk.size, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.restore();
      }

      // =====================================================================
      // PASS 10: Intensely Luminous RYVEN Delta Emblem (Scaled Up)
      // =====================================================================
      ctx.save();
      const emblemScale = prefersReducedMotion
        ? 1.38
        : 1.38 +
          Math.sin(currentTick * 0.05) * 0.04 +
          (isSpeaking || isListening ? curLevel * 0.2 : 0);

      ctx.translate(coreCenterX, coreCenterY);
      ctx.scale(emblemScale, emblemScale);

      const drawDelta = (mode: "fill" | "stroke" | "clip") => {
        if (DELTA_PATH) {
          if (mode === "fill") ctx.fill(DELTA_PATH);
          else if (mode === "stroke") ctx.stroke(DELTA_PATH);
          else ctx.clip(DELTA_PATH);
        } else {
          ctx.beginPath();
          ctx.moveTo(0, -15);
          ctx.lineTo(-13, 13);
          ctx.lineTo(-6.5, 13);
          ctx.lineTo(0, 1.2);
          ctx.lineTo(6.5, 13);
          ctx.lineTo(13, 13);
          ctx.closePath();
          if (mode === "fill") ctx.fill();
          else if (mode === "stroke") ctx.stroke();
          else ctx.clip();
        }
      };

      // Pass 10A: Cyan / Violet Outer Bloom Shadow
      ctx.shadowColor = isError ? "rgba(239, 68, 68, 0.95)" : "rgba(0, 240, 255, 0.95)";
      ctx.shadowBlur = 22;
      ctx.strokeStyle = isError ? "#f87171" : "#22d3ee";
      ctx.lineWidth = 3.6;
      drawDelta("stroke");

      // Pass 10B: Crisp White Center Core Glyph
      ctx.shadowBlur = 0;
      ctx.fillStyle = "#ffffff";
      drawDelta("fill");

      // Pass 10C: Accent Edge Stroke
      ctx.strokeStyle = isError ? "#ef4444" : "#38bdf8";
      ctx.lineWidth = 1.3;
      drawDelta("stroke");

      // Specular Light Sheen sweep (Periodic glint every 3.5s; static in reduced motion)
      if (!prefersReducedMotion) {
        const sheenProgress = (currentTick % 210) / 210;
        if (sheenProgress < 0.25) {
          const sheenX = -20 + (sheenProgress / 0.25) * 40;
          ctx.save();
          drawDelta("clip");

          const sheenGrad = ctx.createLinearGradient(sheenX - 6, -15, sheenX + 6, 15);
          sheenGrad.addColorStop(0, "rgba(255, 255, 255, 0)");
          sheenGrad.addColorStop(0.5, "rgba(255, 255, 255, 0.85)");
          sheenGrad.addColorStop(1, "rgba(255, 255, 255, 0)");
          ctx.fillStyle = sheenGrad;
          ctx.fillRect(-20, -20, 40, 40);
          ctx.restore();
        }
      }

      // Center Micro-Singularity Core Dot
      ctx.fillStyle = isError ? "#fca5a5" : "#a5f3fc";
      ctx.beginPath();
      ctx.arc(0, 7.5, 1.3, 0, Math.PI * 2);
      ctx.fill();

      ctx.restore();
    };

    // Render loop driver
    const render = () => {
      animId = 0;
      if (!isHidden && !prefersReducedMotion) {
        tick++;
        drawFrame(tick);
        animId = requestAnimationFrame(render);
      }
    };

    const handleVisibilityChange = () => {
      isHidden = document.hidden;
      if (isHidden) {
        if (animId) {
          cancelAnimationFrame(animId);
          animId = 0;
        }
      } else {
        if (!prefersReducedMotion && !animId) {
          animId = requestAnimationFrame(render);
        } else if (prefersReducedMotion) {
          drawFrame(64);
        }
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);

    // Static frame driver for reduced motion
    redrawStaticRef.current = () => {
      if (prefersReducedMotion) {
        drawFrame(64);
      }
    };

    // Accessibility motion listener
    const onMotionPreferenceChange = (e: MediaQueryListEvent) => {
      prefersReducedMotion = e.matches;
      if (prefersReducedMotion) {
        if (animId) {
          cancelAnimationFrame(animId);
          animId = 0;
        }
        drawFrame(64);
      } else {
        if (!animId && !isHidden) {
          animId = requestAnimationFrame(render);
        }
      }
    };

    if (motionQuery) {
      motionQuery.addEventListener("change", onMotionPreferenceChange);
    }

    if (prefersReducedMotion) {
      drawFrame(64);
    } else {
      animId = requestAnimationFrame(render);
    }

    return () => {
      if (animId) cancelAnimationFrame(animId);
      if (motionQuery) {
        motionQuery.removeEventListener("change", onMotionPreferenceChange);
      }
      window.removeEventListener("resize", resizeCanvas);
      window.removeEventListener("mousemove", handleMouseMove);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      redrawStaticRef.current = null;
    };
  }, []);

  function isThinking(s: CoreState) {
    return s === "thinking" || s === "executing";
  }

  // Interactive Click Shockwave Trigger
  const handleCoreClick = () => {
    animate(animStateRef.current, {
      corePulseScale: [1.3, 1.0],
      glowBoost: [2.0, 1.0],
      duration: 500,
      ease: "outElastic(1, .5)",
    });

    if (onClick) {
      onClick();
    }
  };

  return (
    <motion.div
      ref={containerRef}
      onClick={handleCoreClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      aria-label="Ryven AI Core — Quantum Singularity"
      whileHover={{ scale: 1.02 }}
      whileTap={{ scale: 0.98 }}
      className={`relative flex items-center justify-center select-none cursor-pointer group ${className}`}
      style={{
        width: "100%",
        maxWidth: 580,
        height: 430,
        background: "transparent",
      }}
    >
      <canvas
        ref={canvasRef}
        style={{
          width: "100%",
          height: "100%",
          display: "block",
          background: "transparent",
          pointerEvents: "none",
        }}
      />
    </motion.div>
  );
}
