# StudyPilot 个人学习助手 API 文档

## 1. 文档定位

API 文档定义前端页面与后端功能模块的协作契约。当前项目以 Python 内部函数为主，未来可平滑迁移到 FastAPI。

## 2. 通用响应格式

```json
{
  "success": true,
  "data": {},
  "message": "ok",
  "error": null
}
```

## 3. Intent API

### parse_intent

请求：

```json
{
  "user_input": "周五前完成 Python 作业"
}
```

响应：

```json
{
  "success": true,
  "data": {
    "intent": "add_todo",
    "course": "Python",
    "task": "完成作业",
    "deadline": "周五",
    "priority": "高"
  }
}
```

## 4. Task API

### add_todo

请求：

```json
{
  "course": "Python",
  "task": "完成列表推导式作业",
  "deadline": "2026-06-26",
  "priority": "高"
}
```

响应：

```json
{
  "success": true,
  "data": {
    "id": 12,
    "status": "未完成"
  },
  "message": "任务添加成功"
}
```

### list_todos

请求：

```json
{
  "course": "Python",
  "status": "未完成"
}
```

响应：

```json
{
  "success": true,
  "data": [
    {
      "id": 12,
      "course": "Python",
      "task": "完成列表推导式作业",
      "deadline": "2026-06-26",
      "status": "未完成",
      "priority": "高"
    }
  ]
}
```

### complete_todo

请求：

```json
{
  "id": 12
}
```

响应：

```json
{
  "success": true,
  "message": "任务已完成"
}
```

## 5. RAG API

### ask_note

请求：

```json
{
  "question": "什么是列表推导式？",
  "course": "Python",
  "top_k": 3
}
```

响应：

```json
{
  "success": true,
  "data": {
    "answer": "列表推导式是一种用简洁语法生成列表的方法...",
    "confidence": "高可信",
    "sources": [
      {
        "note_id": 1,
        "course": "Python",
        "title": "列表推导式",
        "snippet": "[x*2 for x in range(10)] 生成新列表",
        "score": 0.86
      }
    ]
  }
}
```

### rebuild_vector_index

请求：

```json
{}
```

响应：

```json
{
  "success": true,
  "data": {
    "note_count": 20,
    "chunk_count": 35
  },
  "message": "向量库重建完成"
}
```

## 6. Plan API

### make_today_plan

请求：

```json
{
  "available_hours": 3
}
```

响应：

```json
{
  "success": true,
  "data": {
    "priority_task": "完成 Python 列表推导式作业",
    "plan": [
      {"time": "09:00-10:30", "action": "完成 Python 作业"},
      {"time": "14:00-14:30", "action": "复习英语 Unit5"},
      {"time": "19:30-20:00", "action": "RAG 笔记测验"}
    ]
  }
}
```

## 7. Quiz API

### generate_quiz

请求：

```json
{
  "course": "Python",
  "count": 5,
  "question_type": "short_answer"
}
```

响应：

```json
{
  "success": true,
  "data": [
    {
      "question": "列表推导式的基本语法是什么？",
      "answer": "[表达式 for 变量 in 可迭代对象]",
      "note_id": 1,
      "chunk_id": "note-1-0"
    }
  ]
}
```

### submit_answer

请求：

```json
{
  "question_id": 1,
  "user_answer": "用一行代码生成列表"
}
```

响应：

```json
{
  "success": true,
  "data": {
    "is_correct": true,
    "explanation": "回答基本正确，但可以补充语法结构。"
  }
}
```

## 8. Report API

### generate_report_materials

请求：

```json
{
  "date_range": "this_week"
}
```

响应：

```json
{
  "success": true,
  "data": {
    "daily_logs": "...",
    "reflection": "...",
    "summary": "...",
    "missing_screenshots": ["AI 问笔记来源引用截图"]
  }
}
```

## 9. Health API

### check_project_health

响应：

```json
{
  "success": true,
  "data": {
    "sqlite": "pass",
    "vector_db": "pass",
    "api_key": "warn",
    "dependencies": "pass",
    "demo_data": "pass"
  }
}
```

