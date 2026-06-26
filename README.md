# StudyPilot 个人学习助手

StudyPilot 是一个面向学生的 AI 个人学习助手，支持学习任务管理、个人笔记 RAG 问答、今日学习计划、复习训练、错题记录和实训报告素材辅助。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\create_sample_data.py
python src\init_db.py
python src\note_vector.py
python src\app.py
```

如果没有配置 API Key，系统会使用本地演示模式生成回复，方便课堂演示和截图。

## API Key 配置

复制 `.env.example` 为 `.env`，填入 DeepSeek API Key：

```powershell
Copy-Item .env.example .env
notepad .env
```

没有 API Key 也可以运行，系统会使用本地关键词检索和规则意图解析。

## 部署到 Render

本项目是 Python + NiceGUI Web 应用，不适合部署到 GitHub Pages 这类纯静态托管平台。推荐使用 Render 的 Web Service：

1. 将仓库连接到 Render。
2. 选择 Blueprint 或 Web Service 部署。
3. 使用仓库中的 `render.yaml` 自动配置构建和启动命令。
4. 首次部署默认使用 `LLM_PROVIDER=demo`，不配置 API Key 也可以访问演示功能。

Render 会自动提供公网访问链接。若后续要接入真实模型，在 Render 环境变量中配置 `.env.example` 里的 API Key 字段即可。

## 向量库说明

当前项目支持两种检索模式：

- 默认可运行模式：关键词检索兜底，不需要安装 Chroma，适合课堂演示。
- 向量库增强模式：安装 `chromadb` 后运行 `python src\note_vector.py`，系统会构建 `note_vector_db/`。

如果安装完整依赖耗时较长，可以先只安装：

```powershell
pip install nicegui pandas openpyxl openai python-dotenv
```

## 演示流程建议

1. 打开学习驾驶舱，展示今日待办、课程笔记、学习计划。
2. 进入 AI 问笔记，输入：`什么是 RAG？`，展示回答和来源引用。
3. 进入任务管理，输入：`周五前完成 Python 列表推导式作业`。
4. 进入复习训练，选择 Python，点击生成测验。
5. 进入学习周报，展示截图清单和报告素材。
6. 进入项目健康，展示数据库、依赖和演示数据状态。

## 当前完成度

- [x] 示例数据
- [x] SQLite 数据库
- [x] NiceGUI 界面
- [x] 任务管理
- [x] AI 问笔记
- [x] 来源引用
- [x] 学习计划
- [x] 复习测验
- [x] 项目健康检查
- [x] DeepSeek/OpenAI SDK 可选接入
- [ ] Chroma 向量库增强模式
- [ ] 页面截图与实训报告填充

## 目录结构

```text
data/                 示例 Excel 和 SQLite 数据库
docs/                 需求、设计、技术文档
note_vector_db/        Chroma 向量库
scripts/              辅助脚本
src/                  应用源码
```

## 核心功能

- NiceGUI 学习驾驶舱
- 自然语言添加和完成任务
- 基于个人笔记的 RAG 问答
- 来源引用和可信度提示
- 今日学习计划
- 闪卡/测验生成
- 项目健康检查和报告素材辅助
