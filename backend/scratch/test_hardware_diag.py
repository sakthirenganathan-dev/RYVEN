import asyncio
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.abspath("backend"))

from app.ai.hardware import HardwareDiagnostics


async def main():
    profile = await HardwareDiagnostics.get_profile(force_refresh=True)
    print("=== HARDWARE PROFILE ===")
    print(json.dumps(profile.model_dump(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
