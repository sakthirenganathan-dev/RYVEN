import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

transcript_path = r"C:\Users\sakth\.gemini\antigravity-ide\brain\7df9d0f9-2799-489c-b7a7-89f30c69938c\.system_generated\logs\transcript_full.jsonl"

with open(transcript_path, "r", encoding="utf-8") as f:
    for idx, line in enumerate(f):
        if idx + 1 == 6075:
            obj = json.loads(line)
            with open(r"e:\project\project\view-archive-buddy-main\backend\scratch\full_prompt_m15_1_untruncated.txt", "w", encoding="utf-8") as out:
                out.write(obj.get("content", ""))
            print("Wrote full untruncated prompt to backend/scratch/full_prompt_m15_1_untruncated.txt")
            break
