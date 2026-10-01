import { cn } from "@/lib/utils";

export function HolographicText({
  children,
  className,
  size = "md",
}: {
  children: React.ReactNode;
  className?: string;
  size?: "sm" | "md" | "lg" | "xl";
}) {
  const sizes = {
    sm: "text-xs tracking-[0.3em]",
    md: "text-sm tracking-[0.34em]",
    lg: "text-2xl tracking-[0.42em]",
    xl: "text-4xl md:text-5xl tracking-[0.5em]",
  } as const;
  return (
    <span
      className={cn("holo-title text-glow anim-flicker text-holo", sizes[size], className)}
      style={{ fontFamily: "var(--font-display)" }}
    >
      {children}
    </span>
  );
}

export function HoloLabel({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <span className={cn("holo-label block", className)}>{children}</span>;
}
