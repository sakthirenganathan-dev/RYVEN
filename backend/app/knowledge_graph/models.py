"""Domain models for RYVEN Knowledge Graph and Context Optimization Engine (M11.5)."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class NodeType(str, Enum):
    """Types of nodes in the knowledge graph."""
    PROJECT = "project"
    DIRECTORY = "directory"
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    IMPORT = "import"
    DEPENDENCY = "dependency"
    SYMBOL = "symbol"


class RelationType(str, Enum):
    """Types of relationships/edges in the knowledge graph."""
    CONTAINS = "CONTAINS"
    IMPORTS = "IMPORTS"
    CALLS = "CALLS"
    DEPENDS_ON = "DEPENDS_ON"
    DEFINES = "DEFINES"
    EXTENDS = "EXTENDS"
    IMPLEMENTS = "IMPLEMENTS"
    REFERENCES = "REFERENCES"
    EXPORTS = "EXPORTS"
    USED_BY = "USED_BY"


class GraphNode(BaseModel):
    """A single node in the knowledge graph."""
    id: str = Field(..., description="Unique node identifier, e.g. path:symbol")
    name: str = Field(..., description="Human-readable symbol or entity name")
    type: NodeType = Field(..., description="Node category")
    file_path: Optional[str] = Field(None, description="Relative path to source file if applicable")
    line_start: Optional[int] = Field(None, description="Starting line in source file (1-indexed)")
    line_end: Optional[int] = Field(None, description="Ending line in source file (1-indexed)")
    docstring: Optional[str] = Field(None, description="Docstring or comment summary")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary safe metadata")


class GraphEdge(BaseModel):
    """A directed edge between two nodes in the knowledge graph."""
    source: str = Field(..., description="ID of source node")
    target: str = Field(..., description="ID of target node")
    relation: RelationType = Field(..., description="Relation type")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence score")
    file_path: Optional[str] = Field(None, description="Source file where relation was discovered")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary safe metadata")


class FileIndexState(BaseModel):
    """State record for a single indexed file for incremental cache tracking."""
    rel_path: str
    mtime: float
    size: int
    sha256: str


class IndexState(BaseModel):
    """Overall state record for incremental indexing."""
    project_root: str
    last_indexed_at: str
    files: Dict[str, FileIndexState] = Field(default_factory=dict)


class GraphStatus(str, Enum):
    """Lifecycle and health status of project knowledge graph."""
    READY = "READY"
    STALE = "STALE"
    BUILDING = "BUILDING"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class GraphIndexMetadata(BaseModel):
    """Metadata describing the indexed knowledge graph."""
    project_id: str
    project_root: str
    status: GraphStatus = GraphStatus.READY
    graphify_version: str = "local-ast-v1"
    schema_version: str = "1.0.0"
    created_at: str
    updated_at: str
    source_file_count: int = 0
    node_count: int = 0
    edge_count: int = 0
    is_stale: bool = False
    semantic_mode_enabled: bool = False
    index_duration_ms: float = 0.0



class KnowledgeGraph(BaseModel):
    """Complete graph data structure."""
    metadata: GraphIndexMetadata
    nodes: Dict[str, GraphNode] = Field(default_factory=dict)
    edges: List[GraphEdge] = Field(default_factory=list)


class SymbolSearchResult(BaseModel):
    """Result of querying for a specific symbol."""
    symbol: str
    found: bool
    nodes: List[GraphNode] = Field(default_factory=list)
    callers: List[str] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    dependents: List[str] = Field(default_factory=list)


class GraphQueryResult(BaseModel):
    """Generic query result from the graph query engine."""
    query: str
    success: bool
    matched_nodes: List[GraphNode] = Field(default_factory=list)
    matched_edges: List[GraphEdge] = Field(default_factory=list)
    explanation: Optional[str] = None
    error: Optional[str] = None


class ContextBudgetResult(BaseModel):
    """Context measurement comparing baseline unoptimized context to graph-optimized context."""
    task: str
    baseline_files_count: int
    baseline_characters: int
    baseline_estimated_tokens: int
    optimized_files_count: int
    optimized_characters: int
    optimized_estimated_tokens: int
    character_reduction: int
    token_reduction: int
    reduction_percentage: float
    relevant_files: List[str] = Field(default_factory=list)
    relevant_symbols: List[str] = Field(default_factory=list)


class OptimizedContextPackage(BaseModel):
    """Compact context package generated for Qwen."""
    task: str
    summary: str
    relevant_files: List[str] = Field(default_factory=list)
    relevant_symbols: List[str] = Field(default_factory=list)
    dependency_overview: List[str] = Field(default_factory=list)
    source_excerpts: Dict[str, str] = Field(default_factory=dict)
    budget: ContextBudgetResult
