"""Workflow planning engine converting user intent into safe, structured workflow definitions."""

import re
from typing import Optional
from app.core.logging_config import logger
from app.tools.registry import ToolRegistry
from app.workflows.models import WorkflowDefinition, WorkflowStep


class WorkflowPlanner:
    """Produces structured, validated workflow plans from supported high-level user goals."""

    # Regex patterns identifying workspace preparation requests
    WORKSPACE_PREP_PATTERNS = [
        r"\b(?:prepare(?:\s+my)?(?:\s+(?:development|dev))?\s+workspace)\b",
        r"\b(?:set(?:\s+)?up(?:\s+my)?(?:\s+(?:development|dev))?\s+workspace)\b",
        r"\b(?:open(?:\s+my)?(?:\s+(?:development|dev))\s+workspace)\b",
        r"\b(?:initialize(?:\s+my)?(?:\s+(?:development|dev))?\s+workspace)\b",
    ]

    # Regex patterns identifying project scaffolding requests
    PROJECT_CREATION_PATTERNS = [
        r"\b(?:create|build|scaffold|generate|setup|set\s+up)\s+(?:a\s+)?(?:new\s+)?(?:(?P<type>react|python|web|vanilla|html|node|frontend)\s+)?project\s+(?:called|named)\s+['\"]?(?P<name>[^\s'\"]+)['\"]?\b",
        r"\b(?:create|build|scaffold|generate|setup|set\s+up)\s+(?:a\s+)?(?:new\s+)?project\s+['\"]?(?P<name>(?!in\b|using\b|with\b)[^\s'\"]+)['\"]?\s+(?:in|using|with)\s+(?P<type>react|python|web|vanilla|html|node)\b",
        r"\b(?:create|build|scaffold|generate|setup|set\s+up)\s+(?:a\s+)?(?:new\s+)?(?:(?P<type>react|python|web|vanilla|html|node|frontend)\s+)?project\s+['\"]?(?P<name>(?!in\b|using\b|with\b|for\b|from\b)[a-zA-Z0-9_\-]+)['\"]?\b",
    ]

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        if registry is None:
            from app.tools.registry import create_default_registry
            self.registry = create_default_registry()
        else:
            self.registry = registry
        self._compiled_workspace_prep = [
            re.compile(p, re.IGNORECASE) for p in self.WORKSPACE_PREP_PATTERNS
        ]
        self._compiled_project_creation = [
            re.compile(p, re.IGNORECASE) for p in self.PROJECT_CREATION_PATTERNS
        ]

    def can_plan(self, query: str) -> bool:
        """Check if query matches a recognized high-level workflow."""
        clean = query.strip()
        # Strip conversational prefix
        clean = re.sub(r"^(?:ryven|jarvis)[,\s:]+", "", clean, flags=re.IGNORECASE).strip()
        if any(pattern.search(clean) for pattern in self._compiled_workspace_prep):
            return True
        return any(pattern.search(clean) for pattern in self._compiled_project_creation)

    def plan_workspace_preparation(self, query: str = "", requested_by: str = "user") -> Optional[WorkflowDefinition]:
        """Generate the Workspace Preparation Workflow plan using approved Phase 3 tools."""
        steps = [
            WorkflowStep(
                name="Inspect System Environment",
                tool_name="system_info",
                arguments={},
            ),
            WorkflowStep(
                name="Open Project Workspace",
                tool_name="open_folder",
                arguments={"folder": "workspace"},
            ),
            WorkflowStep(
                name="Launch Visual Studio Code",
                tool_name="open_application",
                arguments={"application": "vscode"},
            ),
        ]

        # Verify all tools exist in registry before generating the plan
        for step in steps:
            if not self.registry.has(step.tool_name):
                logger.error(
                    f"Cannot plan workspace preparation: required tool '{step.tool_name}' is not registered."
                )
                return None

        workflow = WorkflowDefinition(
            name="Prepare Development Workspace",
            description="Inspect system diagnostics, open the approved workspace folder, and launch Visual Studio Code.",
            requested_by=requested_by,
            steps=steps,
        )
        logger.info(f"Planned workflow '{workflow.name}' with {len(steps)} steps.")
        return workflow

    def plan_project_creation(
        self,
        project_name: str,
        project_type: str = "web",
        requested_by: str = "user",
    ) -> Optional[WorkflowDefinition]:
        """Generate the Project Builder Workflow plan for creating and scaffolding a project (Phase 4.1 compatibility)."""
        p_type = (project_type or "web").lower().strip()
        from app.tools.project_tool import sanitize_project_name
        from typing import Dict, List

        clean_name = sanitize_project_name(project_name)
        if not clean_name:
            logger.error(f"Cannot plan project creation: invalid project name '{project_name}'")
            return None

        # Determine templates based on project type
        template_files: Dict[str, str] = {}
        if "react" in p_type:
            template_files = {
                "package.json": f'{{\n  "name": "{clean_name.lower()}",\n  "private": true,\n  "version": "0.1.0",\n  "type": "module",\n  "scripts": {{\n    "dev": "vite",\n    "build": "vite build",\n    "preview": "vite preview"\n  }},\n  "dependencies": {{\n    "react": "^18.3.1",\n    "react-dom": "^18.3.1"\n  }},\n  "devDependencies": {{\n    "@vitejs/plugin-react": "^4.3.1",\n    "vite": "^5.4.2"\n  }}\n}}\n',
                "index.html": f'<!DOCTYPE html>\n<html lang="en">\n  <head>\n    <meta charset="UTF-8" />\n    <title>{clean_name}</title>\n  </head>\n  <body>\n    <div id="root"></div>\n    <script type="module" src="/src/main.jsx"></script>\n  </body>\n</html>\n',
                "src/main.jsx": "import React from 'react';\nimport ReactDOM from 'react-dom/client';\nimport App from './App.jsx';\nimport './index.css';\n\nReactDOM.createRoot(document.getElementById('root')).render(\n  <React.StrictMode>\n    <App />\n  </React.StrictMode>,\n);\n",
                "src/App.jsx": f"import React from 'react';\n\nexport default function App() {{\n  return (\n    <main style={{{{ padding: '2rem', fontFamily: 'sans-serif' }}}}>\n      <h1>{clean_name}</h1>\n      <p>Scaffolded safely by RYVEN 2.0 Project Builder Workflow.</p>\n    </main>\n  );\n}}\n",
                "src/index.css": "body {\n  margin: 0;\n  background-color: #0b0f19;\n  color: #f0f6fc;\n}\n",
                "README.md": f"# {clean_name}\n\nReact project scaffolded safely by RYVEN.\n",
            }
        elif "python" in p_type:
            template_files = {
                "main.py": f'"""Main entry point for {clean_name}."""\n\ndef main():\n    print("Welcome to {clean_name}!")\n\nif __name__ == "__main__":\n    main()\n',
                "requirements.txt": f"# Dependencies for {clean_name}\n",
                "README.md": f"# {clean_name}\n\nPython project initialized by RYVEN.\n",
            }
        else:
            # Default Vanilla Web
            template_files = {
                "index.html": f'<!DOCTYPE html>\n<html lang="en">\n<head>\n  <meta charset="UTF-8">\n  <title>{clean_name}</title>\n  <link rel="stylesheet" href="styles.css">\n</head>\n<body>\n  <h1>{clean_name}</h1>\n  <p>Vanilla Web project initialized by RYVEN.</p>\n  <script src="app.js"></script>\n</body>\n</html>\n',
                "styles.css": "body {\n  font-family: sans-serif;\n  background-color: #0b0f19;\n  color: #00f0ff;\n  padding: 2rem;\n}\n",
                "app.js": f'console.log("{clean_name} initialized by RYVEN.");\n',
                "README.md": f"# {clean_name}\n\nWeb project generated by RYVEN.\n",
            }

        steps: List[WorkflowStep] = [
            WorkflowStep(
                name="Create Project Workspace Directory",
                tool_name="create_project_folder",
                arguments={"project_name": clean_name},
                requires_confirmation=True,
            )
        ]

        # Add file creation steps
        for rel_path, content in template_files.items():
            steps.append(
                WorkflowStep(
                    name=f"Generate {rel_path}",
                    tool_name="create_project_file",
                    arguments={
                        "project_name": clean_name,
                        "relative_path": rel_path,
                        "content": content,
                    },
                    requires_confirmation=True,
                )
            )

        # Validation step
        steps.append(
            WorkflowStep(
                name="Validate Project Files",
                tool_name="validate_project_files",
                arguments={
                    "project_name": clean_name,
                    "expected_files": list(template_files.keys()),
                },
                requires_confirmation=False,
            )
        )

        # Verify all tools exist in registry
        for step in steps:
            if not self.registry.has(step.tool_name):
                logger.error(
                    f"Cannot plan project creation: required tool '{step.tool_name}' is not registered."
                )
                return None

        workflow = WorkflowDefinition(
            name=f"Build {p_type.capitalize()} Project: {clean_name}",
            description=f"Scaffold project workspace and generate {len(template_files)} files for {clean_name}.",
            requested_by=requested_by,
            steps=steps,
        )
        logger.info(f"Planned project creation workflow '{workflow.name}' with {len(steps)} steps.")
        return workflow

    def plan_project_development(
        self,
        query: str,
        project_name: Optional[str] = None,
        project_type: Optional[str] = None,
        requested_by: str = "user",
    ) -> Optional[WorkflowDefinition]:
        """Generate complete Project Development workflow using the DEV ENGINE v1 pipeline."""
        from app.dev_engine.specification import ProjectSpecificationEngine
        from app.dev_engine.file_plan import FilePlanEngine
        from app.dev_engine.generator import CodeGenerationEngine
        from typing import List

        spec_engine = ProjectSpecificationEngine()
        file_plan_engine = FilePlanEngine()
        code_gen_engine = CodeGenerationEngine()

        # 1. Synthesize ProjectSpecification
        spec = spec_engine.create_specification(
            project_name=project_name or "my_project",
            query=query,
            explicit_type=project_type,
        )
        if not spec:
            return None

        # 2. Formulate FilePlan
        plan = file_plan_engine.create_file_plan(spec, overwrite_allowed=False)

        # 3. Generate in-memory GeneratedFile representations
        try:
            generated_files = code_gen_engine.generate_files(spec, plan)
        except Exception as exc:
            logger.error(f"CodeGenerationEngine error for '{spec.project_name}': {exc}", exc_info=True)
            return None

        # 4. Formulate Workflow Steps
        steps: List[WorkflowStep] = [
            WorkflowStep(
                name="Create Project Workspace Directory",
                tool_name="create_project_folder",
                arguments={"project_name": spec.project_name},
                requires_confirmation=True,
            )
        ]

        # Add file creation steps
        for gen_file in generated_files:
            steps.append(
                WorkflowStep(
                    name=f"Generate {gen_file.relative_path}",
                    tool_name="create_project_file",
                    arguments={
                        "project_name": spec.project_name,
                        "relative_path": gen_file.relative_path,
                        "content": gen_file.content,
                    },
                    requires_confirmation=True,
                )
            )

        # Validation step
        steps.append(
            WorkflowStep(
                name="Validate Project Files",
                tool_name="validate_project_files",
                arguments={
                    "project_name": spec.project_name,
                    "expected_files": spec.expected_files,
                },
                requires_confirmation=False,
            )
        )

        # Verify all tools exist in registry
        for step in steps:
            if not self.registry.has(step.tool_name):
                logger.error(
                    f"Cannot plan project creation: required tool '{step.tool_name}' is not registered."
                )
                return None

        workflow = WorkflowDefinition(
            name=f"Build {spec.framework.capitalize()} Project: {spec.project_name}",
            description=f"{spec.description} ({len(generated_files)} files planned).",
            requested_by=requested_by,
            steps=steps,
        )
        logger.info(f"Planned DEV ENGINE project workflow '{workflow.name}' with {len(steps)} steps.")
        return workflow

    def plan(self, query: str, requested_by: str = "user") -> Optional[WorkflowDefinition]:
        """Convert a user request into a supported workflow plan, if recognized."""
        clean = query.strip()
        clean = re.sub(r"^(?:ryven|jarvis)[,\s:]+", "", clean, flags=re.IGNORECASE).strip()

        # 1. Workspace prep check
        if any(pattern.search(clean) for pattern in self._compiled_workspace_prep):
            return self.plan_workspace_preparation(clean, requested_by=requested_by)

        # Check for explicit dev engine / rich features request
        is_rich_dev_request = any(
            token in clean.lower()
            for token in ["expense tracker", "dashboard", "features", "authentication", "dark futuristic", "dev engine"]
        )

        # 2. Project creation check
        for pattern in self._compiled_project_creation:
            match = pattern.search(clean)
            if match:
                group_dict = match.groupdict()
                project_name = group_dict.get("name") or "my_project"
                project_type = group_dict.get("type") or "web"
                if is_rich_dev_request:
                    return self.plan_project_development(
                        query=clean,
                        project_name=project_name,
                        project_type=project_type,
                        requested_by=requested_by,
                    )
                return self.plan_project_creation(
                    project_name=project_name,
                    project_type=project_type,
                    requested_by=requested_by,
                )

        if is_rich_dev_request:
            # Fallback to dev engine if rich dev request matches general pattern
            from app.dev_engine.specification import ProjectSpecificationEngine
            spec = ProjectSpecificationEngine().create_specification(query=clean)
            if spec:
                return self.plan_project_development(
                    query=clean,
                    project_name=spec.project_name,
                    project_type=spec.project_type.value,
                    requested_by=requested_by,
                )

        return None
