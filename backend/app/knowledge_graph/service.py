"""Unified Knowledge Graph and Context Optimization Service for RYVEN (M11.5).

Provides high-level API for workflows, tools, and developer HUD with automatic fallback.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.logging_config import logger
from app.knowledge_graph.cache import GraphCacheManager
from app.knowledge_graph.context_builder import ContextBuilder
from app.knowledge_graph.graph_index import GraphIndexManager
from app.knowledge_graph.graph_query import GraphQueryEngine
from app.knowledge_graph.graphify_adapter import GraphifyAdapter
from app.knowledge_graph.models import (
    GraphEdge,
    GraphNode,
    GraphQueryResult,
    KnowledgeGraph,
    OptimizedContextPackage,
    SymbolSearchResult,
)
from app.knowledge_graph.security import KnowledgeGraphSecurity
from app.knowledge_graph.telemetry import KnowledgeGraphTelemetry


class KnowledgeGraphService:
    """High-level service coordinating knowledge graph indexing, queries, and context optimization."""

    def __init__(self, semantic_mode_enabled: bool = False):
        self.semantic_mode_enabled = semantic_mode_enabled
        self.adapter = GraphifyAdapter(semantic_mode_enabled=semantic_mode_enabled)
        self.index_manager = GraphIndexManager(semantic_mode_enabled=semantic_mode_enabled)

    def detect_availability(self) -> Dict[str, Any]:
        """Detect Graphify and structural AST engine availability."""
        return self.adapter.detect_availability()

    def get_status(self, project_path: str) -> Dict[str, Any]:
        """Inspect status and statistics of project knowledge graph without mutating disk."""
        valid_root = KnowledgeGraphSecurity.validate_project_root(project_path)
        cache_mgr = GraphCacheManager(valid_root)
        proj_name = Path(valid_root).name

        if not cache_mgr.has_cache():
            return {
                "status": "UNAVAILABLE",
                "project_id": proj_name,
                "project_root": valid_root,
                "source_files": 0,
                "nodes": 0,
                "relationships": 0,
                "last_indexed": None,
                "is_stale": True,
                "schema_version": "1.0.0",
                "graphify_version": self.adapter.detect_availability().get("version", "structural_ast_engine_v1"),
            }

        graph = cache_mgr.load_graph()
        if not graph:
            return {
                "status": "FAILED",
                "project_id": proj_name,
                "project_root": valid_root,
                "source_files": 0,
                "nodes": 0,
                "relationships": 0,
                "last_indexed": None,
                "is_stale": True,
                "schema_version": "1.0.0",
                "graphify_version": "unknown",
            }

        is_stale = cache_mgr.is_cache_stale()
        return {
            "status": "STALE" if is_stale else "READY",
            "project_id": graph.metadata.project_id,
            "project_root": graph.metadata.project_root,
            "source_files": graph.metadata.source_file_count,
            "nodes": graph.metadata.node_count,
            "relationships": graph.metadata.edge_count,
            "last_indexed": graph.metadata.updated_at,
            "is_stale": is_stale,
            "schema_version": graph.metadata.schema_version,
            "graphify_version": graph.metadata.graphify_version,
            "semantic_mode_enabled": graph.metadata.semantic_mode_enabled,
        }

    def get_or_build(self, project_path: str) -> KnowledgeGraph:

        """Retrieve existing graph or build it automatically."""
        valid_root = KnowledgeGraphSecurity.validate_project_root(project_path)
        proj_name = Path(valid_root).name
        try:
            cache_mgr = GraphCacheManager(valid_root)
            if cache_mgr.has_cache() and not cache_mgr.is_cache_stale():
                graph = cache_mgr.load_graph()
                if graph:
                    KnowledgeGraphTelemetry.cache_hit(proj_name, len(graph.nodes))
                    return graph

            KnowledgeGraphTelemetry.cache_miss(proj_name)
            return self.index_manager.get_or_build(valid_root)
        except Exception as e:
            logger.error(f"Error in get_or_build for {valid_root}: {e}")
            KnowledgeGraphTelemetry.build_failed(proj_name, str(e))
            raise

    def build(self, project_path: str, force: bool = False) -> KnowledgeGraph:
        """Trigger an explicit full build."""
        valid_root = KnowledgeGraphSecurity.validate_project_root(project_path)
        proj_name = Path(valid_root).name
        KnowledgeGraphTelemetry.build_started(proj_name, 0)
        try:
            graph = self.index_manager.build_index(valid_root, force=force)
            KnowledgeGraphTelemetry.build_completed(
                proj_name,
                graph.metadata.source_file_count,
                graph.metadata.node_count,
                graph.metadata.edge_count,
                graph.metadata.index_duration_ms,
            )
            return graph
        except Exception as e:
            logger.error(f"Graph build failed for {valid_root}: {e}")
            KnowledgeGraphTelemetry.build_failed(proj_name, str(e))
            raise

    def update(self, project_path: str) -> KnowledgeGraph:
        """Trigger an incremental update."""
        valid_root = KnowledgeGraphSecurity.validate_project_root(project_path)
        return self.index_manager.update_index(valid_root)

    def find_symbol(self, project_path: str, symbol: str) -> SymbolSearchResult:
        """Locate symbol and its callers/dependencies."""
        graph = self.get_or_build(project_path)
        engine = GraphQueryEngine(graph)
        result = engine.find_symbol(symbol)
        KnowledgeGraphTelemetry.query(
            Path(project_path).name, "find_symbol", symbol, len(result.nodes)
        )
        return result

    def find_dependencies(self, project_path: str, target: str) -> List[GraphEdge]:
        """Find outgoing dependencies of target file or symbol."""
        graph = self.get_or_build(project_path)
        engine = GraphQueryEngine(graph)
        edges = engine.find_dependencies(target)
        KnowledgeGraphTelemetry.query(Path(project_path).name, "find_dependencies", target, len(edges))
        return edges

    def find_dependents(self, project_path: str, target: str) -> List[GraphEdge]:
        """Find incoming dependencies of target file or symbol."""
        graph = self.get_or_build(project_path)
        engine = GraphQueryEngine(graph)
        edges = engine.find_dependents(target)
        KnowledgeGraphTelemetry.query(Path(project_path).name, "find_dependents", target, len(edges))
        return edges

    def find_callers(self, project_path: str, target: str) -> List[GraphNode]:
        """Find functions/methods calling target."""
        graph = self.get_or_build(project_path)
        engine = GraphQueryEngine(graph)
        callers = engine.find_callers(target)
        KnowledgeGraphTelemetry.query(Path(project_path).name, "find_callers", target, len(callers))
        return callers

    def find_path(self, project_path: str, source: str, target: str) -> List[str]:
        """Find shortest relationship path between two symbols/files."""
        graph = self.get_or_build(project_path)
        engine = GraphQueryEngine(graph)
        path = engine.find_path(source, target)
        KnowledgeGraphTelemetry.query(Path(project_path).name, "find_path", f"{source}->{target}", len(path))
        return path

    def explain(self, project_path: str, target: str) -> str:
        """Generate human-readable structural summary of target."""
        graph = self.get_or_build(project_path)
        engine = GraphQueryEngine(graph)
        explanation = engine.explain(target)
        KnowledgeGraphTelemetry.query(Path(project_path).name, "explain", target, 1)
        return explanation

    def query(self, project_path: str, natural_query: str) -> GraphQueryResult:
        """Search graph for natural language keywords."""
        graph = self.get_or_build(project_path)
        engine = GraphQueryEngine(graph)
        res = engine.query(natural_query)
        KnowledgeGraphTelemetry.query(
            Path(project_path).name, "query", natural_query, len(res.matched_nodes)
        )
        return res

    def build_optimized_context(
        self,
        project_path: str,
        task: str,
        target_symbol: Optional[str] = None,
        max_files: int = 5,
    ) -> OptimizedContextPackage:
        """Construct compact context package for Qwen prompt with context budget metrics."""
        valid_root = KnowledgeGraphSecurity.validate_project_root(project_path)
        proj_name = Path(valid_root).name
        try:
            graph = self.get_or_build(valid_root)
            builder = ContextBuilder(valid_root, graph)
            package = builder.build_context(task, target_symbol=target_symbol, max_files=max_files)
            KnowledgeGraphTelemetry.context_optimized(
                proj_name,
                task,
                package.budget.baseline_estimated_tokens,
                package.budget.optimized_estimated_tokens,
                package.budget.reduction_percentage,
            )
            return package
        except Exception as e:
            logger.warning(f"Graph context optimization failed: {e}. Falling back to baseline context.")
            KnowledgeGraphTelemetry.context_fallback(proj_name, str(e))
            # Fallback: read top files directly
            cache_mgr = GraphCacheManager(valid_root)
            files = list(cache_mgr.scan_current_files().keys())[:max_files]
            excerpts: Dict[str, str] = {}
            for f in files:
                try:
                    p = Path(valid_root) / f
                    if p.exists():
                        excerpts[f] = p.read_text(encoding="utf-8", errors="replace")[:2000]
                except Exception:
                    pass

            from app.knowledge_graph.models import ContextBudgetResult
            fallback_budget = ContextBudgetResult(
                task=task,
                baseline_files_count=len(files),
                baseline_characters=sum(len(c) for c in excerpts.values()),
                baseline_estimated_tokens=sum(len(c) // 4 for c in excerpts.values()),
                optimized_files_count=len(files),
                optimized_characters=sum(len(c) for c in excerpts.values()),
                optimized_estimated_tokens=sum(len(c) // 4 for c in excerpts.values()),
                character_reduction=0,
                token_reduction=0,
                reduction_percentage=0.0,
                relevant_files=files,
                relevant_symbols=[],
            )
            return OptimizedContextPackage(
                task=task,
                summary="Fallback context (knowledge graph indexing unavailable or failed).",
                relevant_files=files,
                relevant_symbols=[],
                dependency_overview=[],
                source_excerpts=excerpts,
                budget=fallback_budget,
            )


# Global service instance
knowledge_graph_service = KnowledgeGraphService()
