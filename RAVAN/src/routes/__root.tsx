import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  Link,
  createRootRouteWithContext,
  useRouter,
  HeadContent,
  Scripts,
} from "@tanstack/react-router";
import type { ReactNode } from "react";

import appCss from "../styles.css?url";

function NotFoundComponent() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1
          className="text-7xl font-bold text-foreground"
          style={{ fontFamily: "var(--font-display)" }}
        >
          404
        </h1>
        <h2 className="mt-4 text-xl font-semibold text-foreground">Subsystem Route Not Found</h2>
        <p
          className="mt-2 text-sm text-muted-foreground"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          The requested command sequence or route does not exist within the current environment.
        </p>
        <div className="mt-6">
          <Link
            to="/"
            className="inline-flex items-center justify-center rounded-sm bg-holo/20 px-4 py-2 text-sm font-medium text-holo border border-holo/50 transition-colors hover:bg-holo/30"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            RETURN TO COMMAND CENTER
          </Link>
        </div>
      </div>
    </div>
  );
}

function ErrorComponent({ error, reset }: { error: Error; reset: () => void }) {
  console.error(error);
  const router = useRouter();

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="max-w-md text-center">
        <h1
          className="text-xl font-semibold tracking-tight text-foreground"
          style={{ fontFamily: "var(--font-display)" }}
        >
          Subsystem Diagnostics Anomaly
        </h1>
        <p
          className="mt-2 text-sm text-muted-foreground"
          style={{ fontFamily: "var(--font-mono)" }}
        >
          An error occurred during environment execution. You can attempt a subsystem reload or
          return to command center.
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <button
            onClick={() => {
              router.invalidate();
              reset();
            }}
            className="inline-flex items-center justify-center rounded-sm bg-holo/20 px-4 py-2 text-sm font-medium text-holo border border-holo/50 transition-colors hover:bg-holo/30"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            RETRY SUBSYSTEM
          </button>
          <a
            href="/"
            className="inline-flex items-center justify-center rounded-sm border border-border bg-background px-4 py-2 text-sm font-medium text-foreground transition-colors hover:bg-accent"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            RETURN HOME
          </a>
        </div>
      </div>
    </div>
  );
}

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "RYVEN — Personal AI Assistant" },
      {
        name: "description",
        content:
          "RYVEN — Advanced Personal AI Assistant and Holographic Command Environment featuring spatial system HUDs, neural memory lattice, and voice intelligence.",
      },
      { property: "og:site_name", content: "RYVEN" },
      { property: "og:title", content: "RYVEN — Personal AI Assistant" },
      {
        property: "og:description",
        content:
          "Advanced Personal AI Assistant and Holographic Command Environment: living AI core, voice interaction, and spatial HUD telemetry.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary_large_image" },
      { name: "theme-color", content: "#070d18" },
    ],
    links: [
      {
        rel: "stylesheet",
        href: appCss,
      },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "preconnect", href: "https://fonts.gstatic.com", crossOrigin: "anonymous" },
      {
        rel: "stylesheet",
        href: "https://fonts.googleapis.com/css2?family=Orbitron:wght@400..900&family=Rajdhani:wght@300;400;500;600;700&family=JetBrains+Mono:wght@300;400;500&display=swap",
      },
      { rel: "icon", href: "/favicon.svg", type: "image/svg+xml" },
      { rel: "alternate icon", href: "/favicon.ico", type: "image/x-icon" },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
  notFoundComponent: NotFoundComponent,
  errorComponent: ErrorComponent,
});

function RootShell({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        {children}
        <Scripts />
      </body>
    </html>
  );
}

function RootComponent() {
  const { queryClient } = Route.useRouteContext();

  return (
    <QueryClientProvider client={queryClient}>
      <Outlet />
    </QueryClientProvider>
  );
}
