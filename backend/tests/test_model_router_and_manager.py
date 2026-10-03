"""Tests for RYVEN 3.0 M15.1 Local-First Model Router & Hugging Face Model Manager."""

import pytest
from app.ai.hardware import HardwareDiagnostics, HardwareProfile
from app.ai.hf_manager import HuggingFaceModelManager
from app.ai.models import ModelProfile, ModelProvider, RoutingDecision, TaskType
from app.ai.registry import ModelRegistry
from app.ai.router import ModelRouter
from app.ai.security import ModelSecurityPolicy, ModelSecurityViolationError


@pytest.mark.asyncio
async def test_hardware_detection_safety():
    """Verify hardware diagnostics runs safely and returns a well-formed profile."""
    profile = await HardwareDiagnostics.get_profile(force_refresh=True)

    assert isinstance(profile, HardwareProfile)
    assert profile.os_name is not None
    assert profile.cpu_cores_physical >= 1
    assert profile.ram_total_gb > 0
    assert profile.disk_free_gb > 0
    assert profile.recommended_tier in ("3B", "7B", "14B")


def test_model_registry_initialization():
    """Verify default model profiles are correctly seeded and queried."""
    registry = ModelRegistry()

    qwen = registry.get_model("qwen2.5:7b")
    assert qwen is not None
    assert qwen.provider == ModelProvider.OLLAMA
    assert TaskType.GENERAL_REASONING in qwen.task_types
    assert TaskType.PLANNING in qwen.task_types
    assert qwen.local_or_remote == "local"
    assert qwen.enabled is True

    # Remote model profile must be disabled and forbidden from sensitive data by default
    hf_remote = registry.get_model("hf-remote-qwen-coder")
    assert hf_remote is not None
    assert hf_remote.provider == ModelProvider.HUGGINGFACE_REMOTE
    assert hf_remote.enabled is False
    assert hf_remote.sensitive_data_allowed is False


def test_model_registry_filtering():
    """Verify filtering models by provider and task type."""
    registry = ModelRegistry()

    # List all OLLAMA models
    ollama_models = registry.list_models(provider=ModelProvider.OLLAMA)
    assert any(m.id == "qwen2.5:7b" for m in ollama_models)

    # List all planning models
    planning_models = registry.list_models(task_type=TaskType.PLANNING)
    assert any(m.id == "qwen2.5:7b" for m in planning_models)

    # Register dynamic model
    custom = ModelProfile(
        id="custom-embed",
        provider=ModelProvider.OLLAMA,
        task_types=[TaskType.EMBEDDING],
        local_or_remote="local",
        model_name="bge-small",
        memory_estimate_gb=0.4,
        installed=True,
    )
    registry.register_model(custom)
    embeds = registry.list_models(task_type=TaskType.EMBEDDING, installed_only=True)
    assert any(m.id == "custom-embed" for m in embeds)


@pytest.mark.asyncio
async def test_deterministic_default_qwen_routing():
    """Verify default routing assigns Qwen 2.5 7B to reasoning, planning, and code."""
    router = ModelRouter()

    for task in [
        TaskType.GENERAL_REASONING,
        TaskType.PLANNING,
        TaskType.CODE,
        TaskType.CLASSIFICATION,
    ]:
        decision = await router.route(task_type=task, prompt="Write a hello world function")
        assert decision.selected_model == "qwen2.5:7b"
        assert decision.provider == "OLLAMA"
        assert decision.local_or_remote == "local"
        assert decision.remote_allowed is False


@pytest.mark.asyncio
async def test_vision_and_ocr_fallback_behavior():
    """Verify vision and OCR tasks route to installed local models or fallback to Qwen."""
    registry = ModelRegistry()
    # By default, llava:7b is not installed in the test environment
    router = ModelRouter(registry=registry)

    # Vision fallback when not installed
    v_decision = await router.route(task_type=TaskType.VISION)
    assert v_decision.selected_model == "qwen2.5:7b"
    assert "falling back" in v_decision.reason.lower()

    # Simulate installing a local vision model
    registry.set_installed("llava:7b", True)
    v_decision_installed = await router.route(task_type=TaskType.VISION)
    assert v_decision_installed.selected_model == "llava:7b"
    assert v_decision_installed.provider == "OLLAMA"


@pytest.mark.asyncio
async def test_remote_inference_disabled_by_default():
    """Verify remote models are strictly blocked when remote inference is disabled."""
    security = ModelSecurityPolicy(allow_remote_inference=False)
    registry = ModelRegistry()

    remote_profile = registry.get_model("hf-remote-qwen-coder")
    assert remote_profile is not None

    with pytest.raises(ModelSecurityViolationError) as exc_info:
        await security.validate_invocation(remote_profile, prompt="Explain quicksort")

    assert "remote inference is disabled" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_sensitive_data_protection_blocks_remote_transmission():
    """Verify secrets, api keys, and .env contents are blocked from remote models even if remote is enabled."""
    security = ModelSecurityPolicy(allow_remote_inference=True)
    registry = ModelRegistry()

    remote_profile = registry.get_model("hf-remote-qwen-coder")
    assert remote_profile is not None

    sensitive_prompts = [
        "Here is my secret token: ghp_123456789012345678901234567890123456 please check my repo",
        "My AWS api_key = AKIAIOSFODNN7EXAMPLE and secret_key = 1234567890abcdef",
        "Here is the content of my .env file with DATABASE_URL=postgres://...",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA0...",
    ]

    for sp in sensitive_prompts:
        with pytest.raises(ModelSecurityViolationError) as exc_info:
            await security.validate_invocation(remote_profile, prompt=sp)
        assert "security policy blocked" in str(exc_info.value).lower() or "sensitive" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_huggingface_model_manager_catalog_and_compatibility():
    """Verify HF model catalog queries and hardware compatibility assessments."""
    manager = HuggingFaceModelManager()
    catalog = manager.get_catalog()

    assert len(catalog) >= 3
    qwen_coder = manager.get_catalog_item("Qwen/Qwen2.5-Coder-1.5B-Instruct")
    assert qwen_coder is not None
    assert qwen_coder.parameter_size == "1.5B"

    # Compatibility check against host hardware
    compat = await manager.check_compatibility("Qwen/Qwen2.5-Coder-1.5B-Instruct")
    assert "compatible" in compat
    assert "ram_ok" in compat
    assert "disk_ok" in compat


@pytest.mark.asyncio
async def test_unavailable_model_handling_and_fallback():
    """Verify requesting an invalid or unavailable preferred model falls back safely."""
    router = ModelRouter()

    decision = await router.route(
        task_type=TaskType.GENERAL_REASONING,
        prompt="Tell me a joke",
        preferred_model_id="non-existent-model-xyz",
    )

    # Must fall back gracefully to default Qwen 2.5 7B
    assert decision.selected_model == "qwen2.5:7b"
    assert decision.provider == "OLLAMA"
