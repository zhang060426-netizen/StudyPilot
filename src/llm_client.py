from __future__ import annotations

import json
import base64
import hashlib
import hmac
import sys
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urlencode, urlparse
from typing import Any

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_PROVIDER,
    SPARK_API_KEY,
    SPARK_API_SECRET,
    SPARK_APP_ID,
    SPARK_DOMAIN,
    SPARK_HTTP_BASE_URL,
    SPARK_HTTP_MODEL,
    SPARK_WS_URL,
)


def has_api_key() -> bool:
    if LLM_PROVIDER == "spark":
        return bool(SPARK_APP_ID and SPARK_API_KEY and SPARK_API_SECRET)
    return bool(DEEPSEEK_API_KEY.strip())


def chat_text(prompt: str, system: str = "你是一个严谨的个人学习助手。") -> str:
    if LLM_PROVIDER == "spark":
        return spark_chat_text(prompt, system) or spark_http_chat_text(prompt, system)

    if not has_api_key():
        return ""

    try:
        from openai import OpenAI
    except ModuleNotFoundError:
        return ""

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL, timeout=8.0)
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        temperature=0.3,
    )
    return response.choices[0].message.content or ""


def chat_json(prompt: str, system: str = "你只输出合法 JSON。") -> dict[str, Any] | None:
    if LLM_PROVIDER == "spark":
        text = spark_chat_text(prompt, system) or spark_http_chat_text(prompt, system)
        if not text:
            return None
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start >= 0 and end > start:
                return json.loads(text[start : end + 1])
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    if not has_api_key():
        return None

    try:
        from openai import OpenAI
    except ModuleNotFoundError:
        return None

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL, timeout=8.0)
    response = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.1,
    )
    try:
        return json.loads(response.choices[0].message.content or "{}")
    except json.JSONDecodeError:
        return None


def build_spark_auth_url() -> str:
    parsed = urlparse(SPARK_WS_URL)
    host = parsed.netloc
    path = parsed.path
    date = format_datetime(datetime.now(timezone.utc), usegmt=True)
    signature_origin = f"host: {host}\ndate: {date}\nGET {path} HTTP/1.1"
    signature_sha = hmac.new(
        SPARK_API_SECRET.encode("utf-8"),
        signature_origin.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    signature = base64.b64encode(signature_sha).decode("utf-8")
    authorization_origin = (
        f'api_key="{SPARK_API_KEY}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode("utf-8")).decode("utf-8")
    return f"{SPARK_WS_URL}?{urlencode({'authorization': authorization, 'date': date, 'host': host})}"


def spark_http_chat_text(prompt: str, system: str = "你是一个严谨的个人学习助手。") -> str:
    if not (SPARK_API_KEY and SPARK_API_SECRET):
        return ""
    try:
        from openai import OpenAI
    except ModuleNotFoundError:
        return ""

    try:
        client = OpenAI(
            api_key=f"{SPARK_API_KEY}:{SPARK_API_SECRET}",
            base_url=SPARK_HTTP_BASE_URL.rstrip("/") + "/chat/completions",
        )
        response = client.chat.completions.create(
            model=SPARK_HTTP_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
            max_tokens=2400,
        )
        return response.choices[0].message.content or ""
    except Exception as exc:
        print(f"[llm_client] Spark HTTP call failed: {exc}", file=sys.stderr)
        return ""


def spark_chat_text(prompt: str, system: str = "你是一个严谨的个人学习助手。") -> str:
    if not (SPARK_APP_ID and SPARK_API_KEY and SPARK_API_SECRET):
        return ""
    try:
        import websocket
    except ModuleNotFoundError:
        return ""

    ws = None
    try:
        ws = websocket.create_connection(build_spark_auth_url(), timeout=20)
        payload = {
            "header": {"app_id": SPARK_APP_ID, "uid": "studypilot"},
            "parameter": {
                "chat": {
                    "domain": SPARK_DOMAIN,
                    "temperature": 0.3,
                    "max_tokens": 2400,
                }
            },
            "payload": {
                "message": {
                    "text": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ]
                }
            },
        }
        ws.send(json.dumps(payload, ensure_ascii=False))
        parts: list[str] = []
        while True:
            raw = ws.recv()
            data = json.loads(raw)
            header = data.get("header", {})
            if header.get("code", 0) != 0:
                return ""
            choices = data.get("payload", {}).get("choices", {})
            for item in choices.get("text", []):
                parts.append(item.get("content", ""))
            if choices.get("status") == 2:
                break
        return "".join(parts).strip()
    except Exception as exc:
        print(f"[llm_client] Spark call failed: {exc}", file=sys.stderr)
        return ""
    finally:
        if ws is not None:
            ws.close()
