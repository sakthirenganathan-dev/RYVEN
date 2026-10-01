# RYVEN — Coding Conventions & Guidelines

## Core Principles

- **Preserve Holographic Aesthetic**: Maintain the OKLCH color token system (`--holo`, `--holo-dim`, `--holo-deep`, `--signal`, `--violet`) and holographic utility classes defined in `src/styles.css`.
- **Decoupled Architecture**: All assistant interactions, telemetry data, and background routines belong in `src/services/jarvis.ts` with contracts in `src/types/jarvis.ts`. Never hardcode business or backend logic inside UI components.
- **Motion & Visuals**: Use `motion/react` for animations. Ensure procedural SVGs and Canvas background gracefully adapt to resizing and high-DPI displays.
- **Backend Readiness**: Designed to connect to a Python RYVEN backend created in Antigravity. Keep mock data isolated and adhere strictly to TypeScript contracts.
