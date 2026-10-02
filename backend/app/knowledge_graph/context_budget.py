"""Context Budget Manager for RYVEN (M11.5).

Measures and compares prompt context size between unoptimized baseline and
graph-optimized context packages.
"""

from typing import Dict, List, Optional
from app.knowledge_graph.models import ContextBudgetResult


class ContextBudgetManager:
    """Calculates and tracks measured context size reductions."""

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Estimate token count for a text string using 4 chars per token rule of thumb.
        
        Tokenizers typically average ~3.5 to 4 characters per token for English code.
        We explicitly label this 'estimated_tokens'.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)

    @classmethod
    def calculate_budget(
        cls,
        task: str,
        baseline_files: Dict[str, str],
        optimized_files: Dict[str, str],
        relevant_symbols: Optional[List[str]] = None,
    ) -> ContextBudgetResult:
        """Measure context reduction between baseline and optimized file/excerpt sets.
        
        baseline_files: {rel_path: full_content}
        optimized_files: {rel_path: excerpt_or_targeted_content}
        """
        # Baseline measurements
        baseline_chars = sum(len(content) for content in baseline_files.values())
        baseline_tokens = sum(cls.estimate_tokens(content) for content in baseline_files.values())
        baseline_count = len(baseline_files)

        # Optimized measurements
        optimized_chars = sum(len(content) for content in optimized_files.values())
        optimized_tokens = sum(cls.estimate_tokens(content) for content in optimized_files.values())
        optimized_count = len(optimized_files)

        # Reductions
        char_reduction = max(0, baseline_chars - optimized_chars)
        token_reduction = max(0, baseline_tokens - optimized_tokens)

        if baseline_tokens > 0:
            reduction_pct = round((token_reduction / baseline_tokens) * 100.0, 2)
        else:
            reduction_pct = 0.0

        return ContextBudgetResult(
            task=task,
            baseline_files_count=baseline_count,
            baseline_characters=baseline_chars,
            baseline_estimated_tokens=baseline_tokens,
            optimized_files_count=optimized_count,
            optimized_characters=optimized_chars,
            optimized_estimated_tokens=optimized_tokens,
            character_reduction=char_reduction,
            token_reduction=token_reduction,
            reduction_percentage=reduction_pct,
            relevant_files=sorted(list(optimized_files.keys())),
            relevant_symbols=relevant_symbols or [],
        )
