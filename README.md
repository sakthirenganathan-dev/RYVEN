# RYVEN 2.0

> **Personal AI Assistant & Controlled Laptop Intelligence**

RYVEN 2.0 is an autonomous, holographic AI assistant engineered with local LLM reasoning (powered by Ollama & Qwen 2.5:7b) and a secure, permission-guarded tool execution architecture on Windows.

---

## Architecture Overview

```
USER
 ↓
RYVEN FRONTEND (React 19, Vite, TanStack Router/Start, Motion)
 ↓
FASTAPI BACKEND (Python 3.14, Pydantic V2, Uvicorn)
 ↓
ASSISTANT / AI ROUTER
 ↓
INTENT + TOOL ROUTER (Deterministic classification & AI delegation)
 ↓
PERMISSION & SAFETY GUARD LAYER (Allowlist enforcement & injection blocking)
 ↓
REGISTERED TOOLS (Win32 APIs, native process spawning, safe file discovery)
 ↓
WINDOWS LAPTOP ACTION
 ↓
STRUCTURED RESPONSE & REAL-TIME HUD FEEDBACK
```

---

## Key Features

- **Local Neural Engine:** Private, offline-capable reasoning via Ollama (`qwen2.5:7b`).
- **Controlled Laptop Intelligence:**
  - **OpenApplicationTool:** Safe launching of allowlisted applications (VS Code, Chrome, Notepad, Calculator, Terminal).
  - **OpenWebsiteTool:** Validated HTTP/HTTPS browser navigation with protection against unsafe protocols (`file://`, `javascript:`, `data:`).
  - **OpenFolderTool:** Explorer access restricted to approved user folders (Desktop, Documents, Downloads, Workspace).
  - **SearchFilesTool:** Safe local file discovery without exposure of system roots.
  - **OpenFileTool:** Opening files safely with default system handlers.
  - **Clipboard Tools:** Native Win32 text read and write with length constraints.
  - **SystemInfoTool & SystemStatusTool:** Real-time hardware telemetry and non-destructive OS diagnostics.
- **Security & Safety Guard:** Prohibits arbitrary shell commands (`cmd.exe`, `powershell`), destructive operations (`format`, `del`), path traversal (`../`), and prompt injection attempts.
- **Holographic HUD Interface:** Cyberpunk/sci-fi visual interface with real-time tool activity indicators and speech synthesis integration.

---

## Project Structure

```
├── backend/                  # FastAPI Python backend
│   ├── app/
│   │   ├── ai/               # AI Provider abstraction & Ollama client
│   │   ├── api/              # REST endpoints & chat routes
│   │   ├── core/             # Assistant core, Router, Security Guard, Context
│   │   ├── schemas/          # Pydantic request/response schemas
│   │   └── tools/            # Registered tool system
│   ├── tests/                # Automated pytest suite (70/70 passing)
│   └── requirements.txt
│
└── RAVAN/                    # React / Vite holographic frontend
    ├── src/
    │   ├── components/       # HUD, Core AI visualizer, Transcript layers
    │   ├── hooks/            # useJarvis hook (orchestrates state & chat)
    │   └── services/         # API integration bridge & client fallbacks
    └── package.json
```

---

## Getting Started

### Prerequisites

- **Python 3.11+**
- **Node.js 18+** & npm / bun
- **Ollama** installed with `qwen2.5:7b`:
  ```bash
  ollama run qwen2.5:7b
  ```

### 1. Backend Setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### 2. Frontend Setup

```bash
cd RAVAN
npm install
npm run dev
```

Visit `http://localhost:5173` to interact with RYVEN.

---

## Verification & Testing

Run the automated backend test suite:
```bash
cd backend
pytest -v
```

Run frontend typecheck and production build:
```bash
cd RAVAN
npm run lint
npm run build
```

---

## License

MIT License.
