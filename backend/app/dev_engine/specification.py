"""Project Specification Engine converting natural language requirements into structured models."""

import re
from typing import List, Optional
from app.core.logging_config import logger
from app.dev_engine.constants import (
    MAX_FILES_PER_PROJECT,
    MAX_PROJECT_NAME_LENGTH,
    PROJECT_NAME_REGEX,
)
from app.dev_engine.models import ProjectSpecification, ProjectType
from app.tools.project_tool import sanitize_project_name


class ProjectSpecificationEngine:
    """Understands user intent and synthesizes a structured ProjectSpecification."""

    @staticmethod
    def infer_project_type(query: str) -> str:
        """Determine target framework and language from query tokens."""
        lower = query.lower()
        if "react" in lower or "vite" in lower or "frontend" in lower or "dashboard" in lower:
            return ProjectType.REACT_TS.value
        elif "fastapi" in lower or "api" in lower or "backend" in lower:
            if "python" in lower:
                return ProjectType.PYTHON_API.value
            return ProjectType.PYTHON_APP.value
        elif "python" in lower:
            return ProjectType.PYTHON_APP.value
        return ProjectType.VANILLA_WEB.value

    @staticmethod
    def extract_features(query: str) -> List[str]:
        """Extract requested features and design characteristics from the query."""
        lower = query.lower()
        features: List[str] = []

        feature_keywords = {
            "auth": "User Authentication & Session Management",
            "login": "Secure Login & Registration Views",
            "dark": "Dark Futuristic Holographic Theme",
            "futuristic": "Cyberpunk Holographic Interface Tokens",
            "expense": "Expense & Budget Tracking Engine",
            "dashboard": "Analytical Telemetry Dashboard",
            "todo": "Task & State Management",
            "portfolio": "Developer Showcase & Portfolio Grid",
            "api": "RESTful API Endpoints",
            "database": "Local SQLite / JSON Persistence",
        }

        for kw, desc in feature_keywords.items():
            if kw in lower:
                features.append(desc)

        if not features:
            features.append("Standard Modular Project Architecture")

        return features

    def create_specification(
        self,
        project_name: str,
        query: str = "",
        explicit_type: Optional[str] = None,
    ) -> Optional[ProjectSpecification]:
        """Synthesize a complete, validated ProjectSpecification."""
        clean_name = sanitize_project_name(project_name)
        if not clean_name:
            logger.warning(f"ProjectSpecification rejected: invalid project name '{project_name}'")
            return None

        if len(clean_name) > MAX_PROJECT_NAME_LENGTH:
            logger.warning(f"ProjectSpecification rejected: name exceeds {MAX_PROJECT_NAME_LENGTH} chars")
            return None

        raw_type = explicit_type or self.infer_project_type(query)
        # Normalize colloquial framework tokens
        raw_type_lower = raw_type.lower()
        if "react" in raw_type_lower:
            project_type = ProjectType.REACT_TS.value
        elif "fastapi" in raw_type_lower or ("api" in raw_type_lower and "python" in raw_type_lower):
            project_type = ProjectType.PYTHON_API.value
        elif "python" in raw_type_lower:
            project_type = ProjectType.PYTHON_APP.value
        elif "web" in raw_type_lower or "html" in raw_type_lower or "vanilla" in raw_type_lower:
            project_type = ProjectType.VANILLA_WEB.value
        else:
            project_type = raw_type

        features = self.extract_features(query)

        # Set up language, framework, and expected files based on project type
        if project_type == ProjectType.REACT_TS.value:
            language = "typescript"
            framework = "react"
            pages = ["Home", "Dashboard"] if "dashboard" in query.lower() else ["Home"]
            components = ["Header", "Card", "Button"]
            entry_points = ["index.html", "src/main.tsx"]
            dependencies = ["react@^18.3.1", "react-dom@^18.3.1", "lucide-react@^0.400.0"]
            expected_files = [
                "package.json",
                "tsconfig.json",
                "index.html",
                "src/main.tsx",
                "src/App.tsx",
                "src/index.css",
                "src/components/Header.tsx",
                "README.md",
            ]
        elif project_type == ProjectType.PYTHON_API.value:
            language = "python"
            framework = "fastapi"
            pages = []
            components = ["Router", "ServiceLayer"]
            entry_points = ["main.py"]
            dependencies = ["fastapi", "uvicorn", "pydantic"]
            expected_files = [
                "main.py",
                "requirements.txt",
                "app/__init__.py",
                "app/routes.py",
                "README.md",
            ]
        elif project_type == ProjectType.PYTHON_APP.value:
            language = "python"
            framework = "standard"
            pages = []
            components = ["CLI", "CoreModule"]
            entry_points = ["main.py"]
            dependencies = ["pytest"]
            expected_files = [
                "main.py",
                "requirements.txt",
                "README.md",
            ]
        else:
            # Vanilla Web
            project_type = ProjectType.VANILLA_WEB.value
            language = "javascript"
            framework = "web"
            pages = ["index.html"]
            components = ["styles.css", "app.js"]
            entry_points = ["index.html"]
            dependencies = []
            expected_files = [
                "index.html",
                "styles.css",
                "app.js",
                "README.md",
            ]

        if len(expected_files) > MAX_FILES_PER_PROJECT:
            logger.error(f"ProjectSpecification error: expected files ({len(expected_files)}) exceeds max {MAX_FILES_PER_PROJECT}")
            return None

        spec = ProjectSpecification(
            project_name=clean_name,
            project_type=project_type,
            language=language,
            framework=framework,
            description=f"{framework.capitalize()} project scaffolded with {', '.join(features[:2])}.",
            features=features,
            pages=pages,
            components=components,
            data_models=["Entity", "Metadata"],
            api_requirements=["GET /status"] if "api" in query.lower() else [],
            authentication="token_based" if "auth" in query.lower() or "login" in query.lower() else None,
            styling="dark futuristic" if "dark" in query.lower() or "futuristic" in query.lower() else "modern clean",
            dependencies=dependencies,
            entry_points=entry_points,
            expected_files=expected_files,
            constraints=["Strict sandbox isolation", "No arbitrary terminal execution"],
            generation_strategy="deterministic_templates",
        )

        logger.info(f"ProjectSpecification generated for '{spec.project_name}' ({spec.project_type}) with {len(spec.expected_files)} files.")
        return spec
