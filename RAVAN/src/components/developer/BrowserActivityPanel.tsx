import { useEffect, useState } from "react";
import { StatusIndicator } from "@/components/hud/StatusIndicator";
import { fetchBrowserState, confirmBrowserAction } from "@/services/browser";
import { fetchInternetState, resumeInternetAuth } from "@/services/internet";
import type { BrowserState } from "@/types/browser";
import type { InternetAgentState } from "@/types/internet";

interface BrowserActivityPanelProps {
  sessionId?: string;
}

export function BrowserActivityPanel({ sessionId }: BrowserActivityPanelProps) {
  const [browserState, setBrowserState] = useState<BrowserState | null>(null);
  const [internetState, setInternetState] = useState<InternetAgentState | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [confirmMsg, setConfirmMsg] = useState<string | null>(null);
  const [resumingAuth, setResumingAuth] = useState(false);
  const [authMsg, setAuthMsg] = useState<string | null>(null);

  useEffect(() => {
    let mounted = true;

    const poll = async () => {
      try {
        const [bData, iData] = await Promise.allSettled([
          fetchBrowserState(sessionId),
          fetchInternetState(),
        ]);
        if (mounted) {
          if (bData.status === "fulfilled") setBrowserState(bData.value);
          if (iData.status === "fulfilled") setInternetState(iData.value);
        }
      } catch {
        /* backend offline or polling idle */
      }
    };

    poll();
    const interval = setInterval(poll, 2500);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [sessionId]);

  const handleConfirm = async () => {
    const token = (browserState?.last_action?.details as Record<string, unknown> | undefined)?.[
      "token"
    ];
    if (typeof token !== "string") return;

    setConfirming(true);
    try {
      const res = await confirmBrowserAction(token);
      setConfirmMsg(res.message);
      const updated = await fetchBrowserState(sessionId);
      setBrowserState(updated);
    } catch (err) {
      setConfirmMsg(`Confirmation error: ${String(err)}`);
    } finally {
      setConfirming(false);
    }
  };

  const handleResumeAuth = async () => {
    const token = internetState?.active_auth_session?.resume_token;
    if (!token) return;

    setResumingAuth(true);
    try {
      const res = await resumeInternetAuth(token);
      setAuthMsg(res.message);
      const updated = await fetchInternetState();
      setInternetState(updated);
    } catch (err) {
      setAuthMsg(`Auth resume error: ${String(err)}`);
    } finally {
      setResumingAuth(false);
    }
  };

  const activeStatus =
    internetState?.status !== "IDLE"
      ? internetState?.status
      : browserState?.browser_status || "IDLE";

  const statusTone =
    activeStatus === "ACTIVE" || activeStatus === "EXECUTING" || activeStatus === "NAVIGATING"
      ? "holo"
      : activeStatus === "WAITING_CONFIRMATION" || activeStatus === "WAITING_AUTHENTICATION"
        ? "signal"
        : activeStatus === "FAILED" || activeStatus === "ERROR"
          ? "alert"
          : "muted";

  return (
    <div className="space-y-3">
      {/* Header specs: Unified Internet & Browser Engine */}
      <div className="holo-corners relative border border-border/70 p-3 space-y-2">
        <div className="flex items-center justify-between text-xs tracking-wider">
          <span className="text-muted-foreground uppercase">Internet Agent</span>
          <StatusIndicator
            label={activeStatus || "IDLE"}
            tone={statusTone}
            pulse={
              activeStatus === "NAVIGATING" ||
              activeStatus === "EXECUTING" ||
              activeStatus === "WAITING_AUTHENTICATION"
            }
          />
        </div>

        {/* Current Internet Goal */}
        {internetState?.current_goal && (
          <div className="flex flex-col gap-1 border-t border-border/60 pt-2 text-xs">
            <span
              className="text-[10px] text-muted-foreground uppercase"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              Current Goal
            </span>
            <span className="text-foreground font-mono text-[11px] font-semibold">
              {internetState.current_goal}
            </span>
          </div>
        )}

        {/* Active URL */}
        <div className="flex flex-col gap-1 border-t border-border/60 pt-2 text-xs">
          <span
            className="text-[10px] text-muted-foreground uppercase"
            style={{ fontFamily: "var(--font-mono)" }}
          >
            Active URL
          </span>
          <span
            className="text-foreground truncate font-mono text-[11px] select-all"
            title={internetState?.current_url || browserState?.current_url || "about:blank"}
          >
            {internetState?.current_url || browserState?.current_url || "about:blank"}
          </span>
        </div>

        {/* Page Title */}
        {(internetState?.page_title || browserState?.page_title) && (
          <div className="flex flex-col gap-1 text-xs">
            <span
              className="text-[10px] text-muted-foreground uppercase"
              style={{ fontFamily: "var(--font-mono)" }}
            >
              Page Title
            </span>
            <span className="text-foreground truncate text-[11px]">
              {internetState?.page_title || browserState?.page_title}
            </span>
          </div>
        )}

        {/* Active Action / Progress */}
        {internetState?.active_action && (
          <div className="flex items-center justify-between border-t border-border/60 pt-2 text-[11px] font-mono">
            <span className="text-muted-foreground uppercase text-[10px]">Action</span>
            <span className="text-holo animate-pulse">{internetState.active_action}</span>
          </div>
        )}
      </div>

      {/* Authenticated Session Pause / Resume Notice */}
      {internetState?.active_auth_session && (
        <div className="border border-signal/70 bg-signal/10 p-3 rounded-sm space-y-2">
          <div className="flex items-center gap-1.5 text-xs text-signal font-semibold">
            <span>🔐 Authentication Required</span>
          </div>
          <p className="text-[11px] text-foreground font-mono">
            {internetState.active_auth_session.prompt_message ||
              "Authentication required. Please complete login in the browser."}
          </p>
          <div className="text-[10px] text-muted-foreground font-mono">
            Service:{" "}
            <span className="text-foreground">
              {internetState.active_auth_session.target_service}
            </span>
          </div>
          <button
            onClick={handleResumeAuth}
            disabled={resumingAuth}
            className="w-full py-1.5 text-xs bg-signal/20 hover:bg-signal/30 text-signal border border-signal/50 rounded transition-colors font-mono tracking-wider uppercase font-semibold"
          >
            {resumingAuth ? "Resuming Task..." : "Resume Task (Completed Login)"}
          </button>
          {authMsg && <p className="text-[10px] text-holo font-mono">{authMsg}</p>}
        </div>
      )}

      {/* Human Confirmation Alert */}
      {(browserState?.browser_status === "WAITING_CONFIRMATION" ||
        internetState?.status === "WAITING_CONFIRMATION") && (
        <div className="border border-signal/60 bg-signal/10 p-2.5 rounded-sm space-y-2">
          <div className="flex items-center gap-1.5 text-xs text-signal font-semibold">
            <span>⚠ Safety Boundary</span>
          </div>
          <p className="text-[11px] text-foreground/90 font-mono">
            {internetState?.confirmation_message ||
              "Action targets sensitive or stateful element. Authorization required."}
          </p>
          <button
            onClick={handleConfirm}
            disabled={confirming}
            className="w-full py-1 text-xs bg-signal/20 hover:bg-signal/30 text-signal border border-signal/40 rounded transition-colors font-mono tracking-wider uppercase"
          >
            {confirming ? "Authorizing..." : "Authorize Action"}
          </button>
          {confirmMsg && (
            <p className="text-[10px] text-muted-foreground font-mono">{confirmMsg}</p>
          )}
        </div>
      )}

      {/* Research Summary Card */}
      {internetState?.last_research_result && (
        <div className="border border-border/60 p-2.5 space-y-1.5 text-[11px] font-mono">
          <div className="flex items-center justify-between text-[10px] text-muted-foreground uppercase">
            <span>Research Summary</span>
            <span className="text-holo">
              {internetState.last_research_result.sources.length} Sources
            </span>
          </div>
          <div className="text-foreground font-semibold">
            {internetState.last_research_result.topic}
          </div>
          <p className="text-[10px] text-muted-foreground line-clamp-3">
            {internetState.last_research_result.summary}
          </p>
        </div>
      )}

      {/* Last Operation Record */}
      {browserState?.last_action && (
        <div className="border border-border/60 p-2.5 space-y-1.5 text-[11px] font-mono">
          <div className="flex items-center justify-between text-[10px] text-muted-foreground uppercase">
            <span>Last Browser Action</span>
            <span
              className={
                browserState.last_action.status === "SUCCESS" ? "text-holo" : "text-signal"
              }
            >
              {browserState.last_action.status}
            </span>
          </div>
          <div className="text-foreground font-semibold">
            {browserState.last_action.action_type}
            {browserState.last_action.target ? ` → ${browserState.last_action.target}` : ""}
          </div>
          <div className="text-[10px] text-muted-foreground">
            Latency: {browserState.last_action.duration_ms.toFixed(1)}ms
          </div>
        </div>
      )}

      {/* Page Structure Stats */}
      {browserState?.last_snapshot && (
        <div className="border border-border/40 p-2 text-[10px] text-muted-foreground space-y-1 font-mono">
          <div className="flex justify-between">
            <span>Headings: {browserState.last_snapshot.headings.length}</span>
            <span>Links: {browserState.last_snapshot.links.length}</span>
            <span>Chars: {browserState.last_snapshot.text_content.length}</span>
          </div>
        </div>
      )}
    </div>
  );
}
