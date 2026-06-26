from __future__ import annotations

import re


PATTERNS = {
    "手机号": re.compile(r"1[3-9]\d{9}"),
    "邮箱": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "疑似API Key": re.compile(r"(sk-|DEEPSEEK_API_KEY|api[_-]?key)", re.IGNORECASE),
}


def scan_privacy(text: str) -> list[str]:
    risks = []
    for label, pattern in PATTERNS.items():
        if pattern.search(text):
            risks.append(label)
    return risks

