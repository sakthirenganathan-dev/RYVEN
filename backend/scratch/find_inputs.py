import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

transcript_path = r"C:\Users\sakth\.gemini\antigravity-ide\brain\7df9d0f9-2799-489c-b7a7-89f30c69938c\.system_generated\logs\transcript.jsonl"

with open(transcript_path, "r", encoding="utf-8") as f:
    for idx, line in enumerate(f):
        if '"type":"USER_INPUT"' in line:
            obj = json.loads(line)
            content = obj.get("content", "")
            print(f"=== Line {idx+1} ===")
            print(content[:300].strip())
            print("...\n")
