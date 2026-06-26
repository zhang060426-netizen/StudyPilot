from __future__ import annotations

from datetime import date
from typing import Any

from intent import parse_intent
from rag import ask_note
from safety import check_integrity_risk
from tools import add_todo, complete_todo, list_todos, save_quiz_record, search_todo_by_keyword


class TaskAgent:
    def add_task(self, data: dict[str, Any]) -> str:
        todo = add_todo(data.get("course", "未分类"), data.get("task", ""), data.get("deadline", ""), data.get("priority", "中"))
        return f"已添加任务：#{todo['id']} {todo['course']} - {todo['task']}，截止日期：{todo['deadline'] or '未设置'}，优先级：{todo['priority']}。"

    def list_tasks(self, course: str | None = None) -> str:
        todos = list_todos(status="未完成", course=course if course != "未分类" else None)
        if not todos:
            return "当前没有未完成任务。"
        lines = ["当前未完成任务："]
        for todo in todos[:10]:
            lines.append(f"- #{todo['id']} [{todo['course']}] {todo['task']}，截止：{todo.get('deadline', '')}，优先级：{todo.get('priority', '中')}")
        return "\n".join(lines)

    def complete_task(self, data: dict[str, Any]) -> str:
        todo_id = data.get("todo_id")
        if not todo_id and data.get("keyword"):
            todo = search_todo_by_keyword(data["keyword"])
            todo_id = todo["id"] if todo else None
        if not todo_id:
            return "没有找到要完成的任务，请提供任务编号，例如：完成 3。"
        ok = complete_todo(int(todo_id))
        return f"任务 #{todo_id} 已标记完成。" if ok else f"没有找到任务 #{todo_id}。"


class NoteAgent:
    def ask(self, question: str, course: str | None = None) -> dict[str, Any]:
        return ask_note(question, None if course == "未分类" else course)


class PlanAgent:
    def make_plan(self) -> str:
        todos = list_todos(status="未完成")
        high = [todo for todo in todos if todo.get("priority") == "高"]
        normal = [todo for todo in todos if todo.get("priority") != "高"]
        morning = high[0] if high else (todos[0] if todos else None)
        afternoon = normal[0] if normal else (todos[1] if len(todos) > 1 else None)
        evening = todos[2] if len(todos) > 2 else None
        if not todos:
            return "今天没有未完成任务，可以安排 30 分钟复盘笔记或做一组测验。"
        lines = [f"今日学习计划（{date.today().isoformat()}）："]
        if morning:
            lines.append(f"- 上午：优先完成 [{morning['course']}] {morning['task']}。")
        if afternoon:
            lines.append(f"- 下午：推进 [{afternoon['course']}] {afternoon['task']}。")
        if evening:
            lines.append(f"- 晚上：复盘 [{evening['course']}] {evening['task']} 相关笔记，并做 3 道自测题。")
        lines.append("建议：先处理高优先级和最近截止任务，每完成一个任务就标记状态。")
        return "\n".join(lines)


class QuizAgent:
    def generate_quiz_items(self, course: str = "全部课程", count: int = 5) -> list[dict[str, Any]]:
        result = ask_note(f"{course} 核心知识点", None if course in ["全部课程", "未分类"] else course, top_k=count)
        sources = result["sources"][:count]
        if not sources:
            return []

        all_answers = [self._short_answer(source.get("content", "")) for source in sources]
        items: list[dict[str, Any]] = []
        for index, source in enumerate(sources, 1):
            answer = self._short_answer(source.get("content", ""))
            note_id = source.get("id")
            chunk_id = f"note-{note_id}-0"

            if index % 3 == 0:
                question = f"请填写「{source['title']}」中的一个关键内容。"
                quiz_type = "blank"
                item: dict[str, Any] = {
                    "id": f"{chunk_id}-blank",
                    "course": source["course"],
                    "type": quiz_type,
                    "type_label": "填空题",
                    "question": question,
                    "answer": answer,
                    "reference_answer": answer,
                    "note_id": note_id,
                    "chunk_id": chunk_id,
                }
            else:
                question = f"{source['title']} 的关键内容是什么？"
                options = self._choice_options(answer, all_answers, index)
                correct_key = next(option["key"] for option in options if option["text"] == answer)
                quiz_type = "single"
                item = {
                    "id": f"{chunk_id}-single",
                    "course": source["course"],
                    "type": quiz_type,
                    "type_label": "单选题",
                    "question": question,
                    "answer": correct_key,
                    "reference_answer": answer,
                    "options": options,
                    "note_id": note_id,
                    "chunk_id": chunk_id,
                }

            save_quiz_record(source["course"], question, answer, note_id, chunk_id)
            items.append(item)
        return items

    def generate_quiz(self, course: str = "全部课程", count: int = 5) -> str:
        items = self.generate_quiz_items(course, count)
        if not items:
            return "暂时没有找到可生成测验的笔记，请先补充笔记。"
        lines = ["已根据你的笔记生成复习题："]
        for index, item in enumerate(items, 1):
            lines.append(f"{index}. {item['question']}\n   参考答案：{item['reference_answer']}")
        return "\n".join(lines)

    def _short_answer(self, content: str) -> str:
        cleaned = " ".join((content or "").replace("\n", " ").split())
        return cleaned[:120] if cleaned else "请回到原始笔记复习这一知识点。"

    def _choice_options(self, answer: str, answers: list[str], index: int) -> list[dict[str, str]]:
        distractors = [item for item in answers if item and item != answer]
        fallback = [
            "先定位关键词，再结合例子进行理解。",
            "把概念拆成定义、步骤和注意点三部分复习。",
            "根据上下文判断题目考查的核心概念。",
        ]
        candidates = [answer] + distractors + fallback
        unique: list[str] = []
        for candidate in candidates:
            if candidate not in unique:
                unique.append(candidate)
            if len(unique) == 4:
                break
        while len(unique) < 4:
            unique.append(f"复习笔记中的相关知识点 {len(unique) + 1}")
        shift = (index - 1) % 4
        unique = unique[shift:] + unique[:shift]
        labels = ["A", "B", "C", "D"]
        return [{"key": label, "text": text} for label, text in zip(labels, unique)]


class MainAgent:
    def __init__(self) -> None:
        self.task_agent = TaskAgent()
        self.note_agent = NoteAgent()
        self.plan_agent = PlanAgent()
        self.quiz_agent = QuizAgent()

    def handle(self, user_input: str) -> dict[str, Any]:
        risk = check_integrity_risk(user_input)
        if risk["risk_level"] == "high":
            return {"text": risk["message"], "sources": [], "confidence": "学术诚信提醒", "intent": "integrity_warning"}

        intent = parse_intent(user_input)
        name = intent["intent"]
        if name == "add_todo":
            text = self.task_agent.add_task(intent)
            return {"text": text, "sources": [], "confidence": "", "intent": name}
        if name == "list_todo":
            text = self.task_agent.list_tasks(intent.get("course"))
            return {"text": text, "sources": [], "confidence": "", "intent": name}
        if name == "complete_todo":
            text = self.task_agent.complete_task(intent)
            return {"text": text, "sources": [], "confidence": "", "intent": name}
        if name == "make_plan":
            text = self.plan_agent.make_plan()
            return {"text": text, "sources": [], "confidence": "", "intent": name}
        if name == "generate_quiz":
            text = self.quiz_agent.generate_quiz(intent.get("course", "全部课程"), intent.get("count", 5))
            return {"text": text, "sources": [], "confidence": "", "intent": name}

        answer = self.note_agent.ask(intent.get("question", user_input), intent.get("course"))
        return {"text": answer["answer"], "sources": answer["sources"], "confidence": answer["confidence"], "intent": "query_note"}
