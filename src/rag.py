from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any

from tools import list_notes
from llm_client import chat_text
from config import VECTOR_DIR
from llm_client import has_api_key


def tokenize(text: str) -> list[str]:
    english = re.findall(r"[a-zA-Z]+", text.lower())
    chinese = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    return english + chinese


def score_text(query: str, text: str) -> float:
    query_tokens = tokenize(query)
    text_tokens = tokenize(text)
    if not query_tokens or not text_tokens:
        return 0.0
    q = Counter(query_tokens)
    t = Counter(text_tokens)
    overlap = sum(min(q[token], t[token]) for token in q)
    base = overlap / math.sqrt(len(query_tokens) * len(text_tokens))
    lower_query = query.lower()
    lower_text = text.lower()
    exact_bonus = 0.0
    for token in set(query_tokens):
        if len(token) > 1 and token in lower_text:
            exact_bonus += 0.05
    if lower_query.strip("？? ") in lower_text:
        exact_bonus += 0.2
    return base + exact_bonus


def search_notes(question: str, course: str | None = None, top_k: int = 3) -> list[dict[str, Any]]:
    keyword_results = search_notes_keyword(question, course, top_k=10)
    chroma_results = search_notes_chroma(question, course, top_k=10)
    merged: dict[str, dict[str, Any]] = {}
    for item in chroma_results:
        key = str(item.get("id") or item.get("title"))
        merged[key] = {**item, "score": item.get("score", 0) * 0.6}
    for item in keyword_results:
        key = str(item.get("id") or item.get("title"))
        if key in merged:
            merged[key]["score"] = round(merged[key].get("score", 0) + item.get("score", 0) * 1.2, 3)
        else:
            merged[key] = item
    results = sorted(merged.values(), key=lambda item: item.get("score", 0), reverse=True)
    return [item for item in results[:top_k] if item.get("score", 0) > 0]


def search_notes_keyword(question: str, course: str | None = None, top_k: int = 3) -> list[dict[str, Any]]:
    notes = list_notes(course)
    scored = []
    for note in notes:
        haystack = f"{note.get('course', '')} {note.get('title', '')} {note.get('content', '')} {note.get('tags', '')}"
        score = score_text(question, haystack)
        title_score = score_text(question, note.get("title", ""))
        tag_score = score_text(question, note.get("tags", ""))
        if title_score:
            score += title_score * 0.9
        if tag_score:
            score += tag_score * 0.35
        scored.append({**note, "score": round(score, 3), "snippet": note.get("content", "")[:120]})
    scored.sort(key=lambda item: item["score"], reverse=True)
    return [item for item in scored[:top_k] if item["score"] > 0]


def search_notes_chroma(question: str, course: str | None = None, top_k: int = 3) -> list[dict[str, Any]]:
    if not VECTOR_DIR.exists():
        return []
    try:
        import chromadb
    except ModuleNotFoundError:
        return []

    try:
        client = chromadb.PersistentClient(path=str(VECTOR_DIR))
        collection = client.get_or_create_collection("study_notes")
        where = None if not course or course == "全部课程" else {"course": course}
        result = collection.query(query_texts=[question], n_results=top_k, where=where)
        docs = result.get("documents", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        distances = result.get("distances", [[]])[0]
        items: list[dict[str, Any]] = []
        for doc, metadata, distance in zip(docs, metadatas, distances):
            score = max(0.0, 1.0 - float(distance))
            items.append(
                {
                    "id": metadata.get("note_id"),
                    "course": metadata.get("course", ""),
                    "title": metadata.get("title", ""),
                    "content": doc,
                    "tags": "",
                    "score": round(score, 3),
                    "snippet": doc[:120],
                }
            )
        return items
    except Exception:
        return []


def confidence_from_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "来源不足"
    top = sources[0]["score"]
    if top >= 0.18:
        return "高可信"
    if top >= 0.08:
        return "部分可信"
    return "来源不足"


def has_note_match(sources: list[dict[str, Any]], question: str) -> bool:
    if not sources:
        return False
    top = sources[0]
    terms = [term for term in query_terms(question) if len(term) >= 2 or re.search(r"[A-Za-z]{2,}", term)]
    score = top.get("score", 0)
    if not terms:
        return False
    haystack = f"{top.get('title', '')} {top.get('content', '')} {top.get('tags', '')}".lower()
    exact_hits = 0
    for term in terms:
        if term.lower() in haystack:
            exact_hits += 1
    if exact_hits == 0:
        return False
    if score >= 0.12:
        return True
    return exact_hits >= 2


def query_terms(question: str, limit: int = 6) -> list[str]:
    cleaned = question.strip()
    cleaned = re.sub(r"^(什么是|请问|介绍一下|解释一下|说明一下)", "", cleaned)
    cleaned = re.sub(r"(是什么|有哪些|为什么|怎么做|如何做|如何|吗|呢|？|\?)$", "", cleaned)
    terms = re.findall(r"[A-Za-z][A-Za-z0-9+\-]{1,}|[\u4e00-\u9fff]{2,}", cleaned)
    if not terms:
        terms = [token for token in tokenize(question) if len(token.strip()) > 1]
    stop_terms = {"什么是", "是什么", "为什么", "如何", "怎么", "哪些", "请问", "介绍", "说明", "解释"}
    unique: list[str] = []
    for term in terms:
        if term in stop_terms:
            continue
        if term not in unique:
            unique.append(term)
        if len(unique) == limit:
            break
    return unique


def offline_ai_search_summary(question: str, api_configured: bool = False) -> str:
    focus = "、".join(query_terms(question)) or question
    lower = question.lower()
    if "聊天机器人" in question or "chatbot" in lower:
        return (
            "聊天机器人是一种能够通过文字、语音或多模态方式与用户对话的软件系统。"
            "它的核心作用是理解用户输入、判断用户意图，然后给出回答、建议、操作指令或进一步追问。\n\n"
            "从工作方式看，聊天机器人通常包括三个部分：输入理解、信息处理和答案生成。"
            "早期聊天机器人多依赖固定规则和关键词匹配，只能回答预设问题；现在的聊天机器人更多基于大语言模型，"
            "可以理解更复杂的自然语言，并生成更接近真人表达的回答。\n\n"
            "它的应用场景很多，例如客服问答、学习辅导、资料检索、日程管理、医疗咨询预筛、编程助手和企业内部知识库问答。"
            "如果接入个人笔记或企业文档，它还可以变成一个基于资料回答问题的助手。\n\n"
            "需要注意的是，聊天机器人并不等于绝对正确。它可能理解错问题、引用不完整，或者在缺少资料时生成看似合理但不准确的内容。"
            "因此，重要问题最好结合来源、上下文和人工判断进行核查。"
        )
    if "数字健康" in question:
        return (
            "数字健康是指用数字技术改善健康管理、医疗服务、疾病预防和医学研究的一类理念与实践。"
            "它不是单一产品，而是一个覆盖医疗、公共卫生、个人健康管理和数据分析的综合领域。\n\n"
            "常见的数字健康技术包括可穿戴设备、移动健康 App、远程医疗、电子健康档案、AI 辅助诊断、医学影像分析、"
            "慢病管理系统和医院信息化平台。它们的共同目标是让健康数据更容易被记录、分析和使用，从而提高诊疗效率和健康管理质量。\n\n"
            "举例来说，智能手表可以记录心率和睡眠，远程问诊可以让患者不用到医院也能获得初步医疗服务，"
            "AI 医疗系统可以帮助医生从大量影像或病历中发现异常线索。\n\n"
            "数字健康的价值在于提高医疗可及性、降低重复检查和沟通成本、帮助个人更早发现健康风险。"
            "但它也面临隐私保护、数据安全、算法偏差、医疗责任边界和监管合规等问题。"
        )
    if focus.lower() == "ai" or "人工智能" in question or re.search(r"\bAI\b", question, re.IGNORECASE):
        return (
            "AI，也就是人工智能，是让计算机系统模拟人类理解、推理、生成和决策能力的一类技术。"
            "它的核心目标不是让机器“像人一样有意识”，而是让机器能够根据数据、规则和模型完成原本需要人类智能参与的任务。\n\n"
            "从能力上看，AI 可以识别信息、理解语义、生成内容、发现规律、做出预测，并在一定范围内辅助决策。"
            "常见应用包括聊天机器人、机器翻译、语音识别、图像识别、推荐系统、自动驾驶、医学影像分析、代码助手和学习助手。\n\n"
            "如果简单分类，可以把 AI 理解为三层：基础算法和模型、训练所需的数据、面向具体任务的应用。"
            "现在最常见的大语言模型属于生成式 AI，它擅长处理文字、代码和多模态信息，但仍需要事实核查，不能把所有输出都当成绝对正确。"
        )
    if "什么是" in question or lower.startswith("what is") or lower.startswith("what are"):
        return (
            f"{focus}可以从定义、组成、作用和应用场景四个方面理解。\n\n"
            f"首先，{focus}通常指围绕某一类问题形成的概念、方法、工具或技术体系。"
            "它不只是一个名词，而是包含目标、使用对象、关键方法和实际场景的一整套理解框架。\n\n"
            "其次，要理解它为什么重要，需要看它解决了什么问题。例如它是否提高效率、降低成本、改善体验、帮助决策，"
            "或者让原本复杂的流程变得更容易执行。\n\n"
            "再次，要结合例子理解。一个概念真正有价值，往往体现在具体场景中：谁在使用它、使用它完成什么任务、"
            "它相比传统方式有什么改进。\n\n"
            "最后，也要注意它的限制。任何概念或技术都有适用边界，不能只看优点，还要看风险、成本、准确性和使用条件。"
        )
    if "流程" in question or "步骤" in question or "如何" in question or lower.startswith("how"):
        return (
            f"这个问题可以按流程来理解：先明确目标和输入，再拆分关键步骤，"
            "然后看每一步需要什么工具、数据或判断标准，最后用输出结果验证是否达成目标。"
            "学习时最好把流程画成输入、处理、输出三段。"
        )
    if "区别" in question or "对比" in question or "比较" in question:
        return (
            f"对比类问题建议从定义、使用场景、优点、限制和典型例子五个维度分析。"
            "先判断它们解决的是不是同一个问题，再比较方法和效果，而不是只看名称相似。"
        )
    return (
        f"关于「{focus}」，可以先抓住三个层面：它是什么、为什么重要、如何应用。"
        "回答时先给核心结论，再补充关键背景和例子；如果需要深入学习，再回到资料中核查事实、日期和具体表述。"
    )


def ai_search_summary(question: str) -> tuple[str, str]:
    prompt = (
        "请像 ChatGPT 一样给用户一个完整、清晰、有层次的回答。"
        "不要只回答几句话。请根据问题自然组织内容，尽量包含："
        "核心结论、概念解释、关键要点、具体例子或应用场景、容易误解的地方、最后小结。"
        "如果问题适合步骤说明，请给出分步骤解释；如果适合对比，请给出对比维度。"
        "不要写“我已搜索”“接口未返回”“离线检索”“需要接入 API”等诊断性文字。"
        "不要编造具体网页链接。回答要有信息量，但保持易读。\n\n"
        f"用户问题：{question}"
    )
    text = chat_text(prompt, "你是一个严谨、清晰的通用问答助手。")
    if text:
        return text, "AI 搜索摘要"
    return offline_ai_search_summary(question, has_api_key()), "离线 AI 搜索摘要"


def answer_from_sources(question: str, sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "你的个人笔记库中暂时没有找到足够依据。建议先补充相关课程笔记，或换一个更具体的问题。"
    context = "\n".join(
        f"来源{i + 1}：课程={source.get('course')}，标题={source.get('title')}，内容={source.get('content')}"
        for i, source in enumerate(sources)
    )
    llm_answer = chat_text(
        f"请严格根据以下个人笔记回答问题。回答要包含：简明答案、详细解释、下一步复习建议。\n\n{context}\n\n问题：{question}"
    )
    if llm_answer:
        return llm_answer
    top = sources[0]
    title = top.get("title", "相关笔记")
    content = top.get("content", "")
    return (
        f"根据你的《{top.get('course', '')}》课程笔记「{title}」，可以这样理解：{content}\n\n"
        f"简要回答：这个问题的关键是先回到你的笔记依据，再结合例子理解。你可以继续追问“用例子解释”或“出题考我”。"
    )


def compared_answer(question: str, sources: list[dict[str, Any]], search_text: str, search_label: str) -> str:
    matched = has_note_match(sources, question)
    note_status = "已在你的笔记中找到相关内容" if matched else "在你的笔记中没查询到相关的内容"
    if not matched:
        return (
            f"{search_text}\n\n"
            "---\n\n"
            f"**你的笔记中相关内容：{note_status}。**\n\n"
            "所以上面的回答主要来自 AI 的通用知识。"
            "如果你希望后续回答能引用你的课程资料，可以把相关内容上传到左侧来源面板。"
        )

    context = "\n".join(
        f"来源{i + 1}：课程={source.get('course')}，标题={source.get('title')}，内容={source.get('content')}"
        for i, source in enumerate(sources)
    )
    llm_answer = chat_text(
        "请像 ChatGPT 一样回答用户问题。"
        "必须先给出完整的通用回答，内容要充分，不要只写摘要。"
        "回答应包含核心结论、详细解释、例子/应用场景、注意事项或常见误区、最后小结。"
        "然后再用“你的笔记中相关内容”这一段补充个人笔记依据。"
        "不要输出编号大标题“1.先进行AI搜索/2.再检索笔记”。"
        "不要写接口、离线、API、搜索失败等诊断性文字。"
        "如果通用知识和笔记存在差异，请用自然语言指出。"
        "排版像 ChatGPT，使用清晰小标题、短段落和少量项目符号。\n\n"
        f"用户问题：{question}\n\n"
        f"AI 通用回答草稿：{search_text}\n\n"
        f"个人笔记来源：\n{context}"
    )
    if llm_answer:
        return llm_answer

    note_answer = answer_from_sources(question, sources)
    return (
        f"{search_text}\n\n"
        "---\n\n"
        f"**你的笔记中相关内容：{note_status}。**\n\n"
        f"{note_answer}"
    )


def ask_note(question: str, course: str | None = None, top_k: int = 3) -> dict[str, Any]:
    sources = search_notes(question, course, top_k)
    confidence = confidence_from_sources(sources)
    search_text, search_label = ai_search_summary(question)
    matched = has_note_match(sources, question)
    return {
        "answer": compared_answer(question, sources if matched else [], search_text, search_label),
        "confidence": confidence if matched else "笔记未命中",
        "sources": sources if matched else [],
        "ai_search": search_text,
        "search_label": search_label,
        "note_match": matched,
        "api_available": has_api_key(),
    }
