import { useEffect, useRef } from "react";

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  a: number;
  layer: 0 | 1; // 0 = ambient star dust, 1 = foreground holographic dust
  hue: number;
}

interface Star {
  x: number;
  y: number;
  r: number;
  baseAlpha: number;
  speed: number;
  phase: number;
  layer: 0 | 1 | 2; // 0 = distant micro-star, 1 = mid star, 2 = bright star with cross-flare
  color: string;
}

interface ShootingStar {
  x: number;
  y: number;
  vx: number;
  vy: number;
  len: number;
  alpha: number;
  fadeSpeed: number;
  active: boolean;
}

interface EnergyStreamParticle {
  x: number;
  y: number;
  vy: number;
  vx: number;
  r: number;
  alpha: number;
  maxLife: number;
  life: number;
}

/**
 * RYVEN 3.0 — Cinematic Cosmic & Quantum Reactor Environment
 * Features:
 * - Multi-layered deep space void with rich cyan, electric-blue & violet nebulae
 * - Dual-layer alien mountain ridge silhouette with illuminated cyan rim light
 * - Multi-depth starfield with diffraction spikes on pulsar stars
 * - Occasional shooting stars / meteor streaks
 * - Dimensional quantum reactor rings & calibration arcs positioned behind the central AI core
 * - Volumetric light rays & atmospheric haze streaming from the horizon
 * - Restrained floor reflection and perspective floor grid
 * - Smooth pointer-based 3D parallax with zero interaction blocking
 * - Tab visibility and prefers-reduced-motion optimizations
 */
export function HoloBackground({ pull = false }: { pull?: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const pullRef = useRef(pull);
  pullRef.current = pull;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let width = 0;
    let height = 0;
    let particles: Particle[] = [];
    let stars: Star[] = [];
    let shootingStars: ShootingStar[] = [];
    let energyParticles: EnergyStreamParticle[] = [];

    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const motionQuery =
      typeof window !== "undefined"
        ? window.matchMedia("(prefers-reduced-motion: reduce)")
        : null;
    let prefersReducedMotion = motionQuery ? motionQuery.matches : false;

    const onMotionChange = (e: MediaQueryListEvent) => {
      prefersReducedMotion = e.matches;
      if (prefersReducedMotion) {
        cancelAnimationFrame(raf);
        render();
      } else {
        if (isRunning) raf = requestAnimationFrame(render);
      }
    };
    if (motionQuery) {
      motionQuery.addEventListener("change", onMotionChange);
    }

    // Smooth pointer parallax coordinates
    let targetPx = 0;
    let targetPy = 0;
    let currPx = 0;
    let currPy = 0;

    const onPointerMove = (e: MouseEvent) => {
      if (prefersReducedMotion) return;
      const nx = (e.clientX / (width || 1) - 0.5) * 2; // -1 to 1
      const ny = (e.clientY / (height || 1) - 0.5) * 2;
      targetPx = nx * 14;
      targetPy = ny * 10;
    };

    window.addEventListener("mousemove", onPointerMove, { passive: true });

    const resize = () => {
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

      // 1. Star field: 3 depth layers
      const starDensity = Math.round(Math.min(180, (width * height) / 10000));
      stars = Array.from({ length: starDensity }, (_, idx) => {
        const isBright = idx < 6;
        const isMid = !isBright && idx < Math.round(starDensity * 0.35);
        const layer: 0 | 1 | 2 = isBright ? 2 : isMid ? 1 : 0;

        const colors = [
          "rgba(215, 235, 255,",
          "rgba(180, 225, 255,",
          "rgba(200, 210, 255,",
          "rgba(140, 240, 255,",
        ];
        const color: string = colors[Math.floor(Math.random() * colors.length)] || "rgba(215, 235, 255,";

        return {
          x: Math.random() * width,
          y: Math.random() * height * 0.65, // in sky area above horizon
          r: isBright ? Math.random() * 0.8 + 1.2 : isMid ? Math.random() * 0.5 + 0.7 : Math.random() * 0.4 + 0.3,
          baseAlpha: isBright ? 0.75 : isMid ? Math.random() * 0.4 + 0.3 : Math.random() * 0.25 + 0.1,
          speed: Math.random() * 0.02 + 0.008,
          phase: Math.random() * Math.PI * 2,
          layer,
          color,
        };
      });

      // 2. Cosmic & quantum dust particles
      const particleCount = Math.round(Math.min(75, (width * height) / 18000));
      particles = Array.from({ length: particleCount }, (_, i) => {
        const isFg = i % 3 === 0;
        return {
          x: Math.random() * width,
          y: Math.random() * height,
          vx: (Math.random() - 0.5) * (isFg ? 0.2 : 0.08),
          vy: (Math.random() - 0.5) * (isFg ? 0.2 : 0.08),
          r: isFg ? Math.random() * 1.3 + 0.8 : Math.random() * 0.7 + 0.3,
          a: isFg ? Math.random() * 0.35 + 0.15 : Math.random() * 0.2 + 0.08,
          layer: isFg ? 1 : 0,
          hue: Math.random() > 0.4 ? 190 : 275, // cyan or violet
        };
      });

      // 3. Pool of shooting stars
      shootingStars = Array.from({ length: 3 }, () => ({
        x: 0,
        y: 0,
        vx: 0,
        vy: 0,
        len: 0,
        alpha: 0,
        fadeSpeed: 0.02,
        active: false,
      }));

      // 4. Upward energy stream particles from horizon
      energyParticles = Array.from({ length: 24 }, () => ({
        x: width * 0.5 + (Math.random() - 0.5) * 360,
        y: height * 0.58 + Math.random() * 40,
        vy: -(Math.random() * 0.6 + 0.3),
        vx: (Math.random() - 0.5) * 0.2,
        r: Math.random() * 1.2 + 0.5,
        alpha: Math.random() * 0.6 + 0.2,
        maxLife: Math.random() * 120 + 80,
        life: Math.random() * 80,
      }));
    };

    const onWindowResize = () => {
      resize();
      if (prefersReducedMotion) {
        render();
      }
    };

    resize();
    window.addEventListener("resize", onWindowResize);

    let raf = 0;
    let isRunning = true;
    let tick = 0;

    // Helper to spawn a shooting star
    const maybeSpawnShootingStar = () => {
      if (prefersReducedMotion || Math.random() > 0.008) return;
      const inactive = shootingStars.find((s) => !s.active);
      if (!inactive) return;

      const angle = Math.PI * (0.15 + Math.random() * 0.15); // ~27-54 deg downward angle
      const speed = Math.random() * 10 + 12;
      inactive.x = Math.random() * width * 0.8;
      inactive.y = Math.random() * height * 0.3;
      inactive.vx = Math.cos(angle) * speed;
      inactive.vy = Math.sin(angle) * speed;
      inactive.len = Math.random() * 60 + 50;
      inactive.alpha = 1.0;
      inactive.fadeSpeed = Math.random() * 0.02 + 0.018;
      inactive.active = true;
    };

    // 1. Deep Space Atmospheric Nebulae
    const drawNebulae = (px: number, py: number) => {
      const coreX = width * 0.5 + px * 0.6;
      const coreY = height * 0.42 + py * 0.6;

      // Base Deep Cosmic Glow behind Central AI Core
      const coreNebula = ctx.createRadialGradient(
        coreX, coreY, 20,
        coreX, coreY, width * 0.42
      );
      coreNebula.addColorStop(0, "rgba(0, 220, 255, 0.16)");
      coreNebula.addColorStop(0.3, "rgba(147, 51, 234, 0.11)");
      coreNebula.addColorStop(0.65, "rgba(30, 58, 138, 0.08)");
      coreNebula.addColorStop(1, "rgba(2, 6, 23, 0)");
      ctx.fillStyle = coreNebula;
      ctx.fillRect(0, 0, width, height * 0.75);

      // Upper-Right Violet Nebula Cloud
      const violetCloud = ctx.createRadialGradient(
        width * 0.78 + px * 0.4, height * 0.22 + py * 0.4, 30,
        width * 0.78 + px * 0.4, height * 0.22 + py * 0.4, width * 0.34
      );
      violetCloud.addColorStop(0, "rgba(168, 85, 247, 0.09)");
      violetCloud.addColorStop(0.5, "rgba(99, 102, 241, 0.05)");
      violetCloud.addColorStop(1, "rgba(0, 0, 0, 0)");
      ctx.fillStyle = violetCloud;
      ctx.fillRect(0, 0, width, height * 0.6);

      // Upper-Left Electric Cyan Dust Cloud
      const cyanCloud = ctx.createRadialGradient(
        width * 0.22 + px * 0.4, height * 0.26 + py * 0.4, 20,
        width * 0.22 + px * 0.4, height * 0.26 + py * 0.4, width * 0.32
      );
      cyanCloud.addColorStop(0, "rgba(6, 182, 212, 0.08)");
      cyanCloud.addColorStop(0.5, "rgba(14, 116, 224, 0.04)");
      cyanCloud.addColorStop(1, "rgba(0, 0, 0, 0)");
      ctx.fillStyle = cyanCloud;
      ctx.fillRect(0, 0, width, height * 0.6);

      // Edge Vignetting: keep side panels high-contrast and dark
      const leftVignette = ctx.createLinearGradient(0, 0, width * 0.28, 0);
      leftVignette.addColorStop(0, "rgba(2, 5, 14, 0.72)");
      leftVignette.addColorStop(0.7, "rgba(2, 5, 14, 0.3)");
      leftVignette.addColorStop(1, "rgba(2, 5, 14, 0)");
      ctx.fillStyle = leftVignette;
      ctx.fillRect(0, 0, width * 0.28, height);

      const rightVignette = ctx.createLinearGradient(width, 0, width * 0.72, 0);
      rightVignette.addColorStop(0, "rgba(2, 5, 14, 0.72)");
      rightVignette.addColorStop(0.7, "rgba(2, 5, 14, 0.3)");
      rightVignette.addColorStop(1, "rgba(2, 5, 14, 0)");
      ctx.fillStyle = rightVignette;
      ctx.fillRect(width * 0.72, 0, width * 0.28, height);
    };

    // 2. Multi-Depth Starfield & Bright Star Flares
    const drawStars = (px: number, py: number) => {
      for (const s of stars) {
        const parX = s.layer === 2 ? px * 0.4 : s.layer === 1 ? px * 0.25 : px * 0.12;
        const parY = s.layer === 2 ? py * 0.4 : s.layer === 1 ? py * 0.25 : py * 0.12;
        const sx = s.x + parX;
        const sy = s.y + parY;

        const twinkle = prefersReducedMotion
          ? s.baseAlpha
          : s.baseAlpha + Math.sin(tick * s.speed + s.phase) * 0.22;
        const alpha = Math.max(0.04, Math.min(0.95, twinkle));

        ctx.fillStyle = `${s.color}${alpha})`;
        ctx.beginPath();
        ctx.arc(sx, sy, s.r, 0, Math.PI * 2);
        ctx.fill();

        // 4-point cross diffraction flare for the brightest prominent stars
        if (s.layer === 2 && alpha > 0.4) {
          ctx.strokeStyle = `rgba(210, 245, 255, ${alpha * 0.55})`;
          ctx.lineWidth = 0.6;
          const spikeLen = s.r * 5;

          // Horizontal spike
          ctx.beginPath();
          ctx.moveTo(sx - spikeLen, sy);
          ctx.lineTo(sx + spikeLen, sy);
          ctx.stroke();

          // Vertical spike
          ctx.beginPath();
          ctx.moveTo(sx, sy - spikeLen);
          ctx.lineTo(sx, sy + spikeLen);
          ctx.stroke();
        }
      }
    };

    // 3. Shooting Stars / Meteors
    const drawShootingStars = () => {
      maybeSpawnShootingStar();

      for (const s of shootingStars) {
        if (!s.active) continue;

        ctx.save();
        const tailX = s.x - (s.vx / (Math.hypot(s.vx, s.vy) || 1)) * s.len;
        const tailY = s.y - (s.vy / (Math.hypot(s.vx, s.vy) || 1)) * s.len;

        const grad = ctx.createLinearGradient(tailX, tailY, s.x, s.y);
        grad.addColorStop(0, "rgba(0, 240, 255, 0)");
        grad.addColorStop(0.7, `rgba(56, 189, 248, ${s.alpha * 0.6})`);
        grad.addColorStop(1, `rgba(255, 255, 255, ${s.alpha * 0.95})`);

        ctx.strokeStyle = grad;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.moveTo(tailX, tailY);
        ctx.lineTo(s.x, s.y);
        ctx.stroke();

        // Small head glow
        ctx.fillStyle = `rgba(255, 255, 255, ${s.alpha})`;
        ctx.beginPath();
        ctx.arc(s.x, s.y, 1.2, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();

        s.x += s.vx;
        s.y += s.vy;
        s.alpha -= s.fadeSpeed;
        if (s.alpha <= 0 || s.x > width || s.y > height) {
          s.active = false;
        }
      }
    };

    // 4. Large Faint Quantum Reactor Rings & Dimensional Arcs Behind Core
    const drawQuantumReactorRings = (px: number, py: number) => {
      const cx = width * 0.5 + px * 0.8;
      const cy = height * 0.42 + py * 0.8;

      ctx.save();
      const ringAngle = tick * 0.0018;

      // Outer Dimensional Arc: Segmented outer perimeter (R=270)
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(ringAngle * 0.4);
      ctx.strokeStyle = "rgba(0, 240, 255, 0.12)";
      ctx.lineWidth = 1;
      ctx.setLineDash([48, 16, 8, 16]);
      ctx.beginPath();
      ctx.ellipse(0, 0, 270, 250, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();

      // Middle Quantum Containment Ring with Radial Degree Hashes (R=210)
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(-ringAngle * 0.6);
      ctx.strokeStyle = "rgba(168, 85, 247, 0.14)";
      ctx.lineWidth = 1;
      ctx.setLineDash([14, 8]);
      ctx.beginPath();
      ctx.ellipse(0, 0, 210, 195, 0, 0, Math.PI * 2);
      ctx.stroke();

      // 8 Cardinal Tick Notches
      ctx.strokeStyle = "rgba(0, 240, 255, 0.22)";
      ctx.setLineDash([]);
      for (let i = 0; i < 8; i++) {
        const theta = (i * Math.PI) / 4;
        const x1 = Math.cos(theta) * 200;
        const y1 = Math.sin(theta) * 185;
        const x2 = Math.cos(theta) * 218;
        const y2 = Math.sin(theta) * 202;
        ctx.beginPath();
        ctx.moveTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.stroke();
      }
      ctx.restore();

      // Inner Harmonic Resonator Arc (R=155)
      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(ringAngle * 0.8);
      ctx.strokeStyle = "rgba(56, 189, 248, 0.15)";
      ctx.lineWidth = 1.2;
      ctx.setLineDash([80, 40]);
      ctx.beginPath();
      ctx.ellipse(0, 0, 155, 145, 0, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();

      ctx.restore();
    };

    // 5. Volumetric Atmospheric Light Rays (God Rays)
    const drawVolumetricRays = (px: number, py: number) => {
      const cx = width * 0.5 + px * 0.5;
      const cy = height * 0.42 + py * 0.5;

      ctx.save();
      ctx.globalCompositeOperation = "screen";

      const rays = [
        { angle: -Math.PI * 0.38, spread: 0.16, alpha: 0.045 },
        { angle: -Math.PI * 0.5, spread: 0.22, alpha: 0.06 },
        { angle: -Math.PI * 0.62, spread: 0.18, alpha: 0.045 },
        { angle: Math.PI * 0.22, spread: 0.14, alpha: 0.035 },
        { angle: Math.PI * 0.78, spread: 0.14, alpha: 0.035 },
      ];

      for (const r of rays) {
        const rayLen = width * 0.45;
        const sway = Math.sin(tick * 0.008 + r.angle * 1.5) * 0.04;
        const currentAngle = r.angle + sway;

        const a1 = currentAngle - r.spread / 2;
        const a2 = currentAngle + r.spread / 2;

        const x1 = cx + Math.cos(a1) * rayLen;
        const y1 = cy + Math.sin(a1) * rayLen;
        const x2 = cx + Math.cos(a2) * rayLen;
        const y2 = cy + Math.sin(a2) * rayLen;

        const rayGrad = ctx.createRadialGradient(cx, cy, 10, cx, cy, rayLen);
        rayGrad.addColorStop(0, `rgba(0, 240, 255, ${r.alpha})`);
        rayGrad.addColorStop(0.5, `rgba(56, 189, 248, ${r.alpha * 0.5})`);
        rayGrad.addColorStop(1, "rgba(0, 0, 0, 0)");

        ctx.fillStyle = rayGrad;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(x1, y1);
        ctx.lineTo(x2, y2);
        ctx.closePath();
        ctx.fill();
      }

      ctx.restore();
    };

    // 6. Dual-Layer Distant Alien Mountains & Luminous Horizon
    const drawLandscape = (px: number, py: number) => {
      const horizonY = height * 0.58;

      ctx.save();

      // --- Layer 1: Far Distant Mountain Peaks (dark slate/purple) ---
      const farMountainY = horizonY - 14;
      const farSegments: [number, number][] = [
        [0.0, -15], [0.08, -35], [0.15, -12], [0.22, -45],
        [0.32, -20], [0.42, -52], [0.5, -28], [0.58, -48],
        [0.68, -18], [0.78, -55], [0.86, -22], [0.93, -38], [1.0, -15],
      ];

      ctx.beginPath();
      ctx.moveTo(0, farMountainY);
      for (const [xR, yOff] of farSegments) {
        ctx.lineTo(width * xR + px * 0.4, farMountainY + yOff + py * 0.3);
      }
      ctx.lineTo(width, height);
      ctx.lineTo(0, height);
      ctx.closePath();

      const farGrad = ctx.createLinearGradient(0, horizonY - 60, 0, horizonY);
      farGrad.addColorStop(0, "rgba(10, 16, 36, 0.75)");
      farGrad.addColorStop(1, "rgba(4, 8, 20, 0.85)");
      ctx.fillStyle = farGrad;
      ctx.fill();

      // --- Layer 2: Mid Alien Geometric Ridge with Luminous Cyan Rim ---
      const midSegments: [number, number][] = [
        [0.0, -6], [0.06, -24], [0.12, -8], [0.18, -34],
        [0.26, -12], [0.34, -28], [0.43, -14], [0.5, -18],
        [0.57, -12], [0.66, -32], [0.74, -10], [0.83, -38],
        [0.91, -16], [1.0, -4],
      ];

      ctx.beginPath();
      ctx.moveTo(0, horizonY);
      for (const [xR, yOff] of midSegments) {
        ctx.lineTo(width * xR + px * 0.7, horizonY + yOff + py * 0.5);
      }
      ctx.lineTo(width, height);
      ctx.lineTo(0, height);
      ctx.closePath();

      const midGrad = ctx.createLinearGradient(0, horizonY - 40, 0, horizonY + 25);
      midGrad.addColorStop(0, "rgba(6, 12, 28, 0.96)");
      midGrad.addColorStop(0.35, "rgba(3, 7, 18, 0.98)");
      midGrad.addColorStop(1, "rgba(2, 5, 14, 1)");
      ctx.fillStyle = midGrad;
      ctx.fill();

      // Sharp Cyan/Electric Rim Line on Mid Peaks
      ctx.beginPath();
      ctx.moveTo(0, horizonY);
      for (const [xR, yOff] of midSegments) {
        ctx.lineTo(width * xR + px * 0.7, horizonY + yOff + py * 0.5);
      }
      ctx.strokeStyle = "rgba(56, 189, 248, 0.32)";
      ctx.lineWidth = 1.3;
      ctx.stroke();

      // Atmospheric Luminous Horizon Line Glow
      const horizonGlow = ctx.createRadialGradient(
        width * 0.5 + px * 0.5, horizonY, 5,
        width * 0.5 + px * 0.5, horizonY, width * 0.5
      );
      horizonGlow.addColorStop(0, "rgba(0, 240, 255, 0.25)");
      horizonGlow.addColorStop(0.25, "rgba(99, 102, 241, 0.14)");
      horizonGlow.addColorStop(0.65, "rgba(0, 240, 255, 0.03)");
      horizonGlow.addColorStop(1, "rgba(0, 0, 0, 0)");
      ctx.fillStyle = horizonGlow;
      ctx.fillRect(0, horizonY - 12, width, 55);

      ctx.restore();
    };

    // 7. Perspective Floor Grid with Restrained Reflection & Pedestal Radiance
    const drawFloor = (px: number, py: number) => {
      const horizonY = height * 0.58;
      const vpX = width * 0.5 + px * 0.6;
      const vpY = horizonY + py * 0.4;

      ctx.save();
      // Clip rendering to strictly below horizon
      ctx.beginPath();
      ctx.rect(0, horizonY - 4, width, height - horizonY + 4);
      ctx.clip();

      // Core Soft Vertical Reflection on Polished Floor (Elliptical, zero hard rectangular edges)
      const reflectGrad = ctx.createRadialGradient(vpX, horizonY + 70, 5, vpX, horizonY + 70, 95);
      reflectGrad.addColorStop(0, "rgba(0, 240, 255, 0.2)");
      reflectGrad.addColorStop(0.35, "rgba(56, 189, 248, 0.1)");
      reflectGrad.addColorStop(0.7, "rgba(168, 85, 247, 0.04)");
      reflectGrad.addColorStop(1, "rgba(0, 0, 0, 0)");
      ctx.fillStyle = reflectGrad;
      ctx.beginPath();
      ctx.ellipse(vpX, horizonY + 70, 40, 85, 0, 0, Math.PI * 2);
      ctx.fill();

      // Floor Pedestal Radiance Ellipse
      const pedestalGlow = ctx.createRadialGradient(
        vpX, horizonY + 75, 8,
        vpX, horizonY + 75, 160
      );
      pedestalGlow.addColorStop(0, "rgba(0, 240, 255, 0.24)");
      pedestalGlow.addColorStop(0.4, "rgba(147, 51, 234, 0.12)");
      pedestalGlow.addColorStop(1, "rgba(0, 0, 0, 0)");
      ctx.fillStyle = pedestalGlow;
      ctx.beginPath();
      ctx.ellipse(vpX, horizonY + 75, 160, 46, 0, 0, Math.PI * 2);
      ctx.fill();

      // Perspective Floor Longitudinal Rays (Batched into single gradient & path)
      const numRays = 26;
      const rayGrad = ctx.createLinearGradient(0, vpY, 0, height);
      rayGrad.addColorStop(0, "rgba(0, 240, 255, 0.02)");
      rayGrad.addColorStop(0.25, "rgba(0, 240, 255, 0.14)");
      rayGrad.addColorStop(0.8, "rgba(0, 240, 255, 0.06)");
      rayGrad.addColorStop(1, "rgba(0, 0, 0, 0)");
      ctx.strokeStyle = rayGrad;
      ctx.lineWidth = 1;

      ctx.beginPath();
      for (let i = 0; i <= numRays; i++) {
        const spread = (i / numRays - 0.5) * 2; // -1 to +1
        const bottomX = vpX + spread * width * 1.35;
        ctx.moveTo(vpX + spread * 35, vpY);
        ctx.lineTo(bottomX, height);
      }
      ctx.stroke();

      // Transverse Horizontal Grid Lines with Exponential Depth (Batched)
      const numTransverse = 13;
      const maxTransverseHalf = (width * 1.35) / 2;
      const transGrad = ctx.createLinearGradient(vpX - maxTransverseHalf, 0, vpX + maxTransverseHalf, 0);
      transGrad.addColorStop(0, "rgba(0, 0, 0, 0)");
      transGrad.addColorStop(0.2, "rgba(0, 240, 255, 0.08)");
      transGrad.addColorStop(0.5, "rgba(0, 240, 255, 0.16)");
      transGrad.addColorStop(0.8, "rgba(0, 240, 255, 0.08)");
      transGrad.addColorStop(1, "rgba(0, 0, 0, 0)");
      ctx.strokeStyle = transGrad;

      ctx.beginPath();
      for (let j = 1; j <= numTransverse; j++) {
        const progress = Math.pow(j / numTransverse, 2.2);
        const y = horizonY + progress * (height - horizonY);
        const halfWidth = (width * 1.35 * (progress * 0.95 + 0.05)) / 2;
        ctx.moveTo(vpX - halfWidth, y);
        ctx.lineTo(vpX + halfWidth, y);
      }
      ctx.stroke();

      // Central Catwalk Guide Rails
      const catwalkGrad = ctx.createLinearGradient(vpX, horizonY, vpX, height);
      catwalkGrad.addColorStop(0, "rgba(0, 240, 255, 0.06)");
      catwalkGrad.addColorStop(0.25, "rgba(0, 240, 255, 0.28)");
      catwalkGrad.addColorStop(0.85, "rgba(0, 240, 255, 0.12)");
      catwalkGrad.addColorStop(1, "rgba(0, 0, 0, 0)");
      ctx.strokeStyle = catwalkGrad;
      ctx.lineWidth = 1.4;

      ctx.beginPath();
      ctx.moveTo(vpX - 22, horizonY);
      ctx.lineTo(vpX - width * 0.22, height);
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(vpX + 22, horizonY);
      ctx.lineTo(vpX + width * 0.22, height);
      ctx.stroke();

      ctx.restore();
    };

    // 8. Upward Rising Energy Streams
    const drawEnergyStreams = () => {
      const horizonY = height * 0.58;

      for (const p of energyParticles) {
        if (!prefersReducedMotion) {
          p.y += p.vy;
          p.x += p.vx;
          p.life++;

          if (p.life > p.maxLife || p.y < horizonY - 140) {
            p.x = width * 0.5 + (Math.random() - 0.5) * 320;
            p.y = horizonY + Math.random() * 30;
            p.life = 0;
          }
        }

        const lifeProgress = p.life / p.maxLife;
        const currentAlpha = p.alpha * Math.sin(lifeProgress * Math.PI);

        ctx.fillStyle = `rgba(0, 240, 255, ${currentAlpha})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
    };

    // 9. Floating Cosmic Particles (with optional pull toward core when thinking)
    const drawParticles = (px: number, py: number) => {
      const cx = width * 0.5 + px * 0.8;
      const cy = height * 0.42 + py * 0.8;

      for (const p of particles) {
        if (!prefersReducedMotion) {
          if (pullRef.current && p.layer === 1) {
            const dx = cx - p.x;
            const dy = cy - p.y;
            const dist = Math.hypot(dx, dy) || 1;
            p.x += (dx / dist) * 1.2;
            p.y += (dy / dist) * 1.2;
            if (dist < 45) {
              p.x = Math.random() * width;
              p.y = Math.random() * height;
            }
          } else {
            p.x += p.vx;
            p.y += p.vy;
            if (p.x < 0) p.x = width;
            if (p.x > width) p.x = 0;
            if (p.y < 0) p.y = height;
            if (p.y > height) p.y = 0;
          }
        }

        const pOffX = p.layer === 1 ? px * 1.2 : px * 0.4;
        const pOffY = p.layer === 1 ? py * 1.2 : py * 0.4;

        ctx.globalAlpha = p.a;
        ctx.fillStyle =
          p.hue === 190
            ? "rgba(0, 240, 255, 0.8)"
            : "rgba(192, 132, 252, 0.75)";
        ctx.beginPath();
        ctx.arc(p.x + pOffX, p.y + pOffY, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
    };

    // Main Master Render Frame
    const render = () => {
      if (!isRunning) return;
      ctx.clearRect(0, 0, width, height);
      tick++;

      // Smooth lerp parallax offset
      if (!prefersReducedMotion) {
        currPx += (targetPx - currPx) * 0.04;
        currPy += (targetPy - currPy) * 0.04;
      }

      // Draw all layers in spatial depth order
      drawNebulae(currPx, currPy);
      drawStars(currPx, currPy);
      drawShootingStars();
      drawQuantumReactorRings(currPx, currPy);
      drawVolumetricRays(currPx, currPy);
      drawLandscape(currPx, currPy);
      drawFloor(currPx, currPy);
      drawEnergyStreams();
      drawParticles(currPx, currPy);

      raf = 0;
      if (!prefersReducedMotion) {
        raf = requestAnimationFrame(render);
      }
    };

    const handleVisibility = () => {
      if (document.hidden) {
        isRunning = false;
        if (raf) {
          cancelAnimationFrame(raf);
          raf = 0;
        }
      } else {
        if (!isRunning) {
          isRunning = true;
          if (!prefersReducedMotion && !raf) {
            raf = requestAnimationFrame(render);
          } else if (prefersReducedMotion) {
            render();
          }
        }
      }
    };

    document.addEventListener("visibilitychange", handleVisibility);
    render();

    return () => {
      isRunning = false;
      if (raf) {
        cancelAnimationFrame(raf);
        raf = 0;
      }
      if (motionQuery) motionQuery.removeEventListener("change", onMotionChange);
      window.removeEventListener("resize", onWindowResize);
      window.removeEventListener("mousemove", onPointerMove);
      document.removeEventListener("visibilitychange", handleVisibility);
    };
  }, []);

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden bg-background">
      {/* 1. Volumetric cyan/electric-blue/violet deep celestial gradient */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(120% 90% at 50% 38%, oklch(0.24 0.05 225 / 0.65), transparent 70%), radial-gradient(90% 70% at 50% 95%, oklch(0.26 0.09 285 / 0.35), transparent 80%)",
        }}
      />

      {/* 2. Precision holographic coordinate grid */}
      <div
        className="absolute inset-0 opacity-[0.14]"
        style={{
          backgroundImage:
            "linear-gradient(to right, var(--color-holo-deep) 1px, transparent 1px), linear-gradient(to bottom, var(--color-holo-deep) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
          maskImage: "radial-gradient(110% 85% at 50% 40%, black 25%, transparent 80%)",
        }}
      />

      {/* 3. High-Performance Canvas for Stars, Nebulae, Reactor Rings, Landscape & Rays */}
      <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />

      {/* 4. Micro-scanline screen texture */}
      <div
        className="absolute inset-0 opacity-[0.035] mix-blend-screen pointer-events-none"
        style={{
          backgroundImage:
            "repeating-linear-gradient(to bottom, oklch(0.96 0.01 220 / 0.4) 0 1px, transparent 1px 3px)",
        }}
      />
    </div>
  );
}
