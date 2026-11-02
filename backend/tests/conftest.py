"""Pytest configuration and isolation fixtures for RYVEN backend tests."""

import pytest
from app.ai.registry import model_registry


@pytest.fixture(autouse=True)
def reset_model_registry_defaults():
    """Ensure global model_registry returns to clean initial seed after each test."""
    yield
    model_registry._models.clear()
    model_registry._seed_default_catalog()


@pytest.fixture(autouse=True)
def reset_open_app_tool_state():
    """Ensure OpenApplicationTool idempotency state is cleanly reset between tests."""
    from app.tools.app_tool import OpenApplicationTool
    OpenApplicationTool.reset_state()
    yield
    OpenApplicationTool.reset_state()
