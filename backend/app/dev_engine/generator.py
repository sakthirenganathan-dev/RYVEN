"""Controlled Code Generation Engine producing deterministic, validated in-memory code files."""

import json
from typing import Dict, List, Optional
from app.core.logging_config import logger
from app.dev_engine.constants import (
    MAX_FILE_SIZE_BYTES,
    MAX_TOTAL_PROJECT_SIZE_BYTES,
)
from app.dev_engine.models import (
    FilePlan,
    GeneratedFile,
    ProjectSpecification,
    ProjectType,
)


class CodeGenerationEngine:
    """Synthesizes high-quality, validated in-memory source files from project specifications."""

    def generate_files(
        self,
        spec: ProjectSpecification,
        file_plan: Optional[FilePlan] = None,
    ) -> List[GeneratedFile]:
        """Generate in-memory code representations strictly conforming to limits and templates."""
        p_name = spec.project_name
        p_type = spec.project_type
        features_str = ", ".join(spec.features[:3])

        generated_files: List[GeneratedFile] = []
        raw_templates: Dict[str, str] = {}

        if p_type == ProjectType.REACT_TS.value:
            # 1. package.json
            raw_templates["package.json"] = json.dumps({
                "name": p_name.lower(),
                "private": True,
                "version": "0.1.0",
                "type": "module",
                "scripts": {
                    "dev": "vite",
                    "build": "tsc && vite build",
                    "preview": "vite preview"
                },
                "dependencies": {
                    "react": "^18.3.1",
                    "react-dom": "^18.3.1",
                    "lucide-react": "^0.400.0"
                },
                "devDependencies": {
                    "@types/react": "^18.3.3",
                    "@types/react-dom": "^18.3.0",
                    "@vitejs/plugin-react": "^4.3.1",
                    "typescript": "^5.4.5",
                    "vite": "^5.3.4"
                }
            }, indent=2) + "\n"

            # 2. tsconfig.json
            raw_templates["tsconfig.json"] = json.dumps({
                "compilerOptions": {
                    "target": "ES2020",
                    "useDefineForClassFields": True,
                    "lib": ["ES2020", "DOM", "DOM.Iterable"],
                    "module": "ESNext",
                    "skipLibCheck": True,
                    "moduleResolution": "bundler",
                    "allowImportingTsExtensions": True,
                    "resolveJsonModule": True,
                    "isolatedModules": True,
                    "noEmit": True,
                    "jsx": "react-jsx",
                    "strict": True,
                    "noUnusedLocals": True,
                    "noUnusedParameters": True,
                    "noFallthroughCasesInSwitch": True
                },
                "include": ["src"]
            }, indent=2) + "\n"

            # 3. index.html
            raw_templates["index.html"] = (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "  <head>\n"
                '    <meta charset="UTF-8" />\n'
                '    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
                f"    <title>{p_name} — RYVEN Engine</title>\n"
                "  </head>\n"
                "  <body>\n"
                '    <div id="root"></div>\n'
                '    <script type="module" src="/src/main.tsx"></script>\n'
                "  </body>\n"
                "</html>\n"
            )

            # 4. src/main.tsx
            raw_templates["src/main.tsx"] = (
                "import React from 'react';\n"
                "import ReactDOM from 'react-dom/client';\n"
                "import App from './App';\n"
                "import './index.css';\n\n"
                "ReactDOM.createRoot(document.getElementById('root')!).render(\n"
                "  <React.StrictMode>\n"
                "    <App />\n"
                "  </React.StrictMode>,\n"
                ");\n"
            )

            # 5. src/components/Header.tsx
            raw_templates["src/components/Header.tsx"] = (
                "import React from 'react';\n\n"
                "interface HeaderProps {\n"
                "  title: string;\n"
                "  status: string;\n"
                "}\n\n"
                "export const Header: React.FC<HeaderProps> = ({ title, status }) => {\n"
                "  return (\n"
                "    <header className=\"app-header\">\n"
                "      <div className=\"logo-badge\">RYVEN DEV</div>\n"
                "      <h1>{title}</h1>\n"
                "      <span className=\"status-indicator\">● {status}</span>\n"
                "    </header>\n"
                "  );\n"
                "};\n"
            )

            # 6. src/App.tsx
            raw_templates["src/App.tsx"] = (
                "import React, { useState } from 'react';\n"
                "import { Header } from './components/Header';\n\n"
                f"export default function App() {{\n"
                "  const [count, setCount] = useState<number>(0);\n\n"
                "  return (\n"
                "    <div className=\"container\">\n"
                f"      <Header title=\"{p_name}\" status=\"SYSTEM NOMINAL\" />\n"
                "      <main className=\"card\">\n"
                f"        <h2>Engineered Project: {p_name}</h2>\n"
                f"        <p className=\"subtitle\">Features: {features_str}</p>\n"
                "        <div className=\"action-zone\">\n"
                "          <button onClick={() => setCount(c => c + 1)}>\n"
                "            Increment Metric [{count}]\n"
                "          </button>\n"
                "        </div>\n"
                "      </main>\n"
                "    </div>\n"
                "  );\n"
                "}\n"
            )

            # 7. src/index.css
            raw_templates["src/index.css"] = (
                ":root {\n"
                "  --bg: #07090e;\n"
                "  --card: #0f141e;\n"
                "  --border: #1a2333;\n"
                "  --accent: #00f0ff;\n"
                "  --text: #e6edf3;\n"
                "}\n"
                "body {\n"
                "  margin: 0;\n"
                "  background: var(--bg);\n"
                "  color: var(--text);\n"
                "  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;\n"
                "}\n"
                ".container { max-width: 900px; margin: 2rem auto; padding: 0 1rem; }\n"
                ".app-header { display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); padding-bottom: 1rem; }\n"
                ".logo-badge { background: var(--accent); color: #000; font-weight: bold; padding: 0.25rem 0.5rem; border-radius: 4px; font-size: 0.75rem; }\n"
                ".status-indicator { color: #00ff88; font-size: 0.85rem; font-family: monospace; }\n"
                ".card { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 2rem; margin-top: 2rem; }\n"
                "button { background: var(--accent); color: #000; border: none; padding: 0.75rem 1.5rem; border-radius: 6px; font-weight: bold; cursor: pointer; }\n"
                "button:hover { opacity: 0.9; transform: translateY(-1px); }\n"
            )

            # 8. README.md
            raw_templates["README.md"] = (
                f"# {p_name}\n\n"
                f"{spec.description}\n\n"
                "## Architectural Specification\n"
                f"- **Framework**: React 18 + Vite + TypeScript\n"
                f"- **Styling**: {spec.styling}\n"
                f"- **Features**: {', '.join(spec.features)}\n\n"
                "Generated safely by RYVEN DEV ENGINE v1.\n"
            )

        elif p_type in (ProjectType.PYTHON_APP.value, ProjectType.PYTHON_API.value):
            if p_type == ProjectType.PYTHON_API.value:
                raw_templates["main.py"] = (
                    f'"""FastAPI service entrypoint for {p_name}."""\n\n'
                    "from fastapi import FastAPI\n\n"
                    f'app = FastAPI(title="{p_name}", version="0.1.0")\n\n'
                    '@app.get("/health")\n'
                    "def health_check():\n"
                    f'    return {{"status": "healthy", "service": "{p_name}"}}\n'
                )
                raw_templates["requirements.txt"] = "fastapi>=0.110.0\nuvicorn>=0.28.0\npydantic>=2.6.0\n"
                raw_templates["app/__init__.py"] = '"""Package init."""\n'
                raw_templates["app/routes.py"] = (
                    '"""API endpoints."""\n'
                    "from fastapi import APIRouter\n\n"
                    'router = APIRouter()\n\n'
                    '@router.get("/metrics")\n'
                    'def get_metrics():\n'
                    '    return {"status": "nominal"}\n'
                )
            else:
                raw_templates["main.py"] = (
                    f'"""Main entry point for {p_name}."""\n\n'
                    "def main():\n"
                    f'    print("Starting {p_name}...")\n'
                    f'    print("Features initialized: {features_str}")\n\n'
                    'if __name__ == "__main__":\n'
                    "    main()\n"
                )
                raw_templates["requirements.txt"] = "pytest>=8.0.0\n"

            raw_templates["README.md"] = (
                f"# {p_name}\n\n"
                f"{spec.description}\n\n"
                "Generated safely by RYVEN DEV ENGINE v1.\n"
            )

        else:
            # Vanilla Web
            raw_templates["index.html"] = (
                "<!DOCTYPE html>\n"
                '<html lang="en">\n'
                "<head>\n"
                '  <meta charset="UTF-8">\n'
                '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
                f"  <title>{p_name}</title>\n"
                '  <link rel="stylesheet" href="styles.css">\n'
                "</head>\n"
                "<body>\n"
                '  <div class="hud-container">\n'
                f"    <h1>{p_name}</h1>\n"
                f"    <p class=\"tagline\">{features_str}</p>\n"
                '    <button id="actionBtn">Engage</button>\n'
                '    <div id="output"></div>\n'
                "  </div>\n"
                '  <script src="app.js"></script>\n'
                "</body>\n"
                "</html>\n"
            )
            raw_templates["styles.css"] = (
                "body { background: #07090e; color: #00f0ff; font-family: monospace; padding: 2rem; }\n"
                ".hud-container { border: 1px solid #1a2333; padding: 2rem; border-radius: 8px; max-width: 600px; margin: auto; }\n"
                "button { background: #00f0ff; color: #000; border: none; padding: 0.5rem 1rem; cursor: pointer; font-weight: bold; }\n"
            )
            raw_templates["app.js"] = (
                f'console.log("{p_name} online.");\n'
                "document.getElementById('actionBtn')?.addEventListener('click', () => {\n"
                "  const out = document.getElementById('output');\n"
                "  if (out) out.innerText = 'Telemetry: Active';\n"
                "});\n"
            )
            raw_templates["README.md"] = f"# {p_name}\n\nVanilla Web application scaffolded by RYVEN.\n"

        # Validate each generated file against security bounds
        total_size = 0
        for rel_path, content in raw_templates.items():
            content_bytes = content.encode("utf-8")
            file_size = len(content_bytes)
            if file_size > MAX_FILE_SIZE_BYTES:
                raise ValueError(f"Generated file '{rel_path}' ({file_size}B) exceeds max limit of {MAX_FILE_SIZE_BYTES}B.")

            total_size += file_size
            if total_size > MAX_TOTAL_PROJECT_SIZE_BYTES:
                raise ValueError(f"Total project generation size ({total_size}B) exceeds max limit of {MAX_TOTAL_PROJECT_SIZE_BYTES}B.")

            ext = rel_path.split(".")[-1].lower() if "." in rel_path else "txt"
            lang_map = {
                "ts": "typescript", "tsx": "typescript", "js": "javascript", "jsx": "javascript",
                "py": "python", "html": "html", "css": "css", "json": "json", "md": "markdown",
            }

            gen_file = GeneratedFile(
                relative_path=rel_path,
                content=content,
                language=lang_map.get(ext, "text"),
                validation_metadata={"project": p_name, "template": p_type},
            )
            generated_files.append(gen_file)

        logger.info(f"CodeGenerationEngine generated {len(generated_files)} files ({total_size} bytes total) for '{p_name}'.")
        return generated_files
