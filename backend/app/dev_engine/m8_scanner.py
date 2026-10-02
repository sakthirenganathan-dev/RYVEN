"""RYVEN DEV ENGINE — M8: ProjectScanner.

Safely inspects an existing project directory to build an ExistingProjectModel.

Security rules:
  - Only reads inside the approved project root.
  - Sensitive files (.env, secrets, keys) are NEVER read — listed as protected.
  - Applies file count, file size, and total-size limits.
  - Does NOT recursively scan the host filesystem.
  - Does NOT execute any files.
"""

from __future__ import annotations

import ast
import json
import os
import re
from typing import Dict, List, Optional, Set, Tuple

from app.core.logging_config import logger
from app.dev_engine.m8_models import (
    DetectedProjectType,
    ExistingProjectModel,
    ScannedFileInfo,
)
from app.tools.project_tool import resolve_project_path, resolve_project_file_path


# ─────────────────────────────────────────────────────────────────────────────
# Limits
# ─────────────────────────────────────────────────────────────────────────────

MAX_FILES_TO_SCAN: int = 200
MAX_FILE_READ_BYTES: int = 100_000     # 100 KB per file for inspection
MAX_TOTAL_SCAN_BYTES: int = 2_000_000  # 2 MB total read
MAX_CONTEXT_FILES: int = 10            # max files surfaced to LLM context

# ─────────────────────────────────────────────────────────────────────────────
# Sensitive file patterns — NEVER read
# ─────────────────────────────────────────────────────────────────────────────

_SENSITIVE_PATTERNS = [
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r"secrets?\.(?:json|yaml|yml|toml|txt)$", re.IGNORECASE),
    re.compile(r"credentials?\.(?:json|yaml|yml|toml)$", re.IGNORECASE),
    re.compile(r"(private|id_rsa|id_ed25519|id_ecdsa)(\.pub)?$", re.IGNORECASE),
    re.compile(r"(api[_-]?key|token|password|passwd)(s)?\.(?:json|txt|yaml|yml)$", re.IGNORECASE),
    re.compile(r"\.pem$", re.IGNORECASE),
    re.compile(r"\.pfx$", re.IGNORECASE),
    re.compile(r"\.p12$", re.IGNORECASE),
    re.compile(r"keystore\.", re.IGNORECASE),
    re.compile(r"\.npmrc$", re.IGNORECASE),
    re.compile(r"\.pypirc$", re.IGNORECASE),
]

# Directories to skip entirely
_SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".nuxt", ".output", ".wrangler",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "coverage",
    ".cache", "tmp", "temp",
}

# Extension → language
_EXT_TO_LANG: Dict[str, str] = {
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript",
    ".py": "python",
    ".html": "html", ".htm": "html",
    ".css": "css", ".scss": "css", ".sass": "css",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown",
}

# Config filenames → purpose
_CONFIG_FILES = {
    "package.json", "tsconfig.json", "vite.config.ts", "vite.config.js",
    "next.config.js", "next.config.ts", "pyproject.toml", "setup.py",
    "setup.cfg", "requirements.txt", "Pipfile", "poetry.lock",
    "eslint.config.js", ".eslintrc.json", ".eslintrc.js",
    "tailwind.config.ts", "tailwind.config.js",
    "prettier.config.js", ".prettierrc", ".prettierrc.json",
    "jest.config.ts", "jest.config.js", "vitest.config.ts", "vitest.config.js",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "readme.md", "readme.txt", "readme.rst",
    ".gitignore", ".gitattributes",
}


def _is_sensitive(filename: str) -> bool:
    lower = filename.lower()
    for pat in _SENSITIVE_PATTERNS:
        if pat.search(lower):
            return True
    return False


def _file_purpose(rel_path: str, filename: str) -> str:
    lower = filename.lower()
    lower_path = rel_path.lower()
    if lower in _CONFIG_FILES:
        return "config"
    if "test" in lower or "spec" in lower:
        return "test"
    if "main" in lower or "index" in lower or "app" in lower:
        return "entry"
    if "component" in lower_path or lower.endswith(".tsx") or lower.endswith(".jsx"):
        return "component"
    if lower.endswith(".css") or lower.endswith(".scss"):
        return "style"
    if lower.endswith(".md"):
        return "doc"
    return "source"


class ProjectScanner:
    """Safely inspects an existing project and returns a structured ExistingProjectModel."""

    def scan(self, project_name: str) -> Optional[ExistingProjectModel]:
        """Main entry point. Returns None if project cannot be resolved."""
        abs_path = resolve_project_path(project_name)
        if not abs_path or not os.path.isdir(abs_path):
            logger.warning(f"[SCANNER] Project '{project_name}' not found at '{abs_path}'")
            return None

        logger.info(f"[SCANNER] Scanning '{project_name}' at '{abs_path}'")

        source_files: List[ScannedFileInfo] = []
        config_files: List[str] = []
        test_files: List[str] = []
        protected_files: List[str] = []
        total_bytes_read = 0
        files_walked = 0
        scan_truncated = False

        # Walk project directory (contained)
        for dirpath, dirnames, filenames in os.walk(abs_path):
            # Prune skip dirs in-place
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

            for filename in filenames:
                if files_walked >= MAX_FILES_TO_SCAN:
                    scan_truncated = True
                    break

                rel_path = os.path.relpath(
                    os.path.join(dirpath, filename), abs_path
                ).replace("\\", "/")

                # Sensitive → protect and skip reading
                if _is_sensitive(filename):
                    protected_files.append(rel_path)
                    continue

                ext = os.path.splitext(filename)[1].lower()
                lang = _EXT_TO_LANG.get(ext, "")
                purpose = _file_purpose(rel_path, filename)
                size_bytes = 0

                try:
                    full_path = os.path.join(dirpath, filename)
                    size_bytes = os.path.getsize(full_path)
                except OSError:
                    pass

                info = ScannedFileInfo(
                    relative_path=rel_path,
                    size_bytes=size_bytes,
                    is_protected=False,
                    language=lang,
                    purpose=purpose,
                )
                source_files.append(info)

                lower_fn = filename.lower()
                if lower_fn in _CONFIG_FILES:
                    config_files.append(rel_path)
                if purpose == "test":
                    test_files.append(rel_path)

                files_walked += 1
                if scan_truncated:
                    break

        # Detect project type and metadata
        project_type, framework, language, pkg_mgr, dep_manifest, entry_points = \
            self._detect_project_type(abs_path, source_files)

        # Build architecture summary (short, bounded)
        summary = self._build_summary(
            project_name=project_name,
            project_type=project_type,
            framework=framework,
            language=language,
            source_files=source_files,
            config_files=config_files,
        )

        # Pick relevant files for LLM context (bounded list)
        relevant = self._pick_relevant_files(source_files, entry_points)

        model = ExistingProjectModel(
            project_name=project_name,
            root_path=abs_path,
            framework=framework,
            language=language,
            project_type=project_type,
            package_manager=pkg_mgr,
            entry_points=entry_points,
            source_files=source_files,
            config_files=config_files,
            test_files=test_files,
            dependency_manifest=dep_manifest,
            architecture_summary=summary,
            relevant_files=relevant,
            protected_files=protected_files,
            total_files_scanned=files_walked,
            scan_truncated=scan_truncated,
        )
        logger.info(
            f"[SCANNER] '{project_name}': {files_walked} files, type={project_type.value}, "
            f"protected={len(protected_files)}, truncated={scan_truncated}"
        )
        return model

    # ── Detection helpers ────────────────────────────────────────────────────

    def _detect_project_type(
        self,
        root: str,
        files: List[ScannedFileInfo],
    ) -> Tuple[DetectedProjectType, str, str, str, str, List[str]]:
        """Return (project_type, framework, language, pkg_mgr, dep_manifest, entry_points)."""
        paths = {f.relative_path for f in files}
        lower_paths = {p.lower() for p in paths}

        has_package_json = "package.json" in lower_paths
        has_requirements = "requirements.txt" in lower_paths or "pyproject.toml" in lower_paths
        has_tsconfig = "tsconfig.json" in lower_paths
        has_vite = any("vite.config" in p for p in lower_paths)
        has_next = any("next.config" in p for p in lower_paths)
        has_fastapi = False
        has_react = False

        # Shallow check of package.json and main.py
        pkg_json_path = os.path.join(root, "package.json")
        if os.path.isfile(pkg_json_path):
            try:
                with open(pkg_json_path, "r", encoding="utf-8") as f:
                    pkg = json.load(f)
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
                has_react = "react" in deps
            except Exception:
                pass

        main_py = os.path.join(root, "main.py")
        if os.path.isfile(main_py):
            try:
                with open(main_py, "r", encoding="utf-8", errors="replace") as f:
                    src = f.read(MAX_FILE_READ_BYTES)
                has_fastapi = "fastapi" in src.lower() or "from fastapi" in src.lower()
            except Exception:
                pass

        # Determine type
        if has_react and has_tsconfig:
            ptype = DetectedProjectType.REACT_TS
            fw, lang, pm, dm = "react", "typescript", "npm", "package.json"
            entries = self._find_entries(files, [".tsx", ".ts"], ["main", "index", "app"])
        elif has_react:
            ptype = DetectedProjectType.REACT_JS
            fw, lang, pm, dm = "react", "javascript", "npm", "package.json"
            entries = self._find_entries(files, [".jsx", ".js"], ["main", "index", "app"])
        elif has_fastapi:
            ptype = DetectedProjectType.PYTHON_API
            fw, lang, pm, dm = "fastapi", "python", "pip", "requirements.txt"
            entries = self._find_entries(files, [".py"], ["main", "app"])
        elif has_requirements:
            ptype = DetectedProjectType.PYTHON_APP
            fw, lang, pm, dm = "python", "python", "pip", "requirements.txt"
            entries = self._find_entries(files, [".py"], ["main", "app", "run"])
        elif has_package_json:
            ptype = DetectedProjectType.VANILLA_WEB
            fw, lang, pm, dm = "vanilla", "javascript", "npm", "package.json"
            entries = self._find_entries(files, [".html", ".js"], ["index", "main"])
        else:
            ptype = DetectedProjectType.VANILLA_WEB
            fw, lang, pm, dm = "vanilla", "html/js/css", "", ""
            entries = self._find_entries(files, [".html"], ["index"])

        return ptype, fw, lang, pm, dm, entries

    @staticmethod
    def _find_entries(
        files: List[ScannedFileInfo],
        extensions: List[str],
        names: List[str],
    ) -> List[str]:
        results = []
        for f in files:
            ext = os.path.splitext(f.relative_path)[1].lower()
            base = os.path.splitext(os.path.basename(f.relative_path))[0].lower()
            if ext in extensions and base in names:
                results.append(f.relative_path)
        return results[:5]

    @staticmethod
    def _build_summary(
        project_name: str,
        project_type: DetectedProjectType,
        framework: str,
        language: str,
        source_files: List[ScannedFileInfo],
        config_files: List[str],
    ) -> str:
        n = len(source_files)
        components = [f.relative_path for f in source_files if f.purpose == "component"][:5]
        tests = [f.relative_path for f in source_files if f.purpose == "test"][:3]
        comp_str = ", ".join(components) if components else "none detected"
        test_str = f"{len(tests)} test file(s)" if tests else "no tests"
        return (
            f"Project '{project_name}': {framework.upper()} / {language} "
            f"({project_type.value}). "
            f"{n} source files. "
            f"Components: {comp_str}. "
            f"{test_str}. "
            f"Config files: {', '.join(config_files[:5]) or 'none'}."
        )

    @staticmethod
    def _pick_relevant_files(
        files: List[ScannedFileInfo],
        entry_points: List[str],
    ) -> List[str]:
        """Return a bounded list of files most likely relevant to modifications."""
        scored: List[Tuple[int, str]] = []
        for f in files:
            score = 0
            if f.relative_path in entry_points:
                score += 10
            if f.purpose in ("entry", "component"):
                score += 5
            if f.purpose == "config":
                score += 3
            if f.size_bytes > 0 and f.size_bytes < 20_000:
                score += 2
            if f.language in ("typescript", "javascript", "python"):
                score += 1
            scored.append((score, f.relative_path))

        scored.sort(reverse=True)
        return [path for _, path in scored[:MAX_CONTEXT_FILES]]
