"""RYVEN 3.0 — Runtime Concurrency Control (M15.3.9).

Enforces bounded concurrency for compute-heavy subsystems (LLM inference, project builds,
browser sessions, and knowledge graph writes) to prevent resource exhaustion and thrashing.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, Optional


class ConcurrencyController:
    """Bounded semaphore controller for resource-heavy operations."""

    def __init__(
        self,
        max_concurrent_llm: int = 2,
        max_concurrent_builds: int = 1,
        max_concurrent_browser: int = 2,
        max_concurrent_graph_writes: int = 1,
    ) -> None:
        self.max_concurrent_llm = max_concurrent_llm
        self.max_concurrent_builds = max_concurrent_builds
        self.max_concurrent_browser = max_concurrent_browser
        self.max_concurrent_graph_writes = max_concurrent_graph_writes

        # Semaphores initialized lazily or on instantiation
        self._llm_sem = asyncio.Semaphore(max_concurrent_llm)
        self._build_sem = asyncio.Semaphore(max_concurrent_builds)
        self._browser_sem = asyncio.Semaphore(max_concurrent_browser)
        self._graph_sem = asyncio.Semaphore(max_concurrent_graph_writes)

    @asynccontextmanager
    async def limit_llm(self, timeout: Optional[float] = None) -> AsyncIterator[None]:
        """Limit concurrent local LLM inference generations."""
        if timeout:
            await asyncio.wait_for(self._llm_sem.acquire(), timeout=timeout)
        else:
            await self._llm_sem.acquire()
        try:
            yield
        finally:
            self._llm_sem.release()

    @asynccontextmanager
    async def limit_build(self, timeout: Optional[float] = None) -> AsyncIterator[None]:
        """Limit concurrent project compilation or heavy test executions."""
        if timeout:
            await asyncio.wait_for(self._build_sem.acquire(), timeout=timeout)
        else:
            await self._build_sem.acquire()
        try:
            yield
        finally:
            self._build_sem.release()

    @asynccontextmanager
    async def limit_browser(self, timeout: Optional[float] = None) -> AsyncIterator[None]:
        """Limit concurrent Playwright browser sessions."""
        if timeout:
            await asyncio.wait_for(self._browser_sem.acquire(), timeout=timeout)
        else:
            await self._browser_sem.acquire()
        try:
            yield
        finally:
            self._browser_sem.release()

    @asynccontextmanager
    async def limit_graph_write(self, timeout: Optional[float] = None) -> AsyncIterator[None]:
        """Limit concurrent knowledge graph indexing / disk modifications."""
        if timeout:
            await asyncio.wait_for(self._graph_sem.acquire(), timeout=timeout)
        else:
            await self._graph_sem.acquire()
        try:
            yield
        finally:
            self._graph_sem.release()

    def get_status(self) -> Dict[str, Any]:
        """Return configured concurrency limits."""
        return {
            "max_concurrent_llm": self.max_concurrent_llm,
            "max_concurrent_builds": self.max_concurrent_builds,
            "max_concurrent_browser": self.max_concurrent_browser,
            "max_concurrent_graph_writes": self.max_concurrent_graph_writes,
        }


# Global default singleton
concurrency_controller = ConcurrencyController()
