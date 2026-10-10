"""RYVEN 3.0 — M17.10 Phase 2 Multi-Model Streaming & Response Chunking Tests.

Verifies the streaming contract (AIStreamChunk, StreamEventType, StreamChunkSanitizer),
Ollama incremental streaming, boundary secret and <think> tag redaction,
error normalization, cancellation, Grok & Hugging Face unsupported streaming handling,
and non-streaming backward compatibility.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.ai.adapters import GrokAdapter, HuggingFaceAdapter, OllamaAdapter
from app.ai.contracts import (
    AIProviderError,
    AIRequest,
    AIResponse,
    AIStreamChunk,
    AIUsage,
    ProviderTimeoutError,
    ProviderUnavailableError,
    StreamChunkSanitizer,
    StreamEventType,
    redact_secrets,
)
from app.ai.grok import GrokProvider
from app.ai.hf_local import HuggingFaceLocalProvider
from app.ai.hf_remote import HuggingFaceRemoteProvider
from app.ai.models import ModelProvider
from app.ai.ollama import OllamaProvider, OllamaUnavailableError
from app.ai.provider import ChatMessage


# ============================================================================
# 1. STREAM EVENT MODEL & CONTRACT TESTS
# ============================================================================

def test_stream_event_types_enum():
    assert StreamEventType.START == "start"
    assert StreamEventType.DELTA == "delta"
    assert StreamEventType.DONE == "done"
    assert StreamEventType.ERROR == "error"


def test_ai_stream_chunk_creation_and_properties():
    usage = AIUsage(prompt_tokens=15, completion_tokens=45, total_tokens=60)
    chunk = AIStreamChunk(
        event_type=StreamEventType.DONE,
        delta="",
        model_id="qwen2.5:7b",
        provider=ModelProvider.OLLAMA,
        finish_reason="stop",
        usage=usage,
        latency_ms=150.2,
        metadata={"total_duration": 12345},
    )
    assert chunk.is_done is True
    assert chunk.is_delta is False
    assert chunk.is_start is False
    assert chunk.is_error is False
    assert chunk.model == "qwen2.5:7b"
    assert chunk.finish_reason == "stop"
    assert chunk.usage.total_tokens == 60


def test_ai_stream_chunk_safe_repr():
    chunk = AIStreamChunk(
        event_type=StreamEventType.DELTA,
        delta="Secret Bearer super_secret_token_12345678",
        model_id="qwen2.5:7b",
        provider=ModelProvider.OLLAMA,
        metadata={"token": "raw_token_xyz"},
    )
    repr_str = repr(chunk)
    assert "super_secret_token_12345678" not in repr_str
    assert "qwen2.5:7b" in repr_str
    assert "delta" in repr_str


# ============================================================================
# 2. STREAM CHUNK SANITIZER TESTS (<think> & BOUNDARY SECRETS)
# ============================================================================

def test_sanitizer_normal_text_passthrough():
    sanitizer = StreamChunkSanitizer()
    res1 = sanitizer.feed("Hello ")
    res2 = sanitizer.feed("world!")
    res3 = sanitizer.flush()
    assert res1 + res2 + res3 == "Hello world!"


def test_sanitizer_think_tag_complete():
    sanitizer = StreamChunkSanitizer()
    out = sanitizer.feed("Analysis: <think>This is deep internal reasoning</think>Result is 42.")
    out += sanitizer.flush()
    assert out == "Analysis: Result is 42."
    assert "internal reasoning" not in out


def test_sanitizer_think_tag_split_open():
    sanitizer = StreamChunkSanitizer()
    chunk1 = sanitizer.feed("Start: <th")
    chunk2 = sanitizer.feed("ink>hidden thoughts</think> Finished.")
    chunk3 = sanitizer.flush()
    combined = chunk1 + chunk2 + chunk3
    assert combined == "Start:  Finished."
    assert "hidden thoughts" not in combined


def test_sanitizer_think_tag_split_close():
    sanitizer = StreamChunkSanitizer()
    chunk1 = sanitizer.feed("Start: <think>hidden thoughts</th")
    chunk2 = sanitizer.feed("ink> Done.")
    chunk3 = sanitizer.flush()
    combined = chunk1 + chunk2 + chunk3
    assert combined == "Start:  Done."
    assert "hidden thoughts" not in combined


def test_sanitizer_think_unclosed_discarded_on_flush():
    sanitizer = StreamChunkSanitizer()
    chunk1 = sanitizer.feed("Text before <think>Unfinished thought")
    flushed = sanitizer.flush()
    assert chunk1 == "Text before "
    assert flushed == ""
    assert "Unfinished thought" not in (chunk1 + flushed)


def test_sanitizer_secret_split_bearer():
    sanitizer = StreamChunkSanitizer()
    # "Bearer " is split across two chunks: "Bear" in chunk 1, "er secret_tok_123456 " in chunk 2
    chunk1 = sanitizer.feed("Auth header: Bear")
    chunk2 = sanitizer.feed("er secret_tok_123456 and more text.")
    flushed = sanitizer.flush()
    combined = chunk1 + chunk2 + flushed
    assert "secret_tok_123456" not in combined
    assert "[REDACTED]" in combined
    assert "Auth header: Bearer [REDACTED] and more text." in combined


def test_sanitizer_secret_split_confirmation_token():
    sanitizer = StreamChunkSanitizer()
    chunk1 = sanitizer.feed("Approved with confirmation_token=")
    chunk2 = sanitizer.feed("a1b2c3d4e5f60718293a4b5c6d7e8f90 confirmed.")
    flushed = sanitizer.flush()
    combined = chunk1 + chunk2 + flushed
    assert "a1b2c3d4e5f60718293a4b5c6d7e8f90" not in combined
    assert "[REDACTED]" in combined


def test_sanitizer_empty_and_whitespace():
    sanitizer = StreamChunkSanitizer()
    assert sanitizer.feed("") == ""
    assert sanitizer.feed("   ") == "   "
    assert sanitizer.flush() == ""


# ============================================================================
# 3. OLLAMA TRANSPORT STREAMING TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_ollama_provider_stream_incremental():
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:7b")

    mock_lines = [
        '{"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "Hello "}, "done": false}',
        '{"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "world!"}, "done": false}',
        '{"model": "qwen2.5:7b", "message": {"role": "assistant", "content": ""}, "done": true, "eval_count": 2, "prompt_eval_count": 5}',
    ]

    async def mock_aiter_lines():
        for line in mock_lines:
            yield line

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.aiter_lines = mock_aiter_lines

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        chunks = []
        async for chunk in provider.stream(prompt="Hi"):
            chunks.append(chunk)

    assert len(chunks) == 3
    assert chunks[0]["message"]["content"] == "Hello "
    assert chunks[1]["message"]["content"] == "world!"
    assert chunks[2]["done"] is True
    assert chunks[2]["eval_count"] == 2


@pytest.mark.asyncio
async def test_ollama_provider_stream_404_not_found():
    provider = OllamaProvider(base_url="http://localhost:11434", model="missing-model")

    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        with pytest.raises(OllamaUnavailableError) as exc_info:
            async for _ in provider.stream(prompt="Hi"):
                pass
        assert "is not available on Ollama server" in str(exc_info.value)


@pytest.mark.asyncio
async def test_ollama_provider_stream_malformed_lines_handled():
    provider = OllamaProvider(base_url="http://localhost:11434", model="qwen2.5:7b")

    mock_lines = [
        '{"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "Valid"}, "done": false}',
        'not valid json at all',
        '{"model": "qwen2.5:7b", "message": {"role": "assistant", "content": ""}, "done": true}',
    ]

    async def mock_aiter_lines():
        for line in mock_lines:
            yield line

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.aiter_lines = mock_aiter_lines

    mock_stream_ctx = MagicMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        chunks = []
        async for chunk in provider.stream(prompt="Hi"):
            chunks.append(chunk)

    # Malformed line skipped gracefully
    assert len(chunks) == 2
    assert chunks[0]["message"]["content"] == "Valid"
    assert chunks[1]["done"] is True


# ============================================================================
# 4. OLLAMA ADAPTER STREAMING LIFECYCLE TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_ollama_adapter_stream_lifecycle():
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "qwen2.5:7b"

    async def mock_stream(*args, **kwargs):
        yield {"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "First "}, "done": False}
        yield {"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "Second"}, "done": False}
        yield {
            "model": "qwen2.5:7b",
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "eval_count": 2,
            "prompt_eval_count": 10,
        }

    mock_provider.stream = mock_stream
    adapter = OllamaAdapter(provider=mock_provider)

    req = AIRequest.from_prompt("Count to two")
    chunks = []
    async for chunk in adapter.stream(req):
        chunks.append(chunk)

    # Sequence: START -> DELTA(First ) -> DELTA(Second) -> DONE
    assert len(chunks) == 4
    assert chunks[0].event_type == StreamEventType.START
    assert chunks[0].model_id == "qwen2.5:7b"

    assert chunks[1].event_type == StreamEventType.DELTA
    assert chunks[1].delta == "First "

    assert chunks[2].event_type == StreamEventType.DELTA
    assert chunks[2].delta == "Second"

    assert chunks[3].event_type == StreamEventType.DONE
    assert chunks[3].finish_reason == "stop"
    assert chunks[3].usage.completion_tokens == 2
    assert chunks[3].usage.prompt_tokens == 10
    assert chunks[3].usage.total_tokens == 12
    assert chunks[3].latency_ms is not None


@pytest.mark.asyncio
async def test_ollama_adapter_stream_empty_response():
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "qwen2.5:7b"

    async def mock_stream(*args, **kwargs):
        yield {"model": "qwen2.5:7b", "message": {"role": "assistant", "content": ""}, "done": True}

    mock_provider.stream = mock_stream
    adapter = OllamaAdapter(provider=mock_provider)

    chunks = []
    async for chunk in adapter.stream(AIRequest.from_prompt("Silent")):
        chunks.append(chunk)

    # Sequence: START -> DONE
    assert len(chunks) == 2
    assert chunks[0].event_type == StreamEventType.START
    assert chunks[1].event_type == StreamEventType.DONE


@pytest.mark.asyncio
async def test_ollama_adapter_stream_model_override_restoration():
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "qwen2.5:7b"

    async def mock_stream(*args, **kwargs):
        yield {"model": "codellama", "message": {"role": "assistant", "content": "Code"}, "done": True}

    mock_provider.stream = mock_stream
    adapter = OllamaAdapter(provider=mock_provider)

    req = AIRequest.from_prompt("Generate code", model_id="codellama")
    async for _ in adapter.stream(req):
        pass

    # Verified model restored to original after stream
    assert mock_provider.model == "qwen2.5:7b"


@pytest.mark.asyncio
async def test_ollama_adapter_stream_timeout_mapping():
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "qwen2.5:7b"

    async def mock_stream(*args, **kwargs):
        raise OllamaUnavailableError("Ollama stream timed out after 30 seconds")
        yield  # make it a generator

    mock_provider.stream = mock_stream
    adapter = OllamaAdapter(provider=mock_provider)

    events = []
    with pytest.raises(ProviderTimeoutError):
        async for chunk in adapter.stream(AIRequest.from_prompt("Timeout test")):
            events.append(chunk)

    # Checked that START was emitted, followed by ERROR before exception
    assert len(events) >= 1
    assert events[0].event_type == StreamEventType.START
    assert events[-1].event_type == StreamEventType.ERROR
    assert events[-1].error.error_code == "PROVIDER_TIMEOUT"


@pytest.mark.asyncio
async def test_ollama_adapter_stream_unavailable_mapping():
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "qwen2.5:7b"

    async def mock_stream(*args, **kwargs):
        raise OllamaUnavailableError("Daemon is unreachable")
        yield

    mock_provider.stream = mock_stream
    adapter = OllamaAdapter(provider=mock_provider)

    events = []
    with pytest.raises(ProviderUnavailableError):
        async for chunk in adapter.stream(AIRequest.from_prompt("Unavailable test")):
            events.append(chunk)

    assert events[-1].event_type == StreamEventType.ERROR
    assert events[-1].error.error_code == "PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_ollama_adapter_stream_cancellation():
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "qwen2.5:7b"

    async def infinite_stream(*args, **kwargs):
        while True:
            yield {"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "loop "}, "done": False}
            await asyncio.sleep(0.01)

    mock_provider.stream = infinite_stream
    adapter = OllamaAdapter(provider=mock_provider)

    received = 0
    # Consumer breaks early after 3 chunks
    async for chunk in adapter.stream(AIRequest.from_prompt("Loop")):
        if chunk.is_delta:
            received += 1
            if received >= 3:
                break

    assert received == 3


@pytest.mark.asyncio
async def test_ollama_adapter_stream_redacts_secrets_across_chunks():
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "qwen2.5:7b"

    async def secret_stream(*args, **kwargs):
        yield {"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "Your Bear"}, "done": False}
        yield {"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "er secret_key_1234567890 end"}, "done": True}

    mock_provider.stream = secret_stream
    adapter = OllamaAdapter(provider=mock_provider)

    deltas = []
    async for chunk in adapter.stream(AIRequest.from_prompt("Get key")):
        if chunk.is_delta:
            deltas.append(chunk.delta)

    full_output = "".join(deltas)
    assert "secret_key_1234567890" not in full_output
    assert "[REDACTED]" in full_output


@pytest.mark.asyncio
async def test_ollama_adapter_stream_strips_think_across_chunks():
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "qwen2.5:7b"

    async def think_stream(*args, **kwargs):
        yield {"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "Result: <th"}, "done": False}
        yield {"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "ink>private thought</think> 42"}, "done": True}

    mock_provider.stream = think_stream
    adapter = OllamaAdapter(provider=mock_provider)

    deltas = []
    async for chunk in adapter.stream(AIRequest.from_prompt("Calc")):
        if chunk.is_delta:
            deltas.append(chunk.delta)

    full_output = "".join(deltas)
    assert "private thought" not in full_output
    assert full_output == "Result:  42"


# ============================================================================
# 5. GROK & HUGGING FACE STREAMING ERROR CONTRACT (NO FAKE STREAMING)
# ============================================================================

@pytest.mark.asyncio
async def test_grok_adapter_stream_unsupported_raises():
    mock_grok = MagicMock(spec=GrokProvider)
    mock_grok.model = "grok-2-latest"
    mock_grok.api_key = "some-key"
    adapter = GrokAdapter(provider=mock_grok)

    events = []
    with pytest.raises(ProviderUnavailableError) as exc_info:
        async for chunk in adapter.stream(AIRequest.from_prompt("Stream grok")):
            events.append(chunk)

    assert "Streaming is not supported by Grok provider" in str(exc_info.value)
    assert len(events) == 1
    assert events[0].event_type == StreamEventType.ERROR
    assert events[0].error.error_code == "PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_hf_adapter_local_stream_unsupported_raises():
    mock_local = MagicMock(spec=HuggingFaceLocalProvider)
    mock_local.model_name = "local-model"
    adapter = HuggingFaceAdapter(local_provider=mock_local, prefer_local=True)

    events = []
    with pytest.raises(ProviderUnavailableError) as exc_info:
        async for chunk in adapter.stream(AIRequest.from_prompt("Stream HF local")):
            events.append(chunk)

    assert "Streaming is not supported by Hugging Face provider" in str(exc_info.value)
    assert len(events) == 1
    assert events[0].event_type == StreamEventType.ERROR


@pytest.mark.asyncio
async def test_hf_adapter_remote_stream_unsupported_raises():
    mock_remote = MagicMock(spec=HuggingFaceRemoteProvider)
    mock_remote.model = "remote-model"
    adapter = HuggingFaceAdapter(remote_provider=mock_remote, prefer_local=False)

    events = []
    with pytest.raises(ProviderUnavailableError) as exc_info:
        async for chunk in adapter.stream(AIRequest.from_prompt("Stream HF remote")):
            events.append(chunk)

    assert "Streaming is not supported by Hugging Face provider" in str(exc_info.value)
    assert len(events) == 1
    assert events[0].event_type == StreamEventType.ERROR


# ============================================================================
# 6. BACKWARD COMPATIBILITY: NON-STREAMING GENERATE UNCHANGED
# ============================================================================

@pytest.mark.asyncio
async def test_backward_compatibility_generate_still_works():
    mock_legacy_provider = MagicMock(spec=OllamaProvider)
    mock_legacy_provider.model = "qwen2.5:7b"
    mock_legacy_provider.generate = AsyncMock(
        return_value=MagicMock(
            content="Generate response text",
            model="qwen2.5:7b",
            metadata={"eval_count": 10},
        )
    )

    adapter = OllamaAdapter(provider=mock_legacy_provider)
    resp = await adapter.generate(AIRequest.from_prompt("Generate non-streaming"))

    assert isinstance(resp, AIResponse)
    assert resp.content == "Generate response text"
    assert resp.model_id == "qwen2.5:7b"
    assert resp.provider == ModelProvider.OLLAMA
    mock_legacy_provider.generate.assert_awaited_once()


# ============================================================================
# 7. SAFETY: NO PERSISTENCE OR EXECUTION SIDE EFFECTS
# ============================================================================

@pytest.mark.asyncio
async def test_stream_pure_generation_no_memory_writes():
    # Streaming does not interact with or mutate memory repository
    from app.memory.repository import memory_repository

    initial_count = len(memory_repository.list_semantic_memories(limit=10))

    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "qwen2.5:7b"

    async def stream_data(*args, **kwargs):
        yield {"model": "qwen2.5:7b", "message": {"role": "assistant", "content": "Chunk"}, "done": True}

    mock_provider.stream = stream_data
    adapter = OllamaAdapter(provider=mock_provider)

    async for _ in adapter.stream(AIRequest.from_prompt("Test")):
        pass

    after_count = len(memory_repository.list_semantic_memories(limit=10))
    assert initial_count == after_count


def test_stream_chunk_sanitizer_multiple_secrets_in_single_chunk():
    sanitizer = StreamChunkSanitizer()
    chunk = "Key 1: Bearer tok_112233 and Key 2: xai-998877665544 and confirmation_token=deadbeefcafebabe0123456789abcdef"
    res = sanitizer.feed(chunk) + sanitizer.flush()
    assert "tok_112233" not in res
    assert "xai-998877665544" not in res
    assert "deadbeefcafebabe0123456789abcdef" not in res
    assert "[REDACTED]" in res


def test_stream_chunk_sanitizer_multiple_think_blocks():
    sanitizer = StreamChunkSanitizer()
    chunk1 = "Step 1: <think>thought 1</think>Plan A. "
    chunk2 = "Step 2: <think>thought 2</think>Plan B."
    res = sanitizer.feed(chunk1) + sanitizer.feed(chunk2) + sanitizer.flush()
    assert res == "Step 1: Plan A. Step 2: Plan B."
    assert "thought 1" not in res
    assert "thought 2" not in res


@pytest.mark.asyncio
async def test_ollama_adapter_stream_image_and_options_forwarding():
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "llava:7b"

    received_kwargs = {}

    async def mock_stream(*args, **kwargs):
        nonlocal received_kwargs
        received_kwargs = kwargs
        received_kwargs["images"] = kwargs.get("images")
        yield {"model": "llava:7b", "message": {"role": "assistant", "content": "Analysis"}, "done": True}

    mock_provider.stream = mock_stream
    adapter = OllamaAdapter(provider=mock_provider)

    req = AIRequest.from_prompt(
        "Inspect UI",
        images=["data:image/png;base64,abc12345"],
        temperature=0.4,
        max_tokens=256,
    )

    async for _ in adapter.stream(req):
        pass

    assert received_kwargs.get("images") == ["data:image/png;base64,abc12345"]
    assert received_kwargs.get("temperature") == 0.4
    assert received_kwargs.get("num_predict") == 256


@pytest.mark.asyncio
async def test_stream_error_chunk_redacts_credentials():
    mock_provider = MagicMock(spec=OllamaProvider)
    mock_provider.model = "qwen2.5:7b"

    async def err_stream(*args, **kwargs):
        raise OllamaUnavailableError("Failed with secret Bearer secret_token_xyz_9988")
        yield

    mock_provider.stream = err_stream
    adapter = OllamaAdapter(provider=mock_provider)

    events = []
    with pytest.raises(ProviderUnavailableError):
        async for chunk in adapter.stream(AIRequest.from_prompt("Hi")):
            events.append(chunk)

    err_chunk = events[-1]
    assert err_chunk.is_error is True
    assert "secret_token_xyz_9988" not in str(err_chunk.error)
    assert "[REDACTED]" in str(err_chunk.error)

