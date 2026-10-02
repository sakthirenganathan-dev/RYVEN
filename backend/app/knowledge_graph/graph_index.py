"""Knowledge Graph Index Manager (M11.5).

Handles full graph build, incremental updates, and cache state tracking.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from app.core.logging_config import logger
from app.knowledge_graph.cache import GraphCacheManager
from app.knowledge_graph.graphify_adapter import GraphifyAdapter
from app.knowledge_graph.models import (
    GraphEdge,
    GraphIndexMetadata,
    KnowledgeGraph,
    RelationType,
)

from app.knowledge_graph.security import KnowledgeGraphSecurity


class GraphIndexManager:
    """Orchestrates building, caching, and updating project knowledge graphs."""

    def __init__(self, semantic_mode_enabled: bool = False):
        self.adapter = GraphifyAdapter(semantic_mode_enabled=semantic_mode_enabled)

    def build_index(self, project_path: str, force: bool = False) -> KnowledgeGraph:
        """Perform a full build of the project knowledge graph and persist it."""
        valid_root = KnowledgeGraphSecurity.validate_project_root(project_path)
        cache_mgr = GraphCacheManager(valid_root)

        # Scan allowed source files
        file_states = cache_mgr.scan_current_files()
        file_list = sorted(list(file_states.keys()))

        logger.info(f"Building knowledge graph for '{valid_root}' ({len(file_list)} files)")
        graph = self.adapter.index_project_files(valid_root, file_list)

        # Persist graph, metadata, and index_state
        cache_mgr.save_graph(graph, file_states)
        return graph

    def update_index(self, project_path: str) -> KnowledgeGraph:
        """Perform incremental update on existing index.
        
        If no cache exists, delegates to build_index.
        Only re-indexes NEW and MODIFIED files; purges DELETED files.
        """
        valid_root = KnowledgeGraphSecurity.validate_project_root(project_path)
        cache_mgr = GraphCacheManager(valid_root)

        existing_graph = cache_mgr.load_graph()
        if not existing_graph:
            logger.info("No existing cache found for update. Running full build.")
            return self.build_index(project_path)

        new_files, modified_files, deleted_files, unchanged_files = cache_mgr.detect_changes()

        if not (new_files or modified_files or deleted_files):
            logger.info("Index is already up to date. Zero file modifications detected.")
            existing_graph.metadata.is_stale = False
            return existing_graph

        logger.info(
            f"Incrementally updating knowledge graph: "
            f"+{len(new_files)} new, ~{len(modified_files)} modified, -{len(deleted_files)} deleted"
        )

        root = Path(valid_root)
        files_to_remove = modified_files | deleted_files
        files_to_index = sorted(list(new_files | modified_files))

        # 1. Purge nodes and edges belonging to modified/deleted files
        retained_nodes = {
            nid: node
            for nid, node in existing_graph.nodes.items()
            if node.file_path not in files_to_remove
        }
        retained_edges = [
            edge
            for edge in existing_graph.edges
            if edge.file_path not in files_to_remove
        ]

        # 2. Re-index new and modified files
        new_nodes: Dict[str, Any] = {}
        new_edges: List[GraphEdge] = []
        proj_id = f"project:{root.name}"

        for rel_path in files_to_index:
            f_nodes, f_edges = self.adapter.analyze_file(root, rel_path)
            for node in f_nodes:
                new_nodes[node.id] = node
            new_edges.extend(f_edges)

            file_node_id = f"file:{rel_path}"
            if file_node_id in new_nodes:
                new_edges.append(GraphEdge(
                    source=proj_id,
                    target=file_node_id,
                    relation=RelationType.CONTAINS if hasattr(RelationType, "CONTAINS") else "CONTAINS",
                    file_path=rel_path,
                ))

        # 3. Merge nodes and edges
        merged_nodes = {**retained_nodes, **new_nodes}
        merged_edges = retained_edges + new_edges

        # De-duplicate edges
        seen: Set[str] = set()
        deduped_edges: List[GraphEdge] = []
        for e in merged_edges:
            k = f"{e.source}->{e.target}:{e.relation}"
            if k not in seen:
                seen.add(k)
                deduped_edges.append(e)

        # 4. Update metadata
        current_file_states = cache_mgr.scan_current_files()
        existing_graph.nodes = merged_nodes
        existing_graph.edges = deduped_edges
        existing_graph.metadata.source_file_count = len(current_file_states)
        existing_graph.metadata.node_count = len(merged_nodes)
        existing_graph.metadata.edge_count = len(deduped_edges)
        existing_graph.metadata.is_stale = False

        cache_mgr.save_graph(existing_graph, current_file_states)
        return existing_graph

    def get_or_build(self, project_path: str) -> KnowledgeGraph:
        """Retrieve cached graph, updating incrementally if stale, or building if missing."""
        valid_root = KnowledgeGraphSecurity.validate_project_root(project_path)
        cache_mgr = GraphCacheManager(valid_root)

        if not cache_mgr.has_cache():
            return self.build_index(valid_root)

        if cache_mgr.is_cache_stale():
            return self.update_index(valid_root)

        cached = cache_mgr.load_graph()
        if cached:
            return cached
        return self.build_index(valid_root)
