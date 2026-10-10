"""Configuration management for RYVEN backend."""

import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Load .env file from project or backend directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings(BaseModel):
    """Application settings loaded from environment variables."""

    app_name: str = "RYVEN Backend"
    version: str = "2.0.0"
    environment: str = Field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))
    debug: bool = Field(default_factory=lambda: os.getenv("DEBUG", "false").lower() in ("true", "1", "yes"))
    host: str = Field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    port: int = Field(default_factory=lambda: int(os.getenv("PORT", "8000")))

    # CORS
    allowed_origins: List[str] = Field(
        default_factory=lambda: [
            origin.strip()
            for origin in os.getenv(
                "ALLOWED_ORIGINS",
                "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000,http://localhost:8080,http://127.0.0.1:8080",
            ).split(",")
            if origin.strip()
        ]
    )

    # AI Provider (Ollama)
    ollama_base_url: str = Field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    )
    ollama_model: str = Field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
    ollama_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120.0"))
    )

    # Grok API Provider (M15.1 - Optional / Opt-in)
    grok_api_key: str | None = Field(default_factory=lambda: os.getenv("GROK_API_KEY"))
    grok_base_url: str = Field(
        default_factory=lambda: os.getenv("GROK_BASE_URL", "https://api.x.ai/v1").rstrip("/")
    )
    grok_model: str = Field(default_factory=lambda: os.getenv("GROK_MODEL", "grok-2-latest"))
    grok_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("GROK_TIMEOUT_SECONDS", "60.0"))
    )

    # Hugging Face Provider (M15.1 / M17.10 Phase 7 - Optional / Opt-in)
    hf_api_key: str | None = Field(
        default_factory=lambda: (
            os.getenv("HF_API_KEY")
            or os.getenv("HUGGINGFACE_API_KEY")
            or os.getenv("HF_TOKEN")
        )
    )
    hf_base_url: str = Field(
        default_factory=lambda: os.getenv("HF_BASE_URL", "https://api-inference.huggingface.co/v1").rstrip("/")
    )
    hf_model: str = Field(
        default_factory=lambda: os.getenv("HF_MODEL", "Qwen/Qwen2.5-Coder-32B-Instruct")
    )
    hf_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("HF_TIMEOUT_SECONDS", "60.0"))
    )

    # Remote AI Inference Global Gate (Disabled by default)
    allow_remote_ai_inference: bool = Field(
        default_factory=lambda: os.getenv("ALLOW_REMOTE_AI_INFERENCE", "false").lower() in ("true", "1", "yes")
    )

    # Benchmark Administration & Hardening (M17.11.1)
    benchmark_admin_token: str | None = Field(
        default_factory=lambda: (
            os.getenv("RYVEN_BENCHMARK_TOKEN") or os.getenv("RYVEN_ADMIN_TOKEN")
        )
    )

    # Conversation Context
    max_history_messages: int = Field(
        default_factory=lambda: int(os.getenv("MAX_HISTORY_MESSAGES", "20"))
    )

    # Phase 3 Tool Settings
    max_search_results: int = Field(
        default_factory=lambda: int(os.getenv("MAX_SEARCH_RESULTS", "20"))
    )
    clipboard_max_length: int = Field(
        default_factory=lambda: int(os.getenv("CLIPBOARD_MAX_LENGTH", "10000"))
    )
    tool_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("TOOL_TIMEOUT_SECONDS", "10.0"))
    )

    # Logging
    log_level: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO").upper())


settings = Settings()
