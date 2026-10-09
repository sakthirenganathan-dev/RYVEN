/**
 * RYVEN 3.0 — Multi-Model Provider & Hardware Telemetry Service
 */

import { RYVEN_API_BASE_URL } from "./jarvis";

export interface ProviderHealth {
  provider: string;
  configured: boolean;
  authenticated?: boolean;
  available: boolean;
  local: boolean;
  model: string;
  remote_allowed?: boolean;
  error?: string | null;
}

export interface UnifiedProvidersStatus {
  provider: string;
  active_default: string;
  active_default_model: string;
  providers: {
    ollama: ProviderHealth;
    grok: ProviderHealth;
    huggingface_remote: ProviderHealth;
    huggingface_local: ProviderHealth;
  };
}

export async function fetchProvidersHealth(): Promise<UnifiedProvidersStatus> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/v1/models/providers`);
  if (!res.ok) {
    throw new Error(`Failed to fetch providers health: ${res.status}`);
  }
  return res.json();
}

export interface VisionStatus {
  status: string;
  local_only: boolean;
  remote_vision_allowed: boolean;
  default_model: string;
  vision_models: string[];
  installed_vision_models: string[];
  ocr_engine: string;
  pytesseract_available: boolean;
  pil_available: boolean;
  cv2_available: boolean;
}

export async function fetchVisionStatus(): Promise<VisionStatus> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/v1/vision/status`);
  if (!res.ok) {
    throw new Error(`Failed to fetch vision status: ${res.status}`);
  }
  return res.json();
}

export interface LatencyStats {
  count: number;
  avg_ms: number | null;
  median_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
}

export interface ProviderTelemetry {
  provider: string;
  attempts_count: number;
  success_count: number;
  failure_count: number;
  timeout_count: number;
  rate_limit_count: number;
  latency: LatencyStats;
  first_token_latency: LatencyStats;
  tokens_input: number | null;
  tokens_output: number | null;
  cost_estimate_usd: number | null;
}

export interface ModelTelemetrySummary {
  logical_requests: {
    total: number;
    success: number;
    failed: number;
    security_denied: number;
    cancelled: number;
    fallback_count: number;
    fallback_rate: number;
    local_count: number;
    remote_count: number;
  };
  providers: Record<string, ProviderTelemetry>;
  governance: {
    total_tokens_input: number | null;
    total_tokens_output: number | null;
    total_cost_usd: number | null;
    pricing_configured: boolean;
  };
  buffer_status: {
    events_retained: number;
    max_events: number;
    requests_retained: number;
    max_requests: number;
    active_requests: number;
    active_attempts: number;
  };
}

export async function fetchTelemetrySummary(): Promise<ModelTelemetrySummary> {
  const res = await fetch(`${RYVEN_API_BASE_URL}/api/v1/models/telemetry`);
  if (!res.ok) {
    throw new Error(`Failed to fetch telemetry summary: ${res.status}`);
  }
  return res.json();
}

