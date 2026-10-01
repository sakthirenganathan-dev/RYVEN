import { useEffect, useRef } from "react";

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  a: number;
}

/** Dark technological environment: radial light, fine grid, drifting particles. */
export function HoloBackground({ pull = false }: { pull?: boolean }) {
  const ref = useRef<HTMLCanvasElement | null>(null);
  const pullRef = useRef(pull);
  pullRef.current = pull;

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const styles = getComputedStyle(document.documentElement);
    const holo = styles.getPropertyValue("--holo").trim() || "rgb(120,220,255)";

    let width = 0;
    let height = 0;
    let particles: Particle[] = [];
    const dpr = Math.min(2, window.devicePixelRatio || 1);

    const resize = () => {
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = width * dpr;
      canvas.height = height * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const count = Math.round(Math.min(110, (width * height) / 16000));
      particles = Array.from({ length: count }, () => ({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.18,
        vy: (Math.random() - 0.5) * 0.18,
        r: Math.random() * 1.3 + 0.3,
        a: Math.random() * 0.4 + 0.15,
      }));
    };

    resize();
    window.addEventListener("resize", resize);

    let raf = 0;
    const render = () => {
      ctx.clearRect(0, 0, width, height);
      const cx = width / 2;
      const cy = height * 0.42;

      for (const p of particles) {
        if (pullRef.current) {
          const dx = cx - p.x;
          const dy = cy - p.y;
          const d = Math.hypot(dx, dy) || 1;
          p.x += (dx / d) * 0.9;
          p.y += (dy / d) * 0.9;
          if (d < 40) {
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
        ctx.globalAlpha = p.a;
        ctx.fillStyle = holo;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      raf = requestAnimationFrame(render);
    };
    raf = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden">
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(120% 90% at 50% 38%, oklch(0.28 0.05 225 / 0.55), transparent 62%), radial-gradient(70% 60% at 50% 110%, oklch(0.3 0.07 285 / 0.22), transparent 70%)",
        }}
      />
      <div
        className="absolute inset-0 opacity-[0.16]"
        style={{
          backgroundImage:
            "linear-gradient(to right, var(--color-holo-deep) 1px, transparent 1px), linear-gradient(to bottom, var(--color-holo-deep) 1px, transparent 1px)",
          backgroundSize: "64px 64px",
          maskImage: "radial-gradient(120% 90% at 50% 40%, black, transparent 78%)",
        }}
      />
      <canvas ref={ref} className="absolute inset-0 h-full w-full" />
      <div
        className="absolute inset-0 opacity-[0.05] mix-blend-screen"
        style={{
          backgroundImage:
            "repeating-linear-gradient(to bottom, oklch(0.9 0 0 / 0.5) 0 1px, transparent 1px 3px)",
        }}
      />
    </div>
  );
}
