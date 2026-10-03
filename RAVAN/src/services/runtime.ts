/**
 * RYVEN 3.0 — Runtime Reliability, Performance & Resource Telemetry Service (M15.3.9)
 */

import { RYVEN_API_BASE_URL } from "./jarvis";

export interface HostResources {
  status: "NORMAL" | "WARNING" | "CRITICAL" | "UNKNOWN";
  cpu_pct: number;
  ram_total_gb: number;
  ram_available_gb: number;
  ram_used_gb: number;
  ram_used_pct: number;
  process_memory_mb: number;
  process_memory_pct: number;
  thresholds: {
    ram_warning_pct: number;
    ram_critical_pct: number;
    cpu_warning_pct: number;
  };
  timestamp: string;
}

export interface AggregatePerformance {
  total_operations: number;
  successful_operations: number;
  failed_operations: number;
  avg_latency_ms: number;
  min_latency_ms: number;
  max_latency_ms: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  llm_duration_ms: number;
  tool_duration_ms: number;
  browser_duration_ms: number;
  workflow_duration_ms: number;
  category_counts: Record<string, number>;
  sampled_window_size: number;
}

export interface RuntimeStatusResponse {
  runtime_state: string;
  host_resources: HostResources;
  performance: AggregatePerformance;
  active_or_incomplete_tasks_count: number;
  status: string;
}

export async function fetchRuntimeStatus(): Promise<RuntimeStatusResponse> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/v1/runtime/status`);
  if (!res.ok) {
    throw new Error(`Failed to fetch runtime status: ${res.status}`);
  }
  return res.json();
}

export async function fetchRuntimeResources(): Promise<HostResources> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/v1/runtime/resources`);
  if (!res.ok) {
    throw new Error(`Failed to fetch runtime resources: ${res.status}`);
  }
  return res.json();
}

export async function fetchRuntimePerformance(): Promise<{ aggregate: AggregatePerformance; recent_operations: unknown[] }> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/v1/runtime/performance`);
  if (!res.ok) {
    throw new Error(`Failed to fetch runtime performance: ${res.status}`);
  }
  return res.json();
}
