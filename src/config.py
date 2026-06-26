from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
TODO_XLSX = DATA_DIR / "todo.xlsx"
NOTES_XLSX = DATA_DIR / "notes.xlsx"
DB_PATH = Path(os.getenv("STUDYPILOT_DB_PATH", DATA_DIR / "learning.db"))
VECTOR_DIR = Path(os.getenv("STUDYPILOT_VECTOR_DIR", ROOT_DIR / "note_vector_db"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT_DIR / ".env")
except ModuleNotFoundError:
    pass

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "demo").lower()
SPARK_APP_ID = os.getenv("SPARK_APP_ID", "")
SPARK_API_KEY = os.getenv("SPARK_API_KEY", "")
SPARK_API_SECRET = os.getenv("SPARK_API_SECRET", "")
SPARK_WS_URL = os.getenv("SPARK_WS_URL", "wss://spark-api.xf-yun.com/v1/x1")
SPARK_DOMAIN = os.getenv("SPARK_DOMAIN", "x1")
SPARK_HTTP_BASE_URL = os.getenv("SPARK_HTTP_BASE_URL", "https://spark-api-open.xf-yun.com/v2")
SPARK_HTTP_MODEL = os.getenv("SPARK_HTTP_MODEL", "spark-x")
