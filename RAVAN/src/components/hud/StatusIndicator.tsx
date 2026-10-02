import { cn } from "@/lib/utils";

export function StatusIndicator({
  label,
  tone = "holo",
  pulse = true,
  className,
}: {
  label: string;
  tone?: "holo" | "signal" | "violet" | "muted" | "alert";
  pulse?: boolean;
  className?: string;
}) {
  const tones = {
    holo: "bg-holo",
    signal: "bg-signal",
    violet: "bg-violet",
    muted: "bg-muted-foreground",
    alert: "bg-destructive",
  } as const;
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <span className="relative flex h-1.5 w-1.5">
        {pulse && (
          <span
            className={cn("absolute inset-0 rounded-full opacity-70", tones[tone])}
            style={{ animation: "holo-pulse-ring 2.2s ease-out infinite" }}
          />
        )}
        <span className={cn("relative h-1.5 w-1.5 rounded-full", tones[tone])} />
      </span>
      <span className="holo-label">{label}</span>
    </span>
  );
}
