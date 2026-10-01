"""Live verification script for RYVEN Phase 4 Workflow Engine v1 on Windows."""

import asyncio
import json
from app.core.assistant import Assistant
from app.ai.ollama import OllamaProvider


async def main():
    print("=" * 60)
    print("RYVEN 2.0 PHASE 4 — LIVE WINDOWS VERIFICATION")
    print("=" * 60)

    # 1. Initialize real Assistant connected to local Ollama (qwen2.5:7b)
    assistant = Assistant(ai_provider=OllamaProvider())
    print("\n[+] Assistant initialized with real ToolRegistry, Router, and WorkflowEngine.")

    # 2. Test Workflow execution: "RYVEN, prepare my workspace."
    print("\n--- TEST 1: LIVE WORKSPACE PREPARATION WORKFLOW ---")
    query_wf = "RYVEN, prepare my workspace."
    print(f"User Query: '{query_wf}'")
    
    res_wf = await assistant.process(query_wf)
    print(f"Response Success: {res_wf.success}")
    print(f"Response Type: {res_wf.type}")
    print(f"Response Message: {res_wf.message}")
    print(f"Metadata:")
    print(json.dumps(res_wf.metadata, indent=2))

    assert res_wf.success is True, "Workflow should succeed"
    assert res_wf.type == "workflow", "Type must be 'workflow'"
    assert res_wf.metadata.get("status") == "COMPLETED", "Workflow status must be COMPLETED"
    assert res_wf.metadata.get("steps_completed") == 3, "3 steps must complete"
    print("\n>>> TEST 1: PASS — Real workflow executed and completed successfully!")

    # 3. Test Educational Question: "Explain what a workflow engine is."
    print("\n--- TEST 2: EDUCATIONAL AI QUERY (MUST NOT EXECUTE TOOLS) ---")
    query_ai = "Explain what a workflow engine is in 2 sentences."
    print(f"User Query: '{query_ai}'")
    
    res_ai = await assistant.process(query_ai, session_id="educational_session")
    print(f"Response Success: {res_ai.success}")
    print(f"Response Type: {res_ai.type}")
    print(f"Response Message Preview: {res_ai.message[:200]}...")
    print(f"Metadata: {json.dumps(res_ai.metadata, indent=2)}")

    assert res_ai.success is True, "AI response should succeed"
    assert res_ai.type == "ai", "Type must remain 'ai'"
    assert res_ai.metadata.get("workflow_name") is None, "Must not execute any workflow"
    assert res_ai.tool is None, "Must not execute any tool"
    print("\n>>> TEST 2: PASS — Educational question handled cleanly by local Ollama AI brain!")

    print("\n" + "=" * 60)
    print("ALL REAL WINDOWS VERIFICATION CHECKS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
