"""Cache and incremental file state manager for Knowledge Graph (M11.5)."""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from app.core.logging_config import logger
from app.knowledge_graph.models import (
    FileIndexState,
    GraphIndexMetadata,
    IndexState,
    KnowledgeGraph,
)
from app.knowledge_graph.security import KnowledgeGraphSecurity


class GraphCacheManager:
    """Manages persistence, loading, and incremental file change detection for Knowledge Graphs."""

    CACHE_SUBDIR = Path(".ryven") / "knowledge_graph"
    GRAPH_FILE = "graph.json"
    METADATA_FILE = "metadata.json"
    INDEX_STATE_FILE = "index_state.json"

    def __init__(self, project_root: str):
        self.project_root = Path(KnowledgeGraphSecurity.validate_project_root(project_root))
        self.cache_dir = self.project_root / self.CACHE_SUBDIR

    def _ensure_cache_dir(self) -> Path:
        """Create cache directory if it does not exist."""
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Ensure a .gitignore exists inside .ryven so graph cache is never committed by accident
        ryven_dir = self.project_root / ".ryven"
        gitignore_file = ryven_dir / ".gitignore"
        if not gitignore_file.exists():
            try:
                gitignore_file.write_text("*\n", encoding="utf-8")
            except Exception as e:
                logger.debug(f"Could not write .gitignore in .ryven: {e}")
        return self.cache_dir

    def get_file_hash(self, file_path: Path) -> str:
        """Compute sha256 checksum of a single source file safely."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()

    def scan_current_files(self) -> Dict[str, FileIndexState]:
        """Scan project directory and compute state for all eligible source files."""
        current_state: Dict[str, FileIndexState] = {}
        for root, dirs, files in os.walk(self.project_root):
            # Prune excluded directories in-place
            dirs[:] = [d for d in dirs if not KnowledgeGraphSecurity.is_excluded_dir(d)]

            for file in files:
                if KnowledgeGraphSecurity.is_sensitive_file(file):
                    continue

                full_path = Path(root) / file
                # Verify containment
                if not KnowledgeGraphSecurity.is_safe_relative_path(self.project_root, full_path):
                    continue

                try:
                    rel_path = str(full_path.relative_to(self.project_root)).replace("\\", "/")
                    stat = full_path.stat()
                    # Only index code/config files (.py, .js, .ts, .tsx, .jsx, .json, .html, .css, .md, .sql)
                    suffix = full_path.suffix.lower()
                    if suffix not in {
                        ".py", ".ts", ".tsx", ".js", ".jsx", ".json",
                        ".html", ".css", ".md", ".sql", ".sh", ".yaml", ".yml"
                    }:
                        continue

                    file_hash = self.get_file_hash(full_path)
                    current_state[rel_path] = FileIndexState(
                        rel_path=rel_path,
                        mtime=stat.st_mtime,
                        size=stat.st_size,
                        sha256=file_hash,
                    )
                except Exception as e:
                    logger.warning(f"Failed to read file state for {full_path}: {e}")

        return current_state

    def detect_changes(self) -> Tuple[Set[str], Set[str], Set[str], Set[str]]:
        """Compare current file system state against saved index_state.json.
        
        Returns:
            (new_files, modified_files, deleted_files, unchanged_files)
        """
        previous_state = self.load_index_state()
        current_state = self.scan_current_files()

        if not previous_state:
            # All current files are new
            return set(current_state.keys()), set(), set(), set()

        prev_files = previous_state.files
        curr_keys = set(current_state.keys())
        prev_keys = set(prev_files.keys())

        new_files = curr_keys - prev_keys
        deleted_files = prev_keys - curr_keys
        modified_files: Set[str] = set()
        unchanged_files: Set[str] = set()

        for common in curr_keys & prev_keys:
            curr_item = current_state[common]
            prev_item = prev_files[common]
            # Fast check mtime & size first; if changed check hash
            if curr_item.size != prev_item.size or curr_item.sha256 != prev_item.sha256:
                modified_files.add(common)
            else:
                unchanged_files.add(common)

        return new_files, modified_files, deleted_files, unchanged_files

    def is_cache_stale(self) -> bool:
        """Return True if any files have been added, modified, or deleted since last index."""
        if not self.has_cache():
            return True
        new_f, mod_f, del_f, _ = self.detect_changes()
        return bool(new_f or mod_f or del_f)

    def has_cache(self) -> bool:
        """Check if cached graph files exist on disk."""
        return (
            (self.cache_dir / self.GRAPH_FILE).exists()
            and (self.cache_dir / self.METADATA_FILE).exists()
            and (self.cache_dir / self.INDEX_STATE_FILE).exists()
        )

    def load_graph(self) -> Optional[KnowledgeGraph]:
        """Load and parse cached KnowledgeGraph from disk."""
        if not self.has_cache():
            return None
        try:
            graph_path = self.cache_dir / self.GRAPH_FILE
            data = json.loads(graph_path.read_text(encoding="utf-8"))
            graph = KnowledgeGraph.model_validate(data)
            # Annotate if cache is stale
            graph.metadata.is_stale = self.is_cache_stale()
            return graph
        except Exception as e:
            logger.error(f"Failed to load cached knowledge graph from {self.cache_dir}: {e}")
            return None

    def load_index_state(self) -> Optional[IndexState]:
        """Load saved file index state."""
        state_path = self.cache_dir / self.INDEX_STATE_FILE
        if not state_path.exists():
            return None
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            return IndexState.model_validate(data)
        except Exception as e:
            logger.warning(f"Failed to load index state: {e}")
            return None

    def save_graph(self, graph: KnowledgeGraph, file_states: Optional[Dict[str, FileIndexState]] = None) -> None:
        """Persist graph, metadata, and index state to .ryven/knowledge_graph/."""
        self._ensure_cache_dir()

        # Update metadata timestamp
        graph.metadata.updated_at = datetime.now(timezone.utc).isoformat()
        graph.metadata.node_count = len(graph.nodes)
        graph.metadata.edge_count = len(graph.edges)

        # 1. Save graph.json
        graph_path = self.cache_dir / self.GRAPH_FILE
        graph_path.write_text(graph.model_dump_json(indent=2), encoding="utf-8")

        # 2. Save metadata.json
        meta_path = self.cache_dir / self.METADATA_FILE
        meta_path.write_text(graph.metadata.model_dump_json(indent=2), encoding="utf-8")

        # 3. Save index_state.json
        if file_states is None:
            file_states = self.scan_current_files()

        index_state = IndexState(
            project_root=str(self.project_root),
            last_indexed_at=datetime.now(timezone.utc).isoformat(),
            files=file_states,
        )
        state_path = self.cache_dir / self.INDEX_STATE_FILE
        state_path.write_text(index_state.model_dump_json(indent=2), encoding="utf-8")
        logger.info(f"Successfully saved knowledge graph ({len(graph.nodes)} nodes, {len(graph.edges)} edges) to {self.cache_dir}")
