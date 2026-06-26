from __future__ import annotations


HIGH_RISK_PATTERNS = ["直接帮我写完整", "替我完成", "代写", "直接给可提交", "帮我抄", "整篇报告"]


def check_integrity_risk(text: str) -> dict[str, str]:
    if any(pattern in text for pattern in HIGH_RISK_PATTERNS):
        return {
            "risk_level": "high",
            "message": "这个请求可能越过学习辅助边界。我可以提供提纲、思路、检查清单和示例结构，但不会直接代写可提交内容。",
        }
    return {"risk_level": "low", "message": ""}

