import { defineConfig } from "@lovable.dev/vite-tanstack-config";

/**
 * RYVEN — Vite & TanStack Start Configuration
 * Bundles TanStack Start SSR entrypoint redirected to src/server.ts
 */
export default defineConfig({
  tanstackStart: {
    server: { entry: "server" },
  },
});
