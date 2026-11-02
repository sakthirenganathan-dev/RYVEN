import { useEffect, useRef, useState } from "react";
import { Brain, ArrowRight } from "lucide-react";
import { fetchMemoryStats, type MemoryStatsData } from "@/services/jarvis";

interface Node3D {
  x: number;
  y: number;
  z: number;
}

export function MemoryLatticeWidget({
  onExplore,
}: {
  onExplore?: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [stats, setStats] = useState<MemoryStatsData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const res = await fetchMemoryStats();
        if (mounted) {
          setStats(res);
          setLoading(false);
        }
      } catch {
        if (mounted) setLoading(false);
      }
    };
    load();
    const interval = setInterval(load, 15000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    let width = canvas.width;
    let height = canvas.height;
    let angleX = 0.4;
    let angleY = 0.6;
    let raf = 0;

    // Generate geodesic icosahedron/polyhedral nodes
    const phi = (1 + Math.sqrt(5)) / 2;
    const rawNodes: [number, number, number][] = [
      [-1, phi, 0],
      [1, phi, 0],
      [-1, -phi, 0],
      [1, -phi, 0],
      [0, -1, phi],
      [0, 1, phi],
      [0, -1, -phi],
      [0, 1, -phi],
      [phi, 0, -1],
      [phi, 0, 1],
      [-phi, 0, -1],
      [-phi, 0, 1],
      // Inner core nodes
      [0, 0, 0],
      [0.6, 0.6, 0.6],
      [-0.6, -0.6, 0.6],
      [0.6, -0.6, -0.6],
      [-0.6, 0.6, -0.6],
    ];

    // Normalize and scale nodes
    const radius = 32;
    const nodes: Node3D[] = rawNodes.map(([x, y, z]) => {
      const len = Math.hypot(x, y, z) || 1;
      return {
        x: (x / len) * radius,
        y: (y / len) * radius,
        z: (z / len) * radius,
      };
    });

    // Edges between close nodes
    const edges: [number, number][] = [];
    for (let i = 0; i < nodes.length; i++) {
      const n1 = nodes[i];
      if (!n1) continue;
      for (let j = i + 1; j < nodes.length; j++) {
        const n2 = nodes[j];
        if (!n2) continue;
        const d = Math.hypot(n1.x - n2.x, n1.y - n2.y, n1.z - n2.z);
        if (d < 45) {
          edges.push([i, j]);
        }
      }
    }

    const render = () => {
      ctx.clearRect(0, 0, width, height);
      angleY += 0.012;
      angleX += 0.006;

      const cosY = Math.cos(angleY);
      const sinY = Math.sin(angleY);
      const cosX = Math.cos(angleX);
      const sinX = Math.sin(angleX);

      const cx = width / 2;
      const cy = height / 2;
      const fov = 140;

      // Rotate and project nodes
      const projected = nodes.map((n) => {
        let x1 = n.x * cosY - n.z * sinY;
        let z1 = n.z * cosY + n.x * sinY;
        let y2 = n.y * cosX - z1 * sinX;
        let z2 = z1 * cosX + n.y * sinX;

        const scale = fov / (fov + z2 + 60);
        return {
          x: cx + x1 * scale,
          y: cy + y2 * scale,
          z: z2,
          scale,
        };
      });

      // Draw edges
      for (const [i, j] of edges) {
        const p1 = projected[i];
        const p2 = projected[j];
        if (!p1 || !p2) continue;
        const avgZ = (p1.z + p2.z) / 2;
        const alpha = Math.max(0.12, Math.min(0.65, (avgZ + 40) / 70));

        ctx.strokeStyle = `rgba(0, 240, 255, ${alpha})`;
        ctx.lineWidth = 0.9;
        ctx.beginPath();
        ctx.moveTo(p1.x, p1.y);
        ctx.lineTo(p2.x, p2.y);
        ctx.stroke();
      }

      // Draw node points
      for (const p of projected) {
        const alpha = Math.max(0.2, Math.min(0.95, (p.z + 40) / 70));
        ctx.fillStyle = `rgba(180, 120, 255, ${alpha})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2 * p.scale, 0, Math.PI * 2);
        ctx.fill();

        ctx.fillStyle = `rgba(0, 240, 255, ${alpha * 0.8})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, 1 * p.scale, 0, Math.PI * 2);
        ctx.fill();
      }

      raf = requestAnimationFrame(render);
    };

    render();

    return () => {
      cancelAnimationFrame(raf);
    };
  }, []);

  const projectsCount = stats ? stats.project_scoped_count : loading ? "..." : 0;
  const totalCount = stats ? stats.total_memories : loading ? "..." : 0;
  const episodicCount = stats ? stats.episodic_count : loading ? "..." : 0;
  const preferencesCount = stats ? stats.preference_count : loading ? "..." : 0;

  return (
    <div className="glass-panel p-3.5">
      {/* Header */}
      <div className="flex items-center justify-between pb-2">
        <span className="flex items-center gap-1.5 section-title text-cyan-300">
          <Brain className="h-3.5 w-3.5 text-cyan-400" />
          Memory Lattice
        </span>
        <span className="text-[10px] font-mono text-slate-500">
          {stats ? "LIVE" : loading ? "SYNCING..." : "OFFLINE"}
        </span>
      </div>

      {/* Content: Left stats, 3D Canvas, Right stats */}
      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-2 py-1">
        {/* Left stats */}
        <div className="space-y-1.5 text-[11px] font-mono">
          <div>
            <span className="block text-slate-400">Projects</span>
            <span className="font-bold text-white text-xs">{projectsCount}</span>
          </div>
          <div>
            <span className="block text-slate-400">Episodic</span>
            <span className="font-bold text-white text-xs">{episodicCount}</span>
          </div>
          <div className="text-[10px] text-cyan-400/80">Active Store</div>
        </div>

        {/* Central 3D wireframe canvas */}
        <div className="relative flex items-center justify-center">
          <canvas
            ref={canvasRef}
            width={96}
            height={96}
            className="h-24 w-24 drop-shadow-[0_0_12px_rgba(0,240,255,0.4)]"
          />
        </div>

        {/* Right stats */}
        <div className="space-y-1.5 text-right text-[11px] font-mono">
          <div>
            <span className="block text-slate-400">Total Entries</span>
            <span className="font-bold text-white text-xs">{totalCount}</span>
          </div>
          <div>
            <span className="block text-slate-400">Preferences</span>
            <span className="font-bold text-white text-xs">{preferencesCount}</span>
          </div>
        </div>
      </div>

      {/* Footer Explore Memory action */}
      <button
        type="button"
        onClick={onExplore}
        className="mt-2.5 flex w-full items-center justify-center gap-1.5 rounded-lg border border-cyan-500/30 bg-cyan-950/30 py-1.5 text-xs font-medium text-cyan-300 transition-all hover:border-cyan-400 hover:bg-cyan-900/40 hover:text-white"
      >
        <span>Explore Memory</span>
        <ArrowRight className="h-3 w-3" />
      </button>
    </div>
  );
}
