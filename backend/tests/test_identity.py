"""Unit tests for RYVEN system prompt and identity enforcement."""

from app.core.prompts import RYVEN_SYSTEM_PROMPT, get_system_prompt


def test_ryven_system_prompt_identity():
    """Verify system prompt contains core RYVEN identity and safety instructions."""
    prompt = get_system_prompt()

    assert "RYVEN" in prompt
    assert "personal ai assistant" in prompt.lower()
    # Must instruct not to introduce as Qwen
    assert "Qwen" in prompt
    assert "NEVER introduce yourself as Qwen" in prompt
    # Must forbid arbitrary shell execution
    assert "arbitrary" in prompt.lower()
    assert "registered tools" in prompt.lower()
    assert prompt == RYVEN_SYSTEM_PROMPT.strip()
