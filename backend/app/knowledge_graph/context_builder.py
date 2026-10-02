"""Targeted Context Builder for RYVEN & Qwen (M11.5).

Builds compact, structured context packages from graph queries instead of dumping
entire repositories into LLM prompts.
"""

from pathlib import Path
from typing import Dict, List, Optional, Set

from app.core.logging_config import logger
from app.knowledge_graph.cache import GraphCacheManager
from app.knowledge_graph.context_budget import ContextBudgetManager
from app.knowledge_graph.graph_query import GraphQueryEngine
from app.knowledge_graph.models import (
    KnowledgeGraph,
    OptimizedContextPackage,
    RelationType,
)
from app.knowledge_graph.security import KnowledgeGraphSecurity


class ContextBuilder:
    """Constructs focused context packages for Qwen based on Knowledge Graph relationships."""

    # Configurable Context Budget Limits
    MAX_CONTEXT_FILES = 8
    MAX_CONTEXT_CHARS = 24000
    MAX_CONTEXT_LINES = 600

    def __init__(self, project_root: str, graph: KnowledgeGraph):
        self.project_root = Path(KnowledgeGraphSecurity.validate_project_root(project_root))
        self.graph = graph
        self.query_engine = GraphQueryEngine(graph)

    def _read_file_safe(self, rel_path: str) -> Optional[str]:
        """Safely read file content within project root."""
        full_path = self.project_root / rel_path
        if not KnowledgeGraphSecurity.is_safe_relative_path(self.project_root, full_path):
            return None
        if KnowledgeGraphSecurity.is_sensitive_file(rel_path):
            return None
        try:
            return full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"Failed to read file {rel_path}: {e}")
            return None

    def _extract_snippet(self, content: str, line_start: Optional[int], line_end: Optional[int]) -> str:
        """Extract a targeted snippet from content with a 2-line safety buffer."""
        if not line_start:
            return content

        lines = content.splitlines()
        start = max(0, line_start - 3)
        end = min(len(lines), (line_end or line_start) + 2)
        return "\n".join(lines[start:end])

    def _rank_files(
        self,
        candidate_files: Dict[str, float],
        task_keywords: List[str],
    ) -> List[str]:
        """Rank candidate files deterministically based on structural relevance signals.
        
        Signals:
        1. Direct symbol definition match (+10)
        2. Caller / callee relationship (+7)
        3. Direct dependency (+5)
        4. Import relationship (+3)
        5. Path keyword match (+2)
        """
        scored: Dict[str, float] = dict(candidate_files)
        for rel_path in scored.keys():
            lower_path = rel_path.lower()
            for kw in task_keywords:
                if kw in lower_path:
                    scored[rel_path] = scored.get(rel_path, 0.0) + 2.0

        # Sort descending by score, then alphabetically for deterministic reproducibility
        sorted_files = sorted(scored.keys(), key=lambda f: (-scored[f], f))
        return sorted_files

    def build_context(
        self,
        task: str,
        target_symbol: Optional[str] = None,
        max_files: Optional[int] = None,
    ) -> OptimizedContextPackage:
        """Build a targeted context package for the given user task or symbol."""
        safe_task = KnowledgeGraphSecurity.validate_safe_input_text(task)
        effective_max_files = max_files or self.MAX_CONTEXT_FILES

        file_scores: Dict[str, float] = {}
        relevant_symbols: Set[str] = set()
        dependencies_overview: List[str] = []
        source_excerpts: Dict[str, str] = {}
        task_keywords = [w.lower() for w in safe_task.split() if len(w) > 2]

        # 1. Identify relevant nodes via target_symbol or task keywords
        if target_symbol:
            sym_res = self.query_engine.find_symbol(target_symbol)
            if sym_res.found:
                relevant_symbols.add(sym_res.symbol)
                for node in sym_res.nodes:
                    if node.file_path:
                        file_scores[node.file_path] = file_scores.get(node.file_path, 0.0) + 10.0
                for dep in sym_res.dependencies:
                    relevant_symbols.add(dep)
                    dependencies_overview.append(f"{sym_res.symbol} -> {dep}")
        else:
            # Query graph for task keywords
            query_res = self.query_engine.query(safe_task)
            for node in query_res.matched_nodes:
                relevant_symbols.add(node.name)
                if node.file_path:
                    file_scores[node.file_path] = file_scores.get(node.file_path, 0.0) + 7.0

        # 2. Add direct dependencies of relevant files
        for f in list(file_scores.keys()):
            file_deps = self.query_engine.find_dependencies(f)
            for edge in file_deps:
                dep_node = self.graph.nodes.get(edge.target)
                if dep_node and dep_node.file_path:
                    file_scores[dep_node.file_path] = file_scores.get(dep_node.file_path, 0.0) + 5.0
                    dependencies_overview.append(f"{f} -> {dep_node.file_path} ({edge.relation.value})")

        # Fallback if no relevant files were found
        if not file_scores:
            cache_mgr = GraphCacheManager(str(self.project_root))
            all_files = list(cache_mgr.scan_current_files().keys())
            for f in all_files[:effective_max_files]:
                file_scores[f] = 1.0

        # Rank files deterministically
        ranked_files = self._rank_files(file_scores, task_keywords)[:effective_max_files]


        # 3. Read excerpts for relevant files
        optimized_files_map: Dict[str, str] = {}
        for rel_path in ranked_files:
            full_content = self._read_file_safe(rel_path)

            if full_content is not None:
                # Find if there are specific symbol line ranges in this file
                file_nodes = [
                    n for n in self.graph.nodes.values()
                    if n.file_path == rel_path and n.name in relevant_symbols and n.line_start
                ]
                if file_nodes:
                    # Target excerpt
                    fn = file_nodes[0]
                    snippet = self._extract_snippet(full_content, fn.line_start, fn.line_end)
                    source_excerpts[rel_path] = f"# Excerpt for {fn.name} (Lines {fn.line_start}-{fn.line_end}):\n{snippet}"
                    optimized_files_map[rel_path] = snippet
                else:
                    source_excerpts[rel_path] = full_content
                    optimized_files_map[rel_path] = full_content

        # 4. Measure against baseline (all indexable project files)
        cache_mgr = GraphCacheManager(str(self.project_root))
        all_project_files = cache_mgr.scan_current_files()
        baseline_files_map: Dict[str, str] = {}
        for f in all_project_files.keys():
            content = self._read_file_safe(f)
            if content:
                baseline_files_map[f] = content

        budget = ContextBudgetManager.calculate_budget(
            task=safe_task,
            baseline_files=baseline_files_map,
            optimized_files=optimized_files_map,
            relevant_symbols=sorted(list(relevant_symbols)),
        )

        summary = (
            f"Optimized context for task '{safe_task}' with {len(ranked_files)} files "
            f"and {len(relevant_symbols)} symbols. "
            f"Achieved {budget.reduction_percentage}% estimated context reduction."
        )

        return OptimizedContextPackage(
            task=safe_task,
            summary=summary,
            relevant_files=ranked_files,
            relevant_symbols=sorted(list(relevant_symbols)),
            dependency_overview=sorted(list(set(dependencies_overview))),
            source_excerpts=source_excerpts,
            budget=budget,
        )

