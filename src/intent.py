from __future__ import annotations

import re
from typing import Any

from llm_client import chat_json


COURSE_KEYWORDS = {
    "Python": ["python", "列表", "函数", "pandas", "文件", "异常"],
    "AI应用开发": ["rag", "向量", "chroma", "agent", "智能体", "embedding", "模型", "报告", "演示"],
    "英语": ["英语", "unit", "单词", "虚拟语气", "听力", "作文", "阅读"],
}


def infer_course(text: str) -> str:
    lower = text.lower()
    for course, keywords in COURSE_KEYWORDS.items():
        if any(keyword.lower() in lower for keyword in keywords):
            return course
    return "未分类"


def infer_deadline(text: str) -> str:
    for pattern in [r"\d{4}-\d{1,2}-\d{1,2}", r"周[一二三四五六日天]", r"明天", r"今天", r"下周[一二三四五六日天]?"]:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return ""


def infer_priority(text: str) -> str:
    if any(word in text for word in ["今天", "明天", "周一", "周二", "紧急", "马上", "尽快"]):
        return "高"
    if any(word in text for word in ["复习", "整理", "准备"]):
        return "中"
    return "中"


def parse_intent(user_input: str) -> dict[str, Any]:
    text = user_input.strip()
    lower = text.lower()

    llm_result = parse_intent_with_llm(text)
    if llm_result:
        llm_result.setdefault("raw", text)
        return llm_result

    if any(word in text for word in ["计划", "安排", "今天学", "学习计划"]):
        return {"intent": "make_plan", "date": "today", "raw": text}

    if any(word in text for word in ["出题", "测验", "测试", "闪卡", "考我"]):
        return {"intent": "generate_quiz", "course": infer_course(text), "count": 5, "raw": text}

    explicit_complete = any(word in text for word in ["标记完成", "做完了", "已完成", "完成任务"])
    complete_by_id = bool(re.fullmatch(r"(完成|标记完成)\s*#?\d+", text))
    if (explicit_complete or complete_by_id) and not any(word in text for word in ["什么", "解释", "为什么"]):
        todo_id_match = re.search(r"\d+", text)
        return {
            "intent": "complete_todo",
            "todo_id": int(todo_id_match.group(0)) if todo_id_match else None,
            "keyword": text.replace("完成", "").replace("标记", "").strip(),
            "raw": text,
        }

    if any(word in text for word in ["添加", "新增", "提醒", "作业", "背诵", "整理", "准备", "提交", "完成"]) and not text.endswith("？"):
        cleaned = re.sub(r"^(添加|新增|提醒我|请提醒我)", "", text).strip()
        return {
            "intent": "add_todo",
            "course": infer_course(cleaned),
            "task": cleaned,
            "deadline": infer_deadline(cleaned),
            "priority": infer_priority(cleaned),
            "raw": text,
        }

    if any(word in text for word in ["任务", "待办", "todo", "作业列表"]):
        return {"intent": "list_todo", "course": infer_course(text), "raw": text}

    if any(word in lower for word in ["什么", "解释", "why", "how", "是什么", "怎么", "?"]) or text.endswith("？"):
        return {"intent": "query_note", "question": text, "course": infer_course(text), "raw": text}

    return {"intent": "query_note", "question": text, "course": infer_course(text), "raw": text}


def parse_intent_with_llm(text: str) -> dict[str, Any] | None:
    prompt = f"""
请分析用户输入，输出 JSON。
可选 intent：
- add_todo: 添加新待办
- list_todo: 查看待办
- complete_todo: 完成已有待办
- query_note: 查询/解释笔记知识点
- make_plan: 生成学习计划
- generate_quiz: 生成测验或闪卡

字段要求：
- intent 必填
- course 可选，值可为 Python、AI应用开发、英语、未分类
- task 用于 add_todo
- deadline 用于 add_todo
- priority 可为 高、中、低
- question 用于 query_note
- keyword 用于 complete_todo
- count 用于 generate_quiz

用户输入：{text}
"""
    data = chat_json(prompt)
    if not data or "intent" not in data:
        return None
    if data["intent"] not in {"add_todo", "list_todo", "complete_todo", "query_note", "make_plan", "generate_quiz"}:
        return None
    if data.get("course") in {"", None}:
        data["course"] = infer_course(text)
    return data
