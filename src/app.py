from __future__ import annotations

import asyncio
import csv
import html
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import time
from urllib.parse import quote_plus
from collections import defaultdict
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path

import pandas as pd

try:
    from nicegui import app, ui
except ModuleNotFoundError as exc:
    raise SystemExit(
        "NiceGUI is not installed. Run: pip install -r requirements.txt\n"
        "You can still test core logic with: python scripts/smoke_test.py"
    ) from exc

from agents import MainAgent, PlanAgent, QuizAgent
from llm_client import chat_json, chat_text, has_api_key
from note_vector import build_vector_index
from tools import (
    add_note,
    add_note_upload,
    get_connection,
    add_work_log,
    list_note_uploads,
    list_notes,
    list_quiz_records,
    list_todos,
    table_counts,
    update_todo_status,
)


agent = MainAgent()
MINERADIO_DIR = Path(__file__).resolve().parent.parent / "Mineradio"
MINERADIO_HOST = "127.0.0.1"
MINERADIO_PORT = int(os.getenv("MINERADIO_PORT", "3100"))
MINERADIO_URL = f"http://{MINERADIO_HOST}:{MINERADIO_PORT}"
MINERADIO_EXTERNAL_URL = (os.getenv("MINERADIO_EXTERNAL_URL") or "").strip()
if MINERADIO_EXTERNAL_URL and not re.match(r"^https?://", MINERADIO_EXTERNAL_URL, flags=re.I):
    MINERADIO_EXTERNAL_URL = f"https://{MINERADIO_EXTERNAL_URL}"
_mineradio_process: subprocess.Popen | None = None


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def ensure_mineradio_running() -> tuple[bool, str]:
    global _mineradio_process
    if MINERADIO_EXTERNAL_URL:
        return True, "已连接线上 Mineradio。"
    if _port_is_open(MINERADIO_HOST, MINERADIO_PORT):
        return True, "Mineradio 已经在运行。"
    if not (MINERADIO_DIR / "server.js").exists():
        return False, f"没有找到 Mineradio 项目目录: {MINERADIO_DIR}"
    if not (MINERADIO_DIR / "node_modules").exists():
        return False, "Mineradio 依赖还没安装，请在 Mineradio 目录执行 npm install。"

    stdout_path = MINERADIO_DIR.parent / "mineradio_stdout.log"
    stderr_path = MINERADIO_DIR.parent / "mineradio_stderr.log"
    env = os.environ.copy()
    env["HOST"] = MINERADIO_HOST
    env["PORT"] = str(MINERADIO_PORT)
    env.setdefault("MINERADIO_UPDATE_DIR", str(MINERADIO_DIR / "updates"))
    env.setdefault("MINERADIO_BEAT_CACHE_DIR", str(MINERADIO_DIR / "updates" / "beatmaps"))
    try:
        stdout = stdout_path.open("a", encoding="utf-8")
        stderr = stderr_path.open("a", encoding="utf-8")
        _mineradio_process = subprocess.Popen(
            ["node", "server.js"],
            cwd=MINERADIO_DIR,
            env=env,
            stdout=stdout,
            stderr=stderr,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    except OSError as exc:
        return False, f"无法启动 Mineradio: {exc}"

    for _ in range(20):
        if _port_is_open(MINERADIO_HOST, MINERADIO_PORT):
            return True, "Mineradio 已启动。"
        if _mineradio_process.poll() is not None:
            return False, "Mineradio 启动后立即退出，请查看 mineradio_stderr.log。"
        time.sleep(0.15)
    return True, "Mineradio 正在启动，页面可能需要几秒钟加载。"


def open_mineradio() -> None:
    ok, message = ensure_mineradio_running()
    ui.notify(message, color="positive" if ok else "warning")
    if ok:
        ui.navigate.to(MINERADIO_EXTERNAL_URL or MINERADIO_URL, new_tab=True)


@app.get("/api/mineradio/start")
def start_mineradio_api() -> dict[str, object]:
    ok, message = ensure_mineradio_running()
    return {"ok": ok, "message": message, "url": MINERADIO_EXTERNAL_URL or MINERADIO_URL, "mode": "web"}


def build_web_search_url(query: str, mode: str = "web") -> str:
    cleaned = " ".join((query or "").split())
    suffix = " 学习资料 课程笔记" if mode == "web" else " 深度研究 学习资料 课程笔记"
    return f"https://duckduckgo.com/?q={quote_plus(cleaned + suffix)}"


def priority_color(priority: str | None) -> str:
    return {"高": "red", "中": "orange", "低": "green"}.get(priority or "中", "blue")


def metric_card(title: str, value: str, caption: str = "", accent: str = "blue", on_click=None) -> None:
    card = ui.card().classes(f"metric-card accent-{accent} animate-card")
    if on_click:
        card.classes("metric-card-clickable").props('role="button" tabindex="0"').on("click", on_click)
    with card:
        ui.label(title).classes("metric-title")
        ui.label(value).classes("metric-value")
        if caption:
            ui.label(caption).classes("metric-caption")


def build_daily_plan_items(todos: list[dict[str, object]]) -> list[dict[str, str]]:
    high = [todo for todo in todos if todo.get("priority") == "高"]
    normal = [todo for todo in todos if todo.get("priority") != "高"]
    morning = high[0] if high else (todos[0] if todos else None)
    afternoon = normal[0] if normal else (todos[1] if len(todos) > 1 else None)
    evening = todos[2] if len(todos) > 2 else None

    if not todos:
        return [
            {
                "period": "今日",
                "title": "复盘笔记或完成一组自测",
                "detail": "当前没有未完成任务，可以安排 30 分钟整理课程笔记。",
                "meta": "灵活安排",
                "tone": "blue",
            }
        ]

    items: list[dict[str, str]] = []
    if morning:
        items.append(
            {
                "period": "上午",
                "title": f"优先完成 [{morning['course']}] {morning['task']}",
                "detail": "先处理最近截止或高优先级任务，完成后标记状态。",
                "meta": f"截止 {morning.get('deadline') or '未设置'} · {morning.get('priority', '中')}优先级",
                "tone": "red" if morning.get("priority") == "高" else "blue",
            }
        )
    if afternoon:
        items.append(
            {
                "period": "下午",
                "title": f"推进 [{afternoon['course']}] {afternoon['task']}",
                "detail": "把任务拆成可完成的小步骤，优先产出可检查的结果。",
                "meta": f"截止 {afternoon.get('deadline') or '未设置'} · {afternoon.get('priority', '中')}优先级",
                "tone": "orange" if afternoon.get("priority") == "中" else "blue",
            }
        )
    if evening:
        items.append(
            {
                "period": "晚上",
                "title": f"复盘 [{evening['course']}] {evening['task']}",
                "detail": "整理相关笔记，并做 3 道自测题巩固当天学习。",
                "meta": f"截止 {evening.get('deadline') or '未设置'} · 复习训练",
                "tone": "purple",
            }
        )
    return items


def parse_deadline(value: str | None) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def course_color_class(course: str | None) -> str:
    if course == "Python":
        return "calendar-course-python"
    if course == "AI应用开发":
        return "calendar-course-ai"
    if course == "英语":
        return "calendar-course-english"
    return "calendar-course-other"


def priority_tone_class(priority: str | None) -> str:
    return {"高": "priority-high", "中": "priority-mid", "低": "priority-low"}.get(priority or "中", "priority-mid")


def todo_is_completed(todo: dict[str, object]) -> bool:
    return str(todo.get("status") or "") == "已完成"


def calendar_month_window(todos: list[dict[str, object]]) -> tuple[int, int]:
    active_todos = [todo for todo in todos if not todo_is_completed(todo)]
    source = active_todos or todos
    dated = [deadline for todo in source if (deadline := parse_deadline(str(todo.get("deadline") or "")))]
    anchor = min(dated) if dated else date.today()
    return anchor.year, anchor.month


def render_task_calendar(todos: list[dict[str, object]]) -> None:
    year, month = calendar_month_window(todos)
    tasks_by_day: dict[int, list[dict[str, object]]] = defaultdict(list)
    unscheduled: list[dict[str, object]] = []
    for todo in todos:
        deadline = parse_deadline(str(todo.get("deadline") or ""))
        if deadline and deadline.year == year and deadline.month == month:
            tasks_by_day[deadline.day].append(todo)
        else:
            unscheduled.append(todo)

    month_days = monthrange(year, month)[1]
    first_weekday = date(year, month, 1).weekday()
    visible_cells = ((first_weekday + month_days + 6) // 7) * 7
    today = date.today()
    weekday_labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    unfinished_count = sum(1 for todo in todos if not todo_is_completed(todo))
    high_count = sum(1 for day_tasks in tasks_by_day.values() for todo in day_tasks if todo.get("priority") == "高" and not todo_is_completed(todo))
    scheduled_count = sum(len(day_tasks) for day_tasks in tasks_by_day.values())
    busy_days = sum(1 for day_tasks in tasks_by_day.values() if day_tasks)

    with ui.card().classes("content-card calendar-card animate-card"):
        with ui.row().classes("calendar-toolbar items-center no-wrap"):
            with ui.column().classes("gap-0"):
                ui.label("日程概览").classes("section-title")
                ui.label(f"{year}年{month}月 · 根据任务截止日期自动排布").classes("calendar-subtitle")
            with ui.element("div").classes("calendar-toolbar-meta"):
                ui.badge(f"{unfinished_count} 项未完成", color="blue").classes("calendar-count")
                with ui.element("div").classes("calendar-summary-strip"):
                    for label, value, tone in [
                        ("本月排期", f"{scheduled_count} 项", "blue"),
                        ("高优先级", f"{high_count} 项", "red"),
                        ("有任务日期", f"{busy_days} 天", "green"),
                        ("未识别日期", f"{len(unscheduled)} 项", "amber"),
                    ]:
                        with ui.element("div").classes(f"calendar-summary-item calendar-summary-{tone}"):
                            ui.label(value).classes("calendar-summary-value")
                            ui.label(label).classes("calendar-summary-label")
                if unscheduled:
                    with ui.row().classes("unscheduled-row items-center"):
                        ui.icon("event_busy").classes("unscheduled-icon")
                        ui.label("未能识别为日期的任务：").classes("helper-copy")
                        for todo in unscheduled[:3]:
                            color = "grey" if todo_is_completed(todo) else priority_color(str(todo.get("priority") or "中"))
                            ui.badge(f"#{todo.get('id')} {todo.get('deadline') or '未设置'}", color=color).classes("unscheduled-badge")
                ui.button(icon="refresh", on_click=lambda: ui.notify("任务日历已跟随当前待办数据刷新。")).props("flat round").classes("calendar-icon-btn")

        with ui.element("div").classes("calendar-weekdays"):
            for label in weekday_labels:
                ui.label(label).classes("calendar-weekday")

        with ui.element("div").classes("calendar-grid"):
            for index in range(visible_cells):
                day = index - first_weekday + 1
                is_in_month = 1 <= day <= month_days
                day_tasks = tasks_by_day.get(day, []) if is_in_month else []
                cell_classes = ["calendar-day"]
                if not is_in_month:
                    cell_classes.append("calendar-day-muted")
                elif today.year == year and today.month == month and today.day == day:
                    cell_classes.append("calendar-day-today")
                with ui.element("div").classes(" ".join(cell_classes)):
                    if is_in_month:
                        with ui.row().classes("calendar-day-head items-start"):
                            with ui.column().classes("gap-0"):
                                ui.label(str(day)).classes("calendar-date-number")
                                ui.label(f"{len(day_tasks)} 项" if day_tasks else " ").classes("calendar-day-count")
                            ui.space()
                            if any(todo.get("priority") == "高" and not todo_is_completed(todo) for todo in day_tasks):
                                ui.icon("priority_high").classes("calendar-alert")
                        for todo in day_tasks:
                            course = str(todo.get("course") or "未分类")
                            title = f"#{todo.get('id')} {todo.get('task')}"
                            completed_class = "calendar-task-completed" if todo_is_completed(todo) else ""
                            task_element = ui.element("div").classes(
                                f"calendar-task {course_color_class(course)} {priority_tone_class(str(todo.get('priority') or '中'))} {completed_class}"
                            )
                            with task_element:
                                ui.label(title).classes("calendar-task-title")
                                ui.label(course).classes("calendar-task-meta")
                                ui.label(
                                    f"截止：{todo.get('deadline') or '未设置'} · 优先级：{todo.get('priority') or '中'}"
                                ).classes("calendar-task-full")

def render_page_header(title: str, subtitle: str, icon: str) -> None:
    with ui.row().classes("page-head animate-hero items-center"):
        with ui.element("div").classes("page-icon"):
            ui.icon(icon)
        with ui.column().classes("gap-1"):
            ui.label(title).classes("page-title")
            ui.label(subtitle).classes("page-subtitle")


def normalize_course(value: str | None, fallback: str) -> str:
    text = (value or "").strip()
    return text if text and text != "全部课程" else fallback


def normalize_answer_text(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "").strip()).lower()


def answer_matches(user_answer: str | None, reference_answer: str | None) -> bool:
    user_text = normalize_answer_text(user_answer)
    reference_text = normalize_answer_text(reference_answer)
    if not user_text or not reference_text:
        return False
    return user_text in reference_text or reference_text in user_text


def split_plain_text_notes(text: str, course: str, default_title: str) -> list[dict[str, str]]:
    cleaned = text.replace("\r\n", "\n").strip()
    if not cleaned:
        return []
    chunks = [chunk.strip() for chunk in cleaned.split("\n\n") if chunk.strip()]
    if len(chunks) <= 1:
        return [{"course": course, "title": default_title, "content": cleaned, "tags": "上传"}]
    notes: list[dict[str, str]] = []
    for index, chunk in enumerate(chunks, 1):
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        title = lines[0][:36] if lines else f"{default_title} {index}"
        content = "\n".join(lines[1:]).strip() if len(lines) > 1 else chunk
        notes.append({"course": course, "title": title or f"{default_title} {index}", "content": content, "tags": "上传"})
    return notes


def dataframe_to_notes(df: pd.DataFrame, fallback_course: str, default_title: str) -> list[dict[str, str]]:
    if df.empty:
        return []
    normalized = {str(column).strip().lower(): column for column in df.columns}
    course_col = normalized.get("course") or normalized.get("课程")
    title_col = normalized.get("title") or normalized.get("标题")
    content_col = normalized.get("content") or normalized.get("内容") or normalized.get("note") or normalized.get("笔记")
    tags_col = normalized.get("tags") or normalized.get("标签")

    notes: list[dict[str, str]] = []
    for index, row in df.fillna("").iterrows():
        course = normalize_course(str(row.get(course_col, "")) if course_col else "", fallback_course)
        if content_col:
            content = str(row.get(content_col, "")).strip()
        else:
            pieces = [f"{column}: {row[column]}" for column in df.columns if str(row[column]).strip()]
            content = "\n".join(pieces).strip()
        if not content:
            continue
        title = str(row.get(title_col, "")).strip() if title_col else ""
        tags = str(row.get(tags_col, "")).strip() if tags_col else "上传"
        notes.append(
            {
                "course": course,
                "title": title or f"{default_title} {index + 1}",
                "content": content,
                "tags": tags or "上传",
            }
        )
    return notes


def parse_uploaded_notes(file_name: str, raw: bytes, fallback_course: str) -> list[dict[str, str]]:
    suffix = Path(file_name).suffix.lower()
    default_title = Path(file_name).stem or "上传笔记"
    if suffix in {".xlsx", ".xls"}:
        return dataframe_to_notes(pd.read_excel(io.BytesIO(raw)), fallback_course, default_title)
    if suffix == ".csv":
        sample = raw[:4096].decode("utf-8-sig", errors="ignore")
        try:
            dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        except csv.Error:
            dialect = csv.excel
        df = pd.read_csv(io.BytesIO(raw), encoding="utf-8-sig", dialect=dialect)
        return dataframe_to_notes(df, fallback_course, default_title)
    if suffix and suffix not in {".txt", ".md"}:
        return []
    text = raw.decode("utf-8-sig", errors="ignore")
    return split_plain_text_notes(text, fallback_course, default_title)


def render_hero(todos_count: int, high_count: int) -> None:
    with ui.element("section").classes("dashboard-hero animate-hero"):
        with ui.element("div").classes("hero-kicker-pill"):
            ui.icon("dashboard")
            ui.label("StudyPilot 的学习驾驶舱")
        ui.label("让学习进度一目了然").classes("hero-title")
        ui.label("集中管理计划、笔记与复习，清楚知道下一步该学什么。").classes("hero-subtitle")
        with ui.row().classes("hero-cta-row items-center justify-center"):
            ui.button(
                "开始学习",
                icon="arrow_forward",
                on_click=lambda: ui.run_javascript("window.studypilotTabs && window.studypilotTabs.show('plan')"),
            ).props("color=dark").classes("hero-action")
            with ui.element("div").classes("hero-inline-stat"):
                ui.label(f"{todos_count} 项未完成")
                ui.label(f"{high_count} 项高优先级")


def render_mineradio_float() -> None:
    with ui.element("button").props(
        f'type="button" aria-label="打开 Mineradio 沉浸式学习音乐空间" data-mineradio-float data-mineradio-url="{MINERADIO_URL}"'
    ).classes("mineradio-float"):
        with ui.element("span").classes("mineradio-disc").props('aria-hidden="true"'):
            ui.element("span").classes("mineradio-disc-core")
        with ui.element("span").classes("mineradio-float-copy"):
            ui.label("沉浸式学习").classes("mineradio-float-title")
            ui.label("Mineradio").classes("mineradio-float-subtitle")


def render_daily_plan(todos: list[dict[str, object]]) -> None:
    plan_items = build_daily_plan_items(todos)
    high_count = sum(1 for todo in todos if todo.get("priority") == "高")
    course_counts: dict[str, int] = {}
    dated_todos: list[tuple[date, dict[str, object]]] = []
    for todo in todos:
        course = str(todo.get("course") or "未分类")
        course_counts[course] = course_counts.get(course, 0) + 1
        deadline = parse_deadline(str(todo.get("deadline") or ""))
        if deadline:
            dated_todos.append((deadline, todo))
    dated_todos.sort(key=lambda item: item[0])
    nearest_deadline = dated_todos[0][0].isoformat() if dated_todos else "未设置"
    course_focus = sorted(course_counts.items(), key=lambda item: (-item[1], item[0]))[:4]
    total_tasks = len(todos)
    high_ratio = round(high_count / total_tasks * 100) if total_tasks else 0

    with ui.card().classes("content-card plan-card-full animate-card"):
        with ui.row().classes("card-head plan-head items-center"):
            ui.icon("calendar_month").classes("section-icon")
            with ui.column().classes("gap-0"):
                ui.label("今日学习计划").classes("section-title")
                ui.label(f"{date.today().isoformat()} · 按任务优先级生成").classes("plan-date")
        with ui.element("div").classes("plan-list"):
            for item in plan_items:
                with ui.element("div").classes(f"plan-item plan-tone-{item['tone']}"):
                    with ui.row().classes("items-start no-wrap plan-item-row"):
                        ui.checkbox(value=False).props("dense").classes("plan-check")
                        with ui.column().classes("gap-1 plan-item-copy"):
                            with ui.row().classes("items-center plan-title-row"):
                                ui.label(item["period"]).classes("plan-period")
                            ui.label(item["title"]).classes("plan-item-title")
                            ui.label(item["detail"]).classes("plan-item-detail")
                            ui.label(item["meta"]).classes("plan-item-meta")

        with ui.element("div").classes("plan-support-grid"):
            with ui.element("div").classes("plan-panel plan-overview-panel"):
                ui.label("今日节奏").classes("plan-panel-title")
                with ui.element("div").classes("plan-stat-grid"):
                    for label, value, hint in [
                        ("计划分段", str(len(plan_items)), "上午 / 下午 / 晚上"),
                        ("未完成", str(total_tasks), "纳入今日排序"),
                        ("高优先级", f"{high_ratio}%", f"{high_count} 项需要优先看"),
                        ("最近截止", nearest_deadline, "先处理临近日期"),
                    ]:
                        with ui.element("div").classes("plan-stat"):
                            ui.label(label).classes("plan-stat-label")
                            ui.label(value).classes("plan-stat-value")
                            ui.label(hint).classes("plan-stat-hint")

            with ui.element("div").classes("plan-panel plan-focus-panel"):
                ui.label("课程焦点").classes("plan-panel-title")
                if course_focus:
                    with ui.element("div").classes("plan-course-list"):
                        for course, count in course_focus:
                            width = max(18, round(count / max(total_tasks, 1) * 100))
                            with ui.element("div").classes("plan-course-row"):
                                with ui.row().classes("items-center no-wrap plan-course-head"):
                                    ui.label(course).classes("plan-course-name")
                                    ui.space()
                                    ui.label(f"{count} 项").classes("plan-course-count")
                                with ui.element("div").classes("plan-course-bar"):
                                    ui.element("div").classes("plan-course-fill").style(f"width: {width}%")
                else:
                    ui.label("暂无未完成任务，可以把今天留给笔记整理或复习训练。").classes("plan-empty-note")

            with ui.element("div").classes("plan-panel plan-time-panel"):
                ui.label("时间分配").classes("plan-panel-title")
                time_blocks = [
                    ("上午", "45 分钟", "处理最高优先级任务", "red"),
                    ("下午", "60 分钟", "推进可交付的小步骤", "orange"),
                    ("晚上", "35 分钟", "整理笔记并自测", "purple"),
                ]
                for period, duration, copy, tone in time_blocks[: max(1, len(plan_items))]:
                    with ui.element("div").classes(f"plan-time-row plan-time-{tone}"):
                        ui.label(period).classes("plan-time-period")
                        with ui.column().classes("gap-0 plan-time-copy"):
                            ui.label(duration).classes("plan-time-duration")
                            ui.label(copy).classes("plan-time-note")

            with ui.element("div").classes("plan-panel plan-checklist-panel"):
                ui.label("执行清单").classes("plan-panel-title")
                checklist = [
                    "先完成最近截止或高优先级任务",
                    "每完成一项回到任务管理标记状态",
                    "遇到概念卡住时跳到 AI 问笔记查来源",
                    "晚上用复习训练生成 3 道自测题",
                ]
                for index, text in enumerate(checklist, 1):
                    with ui.row().classes("items-center no-wrap plan-checkline"):
                        ui.label(str(index)).classes("plan-check-index")
                        ui.label(text).classes("plan-check-text")


def render_dashboard_summary(
    counts: dict[str, int],
    unfinished_todos: list[dict[str, object]],
    high: list[dict[str, object]],
    dashboard_tabs,
    app_panels,
) -> None:
    def show_dashboard_tab(value: str):
        def handler() -> None:
            dashboard_tabs.set_value(value)
            ui.run_javascript("window.scrollTo({ top: document.querySelector('.dashboard-stage')?.offsetTop || 0, behavior: 'smooth' });")

        return handler

    def show_app_panel(value: str):
        def handler() -> None:
            app_panels.set_value(value)
            ui.run_javascript(
                "window.scrollTo({ top: 0, behavior: 'smooth' });"
                "window.setTimeout(() => window.studypilotTabs?.syncTopHeader?.(), 50);"
            )

        return handler

    with ui.element("div").classes("summary-layout"):
        with ui.column().classes("summary-copy"):
            ui.label("重要总结").classes("feature-title")
            ui.label("把驾驶舱的四个核心指标放在同一个模块里，像参考页的功能标签一样快速扫读。").classes("feature-copy")
            summary_points = [
                ("grid_view", "今日待办", "聚合所有未完成任务，优先看最近截止事项。"),
                ("priority_high", "高优先级", "标记需要先处理的任务，减少临近截止压力。"),
                ("menu_book", "课程笔记", "统计可用于 RAG 问答的个人学习资料。"),
                ("quiz", "测验记录", "沉淀复习训练结果，帮助后续周报复盘。"),
            ]
            for icon, title, text in summary_points:
                with ui.row().classes("summary-point items-center"):
                    ui.icon(icon).classes("summary-point-icon")
                    with ui.column().classes("gap-0"):
                        ui.label(title).classes("summary-point-title")
                        ui.label(text).classes("summary-point-text")
        with ui.element("div").classes("summary-metrics"):
            metric_card("今日待办", str(len(unfinished_todos)), "未完成任务", "blue", show_dashboard_tab("日程概览"))
            metric_card("高优先级", str(len(high)), "建议优先处理", "amber", show_app_panel("任务管理"))
            metric_card("课程笔记", str(counts.get("notes", 0)), "可用于 RAG", "green", show_app_panel("AI 问笔记"))
            metric_card("测验记录", str(counts.get("quiz_records", 0)), "复习训练", "purple", show_app_panel("复习训练"))


def render_empty_calendar() -> None:
    with ui.card().classes("content-card calendar-card animate-card"):
        with ui.row().classes("card-head items-center"):
            ui.icon("assignment").classes("section-icon")
            ui.label("日程概览").classes("section-title")
        ui.label("当前没有未完成任务，可以进入复习训练生成一组自测题。").classes("empty-copy")


def note_excerpt(text: str, limit: int = 120) -> str:
    cleaned = " ".join((text or "").replace("\n", " ").split())
    return cleaned[:limit].rstrip() + ("..." if len(cleaned) > limit else "")


def plain_markdown_text(text: str) -> str:
    cleaned = re.sub(r"```.*?```", " ", text or "", flags=re.S)
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", cleaned)
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", cleaned)
    cleaned = re.sub(r"[*_>#|~-]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def format_media_duration(seconds: int | float) -> str:
    safe_seconds = max(0, round(seconds or 0))
    minutes, remainder = divmod(safe_seconds, 60)
    return f"{minutes:02d}:{remainder:02d}"


def note_keywords(notes: list[dict[str, object]], limit: int = 12) -> list[str]:
    words: list[str] = []
    stop = {"the", "and", "for", "with", "from", "into", "this", "that", "使用", "可以", "需要", "包括", "进行", "生成"}
    for note in notes:
        text = f"{note.get('title', '')} {note.get('tags', '')} {note.get('content', '')}"
        words.extend(re.findall(r"[A-Za-z][A-Za-z0-9+\-]{2,}|[\u4e00-\u9fff]{2,8}", text))
    counts: dict[str, int] = {}
    for word in words:
        normalized = word.strip()
        if not normalized or normalized.lower() in stop:
            continue
        counts[normalized] = counts.get(normalized, 0) + 1
    return [item for item, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:limit]]


def studio_sources(course: str | None = None, limit: int = 9) -> list[dict[str, object]]:
    notes = list_notes(None if course in {None, "全部课程", "未分类"} else course)
    demo_notes = [
        note
        for note in notes
        if "示例素材" in str(note.get("tags") or "") or "2026 AI 工具" in str(note.get("title") or "")
    ]
    if len(demo_notes) >= min(limit, 3):
        return sorted(demo_notes, key=lambda note: (0 if str(note.get("title") or "").startswith("2026 AI 工具综合") else 1, int(note.get("id") or 0)))[:limit]
    return sorted(notes, key=lambda note: len(str(note.get("content") or "")), reverse=True)[:limit]


def studio_source_notice(notes: list[dict[str, object]]) -> str:
    if notes:
        return ""
    return (
        "还没有可用来源。请先在左侧“来源”面板上传 PDF、网页、文本或笔记；"
        "包含事实、日期、数值、对比和案例的材料最适合生成 Studio 输出。"
    )


def compact_note_payload(notes: list[dict[str, object]], limit: int = 9, content_limit: int = 720) -> list[dict[str, str]]:
    payload: list[dict[str, str]] = []
    for note in notes[:limit]:
        payload.append(
            {
                "course": str(note.get("course") or "未分类"),
                "title": str(note.get("title") or "未命名笔记"),
                "tags": str(note.get("tags") or ""),
                "content": note_excerpt(str(note.get("content") or ""), content_limit),
            }
        )
    return payload


def normalize_mindmap_branch(item: object, fallback_title: str, fallback_course: str) -> dict[str, object]:
    if not isinstance(item, dict):
        return {
            "title": fallback_title,
            "course": fallback_course,
            "summary": str(item or fallback_title)[:120],
            "nodes": [],
            "review_questions": [],
        }
    raw_nodes = item.get("nodes") or item.get("children") or item.get("points") or []
    nodes: list[dict[str, object]] = []
    if isinstance(raw_nodes, list):
        for node in raw_nodes[:5]:
            if isinstance(node, dict):
                label = str(node.get("label") or node.get("title") or node.get("name") or "知识点").strip()
                details = node.get("details") or node.get("children") or node.get("evidence") or []
                if isinstance(details, str):
                    detail_list = [details]
                elif isinstance(details, list):
                    detail_list = [str(detail) for detail in details[:4] if str(detail).strip()]
                else:
                    detail_list = []
            else:
                label = str(node).strip()
                detail_list = []
            if label:
                nodes.append({"label": label[:36], "details": detail_list})
    raw_questions = item.get("review_questions") or item.get("questions") or []
    if isinstance(raw_questions, str):
        review_questions = [raw_questions]
    elif isinstance(raw_questions, list):
        review_questions = [str(question) for question in raw_questions[:3] if str(question).strip()]
    else:
        review_questions = []
    return {
        "title": str(item.get("title") or fallback_title)[:48],
        "course": str(item.get("course") or fallback_course)[:24],
        "summary": str(item.get("summary") or item.get("description") or fallback_title)[:180],
        "nodes": nodes,
        "review_questions": review_questions,
    }


def local_mindmap_data(title: str, notes: list[dict[str, object]]) -> dict[str, object]:
    payload = compact_note_payload(notes)
    root_keywords = note_keywords(notes, 8)
    root = root_keywords[0] if root_keywords else (str(notes[0].get("course") or title) if notes else title)
    modules: list[dict[str, object]] = []
    for note in notes[:9]:
        local_keywords = note_keywords([note], 5)
        content = str(note.get("content") or "")
        sentences = [part.strip(" 。；;") for part in re.split(r"[。；;\n]", content) if part.strip()]
        details = sentences[:4] or [note_excerpt(content, 80)]
        nodes = []
        for index, keyword in enumerate(local_keywords[:4] or [str(note.get("title") or "核心概念")]):
            nodes.append({"label": keyword[:36], "details": details[index : index + 2] or details[:1]})
        modules.append(
            {
                "title": str(note.get("title") or "未命名笔记")[:48],
                "course": str(note.get("course") or "未分类")[:24],
                "summary": note_excerpt(content, 150),
                "nodes": nodes,
                "review_questions": [
                    f"{note.get('title')} 的核心概念是什么？",
                    f"{note.get('title')} 与其他笔记有什么联系？",
                    "哪些细节需要回到原始来源核查？",
                ],
            }
        )
    return {
        "root": root,
        "subtitle": f"{len(notes)} 条笔记 · {len(root_keywords)} 个关键词 · 本地生成",
        "modules": modules,
        "source": "local",
    }


def ai_mindmap_data(title: str, notes: list[dict[str, object]]) -> dict[str, object] | None:
    if not notes or not has_api_key():
        return None
    prompt = (
        "请根据这些学习笔记生成一个适合网页展示的思维导图 JSON。"
        "必须按不同笔记分模块，不要把所有笔记混在一个模块里。"
        "每个模块保留课程、笔记标题、摘要、3到5个知识节点、每个节点1到3条证据/解释、2到3个复习问题。"
        "只输出 JSON，结构必须是："
        '{"root":"主题","subtitle":"简短说明","modules":[{"title":"笔记标题","course":"课程","summary":"摘要","nodes":[{"label":"节点","details":["解释"]}],"review_questions":["问题"]}]}。'
        f"\n\n用户点击的 Studio 输出：{title}\n\n笔记来源：\n"
        f"{json.dumps(compact_note_payload(notes), ensure_ascii=False)}"
    )
    data = chat_json(prompt, "你是严谨的学习笔记结构化助手，只输出合法 JSON。")
    if not isinstance(data, dict):
        return None
    modules_raw = data.get("modules")
    if not isinstance(modules_raw, list) or not modules_raw:
        return None
    payload = compact_note_payload(notes)
    modules = []
    for index, item in enumerate(modules_raw[:9]):
        fallback = payload[min(index, len(payload) - 1)] if payload else {"title": "学习资料", "course": "未分类"}
        modules.append(normalize_mindmap_branch(item, fallback["title"], fallback["course"]))
    root = str(data.get("root") or note_keywords(notes, 1)[0] if note_keywords(notes, 1) else title)[:48]
    return {
        "root": root,
        "subtitle": str(data.get("subtitle") or f"{len(notes)} 条笔记 · AI 生成").strip()[:80],
        "modules": modules,
        "source": "ai",
    }


def build_mindmap_data(title: str, notes: list[dict[str, object]], use_ai: bool = True) -> dict[str, object]:
    return (ai_mindmap_data(title, notes) if use_ai else None) or local_mindmap_data(title, notes)


def mindmap_markdown(data: dict[str, object]) -> str:
    modules = data.get("modules") if isinstance(data.get("modules"), list) else []
    lines = [f"### 思维导图：{data.get('root') or '学习资料'}", "", str(data.get("subtitle") or ""), ""]
    for module in modules[:8]:
        if not isinstance(module, dict):
            continue
        lines.append(f"- **{module.get('title') or '笔记模块'}**（{module.get('course') or '未分类'}）")
        if module.get("summary"):
            lines.append(f"  - 摘要：{module.get('summary')}")
        nodes = module.get("nodes") if isinstance(module.get("nodes"), list) else []
        for node in nodes[:4]:
            if not isinstance(node, dict):
                continue
            lines.append(f"  - {node.get('label') or '知识点'}")
            details = node.get("details") if isinstance(node.get("details"), list) else []
            for detail in details[:2]:
                lines.append(f"    - {detail}")
    return "\n".join(lines).strip()


def local_report_markdown(title: str, notes: list[dict[str, object]]) -> str:
    keywords = note_keywords(notes)
    key_line = "、".join(keywords[:8]) or "核心概念、流程、证据、复习问题"
    source_lines = "\n".join(
        f"- **[{note.get('course')}] {note.get('title')}**：{note_excerpt(str(note.get('content') or ''), 150)}"
        for note in notes[:9]
    )
    modules = []
    for index, note in enumerate(notes[:9], 1):
        content = str(note.get("content") or "")
        local_keys = "、".join(note_keywords([note], 4)) or "核心概念"
        modules.append(
            f"#### {index}. {note.get('title')}\n"
            f"- **课程/模块：** {note.get('course') or '未分类'}\n"
            f"- **关键词：** {local_keys}\n"
            f"- **笔记完善摘要：** {note_excerpt(content, 260)}\n"
            f"- **可继续追问：** {note.get('title')} 的关键证据、应用场景和风险边界分别是什么？"
        )

    return (
        f"### {title or '学习资料'} Briefing Doc\n\n"
        "#### 1. 执行摘要\n"
        f"本报告基于 {len(notes)} 条来源生成，围绕 {key_line} 整理为可复习、可展示、可继续追问的学习材料。"
        "整体结论是：先按主题拆分来源，再抽取概念、证据、适用场景和风险，最后把结论转化为复习问题。\n\n"
        "#### 2. 来源概览\n"
        f"{source_lines}\n\n"
        "#### 3. 分模块笔记完善\n"
        + "\n\n".join(modules)
        + "\n\n#### 4. 综合结论\n"
        "- **学习价值：** 这些来源适合用于课堂展示、个人复习、问答检索和后续生成测验。\n"
        "- **使用方法：** 先读每个模块摘要，再点击 AI 问笔记追问细节，最后用复习训练检查掌握度。\n"
        "- **核查提醒：** 涉及日期、排名、工具能力和案例数据时，应回到原始来源确认。"
    )


def ai_report_markdown(title: str, notes: list[dict[str, object]]) -> str | None:
    if not notes or not has_api_key():
        return None
    prompt = (
        "请根据用户的学习笔记生成一份中文 Briefing Doc 报告。"
        "要求：1) 只基于给定笔记，不编造外部事实；2) 先给执行摘要；3) 按不同笔记分模块完善总结；"
        "4) 提炼关键发现、对比关系、风险边界和复习建议；5) 每个模块要写出可继续追问的问题；"
        "6) 输出 Markdown，标题层级从 ### 和 #### 开始，不要输出代码块。"
        f"\n\n用户点击的 Studio 输出：{title}\n\n笔记来源 JSON：\n"
        f"{json.dumps(compact_note_payload(notes, limit=9, content_limit=1300), ensure_ascii=False)}"
    )
    text = chat_text(prompt, "你是严谨的学习报告助手，会把个人笔记完善成结构化报告，但不会代写提交作业。")
    cleaned = (text or "").strip()
    if re.search(r"(?:\s*[,，]\s*){8,}", cleaned):
        return None
    if not cleaned or len(cleaned) < 120:
        return None
    return cleaned


def build_report_markdown(title: str, notes: list[dict[str, object]], use_ai: bool = True) -> tuple[str, bool]:
    ai_text = ai_report_markdown(title, notes) if use_ai else None
    if ai_text:
        return ai_text, True
    return local_report_markdown(title, notes), False


def build_flashcards_for_note(note: dict[str, object]) -> list[dict[str, str]]:
    title = str(note.get("title") or "学习资料")
    content = str(note.get("content") or "")
    keywords = note_keywords([note], 6)
    primary = keywords[0] if keywords else title
    secondary = keywords[1] if len(keywords) > 1 else primary
    tertiary = keywords[2] if len(keywords) > 2 else secondary
    summary = note_excerpt(content, 150) or f"{title} 的核心内容可以从标题和来源中继续提炼。"
    cards = [
        {
            "question": f"{title} 中最核心的概念是什么？",
            "answer": primary,
            "explanation": summary,
            "source": title,
            "course": str(note.get("course") or "未分类"),
        },
        {
            "question": f"{title} 里，{primary} 更像是在说明什么？",
            "answer": "它是帮助理解这条笔记的关键线索。",
            "explanation": note_excerpt(content, 120) or summary,
            "source": title,
            "course": str(note.get("course") or "未分类"),
        },
        {
            "question": f"如果把 {title} 压缩成一句复习提示，应该怎么说？",
            "answer": note_excerpt(content, 72) or primary,
            "explanation": note_excerpt(content, 160) or summary,
            "source": title,
            "course": str(note.get("course") or "未分类"),
        },
        {
            "question": f"{title} 适合用 {secondary} 和 {tertiary} 来怎么复习？",
            "answer": f"把它们作为线索，串起概念、案例和应用。",
            "explanation": summary,
            "source": title,
            "course": str(note.get("course") or "未分类"),
        },
    ]
    return cards


def build_flashcards(notes: list[dict[str, object]], limit: int = 12) -> list[dict[str, str]]:
    cards: list[dict[str, str]] = []
    for note in notes[:4]:
        cards.extend(build_flashcards_for_note(note))
        if len(cards) >= limit:
            break
    if not cards:
        cards.append(
            {
                "question": "当前还没有可用来源，应该先做什么？",
                "answer": "先添加笔记或来源。",
                "explanation": "闪卡会根据左侧来源自动生成问题、答案和解释。添加资料后可以重新生成。",
                "source": "暂无来源",
                "course": "未分类",
            }
        )
    return cards


def build_studio_markdown(kind: str, title: str, notes: list[dict[str, object]], answer_text: str = "") -> str:
    missing = studio_source_notice(notes)
    if missing and not (kind == "audio" and answer_text.strip()):
        return f"### {title}\n\n{missing}"

    keywords = note_keywords(notes)
    source_lines = "\n".join(
        f"- [{note.get('course')}] {note.get('title')}：{note_excerpt(str(note.get('content') or ''), 96)}"
        for note in notes[:5]
    )
    key_line = "、".join(keywords[:8]) or "核心概念、流程、证据、复习问题"
    main_title = str(notes[0].get("title") or title or "学习资料") if notes else str(title or "学习资料")

    if kind == "audio":
        if answer_text.strip():
            return (
                "### AI 答案朗读\n\n"
                "点击播放后，将把你刚刚提问得到的回答以声音形式读出来。\n\n"
                f"{answer_text.strip()}"
            )
        return (
            "### AI 播客脚本\n\n"
            f"**节目主题：** {main_title}\n\n"
            "**Host A：** 今天我们用几分钟梳理这组资料的主线。核心关键词包括 "
            f"{key_line}。\n\n"
            "**Host B：** 我先抓一个重点：资料不是零散事实，而是在说明概念、流程和应用场景之间的关系。\n\n"
            "**Host A：** 如果把它当成复习材料，建议先听结论，再回到来源确认细节。\n\n"
            "**Host B：** 三个适合追问的问题：为什么重要？有哪些步骤？哪些例子能证明它？\n\n"
            "**可引用来源：**\n"
            f"{source_lines}"
        )
    if kind == "mindmap":
        return mindmap_markdown(build_mindmap_data(title, notes, use_ai=False))
    if kind == "report":
        return local_report_markdown(title, notes)
    if kind == "flashcards":
        cards = []
        for index, card in enumerate(build_flashcards(notes, 8), 1):
            cards.append(
                f"**卡片 {index}**\n"
                f"- 正面：{card['question']}\n"
                f"- 背面：{card['answer']}。{card['explanation']}"
            )
        return "### 闪卡\n\n" + "\n\n".join(cards)
    if kind == "quiz":
        rows = []
        for index, note in enumerate(notes[:5], 1):
            rows.append(
                f"{index}. **题目：** {note.get('title')} 最能说明哪个知识点？\n"
                f"   **参考答案：** {note_excerpt(str(note.get('content') or ''), 140)}"
            )
        return "### 测验\n\n" + "\n\n".join(rows)
    if kind == "table":
        lines = [
            "### 数据表格",
            "",
            "| 来源 | 课程 | 关键词 | 可复习问题 |",
            "| --- | --- | --- | --- |",
        ]
        for note in notes[:8]:
            local_keywords = "、".join(note_keywords([note], 3))
            lines.append(
                f"| {note.get('title')} | {note.get('course')} | {local_keywords or '核心概念'} | {note.get('title')} 的关键证据是什么？ |"
            )
        return "\n".join(lines)
    return f"### {title}\n\n已基于来源生成内容。\n\n{source_lines}"


STUDIO_PROFILES: dict[str, dict[str, str]] = {
    "audio": {"icon": "graphic_eq", "tone": "blue", "viewer": "音频概览", "meta": "7:52 · 详细说明"},
    "mindmap": {"icon": "account_tree", "tone": "pink", "viewer": "应用", "meta": "思维导图"},
    "report": {"icon": "article", "tone": "yellow", "viewer": "报告", "meta": "Briefing Doc"},
    "flashcards": {"icon": "style", "tone": "orange", "viewer": "闪卡", "meta": "学习卡片"},
    "quiz": {"icon": "quiz", "tone": "cyan", "viewer": "测验", "meta": "练习题"},
    "table": {"icon": "table_chart", "tone": "blue", "viewer": "数据表格", "meta": "表格"},
}


def studio_output_title(kind: str, fallback: str, notes: list[dict[str, object]]) -> str:
    main_title = str(notes[0].get("title") or "学习资料") if notes else fallback
    keywords = note_keywords(notes, 4)
    focus = keywords[0] if keywords else main_title
    if kind == "audio":
        return f"{focus} 深入探究"
    if kind == "mindmap":
        return f"Mindmap: {focus}"
    if kind == "report":
        return f"{main_title[:34]}: Three Themes to Know"
    if kind == "flashcards":
        return f"Be Smart: Key Terms in {focus}"
    if kind == "quiz":
        return f"{focus} Current Affairs Quiz"
    if kind == "table":
        return f"{focus} Evidence Table"
    return fallback


def build_studio_output(kind: str, title: str, notes: list[dict[str, object]], answer_text: str = "", use_ai: bool = False) -> dict[str, object]:
    profile = STUDIO_PROFILES.get(kind, STUDIO_PROFILES["report"])
    source_count = len(notes)
    source_meta = f"{source_count} 个来源" if source_count else "需要来源"
    meta = f"{profile['meta']} · {source_meta} · 刚刚" if kind in {"audio", "report", "flashcards", "quiz", "table"} else f"{source_meta} · 刚刚"
    mindmap_data = build_mindmap_data(title, notes, use_ai=use_ai) if kind == "mindmap" else None
    flashcard_data = build_flashcards(notes) if kind == "flashcards" else None
    generated_by_ai = False
    if kind == "report":
        markdown, generated_by_ai = build_report_markdown(title, notes, use_ai=use_ai)
    else:
        markdown = mindmap_markdown(mindmap_data) if mindmap_data else build_studio_markdown(kind, title, notes, answer_text if kind == "audio" else "")
    if kind == "mindmap" and mindmap_data and mindmap_data.get("source") == "ai":
        meta = f"AI 生成 · {source_meta} · 刚刚"
    if kind == "report" and generated_by_ai:
        meta = f"AI 生成 · Briefing Doc · {source_meta} · 刚刚"
    speech_text = plain_markdown_text(answer_text if kind == "audio" and answer_text.strip() else markdown)
    return {
        "kind": kind,
        "title": studio_output_title(kind, title, notes),
        "fallback_title": title,
        "meta": meta,
        "source_count": source_count,
        "profile": profile,
        "markdown": markdown,
        "speech_text": speech_text,
        "notes": notes,
        "mindmap": mindmap_data,
        "flashcards": flashcard_data,
        "generated_by_ai": generated_by_ai,
    }


def ensure_studio_demo_source() -> bool:
    demo_notes = {
        "2026 AI 工具综合排名与应用指南": (
            "2026 年 AI 工具生态进入从通用聊天到专业工作流的阶段。ChatGPT、Claude、Gemini、NotebookLM、Microsoft Copilot、Cursor、GitHub Copilot、NxCode、Anki 和 Canva AI 等工具分别覆盖研究、学习、编程、自动化和内容生产。"
            "课程小组在 2026 年 1 月至 6 月记录了 48 次工具试用、12 份学习报告、9 个自动化案例和 6 次课堂演示。综合评价维度包括信息准确性、来源可追溯性、任务完成速度、学习迁移效果、隐私风险和团队协作成本。"
            "总体结论是：学习类工具要优先看来源管理和复习闭环，编程类工具要看工程上下文和可控修改，办公类工具要看协作、权限和格式稳定性。"
        ),
        "2026 AI 工具评估指标与排名方法": (
            "本模块把 AI 工具评估拆成六个指标：准确性、来源可追溯性、任务完成速度、学习迁移效果、隐私风险和协作成本。准确性不是只看答案是否流畅，而是看事实、日期、数值和引用是否可核查。"
            "来源可追溯性决定工具是否适合学习报告和课堂展示；任务速度适合衡量摘要、表格整理、代码补全等重复工作；学习迁移效果关注学生能否从 AI 输出回到自己的理解。"
            "排名方法采用场景加权：学习场景提高来源和复习权重，编程场景提高上下文和测试权重，办公场景提高协作和格式权重。"
        ),
        "NotebookLM 学习场景使用笔记": (
            "NotebookLM 适合来源驱动的学习工作流：先上传可信材料，再围绕来源问答，然后生成音频概览、报告、思维导图、闪卡、测验和数据表格。它的优势在于把回答限定在来源内部，降低无依据发挥的概率。"
            "在个人学习助手中，可以把 NotebookLM 的 Studio 思路转化为本地功能：来源列表负责保存课程笔记，AI 问笔记负责检索和回答，Studio 负责把笔记变成不同格式的复习材料。"
            "使用时要避免只看摘要不看来源，重要日期、排名、政策和工具能力必须回到原文核查。"
        ),
        "ChatGPT Claude Gemini 通用模型对比": (
            "ChatGPT、Claude 和 Gemini 代表通用大模型助手，适合概念解释、写作润色、代码理解、多轮讨论和学习计划拆解。它们的共同优势是表达能力强、迁移场景广，缺点是如果没有来源约束，容易给出看似合理但不可核查的内容。"
            "学习场景中，通用模型适合先帮助学生理解问题、生成提纲、提出复习问题，但最终答案应结合个人笔记和原始资料。"
            "对比时不要只看单次回答质量，还要看长上下文保持、引用处理、隐私边界、中文表达和是否能稳定遵守格式。"
        ),
        "AI 编程工具 Cursor Copilot NxCode 笔记": (
            "Cursor、GitHub Copilot 和 NxCode 代表 AI 编程助手方向。Cursor 强调项目上下文中的修改与解释，GitHub Copilot 适合补全、测试建议和 IDE 内协作，NxCode 的趋势提示显示端到端任务执行正在成为新方向。"
            "评估编程工具时，要看它是否能理解现有代码结构、遵守项目风格、运行测试、解释风险并避免大范围无关重构。"
            "学习者使用编程助手时，应把 AI 生成代码当作草稿：先理解改动意图，再运行测试，最后用自己的语言记录 bug 原因和修复路径。"
        ),
        "AI 自动化工具 n8n Make Agent 工作流": (
            "n8n、Make 与 AI Agent 可把资料收集、摘要、表格整理、邮件提醒和知识库更新串联起来。自动化的核心不是让 AI 代替所有判断，而是把稳定步骤流程化，把不稳定判断留给人工确认。"
            "典型学习自动化包括：定时收集课程资料、生成摘要、写入笔记库、提醒复习、根据错题生成练习。"
            "风险点包括权限过大、误发信息、循环调用成本、个人资料上传边界不清。设计自动化时应设置人工确认、日志记录和失败回退。"
        ),
        "AI 学习工具 Anki Canva Microsoft Copilot": (
            "Anki 适合长期记忆，核心机制是间隔重复和主动回忆；Canva AI 适合把学习内容转成海报、演示和视觉素材；Microsoft Copilot 适合文档、表格和会议场景中的办公辅助。"
            "这些工具不一定直接回答复杂问题，但能补齐学习产物的不同环节：Anki 负责记忆巩固，Canva 负责展示表达，Copilot 负责办公整理。"
            "把它们组合使用时，可以先用来源型工具生成结构，再用 Anki 拆成卡片，用 Canva 做展示，用 Copilot 整理成报告或表格。"
        ),
        "AI 工具风险与学术诚信护栏": (
            "2026 年上半年常见风险包括引用不完整、模型幻觉、过度依赖生成答案、隐私边界不清和把 AI 输出直接当作作业提交。学习助手应把高风险请求转化为提纲、检查清单、讲解和自测，而不是直接代写。"
            "学术诚信护栏的目标不是阻止学习，而是保护学习过程：帮助学生理解资料、改进表达、发现遗漏，但保留学生自己的判断和最终表达。"
            "报告生成时应标明来源数量、保留核查提醒，并把结论写成可追问的问题。"
        ),
        "AI 工具学习报告写作模板": (
            "一份适合课堂展示的 AI 工具学习报告可以分为五部分：执行摘要、工具分类、核心发现、风险与边界、学习建议。执行摘要回答为什么重要；工具分类说明不同工具适合不同场景；核心发现用来源中的事实支撑；风险与边界提醒不要过度相信生成结果。"
            "写作时要避免堆砌工具名称，应按学习任务组织内容，例如资料理解、知识整理、复习训练、展示表达和自动化提醒。"
            "结尾建议给出下一步行动：补充来源、生成测验、回到原文核查、记录个人反思。"
        ),
    }
    changed = False
    existing_titles = {str(note.get("title") or "") for note in list_notes()}
    with get_connection() as conn:
        existing_main = conn.execute("SELECT id, content FROM notes WHERE title = ? LIMIT 1", ("2026 AI 工具综合排名与应用指南",)).fetchone()
        if existing_main and "【报告素材补充】" not in str(existing_main["content"] or ""):
            supplement = "\n\n【报告素材补充】" + "\n\n".join(f"{title}：{content}" for title, content in list(demo_notes.items())[1:])
            conn.execute(
                "UPDATE notes SET content = ?, tags = ?, updated_at = ? WHERE id = ?",
                (
                    f"{existing_main['content']}{supplement}",
                    "NotebookLM,AI工具,2026,Studio,示例素材,报告",
                    datetime.now().isoformat(timespec="seconds"),
                    existing_main["id"],
                ),
            )
            changed = True
        conn.commit()
    for title, content in demo_notes.items():
        if title in existing_titles:
            continue
        add_note("AI应用开发", title, content, "NotebookLM,AI工具,2026,Studio,示例素材,报告")
        changed = True
    if changed:
        add_note_upload("2026_AI_tools_studio_demo.md", "内置示例素材", "AI应用开发", len(demo_notes), "用于演示 NotebookLM Studio 报告、思维导图和九类输出。")
        build_vector_index()
    return changed


def ensure_enriched_studio_notes() -> bool:
    marker = "【思维导图补充】"
    enrichments = {
        "Python 基础语法与变量": (
            "知识结构可以拆成运行规则、数据对象、变量绑定和调试方法四个模块。运行规则强调解释器按语句顺序执行，代码块依靠缩进表示层级；"
            "数据对象包括数字、字符串、布尔值、容器和 None；变量绑定说明变量名只是引用，赋值会让名称指向对象；调试方法包括 type、print、断点和小步运行。"
            "复习时先能读懂一段包含 if、for、函数调用的代码，再练习解释每个变量在执行过程中的变化。易错点是把变量当作盒子、忽略可变对象共享引用、混用 Tab 和空格。"
        ),
        "列表、字典与推导式": (
            "这部分适合按数据结构选择、常用操作、遍历方式、推导式边界和应用场景画图。列表强调顺序、索引、切片和可变性；字典强调键值映射、快速查找和结构化记录。"
            "推导式用于短小清晰的映射与过滤，超过两层嵌套时应退回普通循环。典型题型包括统计词频、按课程分组任务、筛选高优先级待办、把表格行转换为字典列表。"
            "复习路径是先写普通循环，再改写为推导式，最后比较可读性和性能。"
        ),
        "函数参数与返回值": (
            "函数可以拆成定义、调用、参数传递、返回值和副作用五个节点。位置参数负责顺序匹配，关键字参数提升可读性，默认参数表达可选配置，args/kwargs 适合包装器或转发参数。"
            "返回值是函数对外输出，print 只是显示信息；函数应尽量让输入来自参数、输出来自 return。设计函数时可用单一职责、明确命名、少依赖全局状态来检查质量。"
            "常见错题包括默认可变参数、分支漏 return、把 None 当有效结果、参数顺序导致 TypeError。"
        ),
        "文件读写与异常处理": (
            "文件读写思维导图可从路径、模式、编码、上下文管理器和异常恢复展开。with open 保证资源释放，r/w/a/rb/wb 决定读写行为，encoding 决定文本解码方式。"
            "异常处理按 try 捕获风险、except 给出处理、else 表示成功路径、finally 清理资源组织。项目里上传笔记、读取 Excel、写入数据库日志都需要考虑文件不存在、编码不匹配、权限不足和空文件。"
            "复习时可用一个 CSV 导入任务串联读取、解析、校验、报错提示和保存。"
        ),
        "pandas 表格处理": (
            "pandas 的知识结构包括数据读取、DataFrame/Series、数据清洗、分组统计、合并导出和数据库对接。read_excel/read_csv 是输入入口，fillna/dropna/astype 处理质量问题，"
            "groupby/agg 用于统计，merge 用于关联不同表，to_excel/to_sql 用于输出。个人学习助手中 todo、notes、quiz_records 都可以抽象为表格。"
            "画图时可把数据流表示为 Excel/CSV -> DataFrame -> 清洗转换 -> 统计分析 -> SQLite/界面展示。"
        ),
        "RAG 基本流程": (
            "RAG 思维导图应拆成数据准备、索引构建、检索召回、上下文组装、生成回答和质量评估。数据准备包括上传、清洗、分段和 metadata；索引构建包括 embedding 与向量库写入；"
            "检索召回包括关键词、向量和混合检索；上下文组装要控制长度、保留来源；生成回答要求模型只基于资料并给出下一步复习建议。评估维度包括命中率、来源可追溯、答案忠实度和响应速度。"
        ),
        "Embedding 与向量检索": (
            "Embedding 模块可拆成语义表示、相似度计算、索引召回、metadata 过滤和混合检索。语义表示把文本映射到高维向量，相似度可用余弦、内积或距离；"
            "metadata 过滤让用户只查某门课，混合检索弥补专有名词、日期、编号等关键词召回优势。复习时要能解释为什么“deadline”和“截止日期”能被一起检索，也要知道向量检索可能误召回语义相近但事实不同的片段。"
        ),
        "Chroma 向量库": (
            "Chroma 可以按客户端、collection、documents、ids、metadatas 和 query 流程组织。PersistentClient 负责持久化目录，collection 管理一组向量文档，ids 保证片段唯一，metadata 存课程和标题。"
            "查询时 n_results 控制返回数量，where 负责过滤。工程注意点包括重复写入、索引重建、数据删除同步、模型缺失时降级关键词检索。"
        ),
        "Agent 路由": (
            "Agent 路由适合画成入口意图识别、路由决策、工具执行、结果整合和反馈循环。意图包括添加任务、查看待办、查询笔记、生成计划和生成测验。"
            "规则路由适合课程名、日期、关键词明确的输入；大模型路由适合自然表达复杂的输入。边界设计是主智能体只负责判断与分发，TaskAgent/NoteAgent/PlanAgent/QuizAgent 负责领域逻辑，tools 负责数据库操作。"
        ),
        "提示词、可信度与学术诚信护栏": (
            "这条笔记可以拆成提示词约束、来源依据、可信度标签、风险识别和替代帮助。提示词要求基于笔记、给出来源、解释推理和复习建议；可信度根据检索分数、来源数量和问题匹配程度判断。"
            "学术诚信护栏不是拒绝学习，而是把直接代写改为思路、提纲、检查清单、例题讲解和自测。复习时要能区分安全请求、需要提醒的请求和应拒绝的请求。"
        ),
        "英语时态与句子结构": (
            "英语语法思维导图可按句子成分、谓语动词、时态系统、从句修饰和写作检查展开。句子主干是主语加谓语，宾语、表语和补语补足意义，定语和状语提供修饰信息。"
            "时态复习先判断时间，再判断动作状态。长难句分析步骤是找谓语、确定主语宾语、划出从句、还原修饰关系。写作检查关注主谓一致、时态一致和代词指代。"
        ),
        "虚拟语气": (
            "虚拟语气可以按现在相反、过去相反、将来假设、愿望建议和固定句型分类。现在相反常用 If + were/did + would do，过去相反常用 had done + would have done。"
            "建议要求类动词后的 that 从句可用 should do 或动词原形。解题路径是先判断是否真实，再判断时间，最后选择谓语形式。易错点包括混淆真实条件句、忘记主句 would、把 was 用在正式虚拟结构中。"
        ),
        "Unit5 高频词与学习表达": (
            "词汇思维导图适合按学习计划场景组织：deadline 对应截止时间，priority 对应优先级，schedule 对应日程，review 对应复习，evidence/source 对应资料依据。"
            "每个单词都连接搭配和例句，例如 meet a deadline、set priorities、review notes、summarize the main idea。复习路径是词义、搭配、例句、替换表达和口头复述。"
        ),
        "阅读理解技巧": (
            "阅读理解可以分成审题、定位、同义替换、题型判断、证据核查和错题复盘。细节题关注定位句，主旨题关注段落主题，推理题要求基于文本信息，词义题结合上下文和转折词。"
            "错题记录应包含题型、定位句、错误选项诱因和正确依据。常见陷阱包括否定词、绝对化表达、偷换概念、把例子当结论。"
        ),
        "写作连接词与听力跟读": (
            "写作部分可按段落结构、连接词功能、观点展开和检查清单绘图。连接词分为递进、转折、因果、举例和总结，真正作用是表达逻辑，不是堆砌高级词。"
            "听力跟读可按听大意、逐句模仿、标记弱读连读、录音对比和复述输出展开。两者共同目标是让输入变成可表达内容。"
        ),
    }
    changed = False
    with get_connection() as conn:
        for title, addition in enrichments.items():
            row = conn.execute("SELECT id, content FROM notes WHERE title = ? LIMIT 1", (title,)).fetchone()
            if not row:
                continue
            content = str(row["content"] or "")
            if marker in content:
                continue
            conn.execute(
                "UPDATE notes SET content = ?, updated_at = ? WHERE id = ?",
                (f"{content}\n\n{marker}{addition}", datetime.now().isoformat(timespec="seconds"), row["id"]),
            )
            changed = True
        conn.commit()
    if changed:
        try:
            build_vector_index()
        except (sqlite3.Error, RuntimeError, ValueError):
            pass
    return changed


@ui.refreshable
def render_dashboard(app_panels) -> None:
    counts = table_counts()
    unfinished_todos = list_todos(status="未完成")
    calendar_todos = list_todos()
    high = [todo for todo in unfinished_todos if todo.get("priority") == "高"]

    with ui.element("div").classes("dashboard-shell"):
        render_mineradio_float()
        render_hero(len(unfinished_todos), len(high))
        with ui.element("div").classes("dashboard-stage animate-card"):
            with ui.tabs().classes("dashboard-feature-tabs") as dashboard_tabs:
                ui.tab("重要总结", icon="dashboard")
                ui.tab("今日学习计划", icon="calendar_month")
                ui.tab("日程概览", icon="event")

            with ui.tab_panels(dashboard_tabs, value="重要总结").classes("dashboard-feature-panels"):
                with ui.tab_panel("重要总结").classes("dashboard-feature-panel"):
                    render_dashboard_summary(counts, unfinished_todos, high, dashboard_tabs, app_panels)
                with ui.tab_panel("今日学习计划").classes("dashboard-feature-panel"):
                    render_daily_plan(unfinished_todos)
                with ui.tab_panel("日程概览").classes("dashboard-feature-panel"):
                    if calendar_todos:
                        render_task_calendar(calendar_todos)
                    else:
                        render_empty_calendar()


def render_qa(app_panels) -> None:
    ensure_studio_demo_source()
    ensure_enriched_studio_notes()
    courses = ["全部课程"] + sorted({note["course"] for note in list_notes()})
    course = ui.select(courses, value="全部课程", label="课程范围").classes("hidden-course-select")
    notes_count = len(list_notes())
    upload_count = len(list_note_uploads())
    studio_outputs: list[dict[str, object]] = []
    latest_answer: dict[str, str] = {"question": "", "answer": ""}

    def set_question_prompt(prompt: str):
        def handler(event=None) -> None:
            question.set_value(prompt)

        return handler

    def show_app_panel(label: str):
        def handler(event=None) -> None:
            app_panels.set_value(label)
            ui.run_javascript(
                "window.scrollTo({ top: 0, behavior: 'smooth' });"
                "window.setTimeout(() => window.studypilotTabs?.syncTopHeader?.(), 50);"
            )

        return handler

    def current_studio_course() -> str | None:
        try:
            selected = course.value
        except NameError:
            selected = "全部课程"
        return None if selected == "全部课程" else selected

    def show_studio_loading(message: str, detail: str = "正在读取不同笔记模块，并整理节点、证据和复习问题。") -> None:
        try:
            studio_grid_box.classes(add="nlm-studio-hidden")
            studio_sep.classes(add="nlm-studio-hidden")
        except NameError:
            pass
        studio_output_box.clear()
        with studio_output_box:
            with ui.element("div").classes("nlm-studio-loading"):
                ui.spinner(size="32px", color="purple")
                ui.label(message).classes("nlm-studio-loading-title")
                ui.label(detail).classes("nlm-studio-loading-copy")

    async def create_studio_output(kind: str, title: str, notes: list[dict[str, object]], answer_text: str = "", use_ai: bool = False) -> dict[str, object]:
        if kind == "mindmap" and use_ai:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(build_studio_output, kind, title, notes, answer_text, True),
                    timeout=10,
                )
            except (asyncio.TimeoutError, RuntimeError, ValueError):
                return await asyncio.to_thread(build_studio_output, kind, title, notes, answer_text, False)
        if kind == "report" and use_ai:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(build_studio_output, kind, title, notes, answer_text, True),
                    timeout=28,
                )
            except (asyncio.TimeoutError, RuntimeError, ValueError):
                return await asyncio.to_thread(build_studio_output, kind, title, notes, answer_text, False)
        return await asyncio.to_thread(build_studio_output, kind, title, notes, answer_text, use_ai)

    def replace_studio_output(output: dict[str, object]) -> None:
        studio_outputs[:] = [item for item in studio_outputs if item.get("kind") != output.get("kind")]
        studio_outputs.insert(0, output)

    def render_flashcard_source_picker(notes: list[dict[str, object]]) -> None:
        try:
            studio_grid_box.classes(add="nlm-studio-hidden")
            studio_sep.classes(add="nlm-studio-hidden")
        except NameError:
            pass
        studio_output_box.clear()
        with studio_output_box:
            with ui.element("div").classes("nlm-flash-source-view"):
                with ui.element("div").classes("nlm-app-crumb"):
                    with ui.element("button").props('type="button" aria-label="返回 Studio"').classes("nlm-app-back").on("click", lambda _event=None: render_studio_list()):
                        ui.label("Studio")
                    ui.label("›").classes("nlm-app-chev")
                    ui.label("闪卡").classes("nlm-app-kind")
                    ui.space()
                    with ui.element("button").props('type="button" aria-label="放大闪卡选择页"').classes("nlm-app-expand-btn").on(
                        "click",
                        lambda _event=None: ui.run_javascript(
                            """
                            (() => {
                              const viewer = document.querySelector('.nlm-flash-source-view');
                              if (!viewer) return;
                              const expanded = !viewer.classList.contains('nlm-expanded');
                              viewer.classList.toggle('nlm-expanded', expanded);
                              const icon = viewer.querySelector('.nlm-app-expand-btn .q-icon');
                              if (icon) icon.textContent = expanded ? 'close_fullscreen' : 'open_in_full';
                            })();
                            """
                        ),
                    ):
                        ui.icon("open_in_full").classes("nlm-app-expand")
                with ui.element("div").classes("nlm-flash-source-head"):
                    ui.label("选择闪卡来源").classes("nlm-flash-source-title")
                    ui.label("选择一条笔记后，将为该笔记生成可翻转的复习闪卡。").classes("nlm-flash-source-copy")
                with ui.element("div").classes("nlm-flash-source-list"):
                    for index, note in enumerate(notes[:12], 1):
                        note_title = str(note.get("title") or f"来源 {index}")
                        note_course = str(note.get("course") or "未分类")
                        note_excerpt_text = note_excerpt(str(note.get("content") or ""), 118)

                        async def choose_flash_source(_event=None, selected_note=note):
                            selected_notes = [selected_note]
                            output = await create_studio_output("flashcards", "闪卡", selected_notes, "", False)
                            replace_studio_output(output)
                            render_studio_viewer(output)

                        with ui.element("button").props("type=button").classes("nlm-flash-source-item").on("click", choose_flash_source):
                            with ui.element("div").classes("nlm-flash-source-icon"):
                                ui.icon("style")
                            with ui.element("div").classes("nlm-flash-source-main"):
                                ui.label(note_title).classes("nlm-flash-source-name")
                                ui.label(f"{note_course} · {note_excerpt_text}").classes("nlm-flash-source-meta")
                            ui.icon("chevron_right").classes("nlm-flash-source-arrow")

    async def open_studio_output(output: dict[str, object]) -> None:
        if output.get("kind") == "mindmap" and dict(output.get("mindmap") or {}).get("source") != "ai" and has_api_key():
            local_output = await create_studio_output(
                "mindmap",
                str(output.get("fallback_title") or "思维导图"),
                list(output.get("notes") or []),
                "",
                False,
            )
            replace_studio_output(local_output)
            render_studio_viewer(local_output)
            output = await create_studio_output(
                "mindmap",
                str(output.get("fallback_title") or "思维导图"),
                list(output.get("notes") or []),
                "",
                True,
            )
            replace_studio_output(output)
        render_studio_viewer(output)

    def studio_action_handler(kind: str, title: str):
        async def handler(event=None) -> None:
            notes = studio_sources(current_studio_course())
            answer_text = latest_answer["answer"] if kind == "audio" else ""
            if kind == "flashcards":
                render_flashcard_source_picker(notes)
                return
            if kind == "mindmap":
                show_studio_loading("正在根据笔记生成思维导图...")
                ui.notify("正在按不同笔记划分模块并绘制导图。", color="positive")
                await asyncio.sleep(0.05)
            elif kind == "report":
                ui.notify("已根据 9 条来源生成 Briefing Doc。", color="positive")
            output = await create_studio_output(kind, title, notes, answer_text, False)
            replace_studio_output(output)
            answer_box.set_content(str(output.get("markdown") or ""))
            source_count_label.set_text(f"{len(notes)} 个来源 · {date.today().isoformat()}")
            render_studio_viewer(output)
            ui.run_javascript("window.studypilotMotion && window.studypilotMotion.sources();")

        return handler

    def add_demo_source() -> None:
        created = ensure_studio_demo_source()
        refresh_after_upload()
        ui.notify("已添加 Studio 示例素材。" if created else "示例素材已存在，可直接生成 Studio 输出。", color="positive")

    def open_source_search(search_input, mode: str = "web") -> None:
        query = (search_input.value or "").strip()
        if not query:
            ui.notify("请先输入要搜索的主题或网址关键词。", color="warning")
            search_input.run_method("focus")
            return
        ui.navigate.to(build_web_search_url(query, mode), new_tab=True)
        ui.notify("已打开搜索结果。找到合适资料后，可复制内容并保存为来源。", color="positive")

    def native_icon_button(icon: str, label: str, on_click=None, extra_class: str = "") -> None:
        button = ui.element("button").props('type="button" aria-label="' + label + '"').classes(
            f"nlm-native-btn nlm-icon-btn {extra_class}".strip()
        )
        if on_click:
            button.on("click", on_click)
        with button:
            ui.icon(icon).props('aria-hidden="true"')

    def panel_toggle_button(side: str, label: str) -> None:
        with ui.element("button").props(f'type="button" aria-label="{label}"').classes(
            f"nlm-native-btn nlm-panel-toggle nlm-panel-toggle-{side}"
        ).on(
            "click",
            lambda _event=None, panel_side=side: ui.run_javascript(
                f"""
                const shell = document.querySelector('.notebooklm-shell');
                if (shell) shell.classList.toggle('nlm-{panel_side}-collapsed');
                """
            ),
        ):
            ui.element("span").classes("nlm-panel-toggle-icon").props('aria-hidden="true"')

    def native_pill(label: str, icon: str, on_click=None, extra_class: str = "") -> None:
        button = ui.element("button").props("type=button").classes(
            f"nlm-native-btn nlm-pill-btn {extra_class}".strip()
        )
        if on_click:
            button.on("click", on_click)
        with button:
            ui.icon(icon).props('aria-hidden="true"')
            ui.label(label)

    def render_citations(result: dict[str, object]) -> None:
        def clean_source_snippet(text: object) -> str:
            snippet = str(text or "")
            snippet = re.sub(r"可信度可以根据检索分数分为高可信、部分可信和来源不足；?", "", snippet)
            snippet = re.sub(r"(高可信|部分可信|来源不足)", "", snippet)
            return " ".join(snippet.split())

        sources_box.clear()
        with sources_box:
            with ui.element("div").classes("nlm-search-trace"):
                ui.icon("travel_explore").classes("nlm-search-trace-icon")
                with ui.column().classes("gap-0"):
                    ui.label("已先通过 AI 进行回答").classes("nlm-search-trace-title")
                    ui.label("随后已检索你的个人笔记，并将结果与 AI 回答进行对照。").classes("nlm-search-trace-copy")
            sources = result.get("sources") or []
            if sources:
                with ui.row().classes("items-center nlm-citation-head"):
                    ui.icon("travel_explore").classes("section-icon")
                    ui.label("个人笔记检索结果").classes("section-title")
                for source in sources:
                    with ui.card().classes("source-card animate-source"):
                        with ui.row().classes("items-center gap-2"):
                            ui.badge(source["course"], color="green")
                            ui.label(source["title"]).classes("source-title")
                            ui.space()
                            ui.label(f"相似度 {source['score']}").classes("source-score")
                        ui.label(clean_source_snippet(source.get("snippet"))).classes("source-snippet")
            else:
                with ui.element("div").classes("nlm-note-miss"):
                    ui.icon("search_off").classes("nlm-note-miss-icon")
                    ui.label("在你的笔记中没查询到相关的内容").classes("nlm-note-miss-title")
                    ui.label("当前回答来自 AI 通用对话能力；上传相关来源后，可继续生成带笔记依据的回答。").classes("nlm-note-miss-copy")

    async def type_answer(text: str) -> None:
        answer_box.set_content("")
        rendered = ""
        step = 3 if len(text) < 600 else 5
        for index in range(0, len(text), step):
            rendered = text[: index + step]
            answer_box.set_content(rendered + "▍")
            await asyncio.sleep(0.018)
        answer_box.set_content(text)

    async def ask(event=None) -> None:
        if not (question.value or "").strip():
            ui.notify("请先输入一个问题。", color="warning")
            return
        asked = question.value or ""
        selected_course = None if course.value == "全部课程" else course.value
        answer_box.set_content("正在思考...\n\n正在先通过 AI 生成回答，并检索你的个人笔记。")
        sources_box.clear()
        await asyncio.sleep(0.05)
        result = await asyncio.to_thread(agent.note_agent.ask, asked, selected_course)
        answer_text = str(result["answer"])
        latest_answer["question"] = asked
        latest_answer["answer"] = answer_text
        await type_answer(answer_text)
        render_citations(result)
        ui.run_javascript(f"window.studypilotLatestAnswer = {json.dumps(plain_markdown_text(answer_text), ensure_ascii=False)};")
        update_audio_output_from_answer()
        ui.run_javascript("window.studypilotMotion && window.studypilotMotion.sources();")

    def speak_answer(text: str) -> None:
        speech_text = plain_markdown_text(text)
        if not speech_text:
            ui.notify("请先提问，得到回答后再播放音频概览。", color="warning")
            return
        ui.run_javascript(
            f"window.studypilotSpeech && window.studypilotSpeech.play({json.dumps(speech_text, ensure_ascii=False)});"
        )

    def update_audio_output_from_answer() -> None:
        if not latest_answer["answer"].strip():
            return
        notes = studio_sources(current_studio_course())
        output = build_studio_output("audio", "音频概览", notes, latest_answer["answer"])
        studio_outputs[:] = [item for item in studio_outputs if item.get("kind") != "audio"]
        studio_outputs.insert(0, output)
        render_studio_list()

    studio_actions = [
        ("audio", "音频概览", "≋", "blue"),
        ("mindmap", "思维导图", "⌘", "pink"),
        ("report", "报告", "▤", "yellow"),
        ("flashcards", "闪卡", "◩", "orange"),
        ("quiz", "测验", "?", "cyan"),
        ("table", "数据表格", "▦", "blue"),
    ]

    def render_output_preview(output: dict[str, object]) -> None:
        kind = str(output.get("kind") or "report")
        notes = list(output.get("notes") or [])
        keywords = note_keywords(notes, 8)
        focus = html.escape(keywords[0] if keywords else str(output.get("title") or "学习资料"))
        secondary = html.escape(keywords[1] if len(keywords) > 1 else "来源证据")
        third = html.escape(keywords[2] if len(keywords) > 2 else "学习问题")

        if kind == "audio":
            speech_text = str(output.get("speech_text") or output.get("markdown") or "")
            initial_duration = max(24, round(len(plain_markdown_text(speech_text)) / 4.2))
            initial_duration_label = format_media_duration(initial_duration)
            ui.html(
                f"""
                <div class="nlm-media-card">
                  <div class="nlm-media-slide">
                    <span class="nlm-media-brand">NotebookLM</span>
                    <strong>{focus}</strong>
                    <small>{secondary} · {third}</small>
                  </div>
                  <div class="nlm-media-time" data-duration="{initial_duration}">
                    <span class="nlm-media-current">00:00</span>
                    <div class="nlm-media-track" style="--media-progress: 0%">
                      <span class="nlm-media-fill"></span>
                      <span class="nlm-media-knob"></span>
                    </div>
                    <span class="nlm-media-duration">{initial_duration_label}</span>
                  </div>
                </div>
                """,
                sanitize=False,
            )
            speech_payload = html.escape(plain_markdown_text(speech_text), quote=True)
            with ui.element("div").classes("nlm-media-controls"):
                ui.button("1X").props("flat no-caps").classes("nlm-media-control-text")
                ui.button("↶10").props("flat no-caps").classes("nlm-media-control-text")
                ui.html(
                    f"""
                    <button
                      type="button"
                      aria-label="播放答案朗读"
                      class="nlm-media-play"
                      data-speech="{speech_payload}"
                      onclick="(function(button) {{
                        const latest = String(window.studypilotLatestAnswer || '').replace(/\\s+/g, ' ').trim();
                        const answerBox = document.querySelector('.answer-box');
                        const visibleAnswer = String(answerBox ? answerBox.innerText || answerBox.textContent || '' : '').replace(/\\s+/g, ' ').trim();
                        const usableAnswer = /正在思考|正在先通过 AI/.test(visibleAnswer) ? '' : visibleAnswer;
                        const text = (latest || usableAnswer || button.dataset.speech || '').replace(/\\s+/g, ' ').trim();
                        if (!text) {{ alert('请先提问，得到回答后再播放音频概览。'); return; }}
                        if (!('speechSynthesis' in window) || !window.SpeechSynthesisUtterance) {{
                          alert('当前浏览器不支持语音朗读。');
                          return;
                        }}
                        const player = button.closest('.nlm-media-controls')?.previousElementSibling;
                        const currentEl = player?.querySelector('.nlm-media-current');
                        const durationEl = player?.querySelector('.nlm-media-duration');
                        const track = player?.querySelector('.nlm-media-track');
                        const icon = button.querySelector('.nlm-media-play-icon');
                        const format = (seconds) => {{
                          const safe = Math.max(0, Math.round(seconds || 0));
                          const mins = Math.floor(safe / 60);
                          const secs = safe % 60;
                          return `${{String(mins).padStart(2, '0')}}:${{String(secs).padStart(2, '0')}}`;
                        }};
                        const estimatedSeconds = Math.max(18, Math.round(text.length / 4.2));
                        if (durationEl) durationEl.textContent = format(estimatedSeconds);
                        if (button.dataset.playing === 'true') {{
                          window.speechSynthesis.cancel();
                          window.clearInterval(window.studypilotSpeechTimer);
                          button.dataset.playing = 'false';
                          if (icon) icon.textContent = 'play_arrow';
                          button.classList.remove('is-playing');
                          return;
                        }}
                        window.speechSynthesis.cancel();
                        window.clearInterval(window.studypilotSpeechTimer);
                        const startedAt = Date.now();
                        const updateProgress = () => {{
                          const elapsed = Math.min(estimatedSeconds, (Date.now() - startedAt) / 1000);
                          const percent = Math.max(0, Math.min(100, elapsed / estimatedSeconds * 100));
                          if (currentEl) currentEl.textContent = format(elapsed);
                          if (track) track.style.setProperty('--media-progress', `${{percent}}%`);
                          if (elapsed >= estimatedSeconds) window.clearInterval(window.studypilotSpeechTimer);
                        }};
                        updateProgress();
                        window.studypilotSpeechTimer = window.setInterval(updateProgress, 200);
                        button.dataset.playing = 'true';
                        button.classList.add('is-playing');
                        if (icon) icon.textContent = 'pause';
                        const utterance = new SpeechSynthesisUtterance(text);
                        utterance.lang = /[\\u4e00-\\u9fff]/.test(text) ? 'zh-CN' : 'en-US';
                        utterance.rate = 0.95;
                        utterance.pitch = 1;
                        const voices = window.speechSynthesis.getVoices();
                        const preferred = voices.find((voice) => /zh|Chinese|Mandarin|普通话|中文/i.test(`${{voice.lang}} ${{voice.name}}`));
                        if (preferred) utterance.voice = preferred;
                        const finish = () => {{
                          window.clearInterval(window.studypilotSpeechTimer);
                          if (currentEl) currentEl.textContent = format(estimatedSeconds);
                          if (track) track.style.setProperty('--media-progress', '100%');
                          button.dataset.playing = 'false';
                          button.classList.remove('is-playing');
                          if (icon) icon.textContent = 'play_arrow';
                        }};
                        utterance.onend = finish;
                        utterance.onerror = finish;
                        window.speechSynthesis.speak(utterance);
                      }})(this)"
                    ><span class="material-icons nlm-media-play-icon">play_arrow</span></button>
                    """,
                    sanitize=False,
                )
                ui.button("10↷").props("flat no-caps").classes("nlm-media-control-text")
                ui.button(icon="fullscreen").props("flat round").classes("nlm-media-control-icon")
            return

        if kind == "mindmap":
            mindmap = output.get("mindmap") if isinstance(output.get("mindmap"), dict) else local_mindmap_data(str(output.get("fallback_title") or "思维导图"), notes)
            modules = mindmap.get("modules") if isinstance(mindmap.get("modules"), list) else []
            module_buttons = []
            branch_sections = []
            for module_index, module in enumerate(modules[:9]):
                if not isinstance(module, dict):
                    continue
                module_title = html.escape(str(module.get("title") or f"笔记 {module_index + 1}"))
                module_course = html.escape(str(module.get("course") or "未分类"))
                active_class = " is-active" if module_index == 0 else ""
                module_buttons.append(
                    f"""
                    <button type="button" class="nlm-map-module{active_class}" data-map-index="{module_index}">
                      <span>{module_title}</span>
                      <small>{module_course}</small>
                    </button>
                    """
                )
                nodes = module.get("nodes") if isinstance(module.get("nodes"), list) else []
                node_cards = []
                for node_index, node in enumerate(nodes[:5]):
                    if not isinstance(node, dict):
                        continue
                    label = html.escape(str(node.get("label") or "知识点"))
                    details = node.get("details") if isinstance(node.get("details"), list) else []
                    detail_items = "".join(f"<li>{html.escape(str(detail))}</li>" for detail in details[:3])
                    node_cards.append(
                        f"""
                        <div class="nlm-map-node" style="--node-index:{node_index}">
                          <strong>{label}</strong>
                          <ul>{detail_items}</ul>
                        </div>
                        """
                    )
                questions = module.get("review_questions") if isinstance(module.get("review_questions"), list) else []
                question_items = "".join(f"<li>{html.escape(str(question))}</li>" for question in questions[:3])
                hidden = "" if module_index == 0 else " hidden"
                branch_sections.append(
                    f"""
                    <section class="nlm-map-view{hidden}" data-map-view="{module_index}">
                      <div class="nlm-map-root">
                        <small>{module_course}</small>
                        <strong>{module_title}</strong>
                      </div>
                      <div class="nlm-map-branches">{''.join(node_cards)}</div>
                      <div class="nlm-map-review">
                        <span>复习问题</span>
                        <ol>{question_items}</ol>
                      </div>
                    </section>
                    """
                )
            root = html.escape(str(mindmap.get("root") or focus))
            subtitle = html.escape(str(mindmap.get("subtitle") or f"{len(notes)} 个来源"))
            ui.html(
                f"""
                <div class="nlm-map-stage">
                  <div class="nlm-map-head">
                    <span>Mindmap</span>
                    <strong>{root}</strong>
                    <small>{subtitle}</small>
                  </div>
                  <div class="nlm-map-tools"><button>⌃</button><button>+</button><button>-</button><button>⌄</button></div>
                  <div class="nlm-map-modules">{''.join(module_buttons)}</div>
                  <div class="nlm-map-canvas">{''.join(branch_sections)}</div>
                </div>
                """,
                sanitize=False,
            )
            ui.run_javascript(
                """
                (() => {
                  const stages = document.querySelectorAll('.nlm-map-stage');
                  const stage = stages[stages.length - 1];
                  if (!stage || stage.dataset.bound === '1') return;
                  stage.dataset.bound = '1';
                  stage.querySelectorAll('.nlm-map-module').forEach((button) => {
                    button.addEventListener('click', () => {
                      const index = button.dataset.mapIndex;
                      stage.querySelectorAll('.nlm-map-module').forEach(item => item.classList.toggle('is-active', item === button));
                      stage.querySelectorAll('.nlm-map-view').forEach(view => view.hidden = view.dataset.mapView !== index);
                    });
                  });
                  if (!window.studypilotMindmapEscapeBound) {
                    window.studypilotMindmapEscapeBound = true;
                    window.addEventListener('keydown', (event) => {
                      if (event.key !== 'Escape') return;
                      document.querySelectorAll('.nlm-mindmap-viewer.nlm-map-expanded').forEach((viewer) => {
                        viewer.classList.remove('nlm-map-expanded');
                        viewer.classList.remove('nlm-expanded');
                        const icon = viewer.querySelector('.nlm-app-expand-btn .q-icon');
                        if (icon) icon.textContent = 'open_in_full';
                      });
                      document.body.classList.remove('nlm-map-expanded-lock');
                      document.body.classList.remove('nlm-viewer-expanded-lock');
                    });
                  }
                })();
                """
            )
            return

        if kind == "flashcards":
            cards = output.get("flashcards") if isinstance(output.get("flashcards"), list) else build_flashcards(notes)
            cards_json = html.escape(json.dumps(cards, ensure_ascii=False), quote=True)
            flashcard_source_notes = list(notes)
            ui.html(
                f"""
                <div class="nlm-flash-viewer" data-cards="{cards_json}" onclick="window.studypilotFlashcards && window.studypilotFlashcards.init(this)">
                  <div class="nlm-flash-hint">按空格键可将抽认卡翻面，按“← / →”可浏览卡片</div>
                  <div class="nlm-flash-head">
                    <button type="button" class="nlm-flash-source-toggle" aria-label="查看 9 个来源" onclick="event.stopPropagation(); window.studypilotFlashcards && window.studypilotFlashcards.openSources(this.closest('.nlm-flash-viewer'))">查看 9 个来源</button>
                    <button type="button" class="nlm-flash-expand-btn" aria-label="放大闪卡查看器" onclick="event.stopPropagation(); (() => {{ const viewer = this.closest('.nlm-flash-viewer'); if (!viewer) return; const expanded = !viewer.classList.contains('nlm-flash-expanded'); viewer.classList.toggle('nlm-flash-expanded', expanded); this.querySelector('.q-icon').textContent = expanded ? 'close_fullscreen' : 'open_in_full'; }})()"><span class="q-icon material-icons">open_in_full</span></button>
                  </div>
                  <div class="nlm-flash-card-shell">
                    <div class="nlm-flash-card is-front" onclick="if (!event.target.closest('button')) window.studypilotFlashcards.flip(this.closest('.nlm-flash-viewer'))">
                      <div class="nlm-flash-face nlm-flash-front">
                        <div class="nlm-flash-count">1/{len(cards)}</div>
                        <button type="button" class="nlm-flash-more" aria-label="更多">more_vert</button>
                        <div class="nlm-flash-question"></div>
                        <button type="button" class="nlm-flash-reveal" onclick="event.stopPropagation(); window.studypilotFlashcards.flip(this.closest('.nlm-flash-viewer'))">查看答案</button>
                      </div>
                      <div class="nlm-flash-face nlm-flash-back">
                        <div class="nlm-flash-answer"></div>
                        <button type="button" class="nlm-flash-explain" onclick="event.stopPropagation(); window.studypilotFlashcards.explain(this.closest('.nlm-flash-viewer'))"><span class="material-icons">drive_file_move</span>解释</button>
                        <p class="nlm-flash-explanation"></p>
                      </div>
                    </div>
                  </div>
                  <div class="nlm-flash-actions">
                    <button type="button" class="nlm-flash-round nlm-flash-prev" aria-label="上一张" onclick="event.stopPropagation(); window.studypilotFlashcards.move(this.closest('.nlm-flash-viewer'), -1)"><span class="material-icons">arrow_back</span></button>
                    <button type="button" class="nlm-flash-score nlm-flash-wrong" aria-label="答错" onclick="event.stopPropagation(); window.studypilotFlashcards.score(this.closest('.nlm-flash-viewer'), 'wrong')"><span class="material-icons">close</span><strong>0</strong></button>
                    <button type="button" class="nlm-flash-score nlm-flash-right" aria-label="答对" onclick="event.stopPropagation(); window.studypilotFlashcards.score(this.closest('.nlm-flash-viewer'), 'right')"><strong>0</strong><span class="material-icons">check</span></button>
                    <button type="button" class="nlm-flash-round nlm-flash-next" aria-label="下一张" onclick="event.stopPropagation(); window.studypilotFlashcards.move(this.closest('.nlm-flash-viewer'), 1)"><span class="material-icons">arrow_forward</span></button>
                  </div>
                </div>
                """,
                sanitize=False,
            )
            with ui.element("button").props('type="button" aria-label="打开闪卡来源选择"').classes("nlm-flash-source-proxy nlm-studio-hidden").on(
                "click",
                lambda _event=None, selected_notes=flashcard_source_notes: render_flashcard_source_picker(selected_notes),
            ):
                ui.label("打开闪卡来源选择")
            ui.run_javascript(
                """
                window.studypilotFlashcards = window.studypilotFlashcards || {
                  ensure(root) {
                    if (!root) return;
                    if (!root.dataset.cardsParsed) {
                      root._cards = JSON.parse(root.dataset.cards || "[]");
                      root._index = Number(root.dataset.index || 0);
                      root._wrong = Number(root.dataset.wrong || 0);
                      root._right = Number(root.dataset.right || 0);
                      root.dataset.cardsParsed = "true";
                    }
                  },
                  init(root) {
                    this.ensure(root);
                    this.render(root);
                    if (!window.studypilotFlashKeyBound) {
                      window.studypilotFlashKeyBound = true;
                      document.addEventListener("keydown", (event) => {
                        const root = document.querySelector(".nlm-flash-viewer");
                        if (!root || !root.isConnected) return;
                        if (event.target && ["INPUT", "TEXTAREA"].includes(event.target.tagName)) return;
                        if (event.code === "Space") {
                          event.preventDefault();
                          window.studypilotFlashcards.flip(root);
                        }
                        if (event.key === "ArrowLeft") window.studypilotFlashcards.move(root, -1);
                        if (event.key === "ArrowRight") window.studypilotFlashcards.move(root, 1);
                      });
                    }
                  },
                  render(root) {
                    this.ensure(root);
                    const cards = root._cards || [];
                    const item = cards[root._index] || {};
                    root.querySelector(".nlm-flash-count").textContent = `${root._index + 1}/${cards.length || 1}`;
                    root.querySelector(".nlm-flash-question").textContent = item.question || "暂无问题";
                    root.querySelector(".nlm-flash-answer").textContent = item.answer || "暂无答案";
                    root.querySelector(".nlm-flash-explanation").textContent = item.explanation || "";
                    root.querySelector(".nlm-flash-wrong strong").textContent = String(root._wrong || 0);
                    root.querySelector(".nlm-flash-right strong").textContent = String(root._right || 0);
                    const card = root.querySelector(".nlm-flash-card");
                    card.classList.remove("is-back");
                    card.classList.add("is-front");
                  },
                  flip(root) {
                    this.ensure(root);
                    const card = root.querySelector(".nlm-flash-card");
                    card.classList.toggle("is-front");
                    card.classList.toggle("is-back");
                  },
                  move(root, delta) {
                    this.ensure(root);
                    const total = (root._cards || []).length || 1;
                    root._index = (root._index + delta + total) % total;
                    root.dataset.index = String(root._index);
                    this.render(root);
                  },
                  openSources(root) {
                    this.ensure(root);
                    const proxy = document.querySelector('.nlm-flash-source-proxy');
                    if (proxy) proxy.click();
                  },
                  score(root, type) {
                    this.ensure(root);
                    if (type === "wrong") root._wrong = (root._wrong || 0) + 1;
                    if (type === "right") root._right = (root._right || 0) + 1;
                    root.querySelector(".nlm-flash-wrong strong").textContent = String(root._wrong || 0);
                    root.querySelector(".nlm-flash-right strong").textContent = String(root._right || 0);
                  },
                  explain(root) {
                    this.ensure(root);
                    const item = (root._cards || [])[root._index] || {};
                    const prompt = `请解释这个闪卡问题：${item.question || ""}。参考答案：${item.answer || ""}。请结合来源：${item.source || ""}。`;
                    const input = document.querySelector(".nlm-prompt-input input, .nlm-prompt-input textarea");
                    const send = document.querySelector(".nlm-send-btn");
                    if (input) {
                      input.value = prompt;
                      input.dispatchEvent(new Event("input", { bubbles: true }));
                      input.dispatchEvent(new Event("change", { bubbles: true }));
                    }
                    if (send) send.click();
                  }
                };
                document.querySelectorAll(".nlm-flash-viewer").forEach((root) => window.studypilotFlashcards.init(root));
                """
            )
            return

        if kind == "quiz":
            with ui.element("div").classes("nlm-quiz-preview"):
                for index, note in enumerate(notes[:3], 1):
                    with ui.element("div").classes("nlm-quiz-question"):
                        ui.label(f"问题 {index}").classes("nlm-quiz-kicker")
                        ui.label(f"{note.get('title')} 最能说明哪个知识点？").classes("nlm-quiz-title")
                        for option in ["政策变化", "市场动态", "数据与隐私", "AI 医疗应用"]:
                            ui.label(option).classes("nlm-quiz-option")
            return

        if kind == "table":
            rows = "".join(
                f"<tr><td>{html.escape(str(note.get('title') or '来源'))}</td><td>{html.escape(str(note.get('course') or '未分类'))}</td><td>{html.escape('、'.join(note_keywords([note], 2)) or '核心概念')}</td></tr>"
                for note in notes[:5]
            )
            ui.html(
                f"""
                <table class="nlm-data-preview">
                  <thead><tr><th>来源</th><th>课程</th><th>关键词</th></tr></thead>
                  <tbody>{rows}</tbody>
                </table>
                """,
                sanitize=False,
            )
            return

        ui.markdown(str(output.get("markdown") or "")).classes("nlm-report-preview")

    def render_studio_list() -> None:
        studio_outputs[:] = [output for output in studio_outputs if output.get("kind") != "video"]
        try:
            studio_grid_box.classes(remove="nlm-studio-hidden")
            studio_sep.classes(remove="nlm-studio-hidden")
        except NameError:
            pass
        notes = studio_sources(current_studio_course())
        if not studio_outputs and notes:
            for kind, title, _symbol, _tone in studio_actions:
                studio_outputs.append(build_studio_output(kind, title, notes))

        studio_output_box.clear()
        with studio_output_box:
            if not studio_outputs:
                ui.icon("auto_fix_high").classes("nlm-studio-spark")
                ui.label("Studio 输出将保存在此处。").classes("nlm-studio-empty-title")
                ui.label("添加来源后，点击即可生成音频概览、思维导图、报告、闪卡、测验和数据表格。").classes("nlm-studio-empty-copy")
                with ui.element("button").classes("nlm-native-btn nlm-add-note-btn").on("click", lambda: upload_dialog.open()):
                    ui.icon("sticky_note_2").props('aria-hidden="true"')
                    ui.label("添加笔记")
                with ui.element("button").classes("nlm-native-btn nlm-demo-source-btn").on("click", add_demo_source):
                    ui.icon("auto_awesome").props('aria-hidden="true"')
                    ui.label("添加示例素材")
                return

            with ui.element("div").classes("nlm-studio-notice"):
                ui.icon("auto_awesome").classes("nlm-studio-notice-icon")
                ui.label("这些 Studio 输出内容可生成音频概览、思维导图、报告和练习资料，深入解读笔记本主题！")
            with ui.element("div").classes("nlm-studio-output-list"):
                for output in studio_outputs:
                    profile = dict(output.get("profile") or {})
                    classes = f"nlm-studio-output-item nlm-output-{profile.get('tone', 'blue')}"
                    with ui.element("button").props("type=button").classes(classes).on(
                        "click", lambda _event=None, item=output: open_studio_output(item)
                    ):
                        ui.icon(str(profile.get("icon") or "article")).classes("nlm-studio-output-icon")
                        with ui.element("div").classes("nlm-studio-output-main"):
                            ui.label(str(output.get("title") or "Studio 输出")).classes("nlm-studio-output-name")
                            ui.label(str(output.get("meta") or "")).classes("nlm-studio-output-meta")
                        if output.get("kind") == "audio":
                            ui.icon("play_arrow").classes("nlm-studio-output-play")
                        ui.icon("more_vert").classes("nlm-studio-output-kebab")

    def render_studio_viewer(output: dict[str, object]) -> None:
        studio_grid_box.classes(add="nlm-studio-hidden")
        studio_sep.classes(add="nlm-studio-hidden")
        profile = dict(output.get("profile") or {})
        source_count = int(output.get("source_count") or 0)
        viewer_name = str(profile.get("viewer") or "应用")
        is_mindmap = output.get("kind") == "mindmap"
        close_label = f"关闭{viewer_name}查看器" if viewer_name not in {"应用"} else "关闭应用查看器"
        studio_output_box.clear()
        with studio_output_box:
            viewer_classes = "nlm-app-viewer nlm-expandable-viewer"
            if is_mindmap:
                viewer_classes += " nlm-mindmap-viewer"
            with ui.element("div").classes(viewer_classes):
                with ui.element("div").classes("nlm-app-crumb"):
                    with ui.element("button").props(f'type="button" aria-label="{close_label}"').classes("nlm-app-back").on("click", lambda _event=None: render_studio_list()):
                        ui.label("Studio")
                    ui.label("›").classes("nlm-app-chev")
                    ui.label(viewer_name).classes("nlm-app-kind")
                    ui.space()
                    with ui.element("button").props(f'type="button" aria-label="放大{viewer_name}"').classes("nlm-app-expand-btn").on(
                        "click",
                        lambda _event=None: ui.run_javascript(
                            """
                            (() => {
                              const viewer = document.querySelector('.nlm-expandable-viewer');
                              if (!viewer) return;
                              const shell = viewer.closest('.notebooklm-shell');
                              const expanded = !viewer.classList.contains('nlm-expanded');
                              viewer.classList.toggle('nlm-expanded', expanded);
                              if (viewer.classList.contains('nlm-mindmap-viewer')) {
                                viewer.classList.toggle('nlm-map-expanded', expanded);
                              }
                              if (shell) shell.classList.toggle('nlm-studio-wide', expanded);
                              document.body.classList.remove('nlm-viewer-expanded-lock', 'nlm-map-expanded-lock');
                              const icon = viewer.querySelector('.nlm-app-expand-btn .q-icon');
                              if (icon) icon.textContent = expanded ? 'close_fullscreen' : 'open_in_full';
                              if (!window.studypilotViewerEscapeBound) {
                                window.studypilotViewerEscapeBound = true;
                                window.addEventListener('keydown', (event) => {
                                  if (event.key !== 'Escape') return;
                                  document.querySelectorAll('.nlm-expandable-viewer.nlm-expanded').forEach((item) => {
                                    item.classList.remove('nlm-expanded', 'nlm-map-expanded');
                                    const itemIcon = item.querySelector('.nlm-app-expand-btn .q-icon');
                                    if (itemIcon) itemIcon.textContent = 'open_in_full';
                                  });
                                  document.querySelectorAll('.notebooklm-shell.nlm-studio-wide').forEach((shell) => {
                                    shell.classList.remove('nlm-studio-wide');
                                  });
                                  document.body.classList.remove('nlm-viewer-expanded-lock', 'nlm-map-expanded-lock');
                                });
                              }
                            })();
                            """
                        ),
                    ):
                        ui.icon("open_in_full").classes("nlm-app-expand")
                with ui.element("div").classes("nlm-app-titlebar"):
                    ui.label(str(output.get("title") or "Studio 输出")).classes("nlm-app-title")
                    ui.space()
                    if output.get("kind") == "audio":
                        ui.icon("download").classes("nlm-app-action-icon")
                    elif output.get("kind") == "report":
                        report_text = str(output.get("markdown") or "")
                        with ui.element("button").props('type="button" aria-label="复制报告内容"').classes("nlm-app-action-btn").on(
                            "click",
                            lambda _event=None, text=report_text: ui.run_javascript(
                                f"""
                                (() => {{
                                  const text = {json.dumps(text, ensure_ascii=False)};
                                  navigator.clipboard?.writeText(text).then(() => {{
                                    window.$q?.notify?.({{message: '报告内容已复制', color: 'positive'}});
                                  }}).catch(() => {{
                                    const area = document.createElement('textarea');
                                    area.value = text;
                                    document.body.appendChild(area);
                                    area.select();
                                    document.execCommand('copy');
                                    area.remove();
                                    window.$q?.notify?.({{message: '报告内容已复制', color: 'positive'}});
                                  }});
                                }})();
                                """
                            ),
                        ):
                            ui.icon("content_copy").classes("nlm-app-action-icon")
                with ui.element("button").props("type=button").classes("nlm-source-chip").on("click", lambda: ui.notify("来源引用会从左侧已选笔记中读取。")):
                    ui.label(f"查看 {source_count} 个来源")
                with ui.element("div").classes("nlm-app-body"):
                    render_output_preview(output)
                with ui.element("div").classes("nlm-app-feedback"):
                    with ui.element("button").props("type=button").classes("nlm-feedback-btn"):
                        ui.icon("thumb_up")
                        ui.label(f"{viewer_name}不错" if viewer_name != "应用" else "优质内容")
                    with ui.element("button").props("type=button").classes("nlm-feedback-btn"):
                        ui.icon("thumb_down")
                        ui.label(f"{viewer_name}不好" if viewer_name != "应用" else "劣质内容")

    with ui.element("div").classes("notebooklm-shell animate-hero"):
        with ui.row().classes("notebooklm-topbar items-center"):
            with ui.element("div").classes("notebooklm-logo"):
                ui.icon("podcasts")
            with ui.column().classes("gap-0 notebooklm-title-block"):
                ui.label("AI 问笔记").classes("notebooklm-title")
                ui.label("StudyPilot Notebook").classes("notebooklm-subtitle")
            with ui.element("nav").classes("nlm-project-nav"):
                nav_items = [
                    ("学习驾驶舱", "dashboard"),
                    ("AI 问笔记", "chat"),
                    ("任务管理", "checklist"),
                    ("复习训练", "school"),
                ]
                for label, icon in nav_items:
                    classes = "nlm-project-nav-item"
                    if label == "AI 问笔记":
                        classes += " nlm-project-nav-active"
                    with ui.element("button").classes(classes).on("click", show_app_panel(label)):
                        ui.icon(icon).classes("nlm-project-nav-icon")
                        ui.label(label).classes("nlm-project-nav-label")
            ui.space()
            with ui.element("button").classes("nlm-native-btn nlm-create-btn").on(
                "click", lambda: ui.notify("当前项目使用本地课程笔记作为一个学习笔记本。")
            ):
                ui.icon("add").props('aria-hidden="true"')
                ui.label("创建笔记本")
            with ui.element("button").classes("nlm-native-btn nlm-top-btn").on(
                "click", lambda: question.set_value("请分析这些笔记的核心知识结构。")
            ):
                ui.icon("trending_up").props('aria-hidden="true"')
                ui.label("分析")
            with ui.element("button").classes("nlm-native-btn nlm-top-btn").on(
                "click", lambda: ui.notify("本地学习助手暂不上传或分享个人笔记。")
            ):
                ui.icon("share").props('aria-hidden="true"')
                ui.label("分享")
            with ui.element("button").classes("nlm-native-btn nlm-top-btn").on("click", lambda: ui.notify("可在 .env 中配置模型与服务。")):
                ui.icon("settings").props('aria-hidden="true"')
                ui.label("设置")

        with ui.element("div").classes("notebooklm-panels"):
            with ui.element("section").classes("nlm-panel nlm-sources-panel animate-card"):
                with ui.row().classes("nlm-panel-head items-center"):
                    ui.label("来源").classes("nlm-panel-title")
                    ui.space()
                    panel_toggle_button("sources", "收起或展开来源")
                with ui.element("div").classes("nlm-rail nlm-sources-rail"):
                    with ui.element("button").props('type="button" aria-label="添加来源"').classes("nlm-rail-action").on("click", lambda: upload_dialog.open()):
                        ui.label("+").classes("nlm-rail-glyph")
                    for glyph, tone, title in [
                        ("N", "black", "来源 1"),
                        ("C", "red", "来源 2"),
                        ("☆", "cyan", "来源 3"),
                        ("u", "gray", "来源 4"),
                        ("▦", "multi", "来源 5"),
                        ("π", "red", "来源 6"),
                        ("◉", "black", "来源 7"),
                        ("▱", "blue", "来源 8"),
                    ]:
                        with ui.element("button").props(f'type="button" aria-label="{title}"').classes(f"nlm-rail-source nlm-rail-{tone}"):
                            ui.label(glyph).classes("nlm-rail-glyph")
                with ui.element("button").props('type="button"').classes("nlm-native-btn nlm-add-source-btn").on("click", lambda: upload_dialog.open()):
                    ui.icon("add").props('aria-hidden="true"')
                    ui.label("添加来源")
                with ui.element("div").classes("nlm-search-card"):
                    source_search = ui.input("在网络中搜索新来源").props("borderless dense").classes("nlm-search-input")
                    with ui.row().classes("items-center gap-2"):
                        native_pill("Web", "language", lambda: open_source_search(source_search, "web"), "nlm-chip")
                        native_pill("Fast Research", "travel_explore", lambda: open_source_search(source_search, "research"), "nlm-chip")
                        ui.space()
                        native_icon_button("search", "搜索来源", lambda: open_source_search(source_search, "web"), "nlm-search-btn")
                    source_search.on("keydown.enter", lambda _event=None: open_source_search(source_search, "web"))
                with ui.row().classes("nlm-source-summary items-center"):
                    ui.icon("auto_awesome").classes("nlm-small-icon")
                    ui.label(f"{notes_count} 条笔记 · {upload_count} 次导入").classes("nlm-muted")
                    ui.space()
                    ui.label("全选").classes("nlm-muted")
                    ui.checkbox(value=True).props("dense").classes("nlm-check")

                source_list_box = ui.column().classes("nlm-source-list")
                upload_history_box = ui.column().classes("nlm-upload-list")

            with ui.element("section").classes("nlm-panel nlm-chat-panel animate-card"):
                with ui.row().classes("nlm-panel-head items-center"):
                    ui.label("对话").classes("nlm-panel-title")
                    ui.space()
                    with ui.element("div").classes("nlm-more-wrap"):
                        with ui.element("button").props('type="button" aria-label="更多对话选项"').classes("nlm-native-btn nlm-icon-btn nlm-more-btn"):
                            ui.icon("more_vert").props('aria-hidden="true"')
                        with ui.element("div").classes("nlm-more-tooltip"):
                            ui.label("对话记录现在会在会话之间保存，")
                            ui.label("您可以在此处删除对话记录")
                        with ui.element("div").classes("nlm-more-menu"):
                            ui.label("自定义笔记本").classes("nlm-more-menu-item")
                            ui.label("删除对话记录").classes("nlm-more-menu-item nlm-more-disabled")
                            ui.label("只有您自己能看到对话记录。").classes("nlm-more-menu-note")
                with ui.element("div").classes("nlm-chat-scroll"):
                    with ui.row().classes("justify-end w-full"):
                        native_pill("自定义", "auto_awesome", lambda: ui.notify("可根据课程目标自定义回答风格。"), "nlm-customize")
                    with ui.column().classes("nlm-answer-wrap"):
                        ui.icon("construction").classes("nlm-answer-mark")
                        ui.label("个人课程资料问答").classes("nlm-answer-title")
                        source_count_label = ui.label(f"{notes_count} 个来源 · {date.today().isoformat()}").classes("nlm-answer-meta")
                        with ui.element("div").classes("nlm-answer-body"):
                            answer_box = ui.markdown(
                                "这些资料会作为回答依据。你可以先问：**RAG 的流程是什么？**"
                            ).classes("answer-box")
                        with ui.row().classes("nlm-answer-actions items-center"):
                            native_pill("保存到笔记", "push_pin", lambda: ui.notify("回答保存入口已预留。"), "nlm-action-btn")
                            native_icon_button("content_copy", "复制回答")
                            native_icon_button("thumb_up", "有帮助")
                            native_icon_button("thumb_down", "没有帮助")
                        sources_box = ui.column().classes("nlm-citation-list")
                with ui.row().classes("nlm-prompt-bar items-center"):
                    question = ui.input(placeholder="开始输入...").props("borderless").classes("nlm-prompt-input")
                    prompt_count_label = ui.label(f"{notes_count} 个来源").classes("nlm-prompt-count")
                    native_icon_button("arrow_forward", "发送问题", ask, "nlm-send-btn")

            with ui.element("section").classes("nlm-panel nlm-studio-panel animate-card"):
                with ui.row().classes("nlm-panel-head items-center"):
                    ui.label("Studio").classes("nlm-panel-title")
                    ui.space()
                    panel_toggle_button("studio", "收起或展开 Studio")
                with ui.element("div").classes("nlm-rail nlm-studio-rail"):
                    for kind, title, symbol, tone in studio_actions:
                        with ui.element("button").props(f'type="button" aria-label="{title}"').classes(f"nlm-rail-studio nlm-tile-{tone}").on("click", studio_action_handler(kind, title)):
                            ui.label(symbol).classes("nlm-rail-glyph")
                with ui.element("div").classes("nlm-studio-grid") as studio_grid_box:
                    for kind, title, symbol, tone in studio_actions:
                        with ui.element("button").classes(f"nlm-studio-tile nlm-tile-{tone}").on("click", studio_action_handler(kind, title)):
                            ui.label(symbol).classes("nlm-tile-icon")
                            ui.label(title).classes("nlm-tile-title")
                            ui.label("›").classes("nlm-tile-arrow")
                studio_sep = ui.separator().classes("nlm-studio-sep")
                studio_output_box = ui.column().classes("nlm-studio-empty")
                render_studio_list()

    def render_sources() -> None:
        source_list_box.clear()
        upload_history_box.clear()
        notes = list_notes()
        with source_list_box:
            if not notes:
                with ui.column().classes("nlm-source-empty"):
                    ui.icon("docs").classes("nlm-empty-icon")
                    ui.label("已保存的来源将显示在此处").classes("nlm-empty-title")
                    ui.label("点击上方的“添加来源”即可添加 PDF、网站、文本、视频或音频文件。").classes("nlm-empty-copy")
            else:
                for note in notes[:9]:
                    with ui.row().classes("nlm-source-item items-center"):
                        ui.icon("article").classes("nlm-source-type")
                        with ui.column().classes("gap-0 nlm-source-copy"):
                            ui.label(str(note.get("title") or "未命名笔记")).classes("nlm-source-title")
                            ui.label(str(note.get("course") or "未分类")).classes("nlm-source-meta")
                        ui.checkbox(value=True).props("dense").classes("nlm-check")
                if len(notes) > 9:
                    ui.label(f"还有 {len(notes) - 9} 条来源，可通过课程范围过滤后问答。").classes("nlm-muted nlm-source-more")
        with upload_history_box:
            records = list_note_uploads()
            if records:
                ui.label("导入记录").classes("nlm-mini-title")
                for record in records[:3]:
                    with ui.element("div").classes("nlm-upload-record"):
                        ui.label(record.get("file_name") or "未命名笔记").classes("nlm-upload-title")
                        ui.label(f"{record.get('source_type') or '上传'} · {record.get('note_count', 0)} 条").classes("nlm-source-meta")

    def refresh_after_upload() -> None:
        fresh_courses = ["全部课程"] + sorted({note["course"] for note in list_notes()})
        course.options = fresh_courses
        fresh_count = len(list_notes())
        source_count_label.set_text(f"{fresh_count} 个来源 · {date.today().isoformat()}")
        prompt_count_label.set_text(f"{fresh_count} 个来源")
        render_sources()
        studio_outputs.clear()
        render_studio_list()

    async def handle_file_upload(event) -> None:
        raw = await event.file.read()
        selected = normalize_course(upload_course.value, "未分类")
        parsed_notes = parse_uploaded_notes(event.file.name, raw, selected)
        if not parsed_notes:
            ui.notify("暂不支持该文件格式，请上传 xlsx、csv、txt 或 md。", color="warning")
            return
        for item in parsed_notes:
            add_note(item["course"], item["title"], item["content"], item.get("tags", "上传"))
        add_note_upload(
            event.file.name,
            "文件上传",
            selected,
            len(parsed_notes),
            f"已从文件解析：{', '.join(item['title'] for item in parsed_notes[:3])}",
        )
        build_vector_index()
        refresh_after_upload()
        upload_dialog.close()
        ui.notify(f"已上传 {len(parsed_notes)} 条笔记，并更新问答索引。", color="positive")

    def save_pasted_note() -> None:
        text = pasted_note.value.strip()
        if not text:
            ui.notify("请先粘贴笔记内容。", color="warning")
            return
        selected = normalize_course(upload_course.value, "未分类")
        title = pasted_title.value.strip() or f"{selected} 上传笔记"
        add_note(selected, title, text, "复制文本,上传")
        add_note_upload(title, "复制的文字", selected, 1, "已从复制文本生成一条个人笔记。")
        build_vector_index()
        pasted_note.set_value("")
        pasted_title.set_value("")
        refresh_after_upload()
        upload_dialog.close()
        ui.notify("已保存复制文本，并更新问答索引。", color="positive")

    def reveal_paste_form() -> None:
        ui.run_javascript("document.querySelector('.upload-form-area')?.classList.add('is-visible')")
        pasted_note.run_method("focus")

    with ui.dialog() as upload_dialog, ui.card().classes("upload-dialog nlm-upload-dialog"):
        with ui.row().classes("upload-dialog-head items-start w-full"):
            ui.space()
            with ui.column().classes("gap-1 upload-title-wrap"):
                ui.label("根据以下内容生成学习资料概览").classes("upload-dialog-title")
            ui.space()
            ui.button(icon="close", on_click=upload_dialog.close).props("flat round").classes("icon-btn upload-close")
        with ui.element("div").classes("upload-search-shell"):
            upload_search = ui.input("在网络中搜索新来源").props("borderless").classes("upload-search-input")
            with ui.row().classes("gap-2 items-center"):
                ui.button("Web", icon="language", on_click=lambda: open_source_search(upload_search, "web")).props("flat").classes("chip-btn")
                ui.button("Fast Research", icon="travel_explore", on_click=lambda: open_source_search(upload_search, "research")).props("flat").classes("chip-btn")
                ui.space()
                ui.button(icon="search", on_click=lambda: open_source_search(upload_search, "web")).props("round flat").classes("search-round")
            upload_search.on("keydown.enter", lambda _event=None: open_source_search(upload_search, "web"))
        with ui.element("div").classes("upload-dropzone"):
            drop_uploader = ui.upload(on_upload=handle_file_upload, label="上传文件", auto_upload=True, max_file_size=5_000_000).props(
                "accept=.xlsx,.xls,.csv,.txt,.md"
            ).classes("upload-widget upload-dropzone-uploader")
            ui.label("拖放文件").classes("drop-title")
            ui.label("Excel、CSV、TXT、Markdown").classes("drop-copy")
            with ui.row().classes("upload-source-actions"):
                ui.button(
                    "上传文件",
                    icon="add",
                    on_click=lambda: ui.run_javascript(
                        "document.querySelector('.upload-dropzone-uploader input[type=file]')?.click()"
                    ),
                ).props("flat").classes("source-action-btn")
                ui.button("网站", icon="link", on_click=lambda: open_source_search(upload_search, "web")).props("flat").classes("source-action-btn")
                ui.button("云端硬盘", icon="cloud", on_click=lambda: ui.notify("云端硬盘入口已预留，可先使用上传文件。")).props("flat").classes("source-action-btn")
                ui.button("复制的文字", icon="content_paste", on_click=reveal_paste_form).props("flat").classes("source-action-btn")
        with ui.column().classes("upload-form-area"):
            upload_course = ui.select(["AI应用开发", "Python", "英语", "未分类"], value="AI应用开发", label="上传到课程").classes("w-64")
            pasted_title = ui.input("复制文字的笔记标题", placeholder="例如：RAG 复习补充").classes("w-full")
            pasted_note = ui.textarea("粘贴笔记内容", placeholder="把课程笔记粘贴到这里，点击保存后可用于 AI 问笔记。").classes("w-full")
            ui.button("保存复制的文字", icon="save", on_click=save_pasted_note).props("color=dark").classes("upload-save-btn")

    question.on("keydown.enter", ask)
    render_sources()


@ui.refreshable
def render_unfinished_tasks() -> None:
    with ui.card().classes("content-card task-board-card w-full animate-card"):
        rows = list_todos()
        unfinished = [row for row in rows if not todo_is_completed(row)]
        completed_count = len(rows) - len(unfinished)
        high_count = sum(1 for row in unfinished if row.get("priority") == "高")

        task_input = None
        result = None

        def submit_task() -> None:
            response = agent.handle(task_input.value or "")
            result.classes(remove="hidden-result")
            result.set_content(response["text"])
            add_work_log("任务管理", "自然语言任务处理", response["text"])
            render_unfinished_tasks.refresh()
            render_dashboard.refresh()

        with ui.row().classes("task-board-head items-center no-wrap"):
            with ui.element("div").classes("task-board-icon"):
                ui.icon("fact_check")
            with ui.column().classes("gap-0 task-board-title"):
                ui.label("任务列表").classes("section-title")
                ui.label(f"{len(unfinished)} 项未完成 · {high_count} 项高优先级 · {completed_count} 项已完成").classes("task-board-subtitle")
            ui.space()
            with ui.row().classes("task-board-actions items-end no-wrap"):
                task_input = ui.input(
                    "快速添加任务",
                    placeholder="例如：周五前完成 Python 列表推导式作业",
                ).classes("task-inline-input")
                ui.button("解析并执行", icon="arrow_forward", on_click=submit_task).props("color=dark").classes("task-inline-submit")
                ui.button(icon="refresh", on_click=render_unfinished_tasks.refresh).props("flat round").classes("task-refresh-btn")
        result = ui.markdown("").classes("task-inline-result hidden-result")
        if not rows:
            ui.label("暂无任务。").classes("empty-copy")
        else:
            with ui.element("div").classes("todo-table"):
                with ui.element("div").classes("todo-table-row todo-table-head"):
                    ui.label("").classes("todo-check-cell")
                    ui.label("ID").classes("todo-id-cell")
                    ui.label("课程")
                    ui.label("任务")
                    ui.label("截止日期")
                    ui.label("优先级")
                    ui.label("状态").classes("todo-status-cell")

                for row in rows:
                    completed = todo_is_completed(row)
                    row_classes = "todo-table-row todo-table-row-done" if completed else "todo-table-row"
                    row_el = ui.element("div").classes(row_classes)
                    status_ref: dict[str, object] = {}

                    def toggle_todo_status(
                        event,
                        todo_id: int = int(row["id"]),
                        row_element=row_el,
                        status_holder=status_ref,
                    ) -> None:
                        next_status = "已完成" if event.value else "未完成"
                        if update_todo_status(todo_id, next_status):
                            status = status_holder.get("label")
                            status.set_text(next_status)
                            if event.value:
                                status.classes(remove="status-pending", add="status-done")
                                row_element.classes(add="todo-table-row-done")
                            else:
                                status.classes(remove="status-done", add="status-pending")
                                row_element.classes(remove="todo-table-row-done")
                            render_dashboard.refresh()
                            message = "已标记完成" if event.value else "已恢复为未完成"
                            ui.notify(f"任务 #{todo_id} {message}", color="positive")
                        else:
                            ui.notify(f"没有找到任务 #{todo_id}", color="warning")

                    with row_el:
                        ui.checkbox(value=completed, on_change=toggle_todo_status).props("dense").classes("todo-check-cell todo-complete-check")
                        ui.label(str(row.get("id"))).classes("todo-id-cell")
                        ui.label(str(row.get("course") or "未分类")).classes("todo-course-cell")
                        ui.label(str(row.get("task") or "")).classes("todo-task-cell")
                        ui.label(str(row.get("deadline") or "未设置")).classes("todo-date-cell")
                        priority = str(row.get("priority") or "中")
                        priority_class = {"高": "todo-priority-high", "中": "todo-priority-mid", "低": "todo-priority-low"}.get(priority, "todo-priority-mid")
                        ui.label(priority).classes(f"todo-priority-cell todo-priority-pill {priority_class}")
                        status_class = "status-done" if completed else "status-pending"
                        status_ref["label"] = ui.label(str(row.get("status") or "未完成")).classes(f"todo-status-badge {status_class}")


def render_tasks() -> None:
    render_unfinished_tasks()


def render_review() -> None:
    courses = ["全部课程"] + sorted({note["course"] for note in list_notes()})
    records = list_quiz_records()
    default_course = "Python" if "Python" in courses else courses[0]

    with ui.card().classes("content-card review-command-card w-full animate-card"):
        with ui.row().classes("review-command-head items-center no-wrap"):
            with ui.element("div").classes("review-command-icon"):
                ui.icon("school")
            with ui.column().classes("gap-0 review-command-title"):
                ui.label("复习训练").classes("section-title")
                ui.label(f"{len(records)} 条测验记录 · 根据个人笔记生成练习题").classes("review-command-subtitle")
            ui.space()
            with ui.row().classes("review-command-actions items-end no-wrap"):
                course = ui.select(courses, value=default_course, label="选择课程").classes("review-course-select")
                generate_button = ui.button("生成测验", icon="arrow_forward").props("color=dark").classes("review-generate-btn")
        quiz_box = ui.column().classes("quiz-list w-full")

        def generate() -> None:
            items = QuizAgent().generate_quiz_items(course.value, 5)
            quiz_box.clear()
            with quiz_box:
                if not items:
                    ui.label("暂时没有找到可生成测验的笔记，请先补充笔记。").classes("empty-copy")
                    return
                single_count = sum(1 for item in items if item["type"] == "single")
                blank_count = sum(1 for item in items if item["type"] == "blank")
                with ui.row().classes("quiz-summary items-center"):
                    ui.label(f"一、单选题（共 {single_count} 题）").classes("quiz-section-title")
                    ui.label(f"二、填空题（共 {blank_count} 题）").classes("quiz-section-title")
                for index, item in enumerate(items, 1):
                    render_quiz_item(index, item)
                ui.run_javascript("window.studypilotMotion && window.studypilotMotion.sources();")

        generate_button.on("click", generate)

    with ui.card().classes("content-card review-record-card w-full animate-card"):
        with ui.row().classes("review-record-head items-center no-wrap"):
            with ui.element("div").classes("review-record-icon"):
                ui.icon("history_edu")
            with ui.column().classes("gap-0"):
                ui.label("最近测验记录").classes("section-title")
                ui.label("按最近生成顺序展示，方便回到错题和知识点。").classes("review-command-subtitle")
        if not records:
            ui.label("暂无测验记录，先选择课程生成一组练习题。").classes("empty-copy")
        else:
            with ui.element("div").classes("review-record-list"):
                for record in records[:8]:
                    with ui.element("div").classes("review-record-item"):
                        ui.label(str(record["course"])).classes("review-record-course")
                        ui.label(str(record["question"])).classes("review-record-question")


def render_quiz_item(index: int, item: dict[str, object]) -> None:
    answer_state: dict[str, object] = {"value": "", "submitted": False}
    with ui.card().classes("quiz-card animate-source"):
        with ui.row().classes("quiz-question-head items-center"):
            ui.label(f"{index}.").classes("quiz-number")
            ui.label(f"（{item['type_label']}，2.0 分）").classes("quiz-type")
        ui.label(str(item["question"])).classes("quiz-question")

        result_box = ui.column().classes("quiz-result-box hidden")

        if item["type"] == "single":
            options = item.get("options", [])
            option_column = ui.column().classes("quiz-options")
            option_refs: dict[str, object] = {}

            def select_option(key: str) -> None:
                if answer_state["submitted"]:
                    return
                answer_state["value"] = key
                for option_key, option_ref in option_refs.items():
                    if option_key == key:
                        option_ref.classes(add="quiz-option-selected")
                    else:
                        option_ref.classes(remove="quiz-option-selected")

            with option_column:
                for option in options:
                    key = str(option["key"])
                    option_row = ui.element("button").classes("quiz-option")
                    option_refs[key] = option_row
                    option_row.on("click", lambda _, selected_key=key: select_option(selected_key))
                    with option_row:
                        ui.label(key).classes("quiz-option-dot")
                        ui.label(str(option["text"])).classes("quiz-option-text")

            def submit_single() -> None:
                if not answer_state["value"]:
                    ui.notify("请先选择一个选项。", color="warning")
                    return
                answer_state["submitted"] = True
                option_column.classes(add="quiz-options-submitted")
                show_quiz_result(result_box, str(answer_state["value"]), str(item["answer"]), str(item["reference_answer"]))

            ui.button("提交答案", icon="check_circle", on_click=submit_single).classes("primary-btn quiz-submit")
        else:
            answer_input = ui.input("我的答案", placeholder="在这里填写答案").props("outlined").classes("quiz-blank-input")

            def submit_blank() -> None:
                user_answer = str(answer_input.value or "").strip()
                if not user_answer:
                    ui.notify("请先填写答案。", color="warning")
                    return
                answer_state["submitted"] = True
                answer_input.disable()
                correct = answer_matches(user_answer, str(item["reference_answer"]))
                show_quiz_result(result_box, user_answer, str(item["reference_answer"]), str(item["reference_answer"]), correct)

            ui.button("提交答案", icon="check_circle", on_click=submit_blank).classes("primary-btn quiz-submit")


def show_quiz_result(
    result_box,
    user_answer: str,
    correct_answer: str,
    reference_answer: str,
    is_correct: bool | None = None,
) -> None:
    if is_correct is None:
        is_correct = user_answer == correct_answer
    result_box.clear()
    result_box.classes(remove="hidden")
    with result_box:
        with ui.row().classes("quiz-answer-row items-center"):
            ui.label("我的答案:").classes("quiz-answer-label")
            ui.label(user_answer or "未作答").classes("quiz-user-answer")
            ui.space()
            ui.icon("check" if is_correct else "close").classes("quiz-correct-icon" if is_correct else "quiz-wrong-icon")
        with ui.row().classes("quiz-answer-row items-center"):
            ui.label("正确答案:").classes("quiz-answer-label quiz-answer-correct")
            ui.label(correct_answer).classes("quiz-correct-answer")
        ui.label("参考答案:").classes("quiz-reference-title")
        ui.label(reference_answer).classes("quiz-reference-copy")


def main() -> None:
    ui.add_head_html(
        """
        <style>
        :root {
          --sp-blue: #2563EB;
          --sp-ink: #0F172A;
          --sp-muted: #64748B;
          --sp-card: rgba(255,255,255,.88);
        }
        html {
          scroll-behavior: smooth;
        }
        body {
          background: #1CA2D6;
          color: var(--sp-ink);
          font-family: "Microsoft YaHei", "PingFang SC", Inter, Arial, sans-serif;
        }
        body::before {
          content: "";
          position: fixed;
          inset: 0;
          pointer-events: none;
          z-index: -2;
          background: url('https://www.fillout.com/_next/image?url=%2F_next%2Fstatic%2Fmedia%2Fhero.0c57f950.png&w=3840&q=75') center top / cover no-repeat;
        }
        body::after {
          content: "";
          position: fixed;
          inset: 0;
          z-index: -1;
          pointer-events: none;
          background: linear-gradient(180deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0) 62%, rgba(255,255,255,.42) 78%, #FFFFFF 100%);
        }
        .nicegui-content {
          max-width: none;
          margin: 0;
          padding-top: 0 !important;
        }
        .q-header {
          position: fixed !important;
          top: 16px;
          left: 50%;
          transform: translateX(-50%);
          width: min(1280px, calc(100vw - 48px));
          min-height: 64px;
          padding: 0 24px;
          border-radius: 18px;
          background: rgba(213,235,247,.58) !important;
          color: #1F2937 !important;
          border: 1px solid rgba(255,255,255,.46);
          box-shadow: 0 16px 44px rgba(15,23,42,.1), inset 0 1px 0 rgba(255,255,255,.55);
          backdrop-filter: blur(20px) saturate(1.16);
          -webkit-backdrop-filter: blur(20px) saturate(1.16);
          z-index: 3300 !important;
        }
        .app-shell {
          width: 100%;
          min-height: 100vh;
          padding: 16px clamp(14px, 4vw, 64px) 48px;
        }
        .top-header {
          display: grid !important;
          grid-template-columns: minmax(180px, 1fr) auto minmax(280px, 1fr);
          align-items: center;
          column-gap: 24px;
        }
        .top-brand {
          justify-self: start;
          min-width: 210px;
          font-size: 24px;
          font-weight: 900;
          color: #1F2937;
          letter-spacing: 0;
        }
        .top-tabs {
          justify-self: center;
          width: auto;
          max-width: none;
          margin: 0;
          background: transparent !important;
          border: 0 !important;
          box-shadow: none !important;
          padding: 0 !important;
        }
        .top-tabs .q-tab {
          min-height: 40px;
          border-radius: 12px;
          color: #263345;
          font-weight: 750;
        }
        .top-tabs .q-tab--active {
          background: rgba(255,255,255,.56);
          color: #0F172A;
          box-shadow: 0 10px 24px rgba(15,23,42,.1);
        }
        .top-right {
          justify-self: end;
          gap: 18px;
          flex-wrap: nowrap;
        }
        .top-meta {
          color: #31445A;
          font-size: 13px;
          font-weight: 750;
        }
        .top-action {
          border-radius: 10px !important;
          background: #2D2D2D !important;
          background-color: #2D2D2D !important;
          color: white !important;
          font-weight: 800 !important;
          box-shadow: 0 1px 2px rgba(0,0,0,.4), 1px 4px 8px rgba(0,0,0,.14) !important;
        }
        .top-action::before,
        .hero-action::before {
          background: transparent !important;
        }
        .main-tab-panels {
          background: transparent;
        }
        .main-tab-panels > .q-panel-parent,
        .main-tab-panels .q-tab-panel {
          background: transparent;
        }
        .main-tab-panels .q-tab-panel {
          padding: 108px clamp(18px, 4vw, 64px) 36px;
        }
        .main-tab-panels .q-tab-panel:first-child {
          padding: 0;
        }
        .main-tab-panels .q-tab-panel:has(.notebooklm-shell) {
          padding: 0;
          min-height: 100vh;
        }
        .top-header.ai-notes-active {
          opacity: 0;
          visibility: hidden;
          pointer-events: none;
          transform: translateX(-50%) translateY(-16px);
        }
        .q-tabs {
          background: rgba(255,255,255,.42);
          border: 1px solid rgba(148,163,184,.22);
          border-radius: 14px;
          padding: 6px;
          box-shadow: 0 12px 30px rgba(15,23,42,.08);
          backdrop-filter: blur(16px) saturate(1.12);
          -webkit-backdrop-filter: blur(16px) saturate(1.12);
        }
        .q-tab {
          border-radius: 10px;
          min-height: 42px;
        }
        .q-tab--active {
          background: #2563EB;
          color: #fff;
          box-shadow: 0 10px 22px rgba(37,99,235,.28);
        }
        .top-tabs .q-tab,
        .top-tabs .q-tab .q-icon,
        .top-tabs .q-tab .q-tab__label {
          color: #263345 !important;
        }
        .top-tabs .q-tab::before,
        .top-tabs .q-tab .q-focus-helper {
          background: transparent !important;
          opacity: 0 !important;
        }
        .top-tabs .q-tab--active,
        .top-tabs .q-tab[aria-selected="true"] {
          background: rgba(255,255,255,.56) !important;
          color: #0F172A !important;
          box-shadow: 0 10px 24px rgba(15,23,42,.1) !important;
        }
        .top-tabs .q-tab--active .q-icon,
        .top-tabs .q-tab--active .q-tab__label,
        .top-tabs .q-tab[aria-selected="true"] .q-icon,
        .top-tabs .q-tab[aria-selected="true"] .q-tab__label {
          color: #0F172A !important;
        }
        .top-tabs .q-tab__indicator {
          display: none !important;
          opacity: 0 !important;
          height: 0 !important;
        }
        .page-head {
          width: 100%;
          padding: 8px 2px 2px;
          gap: 12px;
        }
        .page-icon {
          width: 42px;
          height: 42px;
          display: grid;
          place-items: center;
          border-radius: 12px;
          color: #2563EB;
          background: #DBEAFE;
          box-shadow: 0 12px 22px rgba(37,99,235,.14);
        }
        .page-title { font-size: 24px; font-weight: 900; color: var(--sp-ink); margin: 0; letter-spacing: 0; }
        .page-subtitle { color: #64748B; font-size: 13px; }
        .section-title { font-size: 16px; font-weight: 800; color: var(--sp-ink); margin-bottom: 8px; }
        .card-head { gap: 8px; margin-bottom: 6px; }
        .section-icon { color: #2563EB; background: #EFF6FF; border-radius: 10px; padding: 6px; }
        .dashboard-shell {
          position: relative;
          width: 100%;
          min-height: 100vh;
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 26px;
          padding: 46px 0 20px;
        }
        .mineradio-float {
          position: fixed;
          right: clamp(16px, 3vw, 36px);
          bottom: clamp(18px, 5vw, 42px);
          z-index: 2500;
          width: 142px;
          min-height: 72px;
          display: grid;
          grid-template-columns: 54px minmax(0, 1fr);
          align-items: center;
          gap: 10px;
          padding: 10px 12px 10px 10px;
          border: 1px solid rgba(255,255,255,.64);
          border-radius: 16px;
          color: #0F172A;
          background: rgba(255,255,255,.72);
          box-shadow: 0 18px 34px rgba(15,23,42,.16), inset 0 1px 0 rgba(255,255,255,.75);
          backdrop-filter: blur(18px) saturate(1.15);
          -webkit-backdrop-filter: blur(18px) saturate(1.15);
          cursor: grab;
          user-select: none;
          touch-action: none;
          transition: transform .18s ease, box-shadow .18s ease, background .18s ease;
        }
        .mineradio-float:hover {
          transform: translateY(-2px);
          background: rgba(255,255,255,.84);
          box-shadow: 0 22px 42px rgba(15,23,42,.2), inset 0 1px 0 rgba(255,255,255,.88);
        }
        .mineradio-float:active {
          cursor: grabbing;
        }
        .mineradio-float.is-dragging {
          transition: none;
          transform: scale(.98);
        }
        .mineradio-disc {
          position: relative;
          width: 54px;
          height: 54px;
          display: block;
          border-radius: 50%;
          background:
            radial-gradient(circle at 50% 50%, #F4F7FB 0 5%, #1F5FAF 6% 12%, #EFF6FF 13% 21%, transparent 22%),
            repeating-radial-gradient(circle at 50% 50%, rgba(255,255,255,.16) 0 1px, transparent 1px 3px),
            radial-gradient(circle at 30% 24%, rgba(255,255,255,.36) 0 10%, transparent 26%),
            conic-gradient(from 28deg, rgba(255,255,255,.18), transparent 16%, rgba(96,165,250,.18) 25%, transparent 36%, rgba(255,255,255,.12) 48%, transparent 64%, rgba(37,99,235,.14) 74%, transparent 100%),
            radial-gradient(circle at 50% 50%, #1C2433 0 47%, #0B1020 72%, #020617 100%);
          box-shadow:
            0 10px 20px rgba(15,23,42,.22),
            inset 0 0 0 1px rgba(255,255,255,.24),
            inset 0 0 0 7px rgba(255,255,255,.035),
            inset 0 0 18px rgba(0,0,0,.62);
          animation: mineradio-spin 10.5s linear infinite;
        }
        .mineradio-disc::before,
        .mineradio-disc::after {
          content: "";
          position: absolute;
          border-radius: 50%;
          pointer-events: none;
        }
        .mineradio-disc::before {
          inset: 5px;
          background:
            linear-gradient(118deg, transparent 0 34%, rgba(255,255,255,.2) 35% 40%, transparent 41% 100%),
            radial-gradient(circle at 50% 50%, transparent 0 34%, rgba(255,255,255,.08) 35% 36%, transparent 37% 52%, rgba(255,255,255,.08) 53% 54%, transparent 55%);
          box-shadow: inset 0 0 0 1px rgba(255,255,255,.08);
        }
        .mineradio-disc::after {
          inset: 18px;
          background:
            radial-gradient(circle at 50% 50%, #111827 0 12%, transparent 13%),
            radial-gradient(circle at 38% 30%, rgba(255,255,255,.34), transparent 36%),
            linear-gradient(145deg, #DCEBFF, #60A5FA 48%, #1D4ED8);
          border: 2px solid rgba(255,255,255,.72);
          box-shadow:
            0 0 0 1px rgba(15,23,42,.18),
            inset 0 1px 3px rgba(255,255,255,.48),
            inset 0 -3px 8px rgba(15,23,42,.2);
        }
        .mineradio-disc-core {
          position: absolute;
          inset: 25px;
          z-index: 1;
          border-radius: 50%;
          background: #F8FAFC;
          box-shadow: 0 0 0 1px rgba(15,23,42,.34), 0 0 0 3px rgba(255,255,255,.24);
        }
        .mineradio-float-copy {
          display: flex;
          min-width: 0;
          flex-direction: column;
          align-items: flex-start;
          gap: 1px;
          text-align: left;
          line-height: 1.1;
        }
        .mineradio-float-title,
        .mineradio-float-subtitle {
          margin: 0;
          white-space: nowrap;
        }
        .mineradio-float-title {
          color: #172033;
          font-size: 13px;
          font-weight: 900;
        }
        .mineradio-float-subtitle {
          color: #2563EB;
          font-size: 12px;
          font-weight: 800;
        }
        @keyframes mineradio-spin {
          to { transform: rotate(360deg); }
        }
        .dashboard-hero {
          width: min(1120px, 100%);
          min-height: 312px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          padding-top: 0;
          text-align: center;
          color: #FFFFFF;
        }
        .hero-kicker-pill {
          display: inline-flex;
          align-items: center;
          gap: 7px;
          height: 36px;
          padding: 0 16px;
          border-radius: 999px;
          color: #1F2937;
          background: rgba(255,255,255,.58);
          border: 1px solid rgba(255,255,255,.42);
          box-shadow: 0 8px 24px rgba(15,23,42,.08);
          backdrop-filter: blur(14px);
          -webkit-backdrop-filter: blur(14px);
          font-weight: 850;
          margin-bottom: 18px;
        }
        .hero-kicker-pill .q-icon { color: #1B6AAE; }
        .hero-title {
          width: 100%;
          font-size: clamp(54px, 7vw, 96px);
          font-weight: 950;
          line-height: .98;
          color: #FFFFFF;
          letter-spacing: 0;
          text-shadow: 0 14px 38px rgba(12,80,133,.18);
        }
        .hero-subtitle {
          margin-top: 20px;
          color: rgba(255,255,255,.96);
          font-size: clamp(18px, 2vw, 29px);
          font-weight: 750;
          line-height: 1.45;
          max-width: 980px;
        }
        .hero-cta-row { gap: 16px; margin-top: 22px; flex-wrap: wrap; }
        .hero-action {
          height: 44px;
          padding: 0 20px !important;
          border-radius: 10px !important;
          background: #2D2D2D !important;
          background-color: #2D2D2D !important;
          color: #FFFFFF !important;
          font-weight: 850 !important;
          box-shadow: 0 1px 2px rgba(0,0,0,.4), 1px 4px 8px rgba(0,0,0,.14) !important;
        }
        .hero-inline-stat {
          display: inline-flex;
          gap: 10px;
          align-items: center;
          min-height: 40px;
          padding: 8px 14px;
          border-radius: 999px;
          color: #24445D;
          background: rgba(255,255,255,.52);
          border: 1px solid rgba(255,255,255,.42);
          backdrop-filter: blur(14px);
          -webkit-backdrop-filter: blur(14px);
          font-size: 13px;
          font-weight: 850;
        }
        .dashboard-stage {
          position: relative;
          width: min(1480px, calc(100% - 36px));
          display: flex;
          flex-direction: column;
          gap: 4px;
          padding: 4px;
          border-radius: 16px 16px 18px 18px;
          background: rgba(255,255,255,.18);
          border: 0;
          box-shadow:
            0 12px 32px -10px rgba(15,23,42,.06),
            0 0 0 1px rgba(255,255,255,.24),
            0 1px 3px rgba(63,70,75,.04);
          backdrop-filter: blur(16px) saturate(1.1);
          -webkit-backdrop-filter: blur(16px) saturate(1.1);
          overflow: hidden;
        }
        .dashboard-feature-tabs {
          width: 100%;
          height: 40px;
          min-height: 40px;
          margin: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          border: 0;
          border-radius: 10px;
          background: transparent;
          box-shadow: none;
          padding: 2px;
        }
        .dashboard-feature-tabs .q-tabs__content {
          width: 100%;
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          justify-content: center;
          gap: 4px;
          margin: 0 auto;
        }
        .dashboard-feature-tabs .q-tab {
          min-height: 36px;
          height: 36px;
          width: auto;
          border-radius: 10px;
          margin: 0;
          color: #597082;
          background: transparent !important;
          box-shadow: none !important;
          font-size: 15px;
          font-weight: 850;
        }
        .dashboard-feature-tabs .q-tab::before,
        .dashboard-feature-tabs .q-tab .q-focus-helper {
          background: transparent !important;
          opacity: 0 !important;
        }
        .dashboard-feature-tabs .q-tab--active {
          color: #1E293B;
          background: transparent !important;
          box-shadow: none !important;
        }
        .dashboard-feature-tabs.dashboard-feature-tabs-user-active .q-tab--active {
          color: #1E293B;
          background: rgba(255,255,255,.64) !important;
          box-shadow: 0 8px 18px rgba(15,23,42,.08), inset 0 1px 0 rgba(255,255,255,.64) !important;
        }
        .dashboard-feature-tabs .q-tab__indicator {
          display: none !important;
          opacity: 0 !important;
          height: 0 !important;
        }
        .dashboard-feature-panels {
          width: 100%;
          background: transparent;
        }
        .dashboard-feature-panels .q-panel,
        .dashboard-feature-panel {
          background: transparent;
        }
        .dashboard-feature-panel {
          padding: 0 !important;
        }
        .summary-layout {
          width: 100%;
          box-sizing: border-box;
          min-height: 640px;
          display: grid;
          grid-template-columns: minmax(360px, .74fr) minmax(680px, 1.26fr);
          gap: 40px;
          align-items: stretch;
          padding: 52px clamp(36px, 4vw, 72px);
          border-radius: 16px;
          background: #FFFFFF;
          margin-top: 0;
        }
        .summary-copy {
          min-width: 0;
          min-height: 560px;
          justify-content: center;
          padding: 0 6px 0 0;
          gap: 24px;
        }
        .feature-title {
          color: #222222;
          font-size: clamp(34px, 4.2vw, 52px);
          font-weight: 950;
          line-height: 1.06;
          letter-spacing: 0;
        }
        .feature-copy {
          color: #4B5563;
          font-size: 17px;
          line-height: 1.75;
          font-weight: 650;
          max-width: 520px;
        }
        .summary-point {
          gap: 14px;
        }
        .summary-point-icon {
          width: 34px;
          height: 34px;
          display: grid;
          place-items: center;
          border-radius: 9px;
          color: #333333;
          background: #F6F7F8;
          border: 1px solid #E6E8EC;
          box-shadow: 0 3px 8px rgba(15,23,42,.05);
        }
        .summary-point-title {
          color: #2B2B2B;
          font-size: 18px;
          font-weight: 900;
        }
        .summary-point-text {
          color: #6B7280;
          font-size: 13px;
          line-height: 1.55;
        }
        .summary-metrics {
          min-width: 0;
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 22px;
          align-content: center;
          padding: clamp(28px, 3.2vw, 48px);
          border-radius: 18px;
          background:
            radial-gradient(circle at 84% 14%, rgba(37,99,235,.08), transparent 30%),
            linear-gradient(180deg, #FFFFFF, #F8FAFC);
          border: 1px solid rgba(148,163,184,.16);
          box-shadow: inset 0 1px 0 rgba(255,255,255,.9);
        }
        .metric-card {
          min-width: 0;
          width: 100%;
          min-height: 172px;
          border-radius: 13px;
          background: #FFFFFF;
          border: 1px solid rgba(148,163,184,.18);
          box-shadow: 0 14px 32px rgba(15,23,42,.06);
          position: relative;
          overflow: hidden;
          padding: clamp(22px, 2vw, 30px);
          transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
        }
        .metric-card::after {
          content: "";
          position: absolute;
          inset: 0 auto 0 0;
          width: 5px;
          background: var(--accent, #2563EB);
        }
        .accent-blue { --accent: #2563EB; }
        .accent-amber { --accent: #F59E0B; }
        .accent-green { --accent: #10B981; }
        .accent-purple { --accent: #7C3AED; }
        .metric-card-clickable {
          cursor: pointer;
        }
        .metric-card-clickable:hover {
          transform: translateY(-2px);
          border-color: rgba(37,99,235,.28);
          box-shadow: 0 18px 40px rgba(15,23,42,.1);
        }
        .metric-card-clickable:focus-visible {
          outline: 3px solid rgba(37,99,235,.26);
          outline-offset: 3px;
        }
        .metric-title { color: var(--sp-muted); font-size: 13px; font-weight: 700; }
        .metric-value { color: var(--sp-ink); font-size: clamp(44px, 4vw, 64px); font-weight: 950; line-height: 1; margin-top: clamp(26px, 3vw, 46px); }
        .metric-caption { color: #94A3B8; font-size: 12px; margin-top: 8px; }
        .content-card {
          border-radius: 12px;
          background: #FFFFFF;
          border: 1px solid rgba(148,163,184,.18);
          box-shadow: 0 16px 38px rgba(15,23,42,.07);
        }
        .plan-card-full {
          width: 100%;
          min-height: 640px;
          padding: 34px 36px;
          border-radius: 16px;
          margin-top: 0;
        }
        .plan-head { margin-bottom: 12px; }
        .plan-date { color: #94A3B8; font-size: 12px; }
        .plan-list {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 14px;
        }
        .plan-item {
          position: relative;
          min-height: 138px;
          padding: 18px 18px 16px 16px;
          border-radius: 10px;
          background: #FFFFFF;
          border: 1px solid rgba(148,163,184,.18);
          box-shadow: 0 10px 22px rgba(15,23,42,.045);
          overflow: hidden;
          transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
        }
        .plan-item::before {
          content: "";
          position: absolute;
          inset: 0 auto 0 0;
          width: 4px;
          background: var(--plan-accent, #2563EB);
        }
        .plan-item:hover {
          transform: translateY(-1px);
          border-color: rgba(37,99,235,.26);
          box-shadow: 0 14px 28px rgba(15,23,42,.07);
        }
        .plan-tone-red { --plan-accent: #EF4444; }
        .plan-tone-orange { --plan-accent: #F59E0B; }
        .plan-tone-purple { --plan-accent: #7C3AED; }
        .plan-tone-green { --plan-accent: #10B981; }
        .plan-tone-blue { --plan-accent: #2563EB; }
        .plan-item-row { gap: 14px; }
        .plan-check {
          width: 32px;
          min-width: 32px;
          margin-top: 2px;
        }
        .plan-check .q-checkbox__bg {
          width: 26px;
          height: 26px;
          border-radius: 6px;
          border: 2px solid #94A3B8;
          background: #FFFFFF;
        }
        .plan-check .q-checkbox__inner--truthy .q-checkbox__bg {
          background: #2563EB;
          border-color: #2563EB;
        }
        .plan-item-copy { min-width: 0; flex: 1; }
        .plan-title-row { gap: 8px; flex-wrap: nowrap; }
        .plan-period {
          min-width: 48px;
          justify-content: center;
          border-radius: 999px;
          font-weight: 800;
          font-size: 13px;
        }
        .plan-item-title {
          color: #1E293B;
          font-size: 16px;
          font-weight: 850;
          line-height: 1.38;
        }
        .plan-item-detail {
          color: #64748B;
          font-size: 13px;
          line-height: 1.65;
        }
        .plan-item-meta {
          color: #5273C7;
          font-size: 13px;
          font-weight: 750;
        }
        .plan-support-grid {
          display: grid;
          grid-template-columns: minmax(300px, 1.05fr) minmax(260px, .9fr) minmax(240px, .75fr) minmax(300px, 1fr);
          gap: 14px;
          margin-top: 18px;
        }
        .plan-panel {
          min-width: 0;
          min-height: 218px;
          padding: 18px 18px 16px;
          border-radius: 10px;
          background: linear-gradient(180deg, #FFFFFF, #F8FAFC);
          border: 1px solid rgba(148,163,184,.18);
          box-shadow: 0 10px 24px rgba(15,23,42,.045);
        }
        .plan-panel-title {
          color: #0F172A;
          font-size: 15px;
          font-weight: 900;
          margin-bottom: 14px;
        }
        .plan-stat-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
        }
        .plan-stat {
          min-height: 76px;
          padding: 11px 12px;
          border-radius: 9px;
          background: #FFFFFF;
          border: 1px solid rgba(148,163,184,.16);
        }
        .plan-stat-label {
          color: #64748B;
          font-size: 12px;
          font-weight: 750;
        }
        .plan-stat-value {
          color: #0F172A;
          font-size: 24px;
          font-weight: 950;
          line-height: 1.15;
          margin-top: 6px;
        }
        .plan-stat-hint,
        .plan-empty-note {
          color: #94A3B8;
          font-size: 12px;
          line-height: 1.55;
          margin-top: 3px;
        }
        .plan-course-list {
          display: grid;
          gap: 12px;
        }
        .plan-course-head {
          gap: 10px;
          margin-bottom: 6px;
        }
        .plan-course-name {
          min-width: 0;
          color: #1E293B;
          font-size: 13px;
          font-weight: 850;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .plan-course-count {
          color: #64748B;
          font-size: 12px;
          font-weight: 750;
        }
        .plan-course-bar {
          height: 8px;
          border-radius: 999px;
          overflow: hidden;
          background: #E2E8F0;
        }
        .plan-course-fill {
          height: 100%;
          border-radius: inherit;
          background: linear-gradient(90deg, #2563EB, #10B981);
        }
        .plan-time-row {
          display: grid;
          grid-template-columns: 48px minmax(0, 1fr);
          gap: 12px;
          align-items: center;
          padding: 10px 0;
          border-bottom: 1px solid rgba(148,163,184,.16);
        }
        .plan-time-row:last-child { border-bottom: 0; }
        .plan-time-period {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          height: 30px;
          border-radius: 999px;
          color: #FFFFFF;
          font-size: 12px;
          font-weight: 850;
          background: var(--time-accent, #2563EB);
        }
        .plan-time-red { --time-accent: #EF4444; }
        .plan-time-orange { --time-accent: #F59E0B; }
        .plan-time-purple { --time-accent: #7C3AED; }
        .plan-time-duration {
          color: #0F172A;
          font-size: 15px;
          font-weight: 900;
        }
        .plan-time-note {
          color: #64748B;
          font-size: 12px;
          line-height: 1.45;
        }
        .plan-checkline {
          gap: 10px;
          padding: 8px 0;
          border-bottom: 1px solid rgba(148,163,184,.14);
        }
        .plan-checkline:last-child { border-bottom: 0; }
        .plan-check-index {
          width: 24px;
          height: 24px;
          min-width: 24px;
          display: grid;
          place-items: center;
          border-radius: 8px;
          color: #2563EB;
          background: #EFF6FF;
          font-size: 12px;
          font-weight: 900;
        }
        .plan-check-text {
          min-width: 0;
          color: #334155;
          font-size: 13px;
          line-height: 1.5;
          font-weight: 650;
        }
        .qa-main { min-height: 520px; }
        .qa-toolbar { margin-bottom: 8px; }
        .course-select { min-width: 220px; }
        .question-input .q-field__control { border-radius: 12px; background: #FFFFFF; }
        .answer-shell {
          min-height: 180px;
          padding: 16px;
          border-radius: 12px;
          background: linear-gradient(180deg, #FFFFFF, #F8FAFC);
          border: 1px solid rgba(148,163,184,.18);
        }
        .source-card {
          border-left: 4px solid #2563EB;
          border-radius: 12px;
          background: #FFFFFF;
          box-shadow: 0 10px 24px rgba(37,99,235,.08);
        }
        .source-title { font-weight: 800; color: #0F172A; }
        .source-score { color: #64748B; font-size: 12px; }
        .source-snippet { color: #475569; font-size: 13px; line-height: 1.7; }
        .source-list { margin-top: 12px; gap: 10px; }
        .upload-history-list { margin-top: 12px; gap: 10px; }
        .notebooklm-shell {
          --q-primary: #000000;
          --q-secondary: #FFFFFF;
          --nlm-bg:
            linear-gradient(180deg, rgba(255,255,255,0) 0%, rgba(255,255,255,.16) 72%, rgba(237,239,250,.76) 100%),
            url('https://www.fillout.com/_next/image?url=%2F_next%2Fstatic%2Fmedia%2Fhero.0c57f950.png&w=3840&q=75') center top / cover no-repeat,
            #EDEFFA;
          --nlm-panel: #FFFFFF;
          --nlm-border: #DDE1EB;
          --nlm-text: #202124;
          --nlm-muted: #5F6368;
          position: fixed;
          inset: 0;
          z-index: 3000;
          width: 100vw;
          height: 100vh;
          min-height: 720px;
          margin: 0;
          padding: 0;
          border-radius: 0;
          background: var(--nlm-bg);
          color: var(--nlm-text);
          overflow: hidden;
        }
        .notebooklm-shell .nlm-add-source-btn,
        .notebooklm-shell .q-btn.nlm-add-source-btn {
          background: #FFFFFF !important;
          background-color: #FFFFFF !important;
          color: #1B1B1C !important;
          border-color: #DDE1EB !important;
        }
        .notebooklm-shell .nlm-add-source-btn::before,
        .notebooklm-shell .nlm-add-source-btn .q-focus-helper {
          background: transparent !important;
          opacity: 0 !important;
        }
        .notebooklm-shell .nlm-add-source-btn * {
          color: #1B1B1C !important;
        }
        .notebooklm-shell::after {
          content: "NotebookLM 提供的内容未必准确，因此请仔细核查回答内容。";
          position: fixed;
          left: 0;
          right: 0;
          bottom: 2px;
          z-index: 1;
          color: #8A8D91;
          font-size: 14px;
          line-height: 18px;
          text-align: center;
          pointer-events: none;
        }
        .notebooklm-topbar {
          height: 64px;
          padding: 12px 24px 0;
          gap: 28px;
          box-sizing: content-box;
        }
        .notebooklm-logo {
          width: 46px;
          height: 46px;
          display: grid;
          place-items: center;
          border-radius: 999px;
          background: #000000;
          color: #FFFFFF;
          flex: 0 0 auto;
        }
        .notebooklm-logo .q-icon { font-size: 28px; }
        .notebooklm-title {
          color: #111827;
          font: 500 28px/1.2 "Google Sans Text", "Noto Sans SC", sans-serif;
          letter-spacing: 0;
        }
        .notebooklm-subtitle {
          color: #64748B;
          font-size: 12px;
          font-weight: 650;
        }
        .nlm-project-nav {
          height: 46px;
          min-width: 520px;
          max-width: 650px;
          flex: 1 1 520px;
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 4px;
          margin: 0 8px 0 10px;
          padding: 3px;
          border: 1px solid rgba(221,225,235,.9);
          border-radius: 999px;
          background: rgba(255,255,255,.78);
          backdrop-filter: blur(14px);
          overflow: hidden;
        }
        .nlm-project-nav-item {
          height: 38px;
          min-width: 76px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 6px;
          padding: 0 10px;
          border: 0;
          border-radius: 999px;
          background: transparent;
          color: #334155;
          cursor: pointer;
          font: 700 13px/1 "Google Sans Text", "Noto Sans SC", sans-serif;
          transition: background .16s ease, color .16s ease, box-shadow .16s ease;
        }
        .nlm-project-nav-item:hover {
          background: rgba(237,239,250,.9);
        }
        .nlm-project-nav-active {
          background: #2563EB;
          color: #FFFFFF;
          box-shadow: 0 8px 18px rgba(37,99,235,.2);
        }
        .nlm-project-nav-icon {
          font-size: 18px;
          color: inherit;
        }
        .nlm-project-nav-label {
          color: inherit;
          white-space: nowrap;
        }
        .nlm-create-btn {
          --q-primary: #000000;
          --q-secondary: #000000;
          --q-accent: #000000;
          height: 40px;
          padding: 0 22px !important;
          border-radius: 96px !important;
          background: #000000 !important;
          color: #FFFFFF !important;
          font: 800 14px "Google Sans Text", "Noto Sans SC", sans-serif;
          box-shadow: none !important;
        }
        .nlm-native-btn {
          appearance: none;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 8px;
          border: 0;
          cursor: pointer;
          font-family: "Google Sans Text", "Noto Sans SC", sans-serif;
          white-space: nowrap;
          letter-spacing: 0;
          user-select: none;
          transition: background .16s ease, color .16s ease, transform .16s ease, box-shadow .16s ease;
        }
        .nlm-native-btn:hover {
          background: #F6F8FC;
        }
        .nlm-native-btn:focus-visible,
        .nlm-project-nav-item:focus-visible,
        .nlm-studio-tile:focus-visible {
          outline: 2px solid #A8C7FA;
          outline-offset: 2px;
        }
        .nlm-native-btn .q-icon {
          color: inherit;
        }
        .nlm-native-btn .q-label,
        .nlm-native-btn div {
          color: inherit;
        }
        .notebooklm-shell .nlm-create-btn,
        .notebooklm-shell .nlm-add-note-btn,
        .notebooklm-shell .q-btn.nlm-create-btn,
        .notebooklm-shell .q-btn.nlm-add-note-btn {
          background: #000000 !important;
          background-color: #000000 !important;
          color: #FFFFFF !important;
          border-color: #000000 !important;
          box-shadow: none !important;
        }
        .notebooklm-shell .nlm-add-source-btn,
        .notebooklm-shell .q-btn.nlm-add-source-btn {
          background: #FFFFFF !important;
          background-color: #FFFFFF !important;
          color: #1B1B1C !important;
          border-color: #DDE1EB !important;
        }
        .notebooklm-shell .nlm-add-source-btn::before,
        .notebooklm-shell .nlm-add-source-btn .q-focus-helper {
          background: transparent !important;
          opacity: 0 !important;
        }
        .notebooklm-shell .nlm-add-source-btn * {
          color: #1B1B1C !important;
        }
        .notebooklm-shell .nlm-create-btn.q-btn,
        .notebooklm-shell .nlm-add-note-btn.q-btn {
          background: #000000 !important;
          background-color: #000000 !important;
        }
        .notebooklm-shell .nlm-create-btn .q-focus-helper,
        .notebooklm-shell .nlm-add-note-btn .q-focus-helper {
          background: transparent !important;
          opacity: 0 !important;
        }
        .notebooklm-shell .nlm-create-btn::before,
        .notebooklm-shell .nlm-add-note-btn::before {
          background: #000000 !important;
          opacity: 1 !important;
        }
        .notebooklm-shell .nlm-create-btn *,
        .notebooklm-shell .nlm-add-note-btn * {
          color: #FFFFFF !important;
        }
        .notebooklm-shell .nlm-top-btn,
        .notebooklm-shell .nlm-add-source-btn,
        .notebooklm-shell .nlm-action-btn,
        .notebooklm-shell .nlm-customize,
        .notebooklm-shell .q-btn.nlm-top-btn,
        .notebooklm-shell .q-btn.nlm-add-source-btn,
        .notebooklm-shell .q-btn.nlm-action-btn,
        .notebooklm-shell .q-btn.nlm-customize {
          background: #FFFFFF !important;
          background-color: #FFFFFF !important;
          color: #1B1B1C !important;
          box-shadow: none !important;
        }
        .notebooklm-shell .nlm-top-btn.q-btn,
        .notebooklm-shell .nlm-add-source-btn.q-btn,
        .notebooklm-shell .nlm-action-btn.q-btn,
        .notebooklm-shell .nlm-customize.q-btn {
          background: #FFFFFF !important;
          background-color: #FFFFFF !important;
          color: #1B1B1C !important;
          border: 1px solid #DDE1EB !important;
        }
        .notebooklm-shell .nlm-top-btn .q-focus-helper,
        .notebooklm-shell .nlm-add-source-btn .q-focus-helper,
        .notebooklm-shell .nlm-action-btn .q-focus-helper,
        .notebooklm-shell .nlm-customize .q-focus-helper {
          background: transparent !important;
          opacity: 0 !important;
        }
        .notebooklm-shell .nlm-top-btn::before,
        .notebooklm-shell .nlm-add-source-btn::before,
        .notebooklm-shell .nlm-action-btn::before,
        .notebooklm-shell .nlm-customize::before {
          background: transparent !important;
          opacity: 0 !important;
        }
        .nlm-top-btn {
          height: 40px;
          padding: 0 18px !important;
          border: 1px solid #DDE1EB !important;
          border-radius: 96px !important;
          background: transparent !important;
          color: #1B1B1C !important;
          font-weight: 800;
          box-shadow: none !important;
        }
        .notebooklm-panels {
          --nlm-left-width: 373px;
          --nlm-right-width: 373px;
          width: 100%;
          height: calc(100vh - 86px);
          min-height: 0;
          display: grid;
          grid-template-columns: var(--nlm-left-width) minmax(0, 1fr) var(--nlm-right-width);
          gap: 16px;
          padding: 0 16px 22px;
          overflow: hidden;
          transition: grid-template-columns .22s ease;
        }
        .notebooklm-shell.nlm-sources-collapsed .notebooklm-panels {
          --nlm-left-width: 72px;
        }
        .notebooklm-shell.nlm-studio-collapsed .notebooklm-panels {
          --nlm-right-width: 72px;
        }
        .notebooklm-shell.nlm-studio-wide:not(.nlm-studio-collapsed) .notebooklm-panels {
          --nlm-left-width: 300px;
          --nlm-right-width: min(660px, 54vw);
        }
        .notebooklm-shell:not(.nlm-sources-collapsed):not(.nlm-studio-collapsed) .notebooklm-panels:has(.nlm-sources-panel:hover) {
          --nlm-left-width: 405px;
          --nlm-right-width: 341px;
        }
        .notebooklm-shell:not(.nlm-sources-collapsed):not(.nlm-studio-collapsed) .notebooklm-panels:has(.nlm-studio-panel:hover) {
          --nlm-left-width: 341px;
          --nlm-right-width: 405px;
        }
        .notebooklm-shell.nlm-studio-wide:not(.nlm-sources-collapsed):not(.nlm-studio-collapsed) .notebooklm-panels:has(.nlm-sources-panel:hover),
        .notebooklm-shell.nlm-studio-wide:not(.nlm-sources-collapsed):not(.nlm-studio-collapsed) .notebooklm-panels:has(.nlm-studio-panel:hover) {
          --nlm-left-width: 300px;
          --nlm-right-width: min(660px, 54vw);
        }
        .notebooklm-shell.nlm-sources-collapsed.nlm-studio-collapsed .notebooklm-panels {
          --nlm-left-width: 72px;
          --nlm-right-width: 72px;
        }
        .nlm-panel {
          min-width: 0;
          height: 100%;
          display: flex;
          flex-direction: column;
          border-radius: 16px;
          background: var(--nlm-panel);
          border: 0;
          overflow: hidden;
          box-shadow: none;
        }
        .nlm-panel-head {
          min-height: 58px;
          padding: 0 20px;
          border-bottom: 1px solid var(--nlm-border);
          gap: 8px;
          flex: 0 0 auto;
        }
        .nlm-panel-title {
          color: #111827;
          font: 500 22px/1.2 "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-icon-btn {
          width: 36px;
          height: 36px;
          flex: 0 0 36px;
          padding: 0;
          border: 0;
          border-radius: 999px;
          background: transparent;
          color: #444746;
        }
        .nlm-icon-btn .q-icon {
          font-size: 20px;
        }
        .nlm-more-wrap {
          position: relative;
          width: 44px;
          height: 44px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          flex: 0 0 44px;
        }
        .nlm-more-btn {
          width: 44px;
          height: 44px;
          flex: 0 0 44px;
          background: transparent;
          color: #3C4043;
        }
        .nlm-more-btn:hover,
        .nlm-more-wrap:focus-within .nlm-more-btn {
          background: #F1F3F4;
        }
        .nlm-more-tooltip {
          position: absolute;
          top: 48px;
          right: -10px;
          z-index: 20;
          width: 278px;
          padding: 8px 10px;
          border-radius: 6px;
          background: #3C4043;
          color: #FFFFFF;
          font: 400 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
          box-shadow: 0 4px 12px rgba(60,64,67,.25);
          opacity: 0;
          pointer-events: none;
          transform: translateY(-4px);
          transition: opacity .12s ease, transform .12s ease;
        }
        .nlm-more-tooltip::after {
          content: "";
          position: absolute;
          right: 32px;
          top: -6px;
          width: 12px;
          height: 12px;
          background: #3C4043;
          transform: rotate(45deg);
        }
        .nlm-more-tooltip div {
          color: #FFFFFF !important;
        }
        .nlm-more-wrap:hover .nlm-more-tooltip {
          opacity: 1;
          transform: translateY(0);
        }
        .nlm-more-menu {
          position: absolute;
          top: 43px;
          right: 0;
          z-index: 30;
          width: 226px;
          padding: 18px 16px 16px;
          border: 1px solid rgba(218,220,224,.88);
          border-radius: 8px;
          background: #FFFFFF;
          box-shadow: 0 2px 8px rgba(60,64,67,.22), 0 1px 3px rgba(60,64,67,.1);
          opacity: 0;
          visibility: hidden;
          pointer-events: none;
          transform: translateY(-4px);
          transition: opacity .12s ease, transform .12s ease, visibility .12s ease;
        }
        .nlm-more-wrap:focus-within .nlm-more-menu {
          opacity: 1;
          visibility: visible;
          pointer-events: auto;
          transform: translateY(0);
        }
        .nlm-more-menu-item {
          color: #202124;
          font: 500 16px/24px "Google Sans Text", "Noto Sans SC", sans-serif;
          padding: 0 0 16px;
          white-space: nowrap;
        }
        .nlm-more-disabled {
          color: #AEB0B4;
        }
        .nlm-more-menu-note {
          color: #AEB0B4;
          font: 400 14px/22px "Google Sans Text", "Noto Sans SC", sans-serif;
          font-style: italic;
          white-space: nowrap;
        }
        .nlm-panel-toggle {
          width: 36px;
          height: 36px;
          flex: 0 0 36px;
          padding: 0;
          border: 0;
          border-radius: 999px;
          background: transparent;
          color: #303236;
        }
        .nlm-panel-toggle:hover {
          background: #F1F3F4;
        }
        .nlm-panel-toggle-icon {
          width: 18px;
          height: 18px;
          display: block;
          position: relative;
          border: 2px solid currentColor;
          border-radius: 2px;
        }
        .nlm-panel-toggle-icon::after {
          content: "";
          position: absolute;
          top: -2px;
          bottom: -2px;
          width: 2px;
          background: currentColor;
        }
        .nlm-panel-toggle-sources .nlm-panel-toggle-icon::after {
          right: 5px;
        }
        .nlm-panel-toggle-studio .nlm-panel-toggle-icon::after {
          left: 5px;
        }
        .nlm-sources-panel,
        .nlm-studio-panel,
        .nlm-sources-panel > *,
        .nlm-studio-panel > * {
          transition: opacity .14s ease, transform .18s ease;
        }
        .notebooklm-shell.nlm-sources-collapsed .nlm-sources-panel,
        .notebooklm-shell.nlm-studio-collapsed .nlm-studio-panel {
          border-radius: 16px;
        }
        .notebooklm-shell.nlm-sources-collapsed .nlm-sources-panel .nlm-panel-head,
        .notebooklm-shell.nlm-studio-collapsed .nlm-studio-panel .nlm-panel-head {
          min-height: 58px;
          justify-content: center;
          padding: 0;
        }
        .notebooklm-shell.nlm-sources-collapsed .nlm-sources-panel .nlm-panel-title,
        .notebooklm-shell.nlm-studio-collapsed .nlm-studio-panel .nlm-panel-title {
          display: none;
        }
        .notebooklm-shell.nlm-sources-collapsed .nlm-sources-panel .q-space,
        .notebooklm-shell.nlm-studio-collapsed .nlm-studio-panel .q-space {
          display: none;
        }
        .notebooklm-shell.nlm-sources-collapsed .nlm-sources-panel > :not(.nlm-panel-head):not(.nlm-rail),
        .notebooklm-shell.nlm-studio-collapsed .nlm-studio-panel > :not(.nlm-panel-head):not(.nlm-rail) {
          display: none !important;
          opacity: 0;
          pointer-events: none;
          transform: translateY(6px);
        }
        .nlm-rail {
          display: none;
          flex: 1 1 auto;
          width: 100%;
          min-height: 0;
          padding: 16px 0;
          border-top: 1px solid var(--nlm-border);
          overflow: hidden auto;
          scrollbar-width: none;
          align-items: center;
          gap: 12px;
        }
        .nlm-rail::-webkit-scrollbar {
          display: none;
        }
        .notebooklm-shell.nlm-sources-collapsed .nlm-sources-rail,
        .notebooklm-shell.nlm-studio-collapsed .nlm-studio-rail {
          display: flex !important;
          flex-direction: column;
          opacity: 1;
          pointer-events: auto;
          transform: none;
        }
        .notebooklm-shell .nlm-rail,
        .notebooklm-shell .nlm-rail * {
          opacity: 1 !important;
          color: inherit;
        }
        .nlm-rail-action,
        .nlm-rail-source,
        .nlm-rail-studio {
          width: 40px;
          height: 40px;
          min-height: 40px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border: 0;
          border-radius: 10px;
          background: #FFFFFF;
          color: #1F2937;
          cursor: pointer;
          font-family: "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-rail-action:hover,
        .nlm-rail-source:hover,
        .nlm-rail-studio:hover {
          background: #F1F3F4;
        }
        .nlm-rail-action {
          font-size: 28px;
          line-height: 1;
        }
        .nlm-rail-source {
          font-weight: 800;
          font-size: 19px;
        }
        .nlm-rail-studio {
          color: var(--tile-color);
          background: var(--tile-bg);
          font-size: 22px;
        }
        .nlm-rail-black { background: #000000; color: #FFFFFF; border-radius: 999px; }
        .nlm-rail-red { background: #FF5138; color: #FFFFFF; }
        .nlm-rail-cyan { color: #00A9F4; background: #FFFFFF; font-size: 28px; }
        .nlm-rail-gray { color: #3C4043; background: #FFFFFF; }
        .nlm-rail-blue { color: #2563EB; background: #FFFFFF; }
        .nlm-rail-multi {
          color: transparent;
          background:
            linear-gradient(90deg, #F1511B 0 50%, #80CC28 50% 100%) top / 100% 50% no-repeat,
            linear-gradient(90deg, #00ADEF 0 50%, #FBBC09 50% 100%) bottom / 100% 50% no-repeat;
        }
        .nlm-rail-glyph {
          line-height: 1;
          letter-spacing: 0;
        }
        .nlm-sources-panel,
        .nlm-studio-panel { width: 100%; }
        .nlm-add-source-btn {
          height: 40px;
          margin: 20px 20px 0 !important;
          border: 1px solid var(--nlm-border) !important;
          border-radius: 96px !important;
          background: #FFFFFF;
          background-color: #FFFFFF;
          color: #1B1B1C;
          font-weight: 800;
          box-shadow: none !important;
        }
        .notebooklm-shell .nlm-add-source-btn *,
        .notebooklm-shell .nlm-top-btn *,
        .notebooklm-shell .nlm-action-btn *,
        .notebooklm-shell .nlm-customize * {
          color: #1B1B1C !important;
        }
        .nlm-search-card {
          margin: 20px 20px 0;
          padding: 18px 10px 12px;
          border: 1px solid var(--nlm-border);
          border-radius: 16px;
          background: #F8Fafd;
        }
        .nlm-search-label {
          margin: 0 0 8px 6px;
          color: #5F6368;
          font-size: 14px;
          font-weight: 650;
        }
        .nlm-search-input {
          width: 100%;
          margin: 0 0 8px 2px;
        }
        .nlm-search-input .q-field__control {
          min-height: 34px;
          color: #202124;
        }
        .nlm-search-input .q-field__label {
          color: #5F6368;
          font-size: 16px;
        }
        .nlm-chip {
          height: 32px;
          border: 1px solid var(--nlm-border);
          border-radius: 96px;
          background: #FFFFFF;
          color: #1B1B1C;
          font-weight: 750;
        }
        .nlm-search-btn {
          width: 40px;
          height: 40px;
          flex-basis: 40px;
          background: rgba(0,0,0,.08);
          color: #5E5E5E;
        }
        .nlm-source-summary {
          padding: 16px 20px 0;
          gap: 8px;
        }
        .nlm-small-icon,
        .nlm-source-type {
          color: #2563EB;
          font-size: 22px;
        }
        .nlm-muted {
          color: #5F6368;
          font-size: 13px;
          font-weight: 650;
        }
        .nlm-check .q-checkbox__bg {
          border-radius: 4px;
          border-color: #CBD5E1;
        }
        .nlm-source-list {
          flex: 1;
          min-height: 0;
          gap: 8px;
          padding: 14px 20px 8px;
          overflow: auto;
          scrollbar-width: thin;
          scrollbar-color: #C7CAD7 transparent;
        }
        .nlm-source-item {
          min-height: 48px;
          gap: 10px;
          padding: 7px 8px 7px 0;
          border-radius: 10px;
          transition: background .14s ease;
        }
        .nlm-source-item:hover {
          background: #F8FAFD;
        }
        .nlm-source-copy { flex: 1; min-width: 0; }
        .nlm-source-title,
        .nlm-upload-title {
          color: #111827;
          font-size: 14px;
          font-weight: 800;
          line-height: 1.35;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .nlm-source-meta {
          color: #64748B;
          font-size: 12px;
          font-weight: 650;
        }
        .nlm-source-more { padding: 4px 0 8px; }
        .nlm-source-empty {
          height: 100%;
          align-items: center;
          justify-content: center;
          text-align: center;
          gap: 8px;
          padding: 16px;
        }
        .nlm-empty-icon {
          color: #747775;
          font-size: 30px;
        }
        .nlm-empty-title {
          color: #303030;
          font-weight: 800;
          font-size: 14px;
        }
        .nlm-empty-copy {
          max-width: 260px;
          color: #747775;
          font-size: 13px;
          line-height: 1.65;
        }
        .nlm-upload-list {
          flex: 0 0 auto;
          gap: 8px;
          padding: 0 18px 16px;
        }
        .nlm-mini-title {
          color: #334155;
          font-size: 13px;
          font-weight: 900;
        }
        .nlm-upload-record {
          padding: 9px 10px;
          border-radius: 12px;
          border: 1px solid #E2E8F0;
          background: #F8FAFC;
        }
        .nlm-chat-panel { position: relative; }
        .nlm-chat-scroll {
          flex: 1;
          min-height: 0;
          overflow: auto;
          padding: 24px 50px 20px;
          scrollbar-width: thin;
          scrollbar-color: #C7CAD7 transparent;
        }
        .nlm-customize {
          height: 32px;
          padding: 0 16px;
          border: 1px solid var(--nlm-border);
          border-radius: 32px;
          background: #FFFFFF;
          color: #1B1B1C;
          font-weight: 800;
        }
        .nlm-answer-wrap {
          max-width: 800px;
          margin: 0 auto;
          gap: 12px;
        }
        .nlm-answer-mark {
          color: #8B5E83;
          font-size: 42px;
          margin-bottom: 22px;
        }
        .nlm-answer-title {
          color: #111827;
          font: 500 36px/1.28 "Google Sans Text", "Noto Sans SC", sans-serif;
          letter-spacing: 0;
        }
        .nlm-answer-meta {
          color: #111827;
          font-size: 15px;
          font-weight: 650;
        }
        .nlm-answer-body {
          margin-top: 58px;
          color: #1F2937;
          font-size: 17px;
          line-height: 1.85;
        }
        .nlm-answer-body .answer-box {
          color: #1F2937;
          line-height: 1.85;
          font-size: 16px;
        }
        .nlm-answer-actions {
          gap: 12px;
          margin-top: 18px;
        }
        .nlm-action-btn {
          min-height: 40px;
          padding: 0 18px;
          border: 1px solid var(--nlm-border);
          border-radius: 96px;
          background: #FFFFFF;
          color: #334155;
          font-weight: 750;
        }
        .nlm-citation-list {
          width: 100%;
          gap: 10px;
          margin-top: 18px;
        }
        .nlm-citation-head { gap: 8px; margin-top: 8px; }
        .nlm-search-trace {
          display: grid;
          grid-template-columns: 28px minmax(0, 1fr);
          gap: 10px;
          align-items: start;
          padding: 12px 14px;
          border: 1px solid #E0E3EA;
          border-radius: 14px;
          background: #F8FAFD;
        }
        .nlm-search-trace-icon {
          color: #4664F2;
          font-size: 22px;
        }
        .nlm-search-trace-title {
          color: #202124;
          font: 500 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-search-trace-copy {
          color: #5F6368;
          font: 400 12px/18px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-note-miss {
          display: grid;
          grid-template-columns: 30px minmax(0, 1fr);
          gap: 10px;
          padding: 14px;
          border: 1px solid #F0D5D5;
          border-radius: 14px;
          background: #FFF8F7;
        }
        .nlm-note-miss-icon {
          grid-row: span 2;
          color: #A43B32;
          font-size: 22px;
        }
        .nlm-note-miss-title {
          color: #202124;
          font: 500 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-note-miss-copy {
          color: #5F6368;
          font: 400 12px/18px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-prompt-bar {
          min-height: 92px;
          margin: 0 20px 20px;
          padding: 0 12px 0 22px;
          border: 1px solid var(--nlm-border);
          border-radius: 20px;
          background: #FFFFFF;
          flex: 0 0 auto;
          gap: 10px;
        }
        .nlm-prompt-input {
          flex: 1;
          min-width: 0;
          font-size: 18px;
        }
        .nlm-prompt-input .q-field__control {
          min-height: 42px;
          background: transparent;
        }
        .nlm-prompt-count {
          color: #5F6368;
          font-size: 14px;
          white-space: nowrap;
        }
        .nlm-send-btn {
          width: 40px;
          height: 40px;
          flex-basis: 40px;
          background: rgba(0,0,0,.08);
          color: #3C4043;
        }
        .nlm-send-btn:hover,
        .nlm-search-btn:hover {
          background: rgba(0,0,0,.12);
        }
        .nlm-studio-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 8px;
          padding: 20px 14px 20px 20px;
        }
        .nlm-studio-tile {
          height: 56px;
          min-width: 0;
          display: grid;
          grid-template-columns: 24px minmax(0, 1fr) 32px;
          align-items: center;
          gap: 6px;
          padding: 0 8px 0 12px;
          border: 0;
          border-radius: 12px;
          color: var(--tile-color);
          background: var(--tile-bg);
          cursor: pointer;
          text-align: left;
          font: 800 12px/16px "Google Sans Text", "Noto Sans SC", sans-serif;
          transition: transform .16s ease, filter .16s ease, box-shadow .16s ease;
        }
        .nlm-studio-tile:hover {
          transform: translateY(-1px);
          filter: brightness(1.015);
          box-shadow: 0 8px 18px rgba(15,23,42,.08);
        }
        .nlm-tile-blue { --tile-bg: #EDEFFA; --tile-color: #224484; }
        .nlm-tile-green { --tile-bg: #E1F1E5; --tile-color: #0F5223; }
        .nlm-tile-yellow { --tile-bg: #F2F2E8; --tile-color: #796731; }
        .nlm-tile-pink { --tile-bg: #F0E9EF; --tile-color: #802272; }
        .nlm-tile-orange { --tile-bg: #F7EDEB; --tile-color: #8C2E2A; }
        .nlm-tile-cyan { --tile-bg: #DEF1F7; --tile-color: #056A95; }
        .nlm-tile-icon {
          width: 20px;
          height: 20px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          overflow: hidden;
          color: var(--tile-color);
          font: 700 18px/20px "Google Sans Text", Arial, sans-serif;
          letter-spacing: 0;
          white-space: nowrap;
        }
        .nlm-tile-title {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .nlm-tile-arrow {
          width: 32px;
          height: 32px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border-radius: 999px;
          background: rgba(0,0,0,.05);
          color: inherit;
          font: 400 26px/1 "Google Sans Text", Arial, sans-serif;
        }
        .nlm-studio-sep { margin: 0 14px; }
        .nlm-studio-hidden {
          display: none !important;
        }
        .nlm-studio-empty {
          flex: 1;
          width: 100%;
          align-items: stretch;
          justify-content: flex-start;
          text-align: left;
          padding: 14px 12px 14px;
          gap: 8px;
          overflow: hidden;
        }
        .nlm-studio-spark {
          color: #4259FF;
          font-size: 38px;
          align-self: center;
        }
        .nlm-studio-empty-title {
          color: #4259FF;
          font-size: 16px;
          font-weight: 850;
          text-align: center;
        }
        .nlm-studio-empty-copy {
          color: #111827;
          font-size: 14px;
          line-height: 1.6;
          text-align: center;
          max-width: 280px;
          align-self: center;
        }
        .nlm-studio-notice {
          display: grid;
          grid-template-columns: 22px minmax(0, 1fr);
          align-items: start;
          gap: 8px;
          margin: 0 4px 12px;
          padding: 12px 14px;
          border-radius: 12px;
          background: linear-gradient(105deg, #F8EAF6 0%, #E8F8E8 100%);
          color: #202124;
          font: 400 13px/18px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-studio-notice-icon {
          color: #D04688;
          font-size: 18px;
        }
        .nlm-studio-output-list {
          display: flex;
          flex-direction: column;
          gap: 2px;
          overflow: auto;
          padding: 4px 0 10px;
        }
        .nlm-studio-output-item {
          width: 100%;
          min-height: 58px;
          display: grid;
          grid-template-columns: 26px minmax(0, 1fr) auto 24px;
          align-items: center;
          gap: 10px;
          padding: 8px 8px 8px 14px;
          border: 0;
          border-radius: 14px;
          background: transparent;
          color: #202124;
          cursor: pointer;
          text-align: left;
          font-family: "Google Sans Text", "Noto Sans SC", sans-serif;
          transition: background .14s ease, transform .14s ease;
        }
        .nlm-studio-output-item:hover {
          background: #F7F8FC;
        }
        .nlm-studio-output-icon {
          color: var(--output-color);
          font-size: 22px;
        }
        .nlm-output-blue { --output-color: #3F5FDB; }
        .nlm-output-green { --output-color: #188038; }
        .nlm-output-yellow { --output-color: #837126; }
        .nlm-output-pink { --output-color: #9C2F83; }
        .nlm-output-orange { --output-color: #B14B28; }
        .nlm-output-cyan { --output-color: #007E9E; }
        .nlm-studio-output-main {
          min-width: 0;
          display: flex;
          flex-direction: column;
          gap: 1px;
        }
        .nlm-studio-output-name {
          color: #202124;
          font: 500 14px/19px "Google Sans Text", "Noto Sans SC", sans-serif;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .nlm-studio-output-meta {
          color: #5F6368;
          font: 400 12px/16px "Google Sans Text", "Noto Sans SC", sans-serif;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .nlm-studio-output-play {
          width: 34px;
          height: 34px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border-radius: 999px;
          background: #EEF2FF;
          color: #4664F2;
          font-size: 22px;
        }
        .nlm-studio-output-kebab {
          color: #5F6368;
          font-size: 20px;
        }
        .nlm-app-viewer {
          height: 100%;
          min-height: 0;
          display: flex;
          flex-direction: column;
          margin: -14px -12px;
          background: #FFFFFF;
        }
        .nlm-app-crumb {
          min-height: 48px;
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 0 16px;
          border-bottom: 1px solid #E8EAED;
          color: #202124;
          font: 400 15px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-app-back {
          border: 0;
          background: transparent;
          color: #202124;
          cursor: pointer;
          padding: 0;
          font: inherit;
        }
        .nlm-app-back:hover { text-decoration: underline; }
        .nlm-app-chev { color: #5F6368; font-size: 22px; }
        .nlm-app-kind { color: #3C4043; }
        .nlm-app-expand {
          color: #5F6368;
          font-size: 20px;
        }
        .nlm-app-expand-btn {
          width: 36px;
          height: 36px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border: 0;
          border-radius: 999px;
          background: transparent;
          color: #5F6368;
          cursor: pointer;
          transition: background .16s ease, color .16s ease, transform .16s ease;
        }
        .nlm-app-expand-btn:hover {
          background: #F1F3F8;
          color: #202124;
          transform: translateY(-1px);
        }
        .nlm-app-titlebar {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 20px 18px 8px;
        }
        .nlm-app-title {
          min-width: 0;
          color: #202124;
          font: 400 24px/30px "Google Sans", "Noto Sans SC", sans-serif;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .nlm-app-action-icon {
          color: #3C4043;
          font-size: 22px;
        }
        .nlm-app-action-btn {
          width: 36px;
          height: 36px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          border: 0;
          border-radius: 999px;
          background: transparent;
          color: #3C4043;
          cursor: pointer;
          transition: background .16s ease, transform .16s ease;
        }
        .nlm-app-action-btn:hover {
          background: #F1F3F8;
          transform: translateY(-1px);
        }
        .nlm-source-chip {
          width: fit-content;
          height: 34px;
          margin: 0 18px 16px;
          padding: 0 18px;
          border: 1px solid #E0E3EA;
          border-radius: 999px;
          background: #FFFFFF;
          color: #202124;
          font: 400 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
          cursor: pointer;
        }
        .nlm-source-chip:hover {
          background: #F8FAFD;
        }
        .nlm-app-body {
          flex: 1;
          min-height: 0;
          overflow: auto;
          padding: 0 18px 22px;
          border-top: 1px solid #EEF0F5;
        }
        .nlm-app-feedback {
          display: flex;
          gap: 14px;
          padding: 14px 18px;
          border-top: 1px solid #E8EAED;
          background: #FFFFFF;
        }
        .nlm-expandable-viewer.nlm-expanded {
          position: relative;
          z-index: 1;
          width: 100%;
          height: 100%;
          min-height: 0;
          border-radius: 0;
          overflow: hidden;
        }
        .nlm-expandable-viewer.nlm-expanded::before {
          content: none;
        }
        .nlm-expandable-viewer.nlm-expanded .nlm-app-body {
          padding: 0 22px 22px;
          overflow: auto;
        }
        .nlm-mindmap-viewer.nlm-expanded .nlm-app-body {
          padding: 0 14px 16px;
          overflow: auto;
        }
        .nlm-expandable-viewer.nlm-expanded .nlm-app-feedback {
          display: none;
        }
        .nlm-expandable-viewer.nlm-expanded .nlm-source-chip {
          margin-bottom: 10px;
        }
        .nlm-expandable-viewer.nlm-expanded .nlm-app-titlebar {
          padding: 16px 22px 10px;
        }
        .nlm-expandable-viewer.nlm-expanded .nlm-app-crumb {
          min-height: 52px;
          padding: 0 22px;
          font-size: 16px;
        }
        .nlm-expandable-viewer.nlm-expanded .nlm-source-chip {
          margin-left: 22px;
        }
        .nlm-expandable-viewer.nlm-expanded .nlm-app-title {
          font-size: 28px;
          line-height: 34px;
        }
        .nlm-expandable-viewer.nlm-expanded .nlm-app-expand-btn {
          background: #F1F3F8;
          color: #202124;
        }
        .nlm-feedback-btn {
          height: 40px;
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 0 16px;
          border: 1px solid #E0E3EA;
          border-radius: 999px;
          background: #FFFFFF;
          color: #202124;
          cursor: pointer;
          font: 500 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-add-note-btn {
          height: 40px;
          margin-top: 16px;
          padding: 0 24px;
          border-radius: 96px;
          background: #000000;
          color: #FFFFFF;
          font-weight: 850;
        }
        .nlm-demo-source-btn {
          height: 38px;
          margin-top: 8px;
          padding: 0 18px;
          border: 1px solid #DDE1EB;
          border-radius: 96px;
          background: #FFFFFF;
          color: #1B1B1C;
          font-weight: 650;
        }
        .nlm-studio-loading {
          min-height: 280px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 10px;
          padding: 24px;
          text-align: center;
        }
        .nlm-studio-loading-title {
          color: #202124;
          font: 600 16px/22px "Google Sans", "Noto Sans SC", sans-serif;
        }
        .nlm-studio-loading-copy {
          max-width: 260px;
          color: #5F6368;
          font: 400 13px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-media-card {
          margin-top: 16px;
        }
        .nlm-media-slide {
          height: 255px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 12px;
          border: 1px solid #E0E3EA;
          border-radius: 14px;
          background:
            linear-gradient(90deg, rgba(0,0,0,.04) 1px, transparent 1px),
            linear-gradient(0deg, rgba(0,0,0,.04) 1px, transparent 1px),
            #FFFFFF;
          background-size: 24px 24px;
          color: #121212;
          overflow: hidden;
        }
        .nlm-media-brand {
          font: 500 11px/14px "Google Sans Text", sans-serif;
          color: #5F6368;
        }
        .nlm-media-slide strong {
          max-width: 80%;
          text-align: center;
          font: 600 34px/40px "Google Sans", "Noto Sans SC", sans-serif;
        }
        .nlm-media-slide small {
          color: #5F6368;
          font: 400 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-media-time {
          display: grid;
          grid-template-columns: auto 1fr auto;
          align-items: center;
          gap: 10px;
          margin-top: 16px;
          color: #202124;
          font: 400 13px/18px "Google Sans Text", sans-serif;
        }
        .nlm-media-track {
          height: 4px;
          border-radius: 999px;
          background: #E5E7EF;
          position: relative;
          overflow: visible;
        }
        .nlm-media-fill {
          position: absolute;
          left: 0;
          top: 0;
          width: var(--media-progress, 0%);
          height: 100%;
          border-radius: 999px;
          background: #4664F2;
          transition: width .18s linear;
        }
        .nlm-media-knob {
          position: absolute;
          left: var(--media-progress, 0%);
          top: -7px;
          width: 18px;
          height: 18px;
          border-radius: 50%;
          background: #4664F2;
          transform: translateX(-50%);
          box-shadow: 0 2px 8px rgba(70,100,242,.26);
          transition: left .18s linear;
        }
        .nlm-media-controls {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 20px;
          margin-top: 28px;
        }
        .nlm-media-controls button {
          border: 0;
          background: transparent;
          color: #4664F2;
          font: 700 14px/18px "Google Sans Text", sans-serif;
        }
        .nlm-media-controls .nlm-media-play {
          width: 56px;
          height: 56px;
          border-radius: 50%;
          background: #4664F2;
          color: #FFFFFF;
          font-size: 25px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          box-shadow: 0 10px 24px rgba(70,100,242,.24);
          transition: transform .16s ease, background .16s ease, box-shadow .16s ease;
        }
        .nlm-media-controls .nlm-media-play:hover {
          transform: translateY(-1px);
          box-shadow: 0 14px 28px rgba(70,100,242,.3);
        }
        .nlm-media-controls .nlm-media-play.is-playing {
          background: #1F2937;
          box-shadow: 0 14px 28px rgba(15,23,42,.28);
        }
        .nlm-media-play-icon {
          font-size: 30px;
          line-height: 1;
        }
        .nlm-map-stage {
          min-height: 520px;
          position: relative;
          margin-top: 18px;
          display: grid;
          grid-template-columns: 220px minmax(0, 1fr);
          grid-template-rows: auto minmax(0, 1fr);
          gap: 14px;
          padding: 16px;
          border-radius: 14px;
          overflow: auto;
          border: 1px solid #E6E0EA;
          background:
            radial-gradient(circle at 18% 12%, rgba(156,47,131,.10), transparent 30%),
            linear-gradient(90deg, rgba(103,116,150,.06) 1px, transparent 1px),
            linear-gradient(0deg, rgba(103,116,150,.06) 1px, transparent 1px),
          #FFFFFF;
          background-size: auto, 28px 28px, 28px 28px, auto;
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-stage {
          height: auto;
          min-height: 570px;
          grid-template-columns: 190px minmax(520px, 1fr);
          gap: 14px;
          padding: 16px;
          margin-top: 12px;
        }
        .nlm-map-head {
          grid-column: 1 / -1;
          display: flex;
          align-items: baseline;
          gap: 10px;
          min-width: 0;
          padding: 4px 58px 4px 2px;
        }
        .nlm-map-head span {
          color: #9C2F83;
          font: 700 12px/16px "Google Sans Text", sans-serif;
          text-transform: uppercase;
        }
        .nlm-map-head strong {
          min-width: 0;
          color: #202124;
          font: 600 20px/26px "Google Sans", "Noto Sans SC", sans-serif;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .nlm-map-head small {
          color: #6B7280;
          font: 400 12px/16px "Google Sans Text", "Noto Sans SC", sans-serif;
          white-space: nowrap;
        }
        .nlm-map-modules {
          display: flex;
          flex-direction: column;
          gap: 8px;
          min-width: 0;
          padding: 2px;
        }
        .nlm-map-module {
          width: 100%;
          display: flex;
          flex-direction: column;
          gap: 2px;
          padding: 11px 12px;
          border: 1px solid rgba(156,47,131,.12);
          border-radius: 12px;
          background: rgba(255,255,255,.78);
          color: #202124;
          text-align: left;
          cursor: pointer;
          box-shadow: 0 8px 20px rgba(31,41,55,.04);
          transition: background .16s ease, border-color .16s ease, transform .16s ease;
        }
        .nlm-map-module:hover {
          transform: translateY(-1px);
          background: #FFF9FD;
          border-color: rgba(156,47,131,.22);
        }
        .nlm-map-module.is-active {
          background: #F0E9EF;
          border-color: rgba(156,47,131,.30);
        }
        .nlm-map-module span {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font: 600 13px/18px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-map-module small {
          color: #6B7280;
          font: 400 11px/15px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-map-canvas {
          min-width: 520px;
          min-height: 430px;
          position: relative;
          border-radius: 14px;
          background: rgba(255,255,255,.72);
          border: 1px solid rgba(226,232,240,.90);
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-canvas {
          min-width: 520px;
          min-height: 500px;
          height: 100%;
        }
        .nlm-map-tools {
          position: absolute;
          right: 18px;
          top: 16px;
          z-index: 2;
          display: flex;
          flex-direction: row;
          border: 1px solid #E0E3EA;
          border-radius: 18px;
          overflow: hidden;
          background: #FFFFFF;
          box-shadow: 0 8px 18px rgba(31,41,55,.08);
        }
        .nlm-map-tools button {
          width: 34px;
          height: 32px;
          border: 0;
          border-right: 1px solid #E0E3EA;
          background: #FFFFFF;
          color: #3C4043;
          font: 500 15px/1 "Google Sans Text", sans-serif;
        }
        .nlm-map-tools button:last-child { border-right: 0; }
        .nlm-map-view {
          position: relative;
          min-height: 430px;
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-view {
          min-height: 500px;
          height: 100%;
        }
        .nlm-map-view[hidden] {
          display: none;
        }
        .nlm-map-root {
          position: absolute;
          left: 22px;
          top: 166px;
          width: 176px;
          min-height: 74px;
          display: flex;
          flex-direction: column;
          justify-content: center;
          gap: 4px;
          padding: 12px 14px;
          border-radius: 12px;
          background: #E9DCF0;
          color: #202124;
          box-shadow: 0 12px 24px rgba(156,47,131,.12);
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-root {
          left: 24px;
          top: 196px;
          width: 170px;
          min-height: 82px;
          padding: 13px 15px;
        }
        .nlm-map-root small {
          color: #7C3A6D;
          font: 600 11px/14px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-map-root strong {
          font: 600 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-map-root::after {
          content: "";
          position: absolute;
          right: -78px;
          top: 50%;
          width: 78px;
          height: 2px;
          background: #CFA7C8;
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-root::after {
          right: -84px;
          width: 84px;
        }
        .nlm-map-branches {
          position: absolute;
          left: 278px;
          top: 46px;
          right: 22px;
          display: grid;
          grid-template-columns: repeat(2, minmax(180px, 1fr));
          gap: 12px;
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-branches {
          left: 290px;
          top: 48px;
          right: 24px;
          grid-template-columns: repeat(2, minmax(190px, 1fr));
          gap: 12px;
        }
        .nlm-map-node {
          position: relative;
          min-height: 94px;
          padding: 12px 14px;
          border: 1px solid #D9E3F5;
          border-radius: 12px;
          background: #F3F8FF;
          color: #202124;
          box-shadow: 0 8px 18px rgba(31,73,125,.06);
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-node {
          min-height: 106px;
          padding: 13px 15px;
        }
        .nlm-map-node::before {
          content: "";
          position: absolute;
          left: -44px;
          top: 50%;
          width: 44px;
          height: 2px;
          background: #C6D8F2;
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-node::before {
          left: -52px;
          width: 52px;
        }
        .nlm-map-node strong {
          display: block;
          color: #1F2937;
          font: 600 13px/18px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-node strong {
          font-size: 14px;
          line-height: 20px;
        }
        .nlm-map-node ul {
          margin: 7px 0 0;
          padding-left: 16px;
          color: #4B5563;
          font: 400 12px/17px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-node ul {
          font-size: 12px;
          line-height: 18px;
        }
        .nlm-map-review {
          position: absolute;
          left: 278px;
          right: 22px;
          bottom: 22px;
          padding: 12px 14px;
          border: 1px solid #E6D7E5;
          border-radius: 12px;
          background: #FFF8FD;
        }
        .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-review {
          left: 290px;
          right: 24px;
          bottom: 24px;
          padding: 13px 15px;
        }
        .nlm-map-review span {
          color: #9C2F83;
          font: 700 12px/16px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-map-review ol {
          margin: 6px 0 0;
          padding-left: 18px;
          color: #4B5563;
          font: 400 12px/18px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-report-preview {
          padding-top: 22px;
          color: #202124;
          font: 400 16px/26px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-expanded .nlm-report-preview {
          max-width: 980px;
          margin: 0 auto;
          padding-top: 28px;
          font-size: 19px;
          line-height: 1.85;
        }
        .nlm-report-preview h3,
        .nlm-report-preview h4 {
          font-family: "Google Sans", "Noto Sans SC", sans-serif;
          color: #202124;
        }
        .nlm-expanded .nlm-report-preview h3 {
          font-size: 30px;
          line-height: 1.3;
          margin: 16px 0 18px;
        }
        .nlm-expanded .nlm-report-preview h4 {
          font-size: 24px;
          line-height: 1.38;
          margin: 24px 0 12px;
        }
        .nlm-expanded .nlm-report-preview li {
          margin: 8px 0;
        }
        .nlm-quiz-preview {
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding-top: 18px;
        }
        .nlm-quiz-question {
          border: 1px solid #E0E3EA;
          border-radius: 14px;
          background: #FFFFFF;
          padding: 16px;
        }
        .nlm-quiz-kicker {
          color: #5F6368;
          font: 500 12px/16px "Google Sans Text", sans-serif;
        }
        .nlm-quiz-title {
          color: #202124;
          font: 500 18px/24px "Google Sans", "Noto Sans SC", sans-serif;
        }
        .nlm-flash-source-view {
          width: 100%;
          min-height: 0;
          height: 100%;
          background: #FFFFFF;
          display: flex;
          flex-direction: column;
          overflow: hidden;
        }
        .nlm-flash-source-view.nlm-expanded {
          position: relative;
          z-index: 1;
          border-radius: 0;
          height: 100%;
          min-height: 0;
        }
        .nlm-flash-source-head {
          padding: 18px 20px 8px;
          flex: 0 0 auto;
        }
        .nlm-flash-source-title {
          color: #202124;
          font: 500 24px/32px "Google Sans", "Noto Sans SC", sans-serif;
        }
        .nlm-flash-source-copy {
          margin-top: 4px;
          color: #6B7280;
          font: 400 13px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-flash-source-list {
          display: flex;
          flex-direction: column;
          gap: 14px;
          padding: 14px 20px 28px;
          flex: 1 1 auto;
          min-height: 0;
          overflow: auto;
          scrollbar-width: thin;
          scrollbar-color: #C7CAD7 transparent;
        }
        .nlm-flash-source-item {
          width: 100%;
          min-height: 96px;
          flex: 0 0 auto;
          display: grid;
          grid-template-columns: 38px minmax(0, 1fr) 18px;
          align-items: start;
          gap: 12px;
          padding: 14px 14px 13px;
          border: 1px solid #E1E5EF;
          border-radius: 16px;
          background: #FFFFFF;
          box-sizing: border-box;
          text-align: left;
          cursor: pointer;
          transition: transform .16s ease, border-color .16s ease, box-shadow .16s ease;
          overflow: hidden;
        }
        .nlm-flash-source-item:hover {
          transform: translateY(-1px);
          border-color: #D9B4A7;
          box-shadow: 0 14px 28px rgba(111,55,38,.08);
        }
        .nlm-flash-source-icon {
          width: 34px;
          height: 34px;
          display: grid;
          place-items: center;
          border-radius: 10px;
          background: #F7EDEB;
          color: #A23B2A;
          margin-top: 2px;
        }
        .nlm-flash-source-main {
          min-width: 0;
          display: flex;
          flex-direction: column;
          gap: 6px;
          padding-top: 0;
        }
        .nlm-flash-source-name {
          color: #202124;
          font: 600 13px/17px "Google Sans Text", "Noto Sans SC", sans-serif;
          overflow-wrap: anywhere;
          word-break: break-word;
          display: -webkit-box;
          -webkit-line-clamp: 2;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }
        .nlm-flash-source-meta {
          color: #6B7280;
          font: 400 11px/15px "Google Sans Text", "Noto Sans SC", sans-serif;
          overflow-wrap: anywhere;
          word-break: break-word;
          display: -webkit-box;
          -webkit-line-clamp: 3;
          -webkit-box-orient: vertical;
          overflow: hidden;
        }
        .nlm-flash-source-arrow {
          color: #A23B2A;
          align-self: center;
          margin-top: 1px;
        }
        .nlm-flash-viewer {
          width: 100%;
          min-height: 0;
          height: 100%;
          padding-top: 6px;
          display: flex;
          flex-direction: column;
          background:
            radial-gradient(circle at 50% 86%, rgba(218,245,237,.62), transparent 30%),
            #FFFFFF;
        }
        .nlm-flash-hint {
          text-align: center;
          color: #7A808A;
          font: 400 12px/18px "Google Sans Text", "Noto Sans SC", sans-serif;
          margin-bottom: 6px;
        }
        .nlm-flash-card-shell {
          width: min(480px, calc(100% - 48px));
          height: min(392px, calc(100vh - 258px));
          min-height: 336px;
          margin: 0 auto;
          perspective: 1400px;
          flex: 0 0 auto;
        }
        .nlm-flash-card {
          position: relative;
          width: 100%;
          height: 100%;
          transform-style: preserve-3d;
          transition: transform .52s cubic-bezier(.2,.72,.18,1);
          cursor: pointer;
        }
        .nlm-flash-card.is-back {
          transform: rotateY(180deg);
        }
        .nlm-flash-face {
          position: absolute;
          inset: 0;
          display: flex;
          flex-direction: column;
          border-radius: 22px;
          backface-visibility: hidden;
          overflow: hidden;
        }
        .nlm-flash-front {
          background: #303030;
          color: #FFFFFF;
          box-shadow: 0 24px 60px rgba(15,23,42,.12);
        }
        .nlm-flash-back {
          transform: rotateY(180deg);
          background: #FFFFFF;
          color: #111827;
          border: 1px solid #DCE2EC;
          box-shadow: 0 24px 60px rgba(15,23,42,.08);
        }
        .nlm-flash-count {
          position: absolute;
          left: 16px;
          top: 14px;
          color: #C9D0DA;
          font: 600 12px/16px "Google Sans Text", sans-serif;
        }
        .nlm-flash-more {
          position: absolute;
          right: 12px;
          top: 8px;
          border: 0;
          background: transparent;
          color: #FFFFFF;
          font-family: "Material Icons";
          font-size: 22px;
        }
        .nlm-flash-question {
          margin: auto 18px;
          padding: 10px 0 12px;
          color: #FFFFFF;
          font: 400 clamp(17px, 2.05vw, 23px)/1.34 "Google Sans", "Noto Sans SC", sans-serif;
          letter-spacing: 0;
          overflow-wrap: anywhere;
          word-break: break-word;
        }
        .nlm-flash-reveal {
          margin: 0 auto 14px;
          border: 0;
          background: transparent;
          color: rgba(255,255,255,.54);
          font: 500 12px/18px "Google Sans Text", "Noto Sans SC", sans-serif;
          cursor: pointer;
        }
        .nlm-flash-answer {
          margin: auto 18px 10px;
          color: #111827;
          font: 400 clamp(17px, 2.05vw, 22px)/1.34 "Google Sans", "Noto Sans SC", sans-serif;
          overflow-wrap: anywhere;
          word-break: break-word;
        }
        .nlm-flash-explain {
          width: fit-content;
          margin-left: 18px;
          display: inline-flex;
          align-items: center;
          gap: 7px;
          padding: 6px 13px;
          border: 1px solid #DCE2EC;
          border-radius: 999px;
          background: #FFFFFF;
          color: #111827;
          font: 700 12px/16px "Google Sans Text", "Noto Sans SC", sans-serif;
          cursor: pointer;
        }
        .nlm-flash-explain .material-icons {
          font-size: 16px;
        }
        .nlm-flash-explanation {
          margin: 10px 18px auto;
          max-height: 96px;
          overflow: auto;
          color: #5F6368;
          font: 400 12px/18px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-flash-actions {
          display: flex;
          align-items: center;
          justify-content: center;
          gap: 18px;
          margin-top: 6px;
          padding-bottom: 8px;
          flex: 0 0 auto;
        }
        .nlm-flash-round,
        .nlm-flash-score {
          height: 42px;
          min-width: 42px;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 9px;
          border: 1px solid #D7DCE8;
          border-radius: 999px;
          background: #FFFFFF;
          color: #4664F2;
          cursor: pointer;
          font: 600 13px/16px "Google Sans Text", sans-serif;
        }
        .nlm-flash-round .material-icons,
        .nlm-flash-score .material-icons {
          font-size: 18px;
        }
        .nlm-flash-score {
          min-width: 54px;
          padding: 0 10px;
        }
        .nlm-flash-wrong {
          color: #EF4444;
        }
        .nlm-flash-right {
          color: #16A34A;
        }
        .nlm-flash-round:last-child {
          border-color: #3E5BFF;
        }
        .nlm-quiz-option {
          margin-top: 8px;
          padding: 10px 12px;
          border: 1px solid #E0E3EA;
          border-radius: 999px;
          color: #202124;
          font: 400 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-data-preview {
          width: 100%;
          margin-top: 18px;
          border-collapse: collapse;
          color: #202124;
          font: 400 13px/18px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-data-preview th,
        .nlm-data-preview td {
          padding: 12px;
          border-bottom: 1px solid #E0E3EA;
          text-align: left;
          vertical-align: top;
        }
        .nlm-data-preview th {
          background: #F8FAFD;
          font-weight: 600;
        }
        /* NotebookLM typography calibration */
        .notebooklm-shell {
          font-family: "Google Sans Text", "Noto Sans SC", Roboto, Arial, sans-serif;
          font-size: 16px;
          font-weight: 400;
          letter-spacing: 0;
        }
        .notebooklm-title {
          font: 400 28px/36px "Google Sans", "Noto Sans SC", sans-serif;
        }
        .notebooklm-subtitle {
          font: 600 13px/16px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-panel-title {
          font: 400 20px/28px "Google Sans", "Noto Sans SC", sans-serif;
        }
        .nlm-add-source-btn {
          height: 40px;
          margin: 20px 20px 0 !important;
          font: 500 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif !important;
        }
        .nlm-search-card {
          margin: 20px 20px 0;
          padding: 16px 10px 10px;
          border-radius: 16px;
        }
        .nlm-search-label {
          margin: 0 0 10px 8px;
          font: 400 16px/24px "Google Sans Text", "Noto Sans SC", sans-serif;
          color: #5F6368;
        }
        .nlm-search-input .q-field__control {
          min-height: 36px;
        }
        .nlm-search-input .q-field__label {
          font: 400 16px/24px "Google Sans Text", "Noto Sans SC", sans-serif;
          color: #5F6368;
        }
        .nlm-chip {
          height: 32px;
          padding: 0 12px !important;
          font: 500 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif !important;
          text-transform: none !important;
        }
        .nlm-chip .q-btn__content,
        .nlm-chip .block {
          text-transform: none !important;
          font: 500 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif !important;
        }
        .nlm-search-btn {
          width: 40px;
          height: 40px;
        }
        .nlm-muted {
          font: 400 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-source-title,
        .nlm-upload-title {
          font: 400 15px/22px "Google Sans Text", "Noto Sans SC", sans-serif;
          color: #202124;
        }
        .nlm-source-meta {
          font: 400 12px/16px "Google Sans Text", "Noto Sans SC", sans-serif;
          color: #5F6368;
        }
        .nlm-answer-title {
          font: 400 34px/44px "Google Sans", "Noto Sans SC", sans-serif;
          color: #000000;
        }
        .nlm-answer-meta {
          font: 400 16px/24px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-answer-body,
        .nlm-answer-body .answer-box {
          font: 400 16px/24px "Google Sans Text", "Noto Sans SC", sans-serif;
          color: #202124;
        }
        .nlm-answer-body .answer-box {
          white-space: normal;
        }
        .nlm-answer-body .answer-box h1,
        .nlm-answer-body .answer-box h2,
        .nlm-answer-body .answer-box h3,
        .nlm-answer-body .answer-box h4 {
          margin: 18px 0 8px;
          color: #202124;
          font-family: "Google Sans", "Noto Sans SC", sans-serif;
          line-height: 1.28;
        }
        .nlm-answer-body .answer-box h1 { font-size: 24px; }
        .nlm-answer-body .answer-box h2 { font-size: 21px; }
        .nlm-answer-body .answer-box h3 { font-size: 18px; }
        .nlm-answer-body .answer-box h4 { font-size: 16px; }
        .nlm-answer-body .answer-box p {
          margin: 0 0 8px;
          line-height: 1.5;
        }
        .nlm-answer-body .answer-box ul,
        .nlm-answer-body .answer-box ol {
          margin: 6px 0 12px 22px;
          padding-left: 14px;
        }
        .nlm-answer-body .answer-box li {
          margin: 0 0 5px;
          line-height: 1.5;
          padding-left: 2px;
        }
        .nlm-answer-body .answer-box li > p {
          margin: 0;
        }
        .nlm-answer-body .answer-box hr {
          margin: 16px 0;
          border: 0;
          border-top: 1px solid #E5E7EB;
        }
        .nlm-action-btn {
          font: 500 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif !important;
        }
        .nlm-prompt-input,
        .nlm-prompt-input input {
          font: 400 18px/28px "Google Sans Text", "Noto Sans SC", sans-serif !important;
        }
        .nlm-prompt-count {
          font: 400 14px/20px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-studio-tile {
          font: 500 12px/16px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-studio-empty-title {
          font: 500 18px/24px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-studio-empty-copy {
          font: 400 16px/24px "Google Sans Text", "Noto Sans SC", sans-serif;
        }
        .nlm-upload-dialog { overflow: visible; }
        .hidden-course-select,
        .hidden-ask-button {
          position: absolute !important;
          width: 1px !important;
          height: 1px !important;
          opacity: 0 !important;
          pointer-events: none !important;
          overflow: hidden !important;
        }
        @media (max-width: 1500px) {
          .notebooklm-panels {
            grid-template-columns: 373px minmax(520px, 1fr) 373px;
            gap: 18px;
          }
          .notebooklm-shell.nlm-studio-wide:not(.nlm-studio-collapsed) .notebooklm-panels {
            grid-template-columns: 300px minmax(320px, 1fr) min(660px, 54vw);
          }
          .nlm-chat-scroll { padding-left: 34px; padding-right: 34px; }
          .nlm-answer-title { font-size: 32px; }
        }
        @media (max-width: 1240px) {
          .notebooklm-shell {
            position: relative;
            min-height: 100vh;
            overflow: auto;
          }
          .notebooklm-panels {
            height: auto;
            min-height: 640px;
            grid-template-columns: 1fr;
            overflow: visible;
          }
          .nlm-panel { min-height: 560px; }
          .notebooklm-topbar { flex-wrap: wrap; height: auto; min-height: 92px; padding: 16px 20px 0; }
        }
        .review-toolbar {
          width: 100%;
          gap: 12px;
          margin-bottom: 8px;
        }
        .review-command-card,
        .review-record-card {
          padding: 24px 26px 28px;
          background: rgba(255,255,255,.42);
          border: 1px solid rgba(255,255,255,.42);
          box-shadow: 0 18px 46px rgba(15,23,42,.055), inset 0 1px 0 rgba(255,255,255,.5);
          backdrop-filter: blur(18px) saturate(1.08);
          -webkit-backdrop-filter: blur(18px) saturate(1.08);
        }
        .review-command-card {
          margin-top: 18px;
          min-height: 0;
          padding: 16px 24px;
        }
        .review-command-head,
        .review-record-head {
          gap: 12px;
          margin-bottom: 0;
        }
        .review-command-icon,
        .review-record-icon {
          width: 38px;
          height: 38px;
          display: grid;
          place-items: center;
          border-radius: 12px;
          color: #2563EB;
          background: rgba(219,234,254,.86);
          box-shadow: 0 12px 24px rgba(37,99,235,.13);
        }
        .review-command-title {
          min-width: 0;
        }
        .review-command-subtitle {
          color: #64748B;
          font-size: 12px;
          line-height: 1.45;
        }
        .review-command-actions {
          min-width: 0;
          gap: 10px;
          justify-content: flex-end;
        }
        .review-course-select {
          width: min(320px, 28vw);
          min-width: 220px;
        }
        .review-course-select .q-field__control {
          min-height: 42px;
          border-radius: 12px !important;
          background: rgba(255,255,255,.92) !important;
          box-shadow: 0 10px 24px rgba(15,23,42,.055);
        }
        .review-course-select .q-field__native,
        .review-course-select .q-field__input {
          padding-left: 14px !important;
        }
        .review-course-select .q-field__label {
          left: 14px !important;
        }
        .review-generate-btn {
          height: 42px;
          min-width: 132px;
          border-radius: 10px !important;
          background: #2D2D2D !important;
          background-color: #2D2D2D !important;
          color: #FFFFFF !important;
          font-weight: 850 !important;
          box-shadow: 0 1px 2px rgba(0,0,0,.4), 1px 4px 8px rgba(0,0,0,.14) !important;
        }
        .review-generate-btn::before {
          background: transparent !important;
        }
        .review-record-list {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 12px;
        }
        .review-record-item {
          display: grid;
          grid-template-columns: 108px minmax(0, 1fr);
          align-items: center;
          gap: 12px;
          min-height: 58px;
          padding: 12px 14px;
          border-radius: 12px;
          background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(248,250,252,.92));
          border: 1px solid rgba(148,163,184,.16);
          box-shadow: 0 10px 22px rgba(15,23,42,.04);
        }
        .review-record-course {
          justify-self: start;
          max-width: 100%;
          padding: 5px 10px;
          border-radius: 999px;
          color: #1D4ED8;
          background: #DBEAFE;
          font-size: 12px;
          font-weight: 850;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .review-record-question {
          color: #0F172A;
          font-size: 14px;
          font-weight: 760;
          line-height: 1.55;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .quiz-list {
          gap: 16px;
          margin-top: 16px;
        }
        .quiz-summary {
          width: 100%;
          gap: 14px;
          padding: 6px 0 2px;
        }
        .quiz-section-title {
          color: #0F172A;
          font-size: 17px;
          font-weight: 900;
        }
        .quiz-card {
          width: 100%;
          border-radius: 8px;
          border: 1px solid rgba(148,163,184,.22);
          background: #FFFFFF;
          box-shadow: 0 12px 28px rgba(15,23,42,.055);
          padding: 22px 24px;
        }
        .quiz-question-head {
          gap: 6px;
          margin-bottom: 8px;
        }
        .quiz-number {
          color: #0F172A;
          font-size: 16px;
          font-weight: 900;
        }
        .quiz-type {
          color: #94A3B8;
          font-size: 14px;
          font-weight: 800;
        }
        .quiz-question {
          color: #0F172A;
          font-size: 17px;
          font-weight: 800;
          line-height: 1.7;
          margin-bottom: 18px;
          word-break: break-word;
        }
        .quiz-options {
          width: 100%;
          gap: 12px;
          margin-bottom: 16px;
        }
        .quiz-option {
          width: 100%;
          min-height: 44px;
          display: grid;
          grid-template-columns: 42px minmax(0, 1fr);
          align-items: center;
          gap: 14px;
          padding: 4px 0;
          border: 0;
          background: transparent;
          color: #0F172A;
          text-align: left;
          cursor: pointer;
          font: inherit;
        }
        .quiz-option-dot {
          width: 38px;
          height: 38px;
          display: grid;
          place-items: center;
          border-radius: 999px;
          border: 1px solid #D9E4F2;
          color: #48617D;
          background: #FFFFFF;
          font-weight: 800;
          transition: all .16s ease;
        }
        .quiz-option-text {
          color: #0F172A;
          font-size: 16px;
          font-weight: 750;
          line-height: 1.65;
          word-break: break-word;
        }
        .quiz-option:hover .quiz-option-dot,
        .quiz-option-selected .quiz-option-dot {
          border-color: #2563EB;
          background: #DBEAFE;
          color: #1D4ED8;
          box-shadow: 0 8px 18px rgba(37,99,235,.16);
        }
        .quiz-options-submitted .quiz-option {
          cursor: default;
        }
        .quiz-blank-input {
          width: min(720px, 100%);
          margin-bottom: 14px;
        }
        .quiz-submit {
          align-self: flex-start;
          margin-top: 2px;
        }
        .quiz-result-box {
          width: 100%;
          margin-top: 18px;
          padding: 18px 24px;
          border-left: 6px solid #E2E8F0;
          background: #F8FAFC;
          border-radius: 0 8px 8px 0;
          gap: 10px;
        }
        .quiz-answer-row {
          width: 100%;
          gap: 10px;
        }
        .quiz-answer-label {
          color: #0F172A;
          font-size: 16px;
          font-weight: 900;
        }
        .quiz-user-answer {
          color: #0F172A;
          font-size: 16px;
          font-weight: 750;
        }
        .quiz-answer-correct,
        .quiz-correct-answer,
        .quiz-reference-title,
        .quiz-reference-copy {
          color: #00A968;
        }
        .quiz-correct-answer,
        .quiz-reference-copy {
          font-size: 15px;
          line-height: 1.75;
          word-break: break-word;
        }
        .quiz-reference-title {
          margin-top: 8px;
          font-size: 16px;
          font-weight: 900;
        }
        .quiz-correct-icon {
          color: #00A968;
          font-size: 26px;
        }
        .quiz-wrong-icon {
          color: #EF4444;
          font-size: 26px;
        }
        .upload-record {
          border-radius: 12px;
          background: #FFFFFF;
          border: 1px solid rgba(37,99,235,.14);
          box-shadow: 0 10px 24px rgba(15,23,42,.06);
        }
        .q-dialog .upload-dialog,
        .q-card.upload-dialog,
        .upload-dialog {
          width: min(860px, calc(100vw - 48px)) !important;
          max-width: none !important;
          max-height: min(92vh, 760px) !important;
          overflow-y: auto;
          border-radius: 18px;
          padding: 36px 30px 30px;
          background:
            radial-gradient(circle at 84% -8%, rgba(201,255,218,.56), transparent 34%),
            radial-gradient(circle at 12% -10%, rgba(223,226,255,.62), transparent 31%),
            #FFFFFF;
          box-shadow: 0 30px 90px rgba(15,23,42,.24);
        }
        .upload-dialog-head {
          position: relative;
          align-items: flex-start;
        }
        .upload-title-wrap { flex: 0 1 auto; align-items: center; text-align: center; }
        .upload-dialog-title {
          font-size: 27px;
          font-weight: 500;
          color: #1F2937;
          letter-spacing: 0;
          line-height: 1.28;
        }
        .upload-dialog-subtitle {
          font-size: 29px;
          font-weight: 400;
          line-height: 1.1;
          background: linear-gradient(90deg, #77D8FF, #65E69D);
          -webkit-background-clip: text;
          background-clip: text;
          color: transparent;
        }
        .icon-btn { color: #334155 !important; }
        .upload-close {
          position: absolute;
          right: -2px;
          top: -10px;
          color: #374151 !important;
        }
        .upload-search-shell {
          width: min(714px, 100%);
          margin: 44px auto 40px;
          border: 1.5px solid #4E5BFF;
          border-radius: 16px;
          padding: 13px 12px 10px;
          background: rgba(255,255,255,.92);
        }
        .upload-search-input .q-field__control { min-height: 38px; }
        .upload-search-input .q-field__label {
          color: #4B5563;
          font-size: 16px;
        }
        .chip-btn,
        .source-action-btn,
        .q-btn.source-action-btn {
          border-radius: 999px !important;
          color: #1F2937 !important;
          background: #FFFFFF !important;
          background-color: #FFFFFF !important;
          border: 1px solid #DDE3EE !important;
          font-weight: 600;
          box-shadow: none !important;
          text-transform: none !important;
        }
        .chip-btn::before,
        .source-action-btn::before,
        .q-btn.source-action-btn::before,
        .chip-btn .q-focus-helper,
        .source-action-btn .q-focus-helper,
        .q-btn.source-action-btn .q-focus-helper {
          background: transparent !important;
          opacity: 0 !important;
        }
        .chip-btn *,
        .source-action-btn *,
        .q-btn.source-action-btn * {
          color: #1F2937 !important;
        }
        .chip-btn {
          min-height: 38px;
          padding: 0 13px !important;
        }
        .search-round {
          width: 46px !important;
          height: 46px !important;
          background: #F3F4F6 !important;
          color: #6B7280 !important;
        }
        .upload-dropzone {
          width: min(792px, 100%);
          margin: 0 auto;
          border: 2px dashed #D8E0EB;
          border-radius: 16px;
          min-height: 328px;
          padding: 76px 28px 58px;
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          gap: 16px;
          background: rgba(255,255,255,.42);
          position: relative;
          overflow: hidden;
          transition: border-color .16s ease, background .16s ease, box-shadow .16s ease;
        }
        .upload-dropzone:hover,
        .upload-dropzone:focus-within {
          border-color: #7D8CFF;
          background: rgba(248,250,255,.68);
          box-shadow: inset 0 0 0 1px rgba(78,91,255,.08);
        }
        .drop-title {
          font-size: 28px;
          font-weight: 400;
          color: #111827;
          line-height: 1.2;
          position: relative;
          z-index: 1;
          pointer-events: none;
        }
        .drop-copy {
          color: #6B7280;
          font-size: 17px;
          text-align: center;
          position: relative;
          z-index: 1;
          pointer-events: none;
        }
        .upload-widget {
          width: auto;
          min-width: 154px;
          height: 52px;
          border-radius: 999px;
          box-shadow: none;
          overflow: hidden;
        }
        .upload-widget:not(.upload-dropzone-uploader),
        .upload-widget:not(.upload-dropzone-uploader) .q-uploader {
          width: 154px !important;
          min-width: 154px !important;
          max-width: 154px !important;
          max-height: 52px !important;
          border-radius: 999px !important;
          box-shadow: none !important;
          border: 1px solid #DDE3EE !important;
          background: #FFFFFF !important;
        }
        .upload-dropzone-uploader {
          position: absolute;
          inset: 0;
          z-index: 0;
          width: 100% !important;
          height: 100% !important;
          min-width: 0 !important;
          border-radius: 16px !important;
          opacity: .01;
          overflow: hidden;
        }
        .upload-dropzone-uploader .q-uploader {
          width: 100% !important;
          height: 100% !important;
          min-height: 100% !important;
          max-width: none !important;
          max-height: none !important;
          border: 0 !important;
          border-radius: 16px !important;
          background: transparent !important;
          box-shadow: none !important;
        }
        .upload-dropzone-uploader .q-uploader__header {
          display: none !important;
        }
        .upload-dropzone-uploader .q-uploader__list {
          height: 100% !important;
          min-height: 100% !important;
          display: block !important;
          background: transparent !important;
        }
        .upload-widget .q-uploader__header {
          min-height: 50px;
          height: 50px;
          border-radius: 999px !important;
          background: #FFFFFF !important;
          color: #111827 !important;
          box-shadow: none !important;
          border: 0 !important;
          padding: 0 18px !important;
        }
        .upload-widget .q-uploader__header::before,
        .upload-widget .q-uploader__header::after {
          display: none !important;
        }
        .upload-widget .q-uploader__header-content {
          color: #111827 !important;
          font-size: 15px !important;
          font-weight: 600 !important;
          line-height: 50px !important;
          text-align: center !important;
          width: 100%;
          justify-content: center !important;
        }
        .upload-widget .q-uploader__title {
          color: #111827 !important;
          font-size: 15px !important;
          font-weight: 600 !important;
          line-height: 50px !important;
          text-align: center !important;
        }
        .upload-widget .q-uploader__subtitle,
        .upload-widget .q-uploader__file,
        .upload-widget .q-uploader__dnd {
          display: none !important;
        }
        .upload-widget .q-uploader__list {
          display: none !important;
        }
        .upload-source-actions {
          justify-content: center;
          gap: 14px;
          flex-wrap: wrap;
          margin-top: 48px;
          position: relative;
          z-index: 2;
        }
        .source-action-btn {
          height: 52px !important;
          min-height: 52px;
          padding: 0 24px !important;
          font-size: 15px;
        }
        .upload-form-area {
          width: min(792px, 100%);
          margin: 0 auto;
          padding-top: 0;
          border-top: 0;
          gap: 12px;
          opacity: .86;
          max-height: 0;
          overflow: hidden;
          pointer-events: none;
          transition: max-height .24s ease, margin-top .24s ease, padding-top .24s ease, opacity .2s ease;
        }
        .upload-form-area.is-visible {
          margin-top: 22px;
          padding-top: 18px;
          border-top: 1px solid #EEF2F7;
          max-height: 460px;
          pointer-events: auto;
          opacity: 1;
        }
        .upload-save-btn {
          align-self: flex-start;
          border-radius: 999px !important;
          background: #111827 !important;
          color: #FFFFFF !important;
          font-weight: 800;
        }
        .task-pill {
          padding: 10px 12px;
          border: 1px solid rgba(148,163,184,.18);
          border-radius: 12px;
          background: rgba(248,250,252,.8);
          margin: 8px 0;
        }
        .task-main { color: #1E293B; font-weight: 650; }
        .task-date { color: #64748B; font-size: 12px; }
        .task-line { padding: 6px 0; color: #334155; }
        .plan-copy { color: #334155; line-height: 1.8; }
        .calendar-card {
          width: 100%;
          min-height: 0;
          padding: 24px 28px 26px;
          border-radius: 16px;
          margin-top: 0;
        }
        .calendar-toolbar {
          display: grid !important;
          grid-template-columns: max-content minmax(0, 1fr);
          align-items: start;
          column-gap: 18px;
          row-gap: 10px;
          margin-bottom: 14px;
        }
        .calendar-toolbar > .q-column {
          min-width: 180px;
        }
        .calendar-toolbar-meta {
          min-width: 0;
          width: auto;
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 8px;
          flex-wrap: wrap;
          justify-self: end;
        }
        .calendar-subtitle { color: #64748B; font-size: 12px; }
        .calendar-count {
          border-radius: 999px;
          padding: 6px 10px;
          font-weight: 750;
        }
        .calendar-icon-btn {
          color: #2563EB !important;
          background: #EFF6FF !important;
        }
        .calendar-summary-strip {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 8px;
          margin: 0;
          flex-wrap: nowrap;
        }
        .calendar-summary-item {
          position: relative;
          width: 96px;
          min-width: 96px;
          padding: 8px 9px 8px 13px;
          border-radius: 10px;
          background: #FFFFFF;
          border: 1px solid rgba(148,163,184,.16);
          box-shadow: 0 8px 18px rgba(15,23,42,.04);
          overflow: hidden;
        }
        .calendar-summary-item::before {
          content: "";
          position: absolute;
          inset: 0 auto 0 0;
          width: 4px;
          background: var(--summary-accent, #2563EB);
        }
        .calendar-summary-blue { --summary-accent: #2563EB; }
        .calendar-summary-red { --summary-accent: #EF4444; }
        .calendar-summary-green { --summary-accent: #10B981; }
        .calendar-summary-amber { --summary-accent: #F59E0B; }
        .calendar-summary-value {
          color: #0F172A;
          font-size: 15px;
          line-height: 1.1;
          font-weight: 950;
        }
        .calendar-summary-label {
          color: #64748B;
          font-size: 10.5px;
          font-weight: 750;
          margin-top: 4px;
        }
        .calendar-weekdays {
          width: 100%;
          box-sizing: border-box;
          display: grid;
          grid-template-columns: repeat(7, minmax(0, 1fr));
          border: 1px solid rgba(20,184,166,.22);
          border-bottom: 0;
          border-radius: 10px 10px 0 0;
          overflow: hidden;
          background: linear-gradient(90deg, rgba(240,253,250,.9), rgba(239,246,255,.92));
        }
        .calendar-weekday {
          padding: 8px 8px;
          text-align: center;
          color: #0F766E;
          font-weight: 850;
          font-size: 12px;
          border-right: 1px solid rgba(20,184,166,.16);
        }
        .calendar-weekday:last-child { border-right: 0; }
        .calendar-grid {
          width: 100%;
          box-sizing: border-box;
          display: grid;
          grid-template-columns: repeat(7, minmax(0, 1fr));
          border-top: 1px solid rgba(148,163,184,.18);
          border-left: 1px solid rgba(148,163,184,.18);
          border-radius: 0 0 10px 10px;
          overflow: visible;
        }
        .calendar-day {
          min-height: 92px;
          padding: 8px 8px 7px;
          background: rgba(255,255,255,.94);
          border-right: 1px solid rgba(148,163,184,.18);
          border-bottom: 1px solid rgba(148,163,184,.18);
          overflow: visible;
          display: flex;
          flex-direction: column;
          transition: transform .18s ease, box-shadow .18s ease, background .18s ease;
        }
        .calendar-day:hover {
          transform: translateY(-1px);
          background: #FFFFFF;
          box-shadow: inset 0 0 0 1px rgba(37,99,235,.18), 0 10px 22px rgba(15,23,42,.06);
          z-index: 1;
        }
        .calendar-day-muted {
          background: rgba(248,250,252,.56);
        }
        .calendar-day-today {
          box-shadow: inset 0 0 0 2px #14B8A6;
          background: linear-gradient(180deg, #FFFFFF, #F0FDFA);
        }
        .calendar-day-head {
          min-height: 25px;
          margin-bottom: 3px;
        }
        .calendar-date-number {
          color: #334155;
          font-size: 17px;
          line-height: 1;
          font-weight: 850;
        }
        .calendar-day-count {
          color: #94A3B8;
          font-size: 11px;
          min-height: 13px;
        }
        .calendar-alert {
          color: #DC2626;
          background: #FFF1F2;
          border-radius: 999px;
          font-size: 14px;
          padding: 2px;
        }
        .calendar-task {
          --task-bg: #EFF6FF;
          --task-border: #BFDBFE;
          --task-text: #1E3A8A;
          --task-meta: #5273C7;
          --task-priority: #38BDF8;
          height: 28px;
          min-height: 28px;
          border-radius: 7px;
          padding: 3px 8px 3px 10px;
          margin-top: 4px;
          overflow: hidden;
          color: var(--task-text);
          background: var(--task-bg);
          border: 1px solid var(--task-border);
          border-left: 4px solid var(--task-priority);
          box-shadow: 0 6px 14px rgba(15,23,42,.055);
          position: relative;
          z-index: 1;
          transition: transform .18s ease, filter .18s ease, box-shadow .18s ease, height .18s ease, border-color .18s ease;
        }
        .calendar-task:hover {
          height: auto;
          min-height: 66px;
          padding: 6px 9px 8px 11px;
          transform: translateY(-8px) scale(1.015);
          filter: saturate(1.04);
          border-color: color-mix(in srgb, var(--task-border) 72%, #2563EB);
          box-shadow: 0 14px 26px rgba(37,99,235,.13);
          z-index: 20;
          overflow: visible;
        }
        .calendar-task-completed {
          --task-bg: #F1F5F9;
          --task-border: #E2E8F0;
          --task-text: #64748B;
          --task-meta: #94A3B8;
          --task-priority: #CBD5E1;
          box-shadow: none;
          filter: saturate(.55);
        }
        .calendar-task-completed:hover {
          transform: translateY(-8px) scale(1.015);
          filter: saturate(.25);
        }
        .calendar-task-completed .calendar-task-title {
          color: #475569;
          text-decoration: line-through;
          text-decoration-thickness: 2px;
          text-decoration-color: rgba(71,85,105,.72);
        }
        .calendar-task-completed .calendar-task-meta {
          color: #64748B;
          opacity: .76;
        }
        .calendar-task-title {
          color: var(--task-text);
          max-width: 100%;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-size: 11.5px;
          line-height: 12.5px;
          font-weight: 800;
        }
        .calendar-task:hover .calendar-task-title {
          white-space: normal;
          overflow: visible;
          text-overflow: clip;
          font-size: 12px;
          line-height: 14px;
        }
        .calendar-task-meta {
          color: var(--task-meta);
          max-width: 100%;
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
          font-size: 10px;
          line-height: 11px;
          opacity: .86;
        }
        .calendar-task-full {
          display: none;
          margin-top: 5px;
          color: var(--task-meta);
          font-size: 10.5px;
          line-height: 12px;
          font-weight: 700;
          white-space: normal;
        }
        .calendar-task:hover .calendar-task-full {
          display: block;
        }
        .calendar-course-python {
          --task-bg: #EEF4FF;
          --task-border: #C7D8FE;
          --task-text: #244484;
          --task-meta: #5B76B7;
        }
        .calendar-course-ai {
          --task-bg: #ECFDF5;
          --task-border: #A7F3D0;
          --task-text: #065F46;
          --task-meta: #0F766E;
        }
        .calendar-course-english {
          --task-bg: #F0FDFA;
          --task-border: #99F6E4;
          --task-text: #0F766E;
          --task-meta: #14B8A6;
        }
        .calendar-course-other {
          --task-bg: #F8FAFC;
          --task-border: #CBD5E1;
          --task-text: #334155;
          --task-meta: #64748B;
        }
        .priority-high { --task-priority: #F59E0B; }
        .priority-mid { --task-priority: #38BDF8; }
        .priority-low { --task-priority: #10B981; }
        .calendar-more {
          color: #64748B;
          font-size: 11px;
          font-weight: 700;
          padding: 4px 2px 0;
        }
        .unscheduled-row {
          min-height: 40px;
          padding: 7px 9px;
          border-radius: 10px;
          background: rgba(248,250,252,.86);
          border: 1px dashed rgba(148,163,184,.38);
          gap: 8px;
          flex-wrap: wrap;
        }
        .unscheduled-icon { color: #F59E0B; }
        .unscheduled-badge {
          border-radius: 999px;
          font-weight: 800;
          padding: 4px 8px;
        }
        .helper-copy, .empty-copy { color: #64748B; font-size: 12px; line-height: 1.6; }
        .task-command-card {
          padding: 0;
          overflow: hidden;
          border: 1px solid rgba(255,255,255,.4);
          background:
            radial-gradient(circle at 12% 20%, rgba(255,255,255,.46), rgba(255,255,255,0) 28%),
            linear-gradient(135deg, rgba(239,246,255,.46), rgba(240,253,250,.38) 54%, rgba(255,255,255,.44));
          box-shadow: 0 18px 46px rgba(15,23,42,.055), inset 0 1px 0 rgba(255,255,255,.5);
          backdrop-filter: blur(18px) saturate(1.08);
          -webkit-backdrop-filter: blur(18px) saturate(1.08);
        }
        .task-command-layout {
          display: grid;
          grid-template-columns: minmax(300px, .72fr) minmax(560px, 1.28fr);
          gap: 28px;
          padding: 30px 32px;
          align-items: center;
        }
        .task-command-redesign {
          display: block;
          width: 100%;
          box-sizing: border-box;
          padding: 28px 32px;
        }
        .task-command-main {
          width: 100% !important;
          max-width: none !important;
          box-sizing: border-box;
          display: grid !important;
          grid-template-columns: 330px minmax(0, 1fr);
          align-items: center;
          gap: 28px;
        }
        .task-command-heading {
          width: 330px;
          min-width: 330px;
          gap: 12px;
        }
        .task-command-copy {
          min-height: 142px;
          padding: 4px 0;
          justify-content: center;
        }
        .task-command-title-row { gap: 12px; margin-bottom: 10px; }
        .task-command-icon,
        .task-board-icon {
          width: 42px;
          height: 42px;
          display: grid;
          place-items: center;
          border-radius: 12px;
          color: #2563EB;
          background: rgba(219,234,254,.86);
          box-shadow: 0 12px 24px rgba(37,99,235,.13);
        }
        .task-command-title {
          color: #0F172A;
          font-size: 22px;
          font-weight: 950;
        }
        .task-command-caption {
          color: #64748B;
          font-size: 13px;
          line-height: 1.55;
          margin-top: 3px;
        }
        .task-command-subtitle,
        .task-board-subtitle {
          color: #64748B;
          font-size: 13px;
          line-height: 1.65;
        }
        .task-command-hint {
          width: fit-content;
          margin-top: 18px;
          padding: 8px 12px;
          border-radius: 999px;
          color: #1D4ED8;
          background: rgba(219,234,254,.72);
          border: 1px solid rgba(191,219,254,.9);
          font-size: 12px;
          font-weight: 800;
        }
        .task-command-form {
          min-width: 0;
          width: 100%;
          padding: 22px;
          border-radius: 12px;
          background: rgba(255,255,255,.76);
          border: 1px solid rgba(148,163,184,.16);
          box-shadow: 0 14px 30px rgba(15,23,42,.06);
        }
        .task-command-entry {
          width: 100%;
          gap: 14px;
        }
        .task-command-input .q-field__control {
          min-height: 54px;
          border-radius: 10px !important;
          background: #FFFFFF !important;
        }
        .task-command-submit {
          align-self: flex-end;
          min-width: 142px;
          height: 44px;
          box-shadow: 0 10px 22px rgba(37,99,235,.18) !important;
        }
        .task-command-result {
          min-height: 32px;
          color: #334155;
          font-size: 13px;
          line-height: 1.65;
        }
        .task-board-card {
          padding: 24px 26px 28px;
          background: rgba(255,255,255,.42);
          border: 1px solid rgba(255,255,255,.42);
          box-shadow: 0 18px 46px rgba(15,23,42,.055), inset 0 1px 0 rgba(255,255,255,.5);
          backdrop-filter: blur(18px) saturate(1.08);
          -webkit-backdrop-filter: blur(18px) saturate(1.08);
        }
        .task-board-head {
          gap: 12px;
          margin-bottom: 18px;
        }
        .task-board-title {
          min-width: 0;
        }
        .task-board-actions {
          min-width: 0;
          gap: 10px;
          justify-content: flex-end;
        }
        .task-inline-input {
          width: min(420px, 34vw);
          min-width: 280px;
        }
        .task-inline-input .q-field__control {
          min-height: 44px;
          border-radius: 12px !important;
          background: rgba(255,255,255,.92) !important;
          box-shadow: 0 10px 24px rgba(15,23,42,.055);
        }
        .task-inline-input .q-field__native,
        .task-inline-input .q-field__input {
          padding-left: 14px !important;
        }
        .task-inline-input .q-field__label {
          left: 14px !important;
          color: #64748B;
          font-weight: 800;
        }
        .task-inline-submit {
          height: 44px;
          min-width: 138px;
          border-radius: 10px !important;
          background: #2D2D2D !important;
          background-color: #2D2D2D !important;
          color: #FFFFFF !important;
          font-weight: 850 !important;
          box-shadow: 0 1px 2px rgba(0,0,0,.4), 1px 4px 8px rgba(0,0,0,.14) !important;
        }
        .task-inline-submit::before {
          background: transparent !important;
        }
        .task-inline-result {
          width: 100%;
          margin: -8px 0 14px;
          padding: 10px 12px;
          border-radius: 10px;
          color: #334155;
          background: rgba(239,246,255,.72);
          border: 1px solid rgba(191,219,254,.72);
          font-size: 13px;
          line-height: 1.6;
        }
        .hidden-result {
          display: none !important;
        }
        .task-refresh-btn {
          width: 44px;
          height: 44px;
          color: #2563EB !important;
          background: #EFF6FF !important;
        }
        .todo-table {
          width: 100%;
          border: 1px solid rgba(148,163,184,.18);
          border-radius: 12px;
          overflow-x: auto;
          overflow-y: hidden;
          background: linear-gradient(180deg, rgba(255,255,255,.98), rgba(248,250,252,.92));
          box-shadow: 0 14px 34px rgba(15,23,42,.055);
        }
        .todo-table-row {
          display: grid;
          grid-template-columns: 48px 72px minmax(120px, .85fr) minmax(260px, 1.6fr) 150px 110px 110px;
          align-items: center;
          min-height: 64px;
          column-gap: 18px;
          padding: 0 20px;
          min-width: 1040px;
          border-bottom: 1px solid rgba(148,163,184,.16);
          color: #0F172A;
          transition: background .16s ease, transform .16s ease, box-shadow .16s ease;
        }
        .todo-table-row:last-child { border-bottom: 0; }
        .todo-table-row:not(.todo-table-head):hover {
          background: rgba(239,246,255,.72);
          box-shadow: inset 4px 0 0 #60A5FA;
        }
        .todo-table-head {
          min-height: 54px;
          color: #475569;
          background: linear-gradient(90deg, #F8FAFC, #EFF6FF);
          font-size: 13px;
          font-weight: 800;
          letter-spacing: 0;
        }
        .todo-table-row-done {
          background: rgba(248,250,252,.72);
          color: #64748B;
        }
        .todo-table-row-done .todo-task-cell {
          text-decoration: line-through;
          text-decoration-thickness: 2px;
          text-decoration-color: rgba(100,116,139,.62);
        }
        .todo-check-cell {
          display: flex;
          justify-content: center;
          min-width: 32px;
        }
        .todo-complete-check .q-checkbox__inner {
          font-size: 28px;
        }
        .todo-id-cell {
          text-align: center;
          font-weight: 800;
          color: #334155;
        }
        .todo-task-cell,
        .todo-course-cell,
        .todo-date-cell,
        .todo-priority-cell {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
        .todo-status-badge {
          justify-self: start;
          min-width: 70px;
          padding: 5px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 800;
          text-align: center;
        }
        .todo-course-cell {
          color: #1E3A8A;
          font-weight: 800;
        }
        .todo-task-cell {
          color: #0F172A;
          font-weight: 760;
        }
        .todo-date-cell {
          color: #334155;
          font-weight: 700;
        }
        .todo-priority-pill {
          justify-self: start;
          width: auto;
          min-width: 42px;
          padding: 5px 10px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 850;
          text-align: center;
        }
        .todo-priority-high {
          color: #B45309;
          background: #FEF3C7;
        }
        .todo-priority-mid {
          color: #1D4ED8;
          background: #DBEAFE;
        }
        .todo-priority-low {
          color: #047857;
          background: #D1FAE5;
        }
        .status-pending {
          color: #1D4ED8;
          background: #DBEAFE;
        }
        .status-done {
          color: #475569;
          background: #E5E7EB;
        }
        .primary-btn { background: #2563EB !important; color: white !important; border-radius: 10px !important; font-weight: 700; }
        .secondary-btn { border-radius: 10px !important; font-weight: 700; }
        .quick-btn { width: 100%; border-radius: 10px !important; margin: 4px 0; }
        .answer-box { white-space: pre-wrap; }
        .notebooklm-shell .answer-box { white-space: normal !important; }
        .animate-card, .animate-hero, .animate-source {
          opacity: 1;
          visibility: visible;
          transform: translateY(0);
          will-change: transform, opacity;
        }
        @media (max-width: 900px) {
          .q-header {
            top: 10px;
            width: calc(100vw - 20px);
            min-height: 58px;
            padding: 0 12px;
            border-radius: 15px;
          }
          .top-header {
            grid-template-columns: auto minmax(0, 1fr);
            column-gap: 12px;
          }
          .top-brand { min-width: auto; font-size: 20px; }
          .top-right { display: none !important; }
          .top-tabs { justify-self: center; max-width: 100%; overflow-x: auto; }
          .top-tabs .q-tabs__content { flex-wrap: nowrap; justify-content: center; }
          .main-tab-panels .q-tab-panel { padding: 88px 12px 28px; }
          .main-tab-panels .q-tab-panel:first-child { padding: 0; }
          .dashboard-shell { padding-top: 56px; gap: 16px; }
          .mineradio-float {
            width: 68px;
            min-height: 68px;
            grid-template-columns: 1fr;
            padding: 7px;
            border-radius: 15px;
          }
          .mineradio-disc {
            width: 54px;
            height: 54px;
            justify-self: center;
          }
          .mineradio-float-copy {
            display: none;
          }
          .dashboard-hero { min-height: 310px; padding: 0 16px; }
          .hero-title { font-size: clamp(42px, 14vw, 62px); }
          .hero-subtitle { font-size: 17px; }
          .dashboard-stage { width: calc(100% - 20px); }
          .dashboard-feature-tabs {
            width: 100%;
            margin-top: 0;
          }
          .dashboard-feature-tabs .q-tabs__content {
            display: flex;
            overflow-x: auto;
            justify-content: center;
            gap: 4px;
          }
          .dashboard-feature-tabs .q-tab {
            min-width: 136px;
            width: 136px;
            font-size: 13px;
          }
          .nlm-mindmap-viewer.nlm-map-expanded .nlm-app-body,
          .nlm-mindmap-viewer.nlm-expanded .nlm-app-body {
            overflow: auto;
            padding: 0 12px 14px;
          }
          .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-stage,
          .nlm-mindmap-viewer.nlm-expanded .nlm-map-stage {
            width: 100%;
            min-width: 0;
            height: auto;
            min-height: 560px;
            grid-template-columns: 180px minmax(420px, 1fr);
          }
          .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-canvas,
          .nlm-mindmap-viewer.nlm-map-expanded .nlm-map-view,
          .nlm-mindmap-viewer.nlm-expanded .nlm-map-canvas,
          .nlm-mindmap-viewer.nlm-expanded .nlm-map-view {
            min-height: 500px;
          }
          .summary-layout {
            grid-template-columns: 1fr;
            padding: 28px 20px;
            min-height: 0;
          }
          .summary-copy { min-height: 0; }
          .summary-metrics { grid-template-columns: 1fr; padding: 16px; }
          .metric-card { min-height: 132px; }
          .metric-value { margin-top: 16px; font-size: 36px; }
          .plan-card-full, .calendar-card {
            min-height: 0;
            padding: 24px 18px;
          }
          .task-command-layout {
            grid-template-columns: 1fr;
            gap: 16px;
            padding: 20px;
          }
          .task-command-redesign { display: block; padding: 20px; }
          .task-command-main,
          .task-command-entry {
            flex-wrap: wrap !important;
          }
          .task-command-main {
            display: grid !important;
            grid-template-columns: 1fr;
          }
          .task-command-heading {
            width: 100%;
            min-width: 0;
          }
          .task-command-form {
            width: 100%;
            padding: 16px;
          }
          .task-command-submit { width: 100%; }
          .task-command-copy { min-height: 0; }
          .task-command-hint { width: auto; }
          .task-board-card { padding: 20px 14px 22px; }
          .task-board-head { flex-wrap: wrap !important; }
          .task-board-actions {
            width: 100%;
            flex-wrap: wrap !important;
            justify-content: flex-start;
          }
          .task-inline-input {
            width: 100%;
            min-width: 0;
          }
          .task-inline-submit { width: calc(100% - 54px); }
          .review-command-card,
          .review-record-card { padding: 20px 14px 22px; }
          .review-command-head,
          .review-record-head {
            flex-wrap: wrap !important;
          }
          .review-command-actions {
            width: 100%;
            flex-wrap: wrap !important;
            justify-content: flex-start;
          }
          .review-course-select {
            width: 100%;
            min-width: 0;
          }
          .review-generate-btn { width: 100%; }
          .review-record-list { grid-template-columns: 1fr; }
          .review-record-item { grid-template-columns: 1fr; align-items: start; }
          .plan-list { grid-template-columns: 1fr; }
          .plan-support-grid { grid-template-columns: 1fr; }
          .plan-panel { min-height: 0; }
          .calendar-toolbar { flex-wrap: wrap !important; }
          .calendar-toolbar-meta { width: 100%; justify-content: flex-start; }
          .calendar-summary-strip { flex-wrap: wrap; justify-content: flex-start; }
          .calendar-summary-item { width: calc(50% - 5px); min-width: 140px; }
          .calendar-card { padding: 24px 14px; overflow-x: auto; }
          .calendar-weekdays,
          .calendar-grid { min-width: 820px; }
          .calendar-day { min-height: 96px; }
          .w-2\\/3, .w-1\\/3 { width: 100% !important; }
        }
        @media (min-width: 901px) and (max-width: 1280px) {
          .q-header { width: min(1120px, calc(100vw - 36px)); }
          .top-header {
            grid-template-columns: minmax(140px, .7fr) auto minmax(180px, .7fr);
            column-gap: 16px;
          }
          .top-brand { min-width: 150px; font-size: 22px; }
          .top-meta { display: none; }
          .dashboard-stage { width: min(1120px, calc(100% - 28px)); }
          .dashboard-feature-tabs { width: 100%; }
          .summary-layout {
            grid-template-columns: minmax(300px, .7fr) minmax(520px, 1.3fr);
            gap: 28px;
            padding: 42px 28px;
          }
          .summary-metrics {
            gap: 16px;
            padding: 24px;
          }
          .plan-list { grid-template-columns: repeat(3, minmax(0, 1fr)); }
          .plan-support-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
          }
          .task-command-layout {
            grid-template-columns: minmax(280px, .72fr) minmax(440px, 1.28fr);
            gap: 18px;
            padding: 24px;
          }
          .task-command-redesign { display: block; padding: 24px; }
          .task-command-main {
            grid-template-columns: 280px minmax(0, 1fr);
            gap: 20px;
          }
          .task-command-heading { width: 280px; min-width: 280px; }
          .task-board-card { padding: 24px 22px 26px; }
          .review-command-card,
          .review-record-card { padding: 24px 22px 26px; }
          .review-command-card {
            margin-top: 18px;
            padding: 16px 22px;
          }
          .review-command-head { gap: 10px; }
          .review-command-actions {
            width: auto;
            justify-content: flex-end;
          }
          .review-course-select {
            width: 280px;
            min-width: 220px;
          }
          .calendar-card { padding: 28px 24px; }
          .calendar-toolbar { column-gap: 12px; }
          .calendar-toolbar > .q-column { min-width: 170px; }
          .calendar-toolbar-meta { justify-content: flex-end; }
          .calendar-summary-strip { flex-wrap: nowrap; justify-content: flex-end; }
          .calendar-summary-item { width: 92px; min-width: 92px; }
          .calendar-day { min-height: 98px; }
          .calendar-task-title { font-size: 11.5px; }
          .plan-item-title { font-size: 14px; }
          .plan-item-detail, .plan-item-meta { font-size: 12px; }
        }
        .top-header .top-tabs .q-tab,
        .top-header .top-tabs .q-tab.bg-primary,
        .top-header .top-tabs .q-tab.text-white {
          background: transparent !important;
          background-color: transparent !important;
          color: #263345 !important;
          box-shadow: none !important;
        }
        .top-header .top-tabs .q-tab.q-tab--active,
        .top-header .top-tabs .q-tab[aria-selected="true"],
        .top-header .top-tabs .q-tab.bg-transparent.q-tab--active,
        .top-header .top-tabs .q-tab.bg-primary.q-tab--active,
        .top-header .top-tabs .q-tab.text-white.q-tab--active,
        .top-header .top-tabs .q-tab.bg-transparent[aria-selected="true"],
        .top-header .top-tabs .q-tab.bg-primary[aria-selected="true"],
        .top-header .top-tabs .q-tab.text-white[aria-selected="true"] {
          background: rgba(255,255,255,.56) !important;
          background-color: rgba(255,255,255,.56) !important;
          color: #0F172A !important;
          box-shadow: 0 10px 24px rgba(15,23,42,.1) !important;
        }
        .top-header .top-tabs .q-tab .q-icon,
        .top-header .top-tabs .q-tab .q-tab__label,
        .top-header .top-tabs .q-tab.bg-primary .q-icon,
        .top-header .top-tabs .q-tab.bg-primary .q-tab__label,
        .top-header .top-tabs .q-tab.text-white .q-icon,
        .top-header .top-tabs .q-tab.text-white .q-tab__label,
        .top-header .top-tabs .q-tab.q-tab--active .q-icon,
        .top-header .top-tabs .q-tab.q-tab--active .q-tab__label,
        .top-header .top-tabs .q-tab[aria-selected="true"] .q-icon,
        .top-header .top-tabs .q-tab[aria-selected="true"] .q-tab__label {
          color: #0F172A !important;
        }
        .top-header .top-tabs .q-tab:not(.q-tab--active):not([aria-selected="true"]) .q-icon,
        .top-header .top-tabs .q-tab:not(.q-tab--active):not([aria-selected="true"]) .q-tab__label {
          color: #263345 !important;
        }
        .top-header .top-tabs .q-tab__indicator,
        .top-header .top-tabs .q-tab::after {
          display: none !important;
          opacity: 0 !important;
          height: 0 !important;
          background: transparent !important;
          border: 0 !important;
        }

        /* Study cockpit cards: calm subject colors with clear task identity. */
        .summary-metrics {
          background:
            radial-gradient(circle at 88% 12%, rgba(20,184,166,.08), transparent 30%),
            linear-gradient(180deg, rgba(255,255,255,.96), rgba(248,251,255,.94));
          border-color: rgba(125,158,198,.24);
          box-shadow: inset 0 1px 0 rgba(255,255,255,.94), 0 12px 34px rgba(37,99,235,.05);
        }
        .metric-card,
        .plan-item,
        .calendar-summary-item {
          background:
            linear-gradient(180deg, rgba(255,255,255,.98) 0%, rgba(248,252,255,.96) 100%);
          border-color: rgba(125,158,198,.28);
          box-shadow: 0 12px 28px rgba(30,64,175,.065);
        }
        .metric-card::after,
        .plan-item::before,
        .calendar-summary-item::before {
          width: 4px;
          background: linear-gradient(180deg, var(--accent-soft, #2F80ED), var(--accent, #14B8A6));
        }
        .metric-card::before,
        .plan-item::after,
        .calendar-summary-item::after {
          content: "";
          position: absolute;
          inset: 0 0 auto 0;
          height: 1px;
          background: linear-gradient(90deg, rgba(255,255,255,.85), rgba(125,211,252,.2), rgba(255,255,255,.7));
          pointer-events: none;
        }
        .accent-blue,
        .plan-tone-blue,
        .calendar-summary-blue {
          --accent: #2F80ED;
          --accent-soft: #60A5FA;
          --plan-accent: #2F80ED;
          --summary-accent: #2F80ED;
        }
        .accent-amber,
        .plan-tone-orange,
        .calendar-summary-amber {
          --accent: #F59E0B;
          --accent-soft: #FBBF24;
          --plan-accent: #F59E0B;
          --summary-accent: #F59E0B;
        }
        .accent-green,
        .plan-tone-green,
        .calendar-summary-green {
          --accent: #14B8A6;
          --accent-soft: #5EEAD4;
          --plan-accent: #14B8A6;
          --summary-accent: #14B8A6;
        }
        .accent-purple,
        .plan-tone-purple {
          --accent: #6366F1;
          --accent-soft: #A5B4FC;
          --plan-accent: #6366F1;
        }
        .plan-tone-red,
        .calendar-summary-red {
          --accent: #EF6A5B;
          --accent-soft: #FCA5A5;
          --plan-accent: #EF6A5B;
          --summary-accent: #EF6A5B;
        }
        .metric-card-clickable:hover,
        .plan-item:hover,
        .calendar-summary-item:hover {
          border-color: rgba(47,128,237,.32);
          box-shadow: 0 16px 34px rgba(30,64,175,.105);
        }
        .metric-title,
        .calendar-summary-label {
          color: #49627F;
        }
        .metric-caption,
        .plan-item-detail,
        .calendar-task-meta,
        .calendar-task-full {
          color: #64748B;
        }
        .metric-value,
        .calendar-summary-value {
          color: #0F172A;
        }
        .plan-period {
          display: inline-flex;
          align-items: center;
          background: color-mix(in srgb, var(--plan-accent, #2F80ED) 12%, #FFFFFF) !important;
          background-color: color-mix(in srgb, var(--plan-accent, #2F80ED) 12%, #FFFFFF) !important;
          color: color-mix(in srgb, var(--plan-accent, #2F80ED) 78%, #0F172A) !important;
          border: 1px solid color-mix(in srgb, var(--plan-accent, #2F80ED) 24%, #FFFFFF) !important;
          box-shadow: inset 0 1px 0 rgba(255,255,255,.72) !important;
          min-width: 48px;
          padding: 3px 10px;
          justify-content: center;
          border-radius: 999px;
          font-weight: 800;
          font-size: 13px;
        }
        .q-badge.plan-period,
        .plan-period.bg-blue,
        .plan-title-row .plan-period {
          background: #EEF4FF !important;
          background-color: #EEF4FF !important;
          color: #1E4F9A !important;
        }
        .plan-check .q-checkbox__bg {
          border-color: #94A3B8;
        }
        .plan-check .q-checkbox__inner--truthy .q-checkbox__bg {
          background: #2563EB;
          border-color: #2563EB;
        }
        .plan-item-meta {
          color: #2F66B8;
        }
        .calendar-summary-strip {
          gap: 10px;
        }
        .calendar-summary-item {
          padding-left: 14px;
        }
        .calendar-course-python,
        .calendar-course-ai,
        .calendar-course-english,
        .calendar-course-other {
          --task-text: #253247;
          --task-meta: #64748B;
        }
        .calendar-course-python {
          --task-bg: #EEF6FF;
          --task-bg-end: #E7F0FF;
          --task-border: #BBD4F6;
          --task-text: #1E4B86;
          --task-meta: #4B73A7;
        }
        .calendar-course-ai {
          --task-bg: #F0F7FF;
          --task-bg-end: #EDEBFF;
          --task-border: #C7D2FE;
          --task-text: #353A82;
          --task-meta: #6266A7;
        }
        .calendar-course-english {
          --task-bg: #F0FDFA;
          --task-bg-end: #E6F7F1;
          --task-border: #A8E6D8;
          --task-text: #0D665E;
          --task-meta: #2F8279;
        }
        .calendar-course-other {
          --task-bg: #FFF7ED;
          --task-bg-end: #FEF3C7;
          --task-border: #FED7AA;
          --task-text: #7C4A14;
          --task-meta: #9A6A2F;
        }
        .calendar-task {
          background: linear-gradient(180deg, var(--task-bg), var(--task-bg-end, #F3F6FA));
          border-color: var(--task-border);
          border-left-width: 4px;
          border-left-color: var(--task-priority);
          box-shadow: 0 5px 12px rgba(30,64,175,.055);
          filter: none;
        }
        .calendar-task:hover {
          filter: none;
          border-color: color-mix(in srgb, var(--task-border) 68%, #2563EB);
          box-shadow: 0 14px 26px rgba(37,99,235,.14);
        }
        .priority-high { --task-priority: #F59E0B; }
        .priority-mid { --task-priority: #2F80ED; }
        .priority-low { --task-priority: #14B8A6; }
        .calendar-task-completed {
          --task-bg: #F1F5F9;
          --task-bg-end: #E8EEF6;
          --task-border: #E2E8F0;
          --task-text: #64748B;
          --task-meta: #94A3B8;
          --task-priority: #CBD5E1;
        }
        .plan-time-period {
          color: var(--time-text, #1E3A8A);
          background:
            linear-gradient(180deg, color-mix(in srgb, var(--time-accent, #2F80ED) 16%, #FFFFFF), color-mix(in srgb, var(--time-accent, #2F80ED) 8%, #FFFFFF));
          border: 1px solid color-mix(in srgb, var(--time-accent, #2F80ED) 30%, #FFFFFF);
          box-shadow: inset 0 1px 0 rgba(255,255,255,.76);
        }
        .plan-time-red {
          --time-accent: #EF6A5B;
          --time-text: #9F2F2B;
        }
        .plan-time-orange {
          --time-accent: #F59E0B;
          --time-text: #8A5200;
        }
        .plan-time-purple {
          --time-accent: #6366F1;
          --time-text: #3730A3;
        }
        </style>
        <script>
        window.studypilotMotion = {
          enter() {
            document.querySelectorAll(".animate-hero, .animate-card").forEach((element) => {
              element.style.opacity = "1";
              element.style.visibility = "visible";
              element.style.transform = "translateY(0)";
            });
          },
          sources() {
            document.querySelectorAll(".animate-source").forEach((element) => {
              element.style.opacity = "1";
              element.style.visibility = "visible";
              element.style.transform = "translateY(0)";
            });
          }
        };
        window.studypilotSpeech = {
          play(text) {
            const spoken = String(text || "").replace(/\\s+/g, " ").trim();
            if (!spoken) {
              alert("请先提问，得到回答后再播放音频概览。");
              return;
            }
            if (!("speechSynthesis" in window) || !window.SpeechSynthesisUtterance) {
              alert("当前浏览器不支持语音朗读。");
              return;
            }
            window.speechSynthesis.cancel();
            const utterance = new SpeechSynthesisUtterance(spoken);
            utterance.lang = /[\u4e00-\u9fff]/.test(spoken) ? "zh-CN" : "en-US";
            utterance.rate = 0.95;
            utterance.pitch = 1;
            const voices = window.speechSynthesis.getVoices();
            const preferred = voices.find((voice) => /zh|Chinese|Mandarin|普通话|中文/i.test(`${voice.lang} ${voice.name}`));
            if (preferred) utterance.voice = preferred;
            window.speechSynthesis.speak(utterance);
          }
        };
        window.studypilotTabs = {
          show(label) {
            const aliases = { plan: "今日学习计划" };
            const target = aliases[label] || label;
            const topLabels = new Set(["学习驾驶舱", "AI 问笔记", "任务管理", "复习训练"]);
            const scope = topLabels.has(target) ? ".top-tabs .q-tab" : ".dashboard-feature-tabs .q-tab";
            const tab = Array.from(document.querySelectorAll(scope)).find((element) => {
              return element.textContent.replace(/\\s+/g, "").includes(target.replace(/\\s+/g, ""));
            });
            if (tab) {
              if (!topLabels.has(target)) {
                document.querySelector(".dashboard-feature-tabs")?.classList.add("dashboard-feature-tabs-user-active");
              }
              tab.click();
              window.setTimeout(() => {
                const stage = document.querySelector(".dashboard-stage");
                if (!stage) return;
                const header = document.querySelector(".q-header");
                const headerOffset = header ? header.getBoundingClientRect().height + 22 : 96;
                const targetTop = window.scrollY + stage.getBoundingClientRect().top - headerOffset;
                window.scrollTo({ top: Math.max(0, targetTop), behavior: "smooth" });
              }, 80);
            }
          }
        };
        window.studypilotTabs.bindDashboardActivation = () => {
          const tabs = document.querySelector(".dashboard-feature-tabs");
          if (!tabs || tabs.dataset.activationBound === "true") return;
          tabs.dataset.activationBound = "true";
          tabs.addEventListener("click", (event) => {
            if (event.target.closest(".q-tab")) {
              tabs.classList.add("dashboard-feature-tabs-user-active");
            }
          });
        };
        window.studypilotTabs.syncTopHeader = () => {
          const header = document.querySelector(".top-header");
          const activeTab = document.querySelector(".top-tabs .q-tab--active, .top-tabs .q-tab[aria-selected='true']");
          const isAiNotes = !!activeTab && activeTab.textContent.replace(/\\s+/g, "").includes("AI问笔记");
          header?.classList.toggle("ai-notes-active", isAiNotes);
        };
        window.studypilotMineradio = {
          async open(element) {
            const targetUrl = element?.dataset?.mineradioUrl || "http://127.0.0.1:3100";
            let opened = null;
            try {
              opened = window.open("about:blank", "_blank");
              const response = await fetch("/api/mineradio/start", { cache: "no-store" });
              const payload = await response.json();
              if (!payload.ok) {
                if (opened) opened.close();
                alert(payload.message || "Mineradio 暂时无法启动。");
                return;
              }
              if (opened) {
                opened.location.href = payload.url || targetUrl;
              } else {
                window.location.href = payload.url || targetUrl;
              }
            } catch (error) {
              if (opened) {
                opened.location.href = targetUrl;
              } else {
                window.location.href = targetUrl;
              }
            }
          },
          bindFloat() {
            const element = document.querySelector("[data-mineradio-float]");
            if (!element || element.dataset.dragBound === "true") return;
            element.dataset.dragBound = "true";
            const storageKey = "studypilot.mineradioFloat";
            const clamp = (value, min, max) => Math.min(Math.max(value, min), max);
            const place = (left, top) => {
              const maxLeft = Math.max(8, window.innerWidth - element.offsetWidth - 8);
              const maxTop = Math.max(72, window.innerHeight - element.offsetHeight - 8);
              element.style.left = `${clamp(left, 8, maxLeft)}px`;
              element.style.top = `${clamp(top, 72, maxTop)}px`;
              element.style.right = "auto";
              element.style.bottom = "auto";
            };
            try {
              const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
              if (saved && Number.isFinite(saved.left) && Number.isFinite(saved.top)) {
                place(saved.left, saved.top);
              }
            } catch (error) {}

            let state = null;
            element.addEventListener("pointerdown", (event) => {
              if (event.button !== undefined && event.button !== 0) return;
              const rect = element.getBoundingClientRect();
              state = {
                pointerId: event.pointerId,
                startX: event.clientX,
                startY: event.clientY,
                left: rect.left,
                top: rect.top,
                dragged: false,
              };
              element.setPointerCapture?.(event.pointerId);
              element.classList.add("is-dragging");
            });
            element.addEventListener("pointermove", (event) => {
              if (!state || state.pointerId !== event.pointerId) return;
              const dx = event.clientX - state.startX;
              const dy = event.clientY - state.startY;
              if (Math.abs(dx) + Math.abs(dy) > 6) state.dragged = true;
              place(state.left + dx, state.top + dy);
            });
            const finish = (event) => {
              if (!state || state.pointerId !== event.pointerId) return;
              const wasDragged = state.dragged;
              state = null;
              element.classList.remove("is-dragging");
              const rect = element.getBoundingClientRect();
              localStorage.setItem(storageKey, JSON.stringify({ left: rect.left, top: rect.top }));
              if (wasDragged) {
                element.dataset.justDragged = "true";
                window.setTimeout(() => { element.dataset.justDragged = "false"; }, 180);
              }
            };
            element.addEventListener("pointerup", finish);
            element.addEventListener("pointercancel", finish);
            element.addEventListener("click", (event) => {
              if (element.dataset.justDragged === "true") {
                event.preventDefault();
                event.stopPropagation();
                return;
              }
              event.preventDefault();
              window.studypilotMineradio.open(element);
            }, true);
            window.addEventListener("resize", () => {
              const rect = element.getBoundingClientRect();
              place(rect.left, rect.top);
            });
          }
        };
        window.addEventListener("load", () => window.studypilotMotion.enter());
        window.addEventListener("load", () => {
          window.studypilotTabs.bindDashboardActivation();
          window.studypilotTabs.syncTopHeader();
          window.studypilotMineradio.bindFloat();
          window.setTimeout(() => window.studypilotTabs.bindDashboardActivation(), 300);
          window.setTimeout(() => window.studypilotTabs.syncTopHeader(), 300);
          window.setTimeout(() => window.studypilotMineradio.bindFloat(), 300);
        });
        document.addEventListener("DOMContentLoaded", () => {
          window.studypilotMotion.enter();
          window.studypilotTabs.bindDashboardActivation();
          window.studypilotTabs.syncTopHeader();
          window.studypilotMineradio.bindFloat();
          window.setTimeout(() => window.studypilotTabs.bindDashboardActivation(), 300);
          window.setTimeout(() => window.studypilotTabs.syncTopHeader(), 300);
          window.setTimeout(() => window.studypilotMineradio.bindFloat(), 300);
        });
        </script>
        """,
        shared=True,
    )

    def show_ai_notes() -> None:
        main_panels.set_value("AI 问笔记")
        ui.run_javascript(
            "window.scrollTo({ top: 0, behavior: 'smooth' });"
            "window.setTimeout(() => window.studypilotTabs?.syncTopHeader?.(), 50);"
        )

    with ui.header().classes("top-header"):
        ui.label("StudyPilot").classes("top-brand")
        with ui.tabs().props("active-color=dark indicator-color=transparent").classes("top-tabs").on(
            "click",
            lambda: ui.timer(0.05, lambda: ui.run_javascript("window.studypilotTabs?.syncTopHeader?.();"), once=True),
        ) as tabs:
            ui.tab("学习驾驶舱", icon="dashboard")
            ui.tab("AI 问笔记", icon="chat")
            ui.tab("任务管理", icon="checklist")
            ui.tab("复习训练", icon="school")
        with ui.row().classes("top-right items-center justify-end"):
            ui.button("开始使用", icon="arrow_forward", on_click=show_ai_notes).props("color=dark").classes("top-action")

    with ui.column().classes("app-shell"):
        with ui.tab_panels(tabs, value="学习驾驶舱").classes("main-tab-panels w-full") as main_panels:
            with ui.tab_panel("学习驾驶舱"):
                render_dashboard(main_panels)
            with ui.tab_panel("AI 问笔记"):
                render_qa(main_panels)
            with ui.tab_panel("任务管理"):
                render_tasks()
            with ui.tab_panel("复习训练"):
                render_review()

    port = int(os.getenv("NICEGUI_PORT") or os.getenv("PORT", "8080"))
    ui.run(title="StudyPilot 个人学习助手", host="0.0.0.0", port=port, reload=False)


if __name__ in {"__main__", "__mp_main__"}:
    main()
