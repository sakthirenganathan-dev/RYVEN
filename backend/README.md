# RYVEN — Python Backend Foundation

A modular, production-ready Python backend for **RYVEN**. This backend receives user instructions, classifies intent between safe system tools and generative AI, executes registered capabilities, and returns structured responses to the React frontend.

---

## Architecture Overview

```
User Message / Voice Transcript
              ↓
          POST /api/chat
              ↓
          Assistant (Core Orchestrator)
              ↓
        IntentRouter
       /            \
[System Intent]    [General AI Intent]
     ↓                    ↓
Safe Tool Registry     AIProvider (Ollama)
  • TimeTool              • HTTP /api/generate
  • SystemStatusTool      • Clean Offline Fallback
     \                    /
      ↓                  ↓
       Structured ChatResponse
```

### Directory Structure

```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI application, CORS, lifespan
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── assistant.py         # Central Assistant orchestrator
│   │   ├── router.py            # Regex/pattern Intent Router
│   │   ├── config.py            # Pydantic environment configuration
│   │   └── logging_config.py    # Structured application logging
│   │
│   ├── ai/
│   │   ├── __init__.py
│   │   ├── provider.py          # AIProvider ABC & normalized AIResponse
│   │   └── ollama.py            # HTTP Ollama client & error handling
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── base.py              # BaseTool abstract base class
│   │   ├── registry.py          # ToolRegistry with safe registration
│   │   ├── time_tool.py         # Local time and date tool
│   │   └── system_tool.py       # CPU, RAM, disk, battery via psutil
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── routes.py            # Endpoints: /api/chat, /api/health, /api/system/status
│   │
│   └── schemas/
│       ├── __init__.py
│       └── messages.py          # Pydantic request and response schemas
│
├── tests/
│   ├── __init__.py
│   ├── test_tools.py            # ToolRegistry, TimeTool, SystemStatusTool tests
│   ├── test_router.py           # IntentRouter classification tests
│   ├── test_assistant.py        # Assistant workflow & Ollama offline handling
│   └── test_api.py              # FastAPI endpoint tests
│
├── data/                        # Storage for local data / future memory
├── requirements.txt             # Python dependencies
├── .env.example                 # Example environment variables
├── .env                         # Local environment configuration
└── README.md                    # Documentation
```

---

## Requirements

- Python >= 3.10 (Tested on Python 3.14)
- (Optional for AI generation) [Ollama](https://ollama.ai) running locally or over network

---

## Setup & Installation

### 1. Create Virtual Environment

```bash
cd backend
python -m venv .venv
```

Activate the environment:
- **Windows (PowerShell)**:
  ```powershell
  .venv\Scripts\Activate.ps1
  ```
- **Linux / macOS**:
  ```bash
  source .venv/bin/activate
  ```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

Copy `.env.example` to `.env` (already done by default):

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `HOST` | `127.0.0.1` | Bind host address |
| `PORT` | `8000` | Bind port number |
| `DEBUG` | `false` | Enable reload and debug logs |
| `ENVIRONMENT` | `development` | Deployment environment |
| `ALLOWED_ORIGINS` | `http://localhost:5173,...` | Allowed CORS origins for React frontend |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama HTTP endpoint |
| `OLLAMA_MODEL` | `llama3` | Ollama model name |
| `OLLAMA_TIMEOUT_SECONDS` | `30.0` | HTTP request timeout |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## Starting the Backend Server

```bash
# Using Python inside venv
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Interactive API documentation will be available at:
- **Swagger UI**: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc**: [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

---

## Running Automated Tests

Run the test suite with `pytest`:

```bash
.venv\Scripts\python.exe -m pytest -v
```

All tests mock the AI engine, so tests pass reliably with or without a live Ollama daemon.

---

## API Endpoints

### 1. `POST /api/chat`
Processes an incoming instruction. Automatically routes to safe tools or the AI provider.

**Request**:
```json
{
  "message": "What time is it?"
}
```

**Tool Response Example**:
```json
{
  "success": true,
  "type": "tool",
  "message": "It is 9:55 PM on Wednesday, September 30, 2026.",
  "tool": "time",
  "metadata": {
    "time": "9:55 PM",
    "date": "Wednesday, September 30, 2026",
    "day_of_week": "Wednesday",
    "iso": "2026-09-30T21:55:00.123456"
  }
}
```

**AI Response Example**:
```json
{
  "success": true,
  "type": "ai",
  "message": "Quantum computing harnesses the phenomena of superposition and entanglement...",
  "tool": null,
  "metadata": {
    "model": "llama3",
    "provider": "ollama",
    "total_duration_ns": 1284500000
  }
}
```

**Ollama Offline Response (Zero Crash Guarantee)**:
```json
{
  "success": false,
  "type": "error",
  "message": "AI engine is currently offline: Ollama service at http://localhost:11434 is unreachable. Ensure Ollama is running and accessible.",
  "tool": null,
  "metadata": {
    "error_type": "AI_PROVIDER_UNAVAILABLE",
    "provider": "ollama"
  }
}
```

### 2. `GET /api/health`
Returns backend health status, registered tools, and AI provider name.

**Response**:
```json
{
  "status": "ok",
  "app": "RYVEN Backend",
  "version": "2.0.0",
  "environment": "development",
  "registered_tools": ["time", "system_status"],
  "ai_provider": "ollama"
}
```

### 3. `GET /api/system/status`
Returns live system hardware telemetry.

**Response**:
```json
{
  "success": true,
  "data": {
    "cpu_percent": 14.2,
    "ram_total_gb": 15.82,
    "ram_used_gb": 7.41,
    "ram_percent": 46.8,
    "disk": {
      "total_gb": 476.12,
      "used_gb": 241.18,
      "free_gb": 234.94,
      "percent": 50.7
    },
    "battery": {
      "available": true,
      "percent": 88.0,
      "power_plugged": true,
      "time_left_seconds": null
    }
  },
  "message": "System Diagnostics: CPU: 14.2%, RAM: 7.41/15.82 GB (46.8%), Disk: 241.18/476.12 GB used (50.7%), Battery: 88.0% (Plugged in)."
}
```

---

## Security Principles

- **No Arbitrary Shell Execution**: The backend strictly rejects direct shell command execution (`sh`, `bash`, `cmd`, `powershell`).
- **Explicit Registration**: Only classes extending `BaseTool` registered inside `ToolRegistry` can perform actions.
- **Safety Flags**: Dangerous tools are designated with `requires_confirmation = True` to support confirmation gates in future phases.

---

## How to Install and Run Ollama

1. Download Ollama from [https://ollama.com](https://ollama.com).
2. Install and launch the Ollama daemon:
   ```bash
   ollama serve
   ```
3. Pull your desired model (e.g., `llama3`, `mistral`, or `phi3`):
   ```bash
   ollama pull llama3
   ```
4. Verify Ollama responds:
   ```bash
   curl http://localhost:11434/api/version
   ```

---

## Frontend Integration Note

The React frontend (`src/services/jarvis.ts`) is designed to communicate with this backend:
- Chat & voice commands map to `POST /api/chat`.
- System telemetry HUD maps to `GET /api/system/status`.
- Connection bridge status checks `GET /api/health`.

---

## Future Roadmap

1. **Phase 2**: Real-time WebSocket streaming for token-by-token responses and live audio stream inputs.
2. **Phase 3**: Vector memory store (local Chroma/SQLite-VSS) for personal long-term memory lattice.
3. **Phase 4**: Advanced safe automation tools (app launching with verified whitelist, developer workspace watcher).
