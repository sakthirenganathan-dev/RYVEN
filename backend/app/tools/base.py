"""Abstract BaseTool definition for RYVEN registered tools."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseTool(ABC):
    """Abstract base class for all safe registered RYVEN tools.

    Security Principle:
    - Tools must perform strictly scoped, programmatic actions.
    - Tools must NEVER allow unvetted arbitrary command line / shell execution.
    - Future mutating or dangerous tools must flag requires_confirmation=True.
    """

    name: str
    description: str
    input_schema: Dict[str, Any] = {}
    requires_confirmation: bool = False

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Dict[str, Any]:
        """Execute the tool action safely and return structured results."""
        raise NotImplementedError("Tools must implement an execute method")

    def get_info(self) -> Dict[str, Any]:
        """Return metadata describing the tool."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "requires_confirmation": self.requires_confirmation,
        }
