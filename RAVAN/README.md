# RYVEN — Personal AI Assistant

An advanced, cinematic personal AI assistant and holographic command environment. RYVEN combines a living holographic intelligence core with voice interaction, spatial HUD telemetry, neural memory lattice, developer diagnostics, and automation protocols.

---

## Key Features

- **Living AI Core**: Multi-layered SVG & Canvas holographic orb reacting to microphone levels, speech synthesis, thinking states, and idle breathing animations.
- **Holographic HUD**: Floating futuristic glassmorphism panels with animated scan lines, corner ticks, and perspective transforms.
- **Voice Intelligence**: Web Speech API integration (speech recognition and synthesis) with automatic simulated fallback for full offline/unsupported browser fidelity.
- **Microphone Waveform**: Real-time Web Audio API frequency analysis driving live audio waves across the core and voice controller.
- **Spatial Panels**:
  - `SYS.01` System Diagnostics: Dynamic CPU, RAM, Battery, Storage, and Network gauges.
  - `DEV.04` Developer Environment: Git status, build pipelines, load monitors, and project trackers.
  - `MEM.03` Neural Memory Lattice: Spatial memory graph with interactive clusters and recall inspector.
  - `AUTO.05` Automation Protocols: Background tasks, continuous daemons, and schedule monitors.
  - `CMD.02` Command Flow: Step-by-step intent decomposition and execution tracer.
- **Floating Navigation**: Quick-switch spatial dock across Core, Chat, System, Memory, Developer, and Automation views.

---

## Architecture & Technology Stack

- **Framework**: TanStack Start (SSR) + React 19 + TanStack Router (File-based)
- **Styling**: Tailwind CSS v4 + OKLCH Color Tokens + Custom Holographic Utilities
- **Motion**: `motion/react` for fluid spring transitions, layout morphing, and staggered scans
- **Icons**: Lucide React
- **Services**: Abstracted service layer (`src/services/jarvis.ts`) with clean TypeScript contracts ready for Antigravity Python backend integration

---

## Getting Started

### Prerequisites

- Node.js >= 20.x
- npm >= 10.x

### Installation

```bash
npm install
```

### Development Server

```bash
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

### Production Build

```bash
npm run build
npm run preview
```

---

## Antigravity Backend Integration

The frontend service layer (`src/services/jarvis.ts`) is decoupled from UI components. Future Python backends developed with Antigravity connect via:

- **Command Pipeline**: REST endpoint (`/api/v2/command`) or WebSocket stream (`/ws/jarvis`).
- **Telemetry Streaming**: SSE / WebSocket subscription for live CPU/GPU/memory hardware metrics.
- **Memory Lattice**: Vector database synchronization with the memory graph.

---

## License

Private / Proprietary — JARVIS 2.0 Project.
