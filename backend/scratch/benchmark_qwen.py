"""Benchmark script for local qwen2.5:7b running on Ollama."""

import asyncio
import sys
import time
import httpx
import psutil

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

OLLAMA_URL = "http://localhost:11434"
MODEL = "qwen2.5:7b"

PROMPTS = {
    "A": "Explain what an API is in simple terms.",
    "B": "Plan the steps required to create a React project called TaskFlow.",
    "C": "Identify whether this request requires a browser, filesystem, Git, deployment, or normal reasoning.",
    "D": "Calculate 125 * 48 and explain the result.",
}


async def benchmark_prompt(client: httpx.AsyncClient, prompt_key: str, prompt_text: str):
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt_text}],
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 128},
    }
    t0 = time.monotonic()
    resp = await client.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120.0)
    elapsed = time.monotonic() - t0
    data = resp.json()
    eval_count = data.get("eval_count", 0)
    eval_duration = data.get("eval_duration", 0)
    prompt_eval_count = data.get("prompt_eval_count", 0)
    prompt_eval_duration = data.get("prompt_eval_duration", 0)
    tokens_per_sec = (eval_count / (eval_duration / 1e9)) if eval_duration else 0.0

    return {
        "prompt_key": prompt_key,
        "status_code": resp.status_code,
        "elapsed_sec": round(elapsed, 3),
        "eval_count": eval_count,
        "eval_duration_sec": round(eval_duration / 1e9, 3) if eval_duration else None,
        "prompt_eval_count": prompt_eval_count,
        "prompt_eval_duration_sec": round(prompt_eval_duration / 1e9, 3) if prompt_eval_duration else None,
        "tokens_per_sec": round(tokens_per_sec, 2),
        "content_length": len(data.get("message", {}).get("content", "")),
        "content_snippet": data.get("message", {}).get("content", "")[:120] + "...",
    }


async def main():
    print("=" * 60)
    print(f"BENCHMARKING LOCAL MODEL: {MODEL} via {OLLAMA_URL}")
    print("=" * 60)

    mem_before = psutil.virtual_memory()
    print(f"RAM before benchmark: {round(mem_before.used / (1024**3), 2)} / {round(mem_before.total / (1024**3), 2)} GB ({mem_before.percent}%)")

    async with httpx.AsyncClient(timeout=180.0) as client:
        # 1. Probe availability
        v_res = await client.get(f"{OLLAMA_URL}/api/version")
        tags_res = await client.get(f"{OLLAMA_URL}/api/tags")
        print(f"Ollama Version: {v_res.json().get('version')}")
        models = [m.get("name") for m in tags_res.json().get("models", [])]
        print(f"Installed Models: {models}")
        assert MODEL in models or any(MODEL in m for m in models), f"Model {MODEL} not found!"

        # 2. Sequential Benchmark (Cold-start vs Warm-start)
        results = []
        is_first = True
        for key, ptext in PROMPTS.items():
            print(f"\nRunning Prompt {key} ('{ptext[:40]}...')...")
            res = await benchmark_prompt(client, key, ptext)
            res["call_type"] = "Cold-start" if is_first else "Warm-start"
            is_first = False
            results.append(res)
            print(f"  -> Type: {res['call_type']}")
            print(f"  -> Latency: {res['elapsed_sec']}s")
            print(f"  -> Eval Count: {res['eval_count']} tokens | Prompt Eval: {res['prompt_eval_count']} tokens")
            print(f"  -> Speed: {res['tokens_per_sec']} tokens/sec")
            print(f"  -> Snippet: {res['content_snippet']}")

        # 3. Concurrent Request Benchmark (2 parallel requests)
        print("\nRunning Concurrent Requests (Prompt A & Prompt D in parallel)...")
        t_c0 = time.monotonic()
        c_resA, c_resD = await asyncio.gather(
            benchmark_prompt(client, "Concurrent-A", PROMPTS["A"]),
            benchmark_prompt(client, "Concurrent-D", PROMPTS["D"]),
        )
        t_c_total = time.monotonic() - t_c0
        print(f"  -> Total Concurrent Elapsed: {round(t_c_total, 3)}s")
        print(f"  -> Req A Latency: {c_resA['elapsed_sec']}s, Speed: {c_resA['tokens_per_sec']} t/s")
        print(f"  -> Req D Latency: {c_resD['elapsed_sec']}s, Speed: {c_resD['tokens_per_sec']} t/s")

        # 4. Failure Behavior (Missing model)
        print("\nTesting Failure Behavior (Requesting non-existent model 'missing-model:latest')...")
        fail_resp = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={"model": "missing-model:latest", "messages": [{"role": "user", "content": "hi"}], "stream": False},
        )
        print(f"  -> Status Code: {fail_resp.status_code}")
        print(f"  -> Response: {fail_resp.text[:120]}")

    mem_after = psutil.virtual_memory()
    print(f"\nRAM after benchmark: {round(mem_after.used / (1024**3), 2)} / {round(mem_after.total / (1024**3), 2)} GB ({mem_after.percent}%)")
    print(f"RAM delta: {round((mem_after.used - mem_before.used) / (1024**2), 2)} MB")
    print("\n" + "=" * 60)
    print("BENCHMARK COMPLETE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
