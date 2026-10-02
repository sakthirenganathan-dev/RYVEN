/**
 * Browser API Service for M14.3 Controlled Browser Engine.
 */

import { RYVEN_API_BASE_URL } from "./jarvis";
import type { BrowserState } from "@/types/browser";

export async function fetchBrowserState(sessionId?: string): Promise<BrowserState> {
  const url = new URL(`${RYVEN_API_BASE_URL}/api/browser/state`);
  if (sessionId) url.searchParams.set("session_id", sessionId);

  const res = await fetch(url.toString());
  if (!res.ok) {
    throw new Error(`Failed to fetch browser state: ${res.status}`);
  }
  return res.json();
}

export async function fetchBrowserSessions(): Promise<Array<{ session_id: string; current_url: string; page_title: string; browser_status: string }>> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/browser/sessions`);
  if (!res.ok) {
    throw new Error(`Failed to fetch browser sessions: ${res.status}`);
  }
  return res.json();
}

export async function confirmBrowserAction(token: string): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/browser/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!res.ok) {
    throw new Error(`Failed to confirm browser action: ${res.status}`);
  }
  return res.json();
}
