from __future__ import annotations

import os
import sys
import atexit
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

_TEMP_DIR = tempfile.TemporaryDirectory(prefix="studypilot-smoke-")
_TEMP_DIR._ignore_cleanup_errors = True
atexit.register(_TEMP_DIR.cleanup)
TEMP_PATH = Path(_TEMP_DIR.name)

os.environ.setdefault("STUDYPILOT_DB_PATH", str(TEMP_PATH / "learning.db"))
os.environ.setdefault("STUDYPILOT_VECTOR_DIR", str(TEMP_PATH / "note_vector_db"))
os.environ.setdefault("LLM_PROVIDER", "demo")

from init_db import init_db

init_db()

from agents import MainAgent
from note_vector import build_vector_index


def main() -> None:
    agent = MainAgent()
    prompts = [
        "什么是RAG？",
        "周五前完成 Python 列表推导式作业",
        "查看待办任务",
        "帮我安排今天的学习计划",
        "根据Python笔记出题",
    ]
    for prompt in prompts:
        result = agent.handle(prompt)
        print(f"\nUSER: {prompt}")
        print(f"INTENT: {result['intent']} CONFIDENCE: {result.get('confidence', '')}")
        print(result["text"][:800])
    print("\nVECTOR:", build_vector_index())


if __name__ == "__main__":
    main()
