"""Registered tools for RYVEN Knowledge Graph and Context Optimization (M11.5).

Tools:
1. graph_build
2. graph_update
3. graph_query
4. graph_find_symbol
5. graph_find_dependencies
6. graph_find_dependents
7. graph_find_callers
8. graph_explain
9. graph_path
"""

from typing import Any, Dict
from app.core.logging_config import logger
from app.knowledge_graph.service import knowledge_graph_service
from app.tools.base import BaseTool


class GraphStatusTool(BaseTool):
    """Tool to inspect project knowledge graph index status and statistics."""

    name = "graph_status"
    description = "Check project knowledge graph status (READY, STALE, UNAVAILABLE, FAILED) and statistics."
    input_schema = {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Path to the project root"},
        },
        "required": ["project_path"],
    }
    requires_confirmation = False

    async def execute(self, project_path: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            status = knowledge_graph_service.get_status(project_path)
            return {"success": True, **status}
        except Exception as e:
            logger.error(f"graph_status tool error: {e}")
            return {"success": False, "error": str(e)}


class GraphBuildTool(BaseTool):

    """Tool to build or rebuild project knowledge graph index."""

    name = "graph_build"
    description = "Index project source files to construct a local structural knowledge graph."
    input_schema = {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Absolute or relative path to the project root"},
            "force": {"type": "boolean", "description": "Whether to force full rebuild ignoring existing cache", "default": False},
        },
        "required": ["project_path"],
    }
    requires_confirmation = False

    async def execute(self, project_path: str, force: bool = False, **kwargs: Any) -> Dict[str, Any]:
        try:
            graph = knowledge_graph_service.build(project_path, force=force)
            return {
                "success": True,
                "project_id": graph.metadata.project_id,
                "source_file_count": graph.metadata.source_file_count,
                "node_count": graph.metadata.node_count,
                "edge_count": graph.metadata.edge_count,
                "duration_ms": graph.metadata.index_duration_ms,
                "graphify_version": graph.metadata.graphify_version,
            }
        except Exception as e:
            logger.error(f"graph_build tool error: {e}")
            return {"success": False, "error": str(e)}


class GraphUpdateTool(BaseTool):
    """Tool to incrementally update knowledge graph for new/modified/deleted files."""

    name = "graph_update"
    description = "Incrementally update knowledge graph for modified or newly added files."
    input_schema = {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Path to the project root"},
        },
        "required": ["project_path"],
    }
    requires_confirmation = False

    async def execute(self, project_path: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            graph = knowledge_graph_service.update(project_path)
            return {
                "success": True,
                "project_id": graph.metadata.project_id,
                "node_count": graph.metadata.node_count,
                "edge_count": graph.metadata.edge_count,
                "updated_at": graph.metadata.updated_at,
                "is_stale": graph.metadata.is_stale,
            }
        except Exception as e:
            logger.error(f"graph_update tool error: {e}")
            return {"success": False, "error": str(e)}


class GraphQueryTool(BaseTool):
    """Tool to query knowledge graph with natural keywords."""

    name = "graph_query"
    description = "Query the project knowledge graph for code structures matching keywords."
    input_schema = {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Path to project root"},
            "query": {"type": "string", "description": "Keywords or search phrase"},
        },
        "required": ["project_path", "query"],
    }
    requires_confirmation = False

    async def execute(self, project_path: str, query: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            result = knowledge_graph_service.query(project_path, query)
            return {
                "success": result.success,
                "query": result.query,
                "explanation": result.explanation,
                "matched_nodes_count": len(result.matched_nodes),
                "matched_nodes": [n.model_dump() for n in result.matched_nodes[:20]],
                "matched_edges": [e.model_dump() for e in result.matched_edges[:20]],
            }
        except Exception as e:
            logger.error(f"graph_query tool error: {e}")
            return {"success": False, "error": str(e)}


class GraphFindSymbolTool(BaseTool):
    """Tool to locate a specific class, function, or symbol definition and callers."""

    name = "graph_find_symbol"
    description = "Find symbol definitions, its callers, dependencies, and enclosing source files."
    input_schema = {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Path to project root"},
            "symbol": {"type": "string", "description": "Name of class, function, or method"},
        },
        "required": ["project_path", "symbol"],
    }
    requires_confirmation = False

    async def execute(self, project_path: str, symbol: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            result = knowledge_graph_service.find_symbol(project_path, symbol)
            return {
                "success": True,
                "symbol": result.symbol,
                "found": result.found,
                "nodes": [n.model_dump() for n in result.nodes],
                "callers": result.callers,
                "dependencies": result.dependencies,
                "dependents": result.dependents,
            }
        except Exception as e:
            logger.error(f"graph_find_symbol tool error: {e}")
            return {"success": False, "error": str(e)}


class GraphFindDependenciesTool(BaseTool):
    """Tool to inspect outgoing dependencies of a symbol or source file."""

    name = "graph_find_dependencies"
    description = "List all outbound dependencies (imports, calls, inheritance) for a target."
    input_schema = {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Path to project root"},
            "target": {"type": "string", "description": "Relative file path or symbol name"},
        },
        "required": ["project_path", "target"],
    }
    requires_confirmation = False

    async def execute(self, project_path: str, target: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            edges = knowledge_graph_service.find_dependencies(project_path, target)
            return {
                "success": True,
                "target": target,
                "dependency_count": len(edges),
                "dependencies": [e.model_dump() for e in edges],
            }
        except Exception as e:
            logger.error(f"graph_find_dependencies tool error: {e}")
            return {"success": False, "error": str(e)}


class GraphFindDependentsTool(BaseTool):
    """Tool to inspect inbound dependents (nodes that import or reference the target)."""

    name = "graph_find_dependents"
    description = "List all inbound dependents (nodes that depend on, import, or call the target)."
    input_schema = {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Path to project root"},
            "target": {"type": "string", "description": "Relative file path or symbol name"},
        },
        "required": ["project_path", "target"],
    }
    requires_confirmation = False

    async def execute(self, project_path: str, target: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            edges = knowledge_graph_service.find_dependents(project_path, target)
            return {
                "success": True,
                "target": target,
                "dependent_count": len(edges),
                "dependents": [e.model_dump() for e in edges],
            }
        except Exception as e:
            logger.error(f"graph_find_dependents tool error: {e}")
            return {"success": False, "error": str(e)}


class GraphFindCallersTool(BaseTool):
    """Tool to find caller functions/methods for a target."""

    name = "graph_find_callers"
    description = "Find all functions or methods that invoke or call the target function/method."
    input_schema = {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Path to project root"},
            "target": {"type": "string", "description": "Name of function or method"},
        },
        "required": ["project_path", "target"],
    }
    requires_confirmation = False

    async def execute(self, project_path: str, target: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            callers = knowledge_graph_service.find_callers(project_path, target)
            return {
                "success": True,
                "target": target,
                "caller_count": len(callers),
                "callers": [c.model_dump() for c in callers],
            }
        except Exception as e:
            logger.error(f"graph_find_callers tool error: {e}")
            return {"success": False, "error": str(e)}


class GraphExplainTool(BaseTool):
    """Tool to generate a human-readable structural summary of an entity."""

    name = "graph_explain"
    description = "Generate a structural summary explaining what an entity is, where it is, and its links."
    input_schema = {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Path to project root"},
            "target": {"type": "string", "description": "File path or symbol name"},
        },
        "required": ["project_path", "target"],
    }
    requires_confirmation = False

    async def execute(self, project_path: str, target: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            explanation = knowledge_graph_service.explain(project_path, target)
            return {
                "success": True,
                "target": target,
                "explanation": explanation,
            }
        except Exception as e:
            logger.error(f"graph_explain tool error: {e}")
            return {"success": False, "error": str(e)}


class GraphPathTool(BaseTool):
    """Tool to find the shortest relationship path between two symbols/files."""

    name = "graph_path"
    description = "Find the shortest relationship path between two symbols or files."
    input_schema = {
        "type": "object",
        "properties": {
            "project_path": {"type": "string", "description": "Path to project root"},
            "source": {"type": "string", "description": "Starting symbol or file name"},
            "target": {"type": "string", "description": "Target symbol or file name"},
        },
        "required": ["project_path", "source", "target"],
    }
    requires_confirmation = False

    async def execute(self, project_path: str, source: str, target: str, **kwargs: Any) -> Dict[str, Any]:
        try:
            path = knowledge_graph_service.find_path(project_path, source, target)
            return {
                "success": True,
                "source": source,
                "target": target,
                "path_length": len(path),
                "path": path,
            }
        except Exception as e:
            logger.error(f"graph_path tool error: {e}")
            return {"success": False, "error": str(e)}
