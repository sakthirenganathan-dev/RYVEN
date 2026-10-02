"""Safe project and framework detector inspecting metadata without arbitrary code execution."""

from __future__ import annotations

import json
import os
from typing import List, Optional
from app.deployment.deployment_models import DeploymentFramework, ProjectDetectionResult
from app.deployment.deployment_security import extract_required_env_vars
from app.deployment.deployment_validator import validate_project_for_deployment


class DeploymentDetector:
    """Detects project framework, build configuration, package managers, and required env vars."""

    def detect(self, project_name: str) -> ProjectDetectionResult:
        """Inspect a project directory and deduce framework and build characteristics."""
        is_valid, msg, proj_dir = validate_project_for_deployment(project_name)
        if not is_valid or not proj_dir:
            return ProjectDetectionResult(
                project_name=project_name,
                project_path="",
                framework=DeploymentFramework.UNKNOWN,
                project_type="invalid",
                is_deployable=False,
                notes=[msg],
            )

        notes: List[str] = []
        framework = DeploymentFramework.UNKNOWN
        project_type = "unknown"
        package_manager = "unknown"
        build_command = ""
        test_command = ""
        output_dir = ""
        is_deployable = False

        # 1. Inspect package.json (Node / React / Next.js / Vite)
        package_json_path = os.path.join(proj_dir, "package.json")
        has_package_json = os.path.isfile(package_json_path)

        if has_package_json:
            try:
                with open(package_json_path, "r", encoding="utf-8", errors="ignore") as f:
                    pkg_data = json.load(f)
            except Exception:
                pkg_data = {}
                notes.append("Found package.json but could not parse JSON content.")

            deps = pkg_data.get("dependencies", {})
            dev_deps = pkg_data.get("devDependencies", {})
            scripts = pkg_data.get("scripts", {})
            all_deps = {**deps, **dev_deps}

            # Check lockfiles to determine package manager
            if os.path.isfile(os.path.join(proj_dir, "pnpm-lock.yaml")):
                package_manager = "pnpm"
            elif os.path.isfile(os.path.join(proj_dir, "yarn.lock")):
                package_manager = "yarn"
            elif os.path.isfile(os.path.join(proj_dir, "package-lock.json")):
                package_manager = "npm"
            else:
                package_manager = "npm"

            # Check for Next.js
            if "next" in all_deps or any(os.path.isfile(os.path.join(proj_dir, f)) for f in ["next.config.js", "next.config.mjs", "next.config.ts"]):
                framework = DeploymentFramework.NEXTJS
                project_type = "frontend"
                build_command = f"{package_manager} run build" if "build" in scripts else "next build"
                test_command = f"{package_manager} test" if "test" in scripts else ""
                output_dir = ".next"
                is_deployable = True
                notes.append("Detected Next.js application.")

            # Check for Vite / React Vite
            elif "vite" in all_deps or any(os.path.isfile(os.path.join(proj_dir, f)) for f in ["vite.config.js", "vite.config.ts", "vite.config.mjs"]):
                framework = DeploymentFramework.REACT_VITE
                project_type = "frontend"
                build_command = f"{package_manager} run build" if "build" in scripts else "npm run build"
                test_command = f"{package_manager} test" if "test" in scripts else ""
                output_dir = "dist"
                is_deployable = True
                notes.append("Detected React + Vite application.")

            else:
                framework = DeploymentFramework.NODE
                project_type = "node"
                build_command = f"{package_manager} run build" if "build" in scripts else ""
                test_command = f"{package_manager} test" if "test" in scripts else ""
                output_dir = "build" if os.path.isdir(os.path.join(proj_dir, "build")) else "dist"
                is_deployable = True
                notes.append("Detected standard Node.js project.")

        # 2. Inspect Python projects (FastAPI / generic Python)
        reqs_path = os.path.join(proj_dir, "requirements.txt")
        pyproject_path = os.path.join(proj_dir, "pyproject.toml")
        has_python = os.path.isfile(reqs_path) or os.path.isfile(pyproject_path) or os.path.isfile(os.path.join(proj_dir, "main.py"))

        if has_python and framework == DeploymentFramework.UNKNOWN:
            package_manager = "pip"
            is_fastapi = False
            # Check requirements.txt or pyproject.toml
            if os.path.isfile(reqs_path):
                try:
                    with open(reqs_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                        if "fastapi" in content or "uvicorn" in content:
                            is_fastapi = True
                except Exception:
                    pass

            if not is_fastapi and os.path.isfile(pyproject_path):
                try:
                    with open(pyproject_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                        if "fastapi" in content or "uvicorn" in content:
                            is_fastapi = True
                except Exception:
                    pass

            # Inspect main.py or app.py
            if not is_fastapi:
                for entry_file in ["main.py", "app.py", "server.py"]:
                    ef = os.path.join(proj_dir, entry_file)
                    if os.path.isfile(ef):
                        try:
                            with open(ef, "r", encoding="utf-8", errors="ignore") as f:
                                code = f.read(4000).lower()
                                if "fastapi" in code:
                                    is_fastapi = True
                                    break
                        except Exception:
                            pass

            if is_fastapi:
                framework = DeploymentFramework.FASTAPI
                project_type = "backend"
                build_command = "pip install -r requirements.txt"
                test_command = "pytest"
                output_dir = ""
                is_deployable = True
                notes.append("Detected FastAPI backend application.")
            else:
                framework = DeploymentFramework.PYTHON
                project_type = "python"
                build_command = "pip install -r requirements.txt" if os.path.isfile(reqs_path) else ""
                test_command = "pytest"
                output_dir = ""
                is_deployable = True
                notes.append("Detected Python project.")

        # 3. Detect static HTML / web
        if framework == DeploymentFramework.UNKNOWN and os.path.isfile(os.path.join(proj_dir, "index.html")):
            framework = DeploymentFramework.REACT_VITE  # Can be served as static site
            project_type = "static_web"
            package_manager = "none"
            build_command = ""
            test_command = ""
            output_dir = "."
            is_deployable = True
            notes.append("Detected static web project with index.html.")

        # Extract required environment variables from project files
        env_vars = extract_required_env_vars(proj_dir)
        if env_vars:
            notes.append(f"Detected {len(env_vars)} required environment variable name(s).")

        return ProjectDetectionResult(
            project_name=project_name,
            project_path=proj_dir,
            framework=framework,
            project_type=project_type,
            package_manager=package_manager,
            build_command=build_command,
            test_command=test_command,
            output_dir=output_dir,
            detected_env_vars=env_vars,
            is_deployable=is_deployable,
            notes=notes,
        )
