"""Tests for RYVEN 3.0 M15.1 Model Routing & Diagnostic API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.asyncio
async def test_api_models_hardware():
    """Verify GET /api/v1/models/hardware returns host hardware profile."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/models/hardware")
        assert res.status_code == 200
        data = res.json()
        assert "os_name" in data
        assert "ram_total_gb" in data
        assert "cpu_cores_physical" in data
        assert "recommended_tier" in data


@pytest.mark.asyncio
async def test_api_models_catalog():
    """Verify GET /api/v1/models/catalog returns registered models and HF catalog."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/models/catalog")
        assert res.status_code == 200
        data = res.json()
        assert "registered_models" in data
        assert "hf_catalog" in data
        assert any(m["id"] == "qwen2.5:7b" for m in data["registered_models"])


@pytest.mark.asyncio
async def test_api_models_providers():
    """Verify GET /api/v1/models/providers returns health of all configured providers."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/models/providers")
        assert res.status_code == 200
        data = res.json()
        assert data["provider"] == "unified"
        assert data["active_default"] == "ollama"
        assert "providers" in data
        assert "ollama" in data["providers"]
        assert "grok" in data["providers"]
        assert "huggingface_remote" in data["providers"]
        assert "huggingface_local" in data["providers"]


@pytest.mark.asyncio
async def test_api_models_route_explainable_decision():
    """Verify GET /api/v1/models/route returns deterministic explainable routing decision."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Planning task -> Qwen
        res = await client.get("/api/v1/models/route?task_type=PLANNING")
        assert res.status_code == 200
        data = res.json()
        assert data["task_type"] == "PLANNING"
        assert data["selected_provider"] == "OLLAMA"
        assert data["selected_model"] == "qwen2.5:7b"
        assert data["local"] is True
        assert data["remote_allowed"] is False
        assert "reason" in data
        assert "fallback" in data
