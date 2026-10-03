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

