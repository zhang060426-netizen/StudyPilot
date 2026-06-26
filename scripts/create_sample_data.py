from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


def create_todos() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"id": 1, "course": "Python", "task": "完成列表推导式练习", "deadline": "2026-06-24", "status": "未完成", "priority": "高"},
            {"id": 2, "course": "Python", "task": "整理函数参数笔记", "deadline": "2026-06-25", "status": "未完成", "priority": "中"},
            {"id": 3, "course": "Python", "task": "复习文件读写代码", "deadline": "2026-06-27", "status": "未完成", "priority": "中"},
            {"id": 4, "course": "Python", "task": "完成 pandas 表格处理作业", "deadline": "2026-06-28", "status": "未完成", "priority": "高"},
            {"id": 5, "course": "Python", "task": "复盘异常处理示例", "deadline": "2026-06-30", "status": "已完成", "priority": "低"},
            {"id": 6, "course": "AI应用开发", "task": "完成 RAG 流程图", "deadline": "2026-06-24", "status": "未完成", "priority": "高"},
            {"id": 7, "course": "AI应用开发", "task": "实现 Chroma 向量库构建", "deadline": "2026-06-25", "status": "未完成", "priority": "高"},
            {"id": 8, "course": "AI应用开发", "task": "整理 Agent 路由代码", "deadline": "2026-06-26", "status": "未完成", "priority": "中"},
            {"id": 9, "course": "AI应用开发", "task": "准备 5 分钟演示脚本", "deadline": "2026-06-29", "status": "未完成", "priority": "中"},
            {"id": 10, "course": "AI应用开发", "task": "整理学习周报演示截图", "deadline": "2026-06-30", "status": "未完成", "priority": "低"},
            {"id": 11, "course": "英语", "task": "背诵 Unit5 单词", "deadline": "2026-06-23", "status": "未完成", "priority": "高"},
            {"id": 12, "course": "英语", "task": "完成虚拟语气练习", "deadline": "2026-06-26", "status": "未完成", "priority": "中"},
            {"id": 13, "course": "英语", "task": "整理阅读理解错题", "deadline": "2026-06-28", "status": "未完成", "priority": "中"},
            {"id": 14, "course": "英语", "task": "复习作文连接词", "deadline": "2026-06-29", "status": "未完成", "priority": "低"},
            {"id": 15, "course": "英语", "task": "听力材料跟读 20 分钟", "deadline": "2026-06-30", "status": "已完成", "priority": "低"},
        ]
    )


def create_notes() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": 1,
                "course": "Python",
                "title": "Python 基础语法与变量",
                "content": "Python 程序由语句、表达式、变量和函数组成。变量不需要提前声明类型，赋值时由解释器根据对象自动绑定类型，例如 name = 'StudyPilot'、score = 95。常见基础类型包括 int、float、str、bool、list、dict、tuple 和 set。学习时要区分变量名与对象本身：变量名只是引用，对象才保存真实数据。命名建议使用小写字母和下划线，表达清楚含义。调试变量时可以使用 type() 查看类型，使用 print() 或断点观察当前值。基础语法的关键是缩进，if、for、while、def、class 后面的代码块必须保持一致缩进，否则会出现 IndentationError。",
                "tags": "语法,变量,类型,缩进",
            },
            {
                "id": 2,
                "course": "Python",
                "title": "列表、字典与推导式",
                "content": "列表用于保存有序可变数据，字典用于保存键值对。列表常用 append、extend、pop、sort、切片等操作；字典常用 get、keys、values、items 和 update。列表推导式可以用简洁语法生成新列表，基本形式是 [表达式 for 变量 in 可迭代对象 if 条件]，例如 [x * 2 for x in range(10) if x % 2 == 0]。字典推导式形式是 {key: value for item in items}。推导式适合表达简单转换和过滤，如果逻辑超过两层循环或包含复杂判断，应改用普通 for 循环以提高可读性。做题时先判断数据结构：需要按顺序访问时用列表，需要按名称、编号或标签查询时用字典。",
                "tags": "列表,字典,推导式,数据结构",
            },
            {
                "id": 3,
                "course": "Python",
                "title": "函数参数与返回值",
                "content": "函数用于把可复用逻辑封装成一个有名字的代码块。Python 函数参数包括位置参数、默认参数、关键字参数和可变参数。默认参数必须放在非默认参数之后，使用 *args 接收多个位置参数，使用 **kwargs 接收多个关键字参数。函数可以通过 return 返回结果；没有 return 时默认返回 None。设计函数时要让函数只做一件清晰的事，输入由参数提供，输出由返回值表达，尽量避免依赖全局变量。调试函数时先确认参数实际传入的值，再确认每个分支是否都返回了期望结果。常见错误包括默认可变参数、参数顺序错误、忘记 return、把打印结果误当返回值。",
                "tags": "函数,参数,返回值,封装",
            },
            {
                "id": 4,
                "course": "Python",
                "title": "文件读写与异常处理",
                "content": "文件读写通常使用 with open(path, mode, encoding='utf-8') as f 的形式。with 会在代码块结束后自动关闭文件，read 用于读取全部内容，readline 读取一行，readlines 读取多行列表，write 用于写入字符串。mode 常见值包括 r、w、a、rb、wb，其中 w 会覆盖原文件，a 会追加内容。异常处理使用 try、except、else、finally。try 中放可能出错的代码，except 捕获异常，else 在没有异常时执行，finally 无论是否出错都会执行。读写文件时常见异常包括 FileNotFoundError、PermissionError 和 UnicodeDecodeError。可靠程序应在文件路径、编码、空文件和异常提示上做处理。",
                "tags": "文件,异常,with,编码",
            },
            {
                "id": 5,
                "course": "Python",
                "title": "pandas 表格处理",
                "content": "pandas 适合处理 Excel、CSV 和二维表格数据。核心结构 DataFrame 类似带行列标签的表格，Series 类似单列数据。常用函数包括 read_excel、read_csv、to_excel、fillna、dropna、groupby、merge、sort_values 和 to_sql。数据清洗一般包括读取数据、检查列名、处理缺失值、转换类型、过滤异常行、分组统计和导出结果。groupby 常用于按课程、状态、日期等字段聚合，例如统计每门课任务数量。处理学习助手项目时，可以用 pandas 从 notes.xlsx 和 todo.xlsx 读取数据，再写入 SQLite。注意 Excel 日期可能被解析为 Timestamp，保存前需要转为字符串。",
                "tags": "pandas,DataFrame,Excel,数据处理",
            },
            {
                "id": 6,
                "course": "AI应用开发",
                "title": "RAG 基本流程",
                "content": "RAG 是 Retrieval-Augmented Generation，中文常叫检索增强生成。它把外部知识库接入大模型回答流程，核心步骤包括资料收集、清洗、文本切分、Embedding 向量化、写入向量库、根据问题检索相关片段、把片段拼接为上下文、再让大模型基于上下文生成答案。RAG 的价值是减少幻觉、支持私有资料问答、让答案能展示来源。个人学习助手中的 AI 问笔记就是一个 RAG 场景：用户提问知识点，系统先从个人课程笔记中找相似内容，再给出可信度、来源和命中片段。RAG 质量取决于笔记内容、切分粒度、Embedding 模型、检索策略和提示词约束。",
                "tags": "RAG,检索增强生成,LLM,流程",
            },
            {
                "id": 7,
                "course": "AI应用开发",
                "title": "Embedding 与向量检索",
                "content": "Embedding 模型把文本转换成向量，相似语义的文本在向量空间中距离更近。向量检索通常使用余弦相似度、内积或欧氏距离判断文本相关性。与关键词检索相比，向量检索能找到表达不同但语义相近的内容，例如“截止日期”和“deadline”可能被映射到较近位置。构建笔记问答系统时，可以把每条笔记或每个切分片段转换为向量，并保存课程、标题、note_id 等 metadata。提问时先把问题向量化，再检索 top_k 个最相近片段。实际项目中常结合关键词检索和向量检索，避免纯向量模型漏掉明确术语、编号或专有名词。",
                "tags": "Embedding,向量,相似度,检索",
            },
            {
                "id": 8,
                "course": "AI应用开发",
                "title": "Chroma 向量库",
                "content": "Chroma 是常用的本地向量数据库，可以保存文本、向量和 metadata，并提供持久化目录。构建个人笔记 RAG 时，可以把课程 course、标题 title、note_id 作为 metadata 保存，便于按课程过滤来源。典型流程是创建 PersistentClient，获取 collection，然后 add documents、ids 和 metadatas。查询时使用 collection.query，传入 query_texts、n_results 和 where 条件。为了保证结果新鲜，笔记新增、删除或更新后应重建或增量更新向量索引。若 Chroma 不可用，可以降级为关键词检索。",
                "tags": "Chroma,向量库,metadata,持久化",
            },
            {
                "id": 9,
                "course": "AI应用开发",
                "title": "Agent 路由",
                "content": "智能体路由是指主智能体根据用户输入判断意图，再选择合适工具、函数或子智能体执行。个人学习助手中常见意图包括 add_todo、list_todo、complete_todo、query_note、make_plan 和 generate_quiz。例如用户说“周五前完成 Python 作业”，系统应调用 TaskAgent 添加任务；用户问“什么是 RAG”，系统应调用 NoteAgent 检索笔记；用户说“帮我安排今天学习计划”，系统应调用 PlanAgent。路由实现可以从规则开始，例如关键词、课程名和日期表达式；后续可接入大模型做意图识别。好的 Agent 设计要有清晰边界：主智能体负责分发，专门智能体负责具体任务，工具函数负责数据库读写。",
                "tags": "Agent,路由,意图识别,工具调用",
            },
            {
                "id": 10,
                "course": "AI应用开发",
                "title": "提示词、可信度与学术诚信护栏",
                "content": "学习类 AI 应用需要在帮助理解和避免代写之间取得平衡。提示词应要求模型严格基于个人笔记回答，输出简明答案、详细解释和下一步复习建议，并展示来源。可信度可以根据检索分数分为高可信、部分可信和来源不足；当没有足够来源时，应提醒用户补充笔记或缩小问题范围。学术诚信护栏要求 AI 帮助学生理解、练习、检查和规划，而不是直接完成作业、考试或论文。遇到高风险请求时，可以提供思路、提纲、检查清单、分步讲解和自测题。系统还应记录使用日志，用于报告展示和行为追踪。",
                "tags": "提示词,可信度,安全,学术诚信",
            },
            {
                "id": 11,
                "course": "英语",
                "title": "英语时态与句子结构",
                "content": "英语句子通常由主语、谓语、宾语、表语、定语、状语和补语组成。学习长难句时先找谓语动词，再判断主语和宾语，最后分析从句和修饰成分。常见时态包括一般现在时、一般过去时、现在进行时、现在完成时和一般将来时。一般现在时表达习惯和客观事实，例如 I review notes every day；现在完成时强调过去动作对现在的影响，例如 I have finished the assignment。写作时要保持主谓一致、时态一致和代词指代清楚。阅读理解遇到复杂句时，可以先删去插入语和修饰语，保留主干。",
                "tags": "语法,时态,句子结构,长难句",
            },
            {
                "id": 12,
                "course": "英语",
                "title": "虚拟语气",
                "content": "虚拟语气用于表达与事实相反、不太可能发生、愿望、建议或假设。与现在事实相反时常用 If + 主语 + were/did, 主语 + would/could/might + do，例如 If I were you, I would review the notes first。与过去事实相反时常用 If + 主语 + had done, 主语 + would have done。表示建议、要求、命令的动词后接 that 从句时，谓语常用 should do 或省略 should，例如 The teacher suggested that we review vocabulary every day。学习虚拟语气时要先判断时间，再选择动词形式。were 可用于所有人称，是考试常见考点。",
                "tags": "语法,虚拟语气,if从句",
            },
            {
                "id": 13,
                "course": "英语",
                "title": "Unit5 高频词与学习表达",
                "content": "Unit5 高频词包括 efficient、deadline、priority、review、schedule、assignment、progress、summarize、evidence 和 source。efficient 表示高效的，deadline 表示截止日期，priority 表示优先级，review 表示复习或回顾，schedule 表示日程安排。常用搭配包括 meet a deadline、set priorities、review notes、make progress、summarize the main idea、provide evidence。记单词时可以用课程场景造句，例如 I set priorities before I review my Python notes。词汇复习建议采用“词义 + 搭配 + 例句 + 复述”的方式，比单独背中文意思更稳。",
                "tags": "词汇,Unit5,搭配,例句",
            },
            {
                "id": 14,
                "course": "英语",
                "title": "阅读理解技巧",
                "content": "阅读理解可以先看题干，再定位关键词。做细节题时回到原文找同义替换，而不是只找完全相同的单词；做主旨题时关注首段、尾段和每段主题句；做推理题时依据原文信息合理推出，不能加入个人常识过多。遇到长难句时，先找主语、谓语和宾语，再分析从句和修饰成分。遇到生词时先根据上下文、词缀和转折关系猜测大意。做错题整理时记录题型、定位句、错误原因和正确依据。常见错误包括定位过宽、忽略否定词、把例子当主旨、被选项中的原文词诱导。",
                "tags": "阅读,定位,主旨,错题",
            },
            {
                "id": 15,
                "course": "英语",
                "title": "写作连接词与听力跟读",
                "content": "英语作文常用连接词包括 firstly、however、therefore、in addition、as a result、for example、in my opinion、on the one hand 和 on the other hand。连接词可以让文章结构更清晰，但不能堆砌，关键是让观点、理由和例子自然衔接。段落通常采用主题句、解释、例子、总结的结构。听力跟读可以分三步：先听大意，再逐句跟读，最后复述。重点关注连读、弱读、重音和语调。跟读时可以把听不清的地方标出来，反复听 3 次后再看文本。写作和听力都需要输出练习：写完后检查时态、主谓一致和拼写；跟读后录音对比自己的语音。",
                "tags": "写作,连接词,听力,跟读",
            },
        ]
    )


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    create_todos().to_excel(DATA_DIR / "todo.xlsx", index=False)
    create_notes().to_excel(DATA_DIR / "notes.xlsx", index=False)
    print(f"Sample data written to {DATA_DIR}")


if __name__ == "__main__":
    main()
