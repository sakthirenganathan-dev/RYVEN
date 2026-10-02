"""Deterministic Graph Query Engine for RYVEN (M11.5).

Fast, graph-theoretic traversal without heavy LLM inference overhead.
"""

from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from app.knowledge_graph.models import (
    GraphEdge,
    GraphNode,
    GraphQueryResult,
    KnowledgeGraph,
    NodeType,
    RelationType,
    SymbolSearchResult,
)
from app.knowledge_graph.security import KnowledgeGraphSecurity


class GraphQueryEngine:
    """Provides deterministic graph queries and relationship traversals."""

    def __init__(self, graph: KnowledgeGraph):
        self.graph = graph
        self._build_indexes()

    def _build_indexes(self):
        """Construct fast adjacency lists and symbol lookup maps."""
        self.outgoing: Dict[str, List[GraphEdge]] = {}
        self.incoming: Dict[str, List[GraphEdge]] = {}
        self.name_to_nodes: Dict[str, List[str]] = {}
        self.file_to_nodes: Dict[str, List[str]] = {}

        for nid, node in self.graph.nodes.items():
            self.name_to_nodes.setdefault(node.name.lower(), []).append(nid)
            if "." in node.name:
                short_name = node.name.split(".")[-1].lower()
                self.name_to_nodes.setdefault(short_name, []).append(nid)
            if node.file_path:
                self.file_to_nodes.setdefault(node.file_path.lower(), []).append(nid)


        for edge in self.graph.edges:
            self.outgoing.setdefault(edge.source, []).append(edge)
            self.incoming.setdefault(edge.target, []).append(edge)

    def find_symbol(self, symbol_name: str) -> SymbolSearchResult:
        """Find matching symbol definitions, callers, and dependencies."""
        safe_sym = KnowledgeGraphSecurity.validate_safe_input_text(symbol_name)
        sym_lower = safe_sym.lower()

        matched_nids: Set[str] = set()

        # Exact match
        if sym_lower in self.name_to_nodes:
            matched_nids.update(self.name_to_nodes[sym_lower])

        # Partial/contains match if exact was empty or for richer matches
        if not matched_nids:
            for name, nids in self.name_to_nodes.items():
                if sym_lower in name:
                    matched_nids.update(nids)

        if not matched_nids:
            return SymbolSearchResult(
                symbol=safe_sym,
                found=False,
                nodes=[],
                callers=[],
                dependencies=[],
                dependents=[],
            )

        nodes = [self.graph.nodes[nid] for nid in matched_nids if nid in self.graph.nodes]
        callers: Set[str] = set()
        dependencies: Set[str] = set()
        dependents: Set[str] = set()

        for nid in matched_nids:
            # Callers: incoming CALLS edges
            for edge in self.incoming.get(nid, []):
                if edge.relation == RelationType.CALLS:
                    caller_node = self.graph.nodes.get(edge.source)
                    callers.add(caller_node.name if caller_node else edge.source)
                dependents.add(edge.source)

            # Dependencies: outgoing IMPORTS, CALLS, EXTENDS, DEPENDS_ON
            for edge in self.outgoing.get(nid, []):
                dep_node = self.graph.nodes.get(edge.target)
                dependencies.add(dep_node.name if dep_node else edge.target)

        return SymbolSearchResult(
            symbol=safe_sym,
            found=True,
            nodes=nodes,
            callers=sorted(list(callers)),
            dependencies=sorted(list(dependencies)),
            dependents=sorted(list(dependents)),
        )

    def find_dependencies(self, target: str) -> List[GraphEdge]:
        """Find all direct dependencies of target file or symbol."""
        safe_target = KnowledgeGraphSecurity.validate_safe_input_text(target)
        target_lower = safe_target.lower()

        relevant_nids: Set[str] = set()

        # Check by file
        if target_lower in self.file_to_nodes:
            relevant_nids.update(self.file_to_nodes[target_lower])
        # Check by symbol name
        if target_lower in self.name_to_nodes:
            relevant_nids.update(self.name_to_nodes[target_lower])
        # Direct node id match
        if target in self.graph.nodes:
            relevant_nids.add(target)

        deps: List[GraphEdge] = []
        for nid in relevant_nids:
            for edge in self.outgoing.get(nid, []):
                if edge.relation in {
                    RelationType.IMPORTS,
                    RelationType.CALLS,
                    RelationType.DEPENDS_ON,
                    RelationType.EXTENDS,
                    RelationType.REFERENCES,
                }:
                    deps.append(edge)

        return deps

    def find_dependents(self, target: str) -> List[GraphEdge]:
        """Find all nodes that depend on or reference the target."""
        safe_target = KnowledgeGraphSecurity.validate_safe_input_text(target)
        target_lower = safe_target.lower()

        relevant_nids: Set[str] = set()
        if target_lower in self.file_to_nodes:
            relevant_nids.update(self.file_to_nodes[target_lower])
        if target_lower in self.name_to_nodes:
            relevant_nids.update(self.name_to_nodes[target_lower])
        if target in self.graph.nodes:
            relevant_nids.add(target)

        deps: List[GraphEdge] = []
        for nid in relevant_nids:
            for edge in self.incoming.get(nid, []):
                deps.append(edge)

        return deps

    def find_callers(self, target: str) -> List[GraphNode]:
        """Find all functions/methods that call the specified target."""
        safe_target = KnowledgeGraphSecurity.validate_safe_input_text(target)
        target_lower = safe_target.lower()

        target_nids = self.name_to_nodes.get(target_lower, [])
        if target in self.graph.nodes:
            target_nids.append(target)

        caller_nodes: List[GraphNode] = []
        seen: Set[str] = set()

        for tid in target_nids:
            for edge in self.incoming.get(tid, []):
                if edge.relation == RelationType.CALLS and edge.source not in seen:
                    seen.add(edge.source)
                    if edge.source in self.graph.nodes:
                        caller_nodes.append(self.graph.nodes[edge.source])

        return caller_nodes

    def find_path(self, source_name: str, target_name: str) -> List[str]:
        """Find shortest connection path between two symbols/files using BFS."""
        src_clean = KnowledgeGraphSecurity.validate_safe_input_text(source_name).lower()
        tgt_clean = KnowledgeGraphSecurity.validate_safe_input_text(target_name).lower()

        src_nids = self.name_to_nodes.get(src_clean) or self.file_to_nodes.get(src_clean, [])
        tgt_nids = set(self.name_to_nodes.get(tgt_clean) or self.file_to_nodes.get(tgt_clean, []))

        if not src_nids or not tgt_nids:
            return []

        # BFS queue: (current_node_id, [path_of_node_ids])
        start_node = src_nids[0]
        queue = deque([(start_node, [start_node])])
        visited = {start_node}

        while queue:
            curr, path = queue.popleft()
            if curr in tgt_nids:
                return [self.graph.nodes[nid].name if nid in self.graph.nodes else nid for nid in path]

            for edge in self.outgoing.get(curr, []):
                nxt = edge.target
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append((nxt, path + [nxt]))

        return []

    def explain(self, target: str) -> str:
        """Provide a human-readable structural summary of a symbol or file."""
        safe_target = KnowledgeGraphSecurity.validate_safe_input_text(target)
        sym_res = self.find_symbol(safe_target)

        if not sym_res.found:
            return f"Symbol or file '{safe_target}' was not found in the project knowledge graph."

        lines = [f"### Structural Knowledge: {safe_target}"]
        for node in sym_res.nodes:
            lines.append(f"- **Type**: `{node.type.value}`")
            lines.append(f"- **File**: `{node.file_path or 'root'}` (lines {node.line_start}-{node.line_end})")
            if node.docstring:
                lines.append(f"- **Documentation**: {node.docstring.strip()}")

        if sym_res.dependencies:
            lines.append(f"- **Dependencies**: {', '.join(sym_res.dependencies[:10])}")
        if sym_res.callers:
            lines.append(f"- **Called By**: {', '.join(sym_res.callers[:10])}")
        if sym_res.dependents:
            lines.append(f"- **Referenced By**: {', '.join(sym_res.dependents[:10])}")

        return "\n".join(lines)

    def query(self, natural_query: str) -> GraphQueryResult:
        """Deterministic keyword-based graph search."""
        safe_q = KnowledgeGraphSecurity.validate_safe_input_text(natural_query)
        words = [w.lower() for w in safe_q.split() if len(w) > 2]

        matched_nids: Set[str] = set()
        for word in words:
            for name, nids in self.name_to_nodes.items():
                if word in name:
                    matched_nids.update(nids)

        matched_nodes = [self.graph.nodes[nid] for nid in matched_nids if nid in self.graph.nodes]
        matched_edges: List[GraphEdge] = []
        for nid in matched_nids:
            matched_edges.extend(self.outgoing.get(nid, [])[:5])

        explanation = f"Found {len(matched_nodes)} matching code structures for query: '{safe_q}'"
        return GraphQueryResult(
            query=safe_q,
            success=True,
            matched_nodes=matched_nodes[:20],
            matched_edges=matched_edges[:20],
            explanation=explanation,
        )
