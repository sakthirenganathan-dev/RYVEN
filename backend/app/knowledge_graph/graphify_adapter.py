"""Graphify Adapter and Local Structural AST Engine for RYVEN (M11.5).

Provides structural code analysis, AST parsing, and optional Graphify integration.
Prioritizes local structural AST extraction with zero external dependencies,
ensuring 100% resilience across all environments.
"""

import ast
import importlib
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from app.core.logging_config import logger
from app.knowledge_graph.models import (
    GraphEdge,
    GraphIndexMetadata,
    GraphNode,
    KnowledgeGraph,
    NodeType,
    RelationType,
)
from app.knowledge_graph.security import KnowledgeGraphSecurity


class GraphifyAdapter:
    """Adapter bridging RYVEN and Graphify/AST structural analysis."""

    def __init__(self, semantic_mode_enabled: bool = False):
        self.semantic_mode_enabled = semantic_mode_enabled

    def detect_availability(self) -> Dict[str, Any]:
        """Detect whether Graphify module or CLI is installed and available."""
        graphify_module = False
        graphify_version = "unavailable"
        graphify_cli_path: Optional[str] = None

        # Check Python module import
        try:
            mod = importlib.import_module("graphify")
            graphify_module = True
            graphify_version = getattr(mod, "__version__", "installed")
        except ImportError:
            graphify_module = False

        # Check CLI binary
        cli = shutil.which("graphify")
        if cli:
            graphify_cli_path = cli

        is_available = graphify_module or (graphify_cli_path is not None)

        return {
            "available": is_available,
            "module_installed": graphify_module,
            "cli_installed": graphify_cli_path is not None,
            "cli_path": graphify_cli_path,
            "version": graphify_version if graphify_module else ("cli" if cli else "structural_ast_engine_v1"),
            "engine": "graphify_native" if is_available else "structural_ast_engine",
            "semantic_mode_enabled": self.semantic_mode_enabled,
        }

    def validate_installation(self) -> Dict[str, Any]:
        """Validate environment readiness and available features."""
        info = self.detect_availability()
        info["supported_languages"] = ["python", "javascript", "typescript", "json", "markdown"]
        info["ast_engine_ready"] = True
        return info

    def extract_python_ast(self, file_path: Path, rel_path: str) -> Tuple[List[GraphNode], List[GraphEdge]]:
        """Parse a Python source file using standard library AST to extract nodes and edges."""
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content, filename=str(file_path))
        except Exception as e:
            logger.debug(f"AST parsing failed for {file_path}: {e}")
            return nodes, edges

        file_node_id = f"file:{rel_path}"
        file_node = GraphNode(
            id=file_node_id,
            name=Path(rel_path).name,
            type=NodeType.FILE,
            file_path=rel_path,
            line_start=1,
            line_end=len(content.splitlines()),
            docstring=ast.get_docstring(tree),
        )
        nodes.append(file_node)

        class ASTVisitor(ast.NodeVisitor):
            def __init__(self):
                self.current_class: Optional[str] = None
                self.current_class_node_id: Optional[str] = None
                self.scope_stack: List[str] = []

            def visit_Import(self, node: ast.Import):
                for alias in node.names:
                    imp_name = alias.name
                    imp_node_id = f"import:{imp_name}"
                    nodes.append(GraphNode(
                        id=imp_node_id,
                        name=imp_name,
                        type=NodeType.IMPORT,
                        file_path=rel_path,
                        line_start=node.lineno,
                        line_end=node.lineno,
                    ))
                    edges.append(GraphEdge(
                        source=file_node_id,
                        target=imp_node_id,
                        relation=RelationType.IMPORTS,
                        file_path=rel_path,
                    ))
                self.generic_visit(node)

            def visit_ImportFrom(self, node: ast.ImportFrom):
                mod_name = node.module or ""
                for alias in node.names:
                    full_name = f"{mod_name}.{alias.name}" if mod_name else alias.name
                    imp_node_id = f"import:{full_name}"
                    nodes.append(GraphNode(
                        id=imp_node_id,
                        name=full_name,
                        type=NodeType.IMPORT,
                        file_path=rel_path,
                        line_start=node.lineno,
                        line_end=node.lineno,
                    ))
                    edges.append(GraphEdge(
                        source=file_node_id,
                        target=imp_node_id,
                        relation=RelationType.IMPORTS,
                        file_path=rel_path,
                    ))
                self.generic_visit(node)

            def visit_ClassDef(self, node: ast.ClassDef):
                class_node_id = f"class:{rel_path}:{node.name}"
                cls_node = GraphNode(
                    id=class_node_id,
                    name=node.name,
                    type=NodeType.CLASS,
                    file_path=rel_path,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    docstring=ast.get_docstring(node),
                )
                nodes.append(cls_node)
                edges.append(GraphEdge(
                    source=file_node_id,
                    target=class_node_id,
                    relation=RelationType.DEFINES,
                    file_path=rel_path,
                ))

                # Inheritance
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        base_name = base.id
                        edges.append(GraphEdge(
                            source=class_node_id,
                            target=f"symbol:{base_name}",
                            relation=RelationType.EXTENDS,
                            file_path=rel_path,
                        ))

                prev_class = self.current_class
                prev_class_id = self.current_class_node_id
                self.current_class = node.name
                self.current_class_node_id = class_node_id

                self.generic_visit(node)

                self.current_class = prev_class
                self.current_class_node_id = prev_class_id

            def visit_FunctionDef(self, node: ast.FunctionDef):
                self._handle_function(node, is_async=False)

            def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
                self._handle_function(node, is_async=True)

            def _handle_function(self, node: Any, is_async: bool):
                func_name = node.name
                if self.current_class:
                    fn_type = NodeType.METHOD
                    fn_node_id = f"method:{rel_path}:{self.current_class}.{func_name}"
                    parent_id = self.current_class_node_id or file_node_id
                else:
                    fn_type = NodeType.FUNCTION
                    fn_node_id = f"function:{rel_path}:{func_name}"
                    parent_id = file_node_id

                fn_node = GraphNode(
                    id=fn_node_id,
                    name=f"{self.current_class}.{func_name}" if self.current_class else func_name,
                    type=fn_type,
                    file_path=rel_path,
                    line_start=node.lineno,
                    line_end=getattr(node, "end_lineno", node.lineno),
                    docstring=ast.get_docstring(node),
                    metadata={"is_async": is_async},
                )
                nodes.append(fn_node)
                edges.append(GraphEdge(
                    source=parent_id,
                    target=fn_node_id,
                    relation=RelationType.DEFINES,
                    file_path=rel_path,
                ))

                # Track function calls inside body
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call):
                        target_name = None
                        if isinstance(sub.func, ast.Name):
                            target_name = sub.func.id
                        elif isinstance(sub.func, ast.Attribute):
                            target_name = sub.func.attr

                        if target_name and target_name != func_name:
                            edges.append(GraphEdge(
                                source=fn_node_id,
                                target=f"symbol:{target_name}",
                                relation=RelationType.CALLS,
                                file_path=rel_path,
                            ))

                self.generic_visit(node)

        visitor = ASTVisitor()
        visitor.visit(tree)
        return nodes, edges

    def extract_js_ts_structural(self, file_path: Path, rel_path: str) -> Tuple[List[GraphNode], List[GraphEdge]]:
        """Extract classes, functions, and imports from JS/TS using robust structural regex patterns."""
        nodes: List[GraphNode] = []
        edges: List[GraphEdge] = []

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return nodes, edges

        lines = content.splitlines()
        file_node_id = f"file:{rel_path}"
        nodes.append(GraphNode(
            id=file_node_id,
            name=Path(rel_path).name,
            type=NodeType.FILE,
            file_path=rel_path,
            line_start=1,
            line_end=len(lines),
        ))

        # 1. Imports
        import_pattern = re.compile(r"""(?:import\s+(?:\{([^}]+)\}|\*\s+as\s+(\w+)|(\w+))\s+from\s+['"]([^'"]+)['"]|require\(['"]([^'"]+)['"]\))""")
        for i, line in enumerate(lines, 1):
            m = import_pattern.search(line)
            if m:
                source_mod = m.group(4) or m.group(5) or "unknown"
                imp_id = f"import:{source_mod}"
                nodes.append(GraphNode(
                    id=imp_id,
                    name=source_mod,
                    type=NodeType.IMPORT,
                    file_path=rel_path,
                    line_start=i,
                    line_end=i,
                ))
                edges.append(GraphEdge(
                    source=file_node_id,
                    target=imp_id,
                    relation=RelationType.IMPORTS,
                    file_path=rel_path,
                ))

        # 2. Classes & Interfaces
        class_pattern = re.compile(r"""(?:export\s+)?(?:class|interface)\s+([A-Za-z0-9_$]+)(?:\s+extends\s+([A-Za-z0-9_$]+))?""")
        for i, line in enumerate(lines, 1):
            m = class_pattern.search(line)
            if m:
                cls_name = m.group(1)
                extends_name = m.group(2)
                cls_id = f"class:{rel_path}:{cls_name}"
                nodes.append(GraphNode(
                    id=cls_id,
                    name=cls_name,
                    type=NodeType.CLASS,
                    file_path=rel_path,
                    line_start=i,
                    line_end=i,
                ))
                edges.append(GraphEdge(
                    source=file_node_id,
                    target=cls_id,
                    relation=RelationType.DEFINES,
                    file_path=rel_path,
                ))
                if extends_name:
                    edges.append(GraphEdge(
                        source=cls_id,
                        target=f"symbol:{extends_name}",
                        relation=RelationType.EXTENDS,
                        file_path=rel_path,
                    ))

        # 3. Functions
        func_pattern = re.compile(r"""(?:export\s+)?(?:async\s+)?(?:function\s+([A-Za-z0-9_$]+)|const\s+([A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>)""")
        for i, line in enumerate(lines, 1):
            m = func_pattern.search(line)
            if m:
                func_name = m.group(1) or m.group(2)
                if func_name:
                    fn_id = f"function:{rel_path}:{func_name}"
                    nodes.append(GraphNode(
                        id=fn_id,
                        name=func_name,
                        type=NodeType.FUNCTION,
                        file_path=rel_path,
                        line_start=i,
                        line_end=i,
                    ))
                    edges.append(GraphEdge(
                        source=file_node_id,
                        target=fn_id,
                        relation=RelationType.DEFINES,
                        file_path=rel_path,
                    ))

        return nodes, edges

    def analyze_file(self, project_root: Path, rel_path: str) -> Tuple[List[GraphNode], List[GraphEdge]]:
        """Analyze a single source file based on extension."""
        full_path = project_root / rel_path
        suffix = full_path.suffix.lower()

        if suffix == ".py":
            return self.extract_python_ast(full_path, rel_path)
        elif suffix in {".ts", ".tsx", ".js", ".jsx"}:
            return self.extract_js_ts_structural(full_path, rel_path)
        else:
            # Generic file node
            file_id = f"file:{rel_path}"
            return [GraphNode(
                id=file_id,
                name=full_path.name,
                type=NodeType.FILE,
                file_path=rel_path,
                line_start=1,
                line_end=1,
            )], []

    def index_project_files(
        self,
        project_root: str,
        files: List[str],
    ) -> KnowledgeGraph:
        """Run structural AST indexing over a list of relative project files."""
        root = Path(KnowledgeGraphSecurity.validate_project_root(project_root))
        start_time = time.time()

        all_nodes: Dict[str, GraphNode] = {}
        all_edges: List[GraphEdge] = []

        # Root Project Node
        proj_id = f"project:{root.name}"
        proj_node = GraphNode(
            id=proj_id,
            name=root.name,
            type=NodeType.PROJECT,
            file_path=None,
            metadata={"absolute_path": str(root)},
        )
        all_nodes[proj_id] = proj_node

        # Index each file
        for rel_path in files:
            f_nodes, f_edges = self.analyze_file(root, rel_path)
            for node in f_nodes:
                all_nodes[node.id] = node
            all_edges.extend(f_edges)

            # Link project contains file
            file_node_id = f"file:{rel_path}"
            if file_node_id in all_nodes:
                all_edges.append(GraphEdge(
                    source=proj_id,
                    target=file_node_id,
                    relation=RelationType.CONTAINS,
                    file_path=rel_path,
                ))

        # Symbol resolution pass:
        # Match symbol:target edges to defined Class/Function nodes if matching name exists
        name_to_ids: Dict[str, List[str]] = {}
        for nid, n in all_nodes.items():
            name_to_ids.setdefault(n.name, []).append(nid)
            if "." in n.name:
                short_name = n.name.split(".")[-1]
                name_to_ids.setdefault(short_name, []).append(nid)


        resolved_edges: List[GraphEdge] = []
        for edge in all_edges:
            if edge.target.startswith("symbol:"):
                sym_name = edge.target[len("symbol:"):]
                targets = name_to_ids.get(sym_name, [])
                if targets:
                    for t in targets:
                        # Don't create self-loop for CALLS/EXTENDS
                        if t != edge.source:
                            resolved_edges.append(GraphEdge(
                                source=edge.source,
                                target=t,
                                relation=edge.relation,
                                confidence=edge.confidence,
                                file_path=edge.file_path,
                            ))
                else:
                    # Keep as symbol node
                    resolved_edges.append(edge)
            else:
                resolved_edges.append(edge)

        # De-duplicate edges
        seen_edges: Set[Tuple[str, str, str]] = set()
        deduped_edges: List[GraphEdge] = []
        for e in resolved_edges:
            key = (e.source, e.target, e.relation.value)
            if key not in seen_edges:
                seen_edges.add(key)
                deduped_edges.append(e)

        duration_ms = (time.time() - start_time) * 1000.0

        metadata = GraphIndexMetadata(
            project_id=root.name,
            project_root=str(root),
            graphify_version="structural_ast_engine_v1",
            schema_version="1.0.0",
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
            source_file_count=len(files),
            node_count=len(all_nodes),
            edge_count=len(deduped_edges),
            is_stale=False,
            semantic_mode_enabled=self.semantic_mode_enabled,
            index_duration_ms=round(duration_ms, 2),
        )

        return KnowledgeGraph(
            metadata=metadata,
            nodes=all_nodes,
            edges=deduped_edges,
        )
