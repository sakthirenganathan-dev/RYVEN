/**
 * RYVEN 3.0 — Internet API Service for the Unified Internet Agent Engine.
 */

import { RYVEN_API_BASE_URL } from "./jarvis";
import type { InternetAgentState } from "@/types/internet";

export async function fetchInternetState(): Promise<InternetAgentState> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/internet/state`);
  if (!res.ok) {
    throw new Error(`Failed to fetch internet agent state: ${res.status}`);
  }
  return res.json();
}

export async function resumeInternetAuth(
  token: string,
): Promise<{ success: boolean; message: string }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/internet/auth-resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  if (!res.ok) {
    throw new Error(`Failed to resume authentication: ${res.status}`);
  }
  return res.json();
}

export async function executeWebSearch(query: string, maxResults = 5) {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/internet/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, max_results: maxResults }),
  });
  if (!res.ok) {
    throw new Error(`Web search failed: ${res.status}`);
  }
  return res.json();
}

export async function executeWebResearch(topic: string, maxSources = 3) {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/internet/research`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ topic, max_sources: maxSources }),
  });
  if (!res.ok) {
    throw new Error(`Web research failed: ${res.status}`);
  }
  return res.json();
}
