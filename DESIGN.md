# Design

## Source of truth

- Status: Draft
- Last refreshed: 2026-06-22
- Primary product surfaces: NiceGUI Web app for StudyPilot personal learning assistant, optional Gradio fallback demo
- Evidence reviewed:
  - `docs/个人学习助手_PRD与技术架构.md`
  - 实训指导书案例B：个人学习助手
  - Report template sections: project overview, architecture diagram, daily records, core code, screenshots, reflection

## Brand

- Personality: clear, reliable, student-friendly, lightly motivational
- Trust signals: source citations for RAG answers, visible task state, progress metrics, explicit AI recommendations
- Avoid: generic chatbot-only layout, decorative landing page, overly playful visuals, unsupported AI claims

## Product goals

- Goals:
  - Help students manage coursework, notes, review, and daily plans in one place.
  - Demonstrate RAG, tool calling, multi-agent routing, and a polished UI for training assessment.
  - Turn personal notes into answerable, reviewable learning assets.
  - Reduce common AI study-tool risks: hallucination, overreliance, privacy leakage, tool fragmentation, and unclear academic boundaries.
- Non-goals:
  - Full LMS replacement.
  - Multi-user class collaboration for the first version.
  - Heavy mobile-native implementation.
- Success signals:
  - User can add a task in natural language.
  - User can ask a note question and see source citations.
  - User can generate a daily plan and a quiz/flashcards from notes.
  - Screenshots clearly show dashboard, RAG, task tools, and review mode.

## Personas and jobs

- Primary personas:
  - AI application development training student.
  - Student preparing for coursework and exams across several classes.
  - Student presenting a practical AI project in a course report.
- User jobs:
  - Know what to study today.
  - Ask questions about personal notes.
  - Convert notes into review materials.
  - Track task completion and weak points.
  - Verify whether an AI answer is grounded in personal notes.
  - Use AI as a tutor without crossing into direct assignment outsourcing.
- Key contexts of use:
  - Desktop browser during training.
  - Classroom demonstration.
  - Report screenshot capture.

## Information architecture

- Primary navigation:
  - 学习驾驶舱
  - AI 问笔记
  - 任务管理
  - 复习训练
  - 学习周报
  - 资料库设置
- Core routes/screens:
  - Dashboard with task metrics, course progress, AI recommendation.
  - RAG Q&A with source selector and citation panel.
  - Task manager with natural-language input and structured parse preview.
  - Review training with flashcards, quizzes, mistakes, mastery state.
  - Weekly report with completion summary and next-week advice.
  - Focus session with timer, task goal, related notes, and reflection.
  - Demo readiness page with screenshot checklist and project health checks.
- Content hierarchy:
  - Current learning priority first.
  - Then actionable controls.
  - Then evidence/details such as citations and database records.

## Design principles

- Principle 1: Make the next study action obvious.
- Principle 2: Every AI answer should show what data it used.
- Principle 3: Prefer tool-like density over marketing-style hero design.
- Principle 4: AI should coach learning, not replace the learner.
- Principle 5: Privacy and academic integrity states must be visible in the product, not hidden in documentation.
- Principle 6: AI 问笔记 follows NotebookLM's creation journey: Inputs -> Chat -> Outputs.
- Principle 7: The note Q&A workspace behaves as a panel system, not three unrelated cards: sources stay on the left, chat remains the central anchor, and Studio/output tools stay on the right.
- Tradeoffs:
  - NiceGUI gives stronger visual control than Gradio while preserving a Python-first stack.
  - Keep a minimal Gradio fallback only if required by the training guide or for quick model demonstration.
  - Keep MVP feasible while documenting richer product directions.
  - For the NotebookLM-inspired surface, prefer native HTML elements plus CSS when NiceGUI/Quasar defaults distort spacing, typography, or icon rendering.

## Visual language

- Color:
  - Primary: `#2563EB`
  - Success: `#10B981`
  - Warning: `#F59E0B`
  - Error: `#EF4444`
  - Background: `#F8FAFC`
  - Card: `#FFFFFF`
  - Text: `#0F172A`
- Typography:
  - Chinese: Microsoft YaHei or PingFang SC.
  - Latin/numeric: Inter or Arial.
  - Title 20-24px, card heading 15-16px, body 14px, helper 12px.
  - AI 问笔记 uses a Google-like hierarchy: top title about 28px, panel headings about 20px, central answer heading about 34px, body about 16px, helper/source metadata 12-14px.
- Spacing/layout rhythm:
  - Dashboard uses compact cards and a two/three-column grid on desktop.
  - Keep vertical spacing moderate; avoid large blank hero areas.
  - AI 问笔记 uses a full-viewport three-panel rhythm: 64px top bar, 16px outer gutters, 373px side panels on desktop, and a flexible center chat panel.
- Shape/radius/elevation:
  - Cards radius 8px.
  - Buttons and status tags use restrained radius.
  - Use light shadow only for dashboard cards and source cards.
  - Notebook-style panels use 16px radius, white surfaces on `#EDEFFA`, almost no shadow, and clear internal dividers.
- Motion:
  - Minimal, product-like, and task-serving.
  - Use GSAP only as an enhancement layer over NiceGUI, not as the UI framework.
  - Animate page/card entrance and RAG source-card reveal with `y`, `autoAlpha`, and `stagger`.
  - Respect `prefers-reduced-motion`; never rely on animation to communicate essential status.
  - Avoid layout-heavy animated properties such as width, height, top, left, margin, and padding.
- Imagery/iconography:
  - Use simple emoji/icon labels only where Gradio makes icon libraries impractical.
  - Icons should support scanning, not decorate empty space.

## Components

- Existing components to reuse:
  - NiceGUI header, left drawer, card, row, column, table, chat message, input, button, dialog, expansion, linear progress.
  - Optional Gradio Blocks only for fallback demo.
- New/changed components:
  - Metric card.
  - Course progress card.
  - Source citation card.
  - Intent parse preview.
  - Flashcard review card.
  - Weekly report panel.
  - Confidence/source radar.
  - Academic integrity warning panel.
  - Study session timer card.
  - Privacy check result panel.
  - Missing screenshot/report material checklist.
  - Project health check card.
  - Task breakdown step list.
  - Code explanation panel for report-ready core functions.
  - NotebookLM-inspired AI 问笔记 workspace:
    - Source/Input panel for note imports, source search affordances, source selection, and upload history.
    - Chat panel as the fixed center of gravity for grounded Q&A, citations, save-to-note, feedback, and the bottom prompt composer.
    - Studio/Output panel for one-click artifact generation such as audio overview, slides, report, flashcards, quiz, infographic, and data table.
    - Project navigation embedded inside the Notebook top bar so the workspace remains full-size while other product pages stay reachable.
- Variants and states:
  - Task status: 未完成, 已完成, 逾期.
  - Priority: 高, 中, 低.
  - Mastery: 掌握, 模糊, 不会.
  - RAG: retrieving, answered, no-source-found, error.
- Token/component ownership:
  - Keep style tokens in `src/ui/styles.py` or a NiceGUI CSS block.
  - Keep reusable cards and panels in `src/ui/components.py`.

## Accessibility

- Target standard: practical WCAG-inspired readability for desktop classroom use.
- Keyboard/focus behavior:
  - Main input, buttons, tabs, and dropdowns must be reachable by keyboard.
- Contrast/readability:
  - Use dark body text on light background.
  - Do not rely on color alone for status; include text labels.
- Screen-reader semantics:
  - Use clear labels for inputs and buttons.
- Reduced motion and sensory considerations:
  - Avoid animated backgrounds and continuous motion.

## Responsive behavior

- Supported breakpoints/devices:
  - Primary: desktop/laptop.
  - Secondary: tablet and narrow browser windows.
- Layout adaptations:
  - Desktop: navigation/filters left, workspace center, status/citations right.
  - Mobile/narrow: stack panels vertically and rely on Tabs.
  - Notebook-style panel states:
    - Standard: Sources + Chat + Studio balanced across the full viewport.
    - Reading + Chat: left source panel can expand when source review is primary.
    - Chat + Writing: Studio/output panel can expand when drafting or artifact generation is primary.
    - Minimal side panels retain essential controls/icons rather than disappearing.
- Touch/hover differences:
  - Buttons should be large enough for touch.
  - Do not hide essential actions behind hover-only UI.

## Interaction states

- Loading:
  - Show "正在检索你的笔记..." or "正在生成学习计划...".
- Empty:
  - Provide sample prompts and import instructions.
- Error:
  - Explain whether the error came from API, database, vector index, or parsing.
- Low confidence:
  - State that the answer is not well supported by personal notes and suggest adding notes or switching to general-knowledge mode.
- Academic integrity risk:
  - Offer explanation, outline, checklist, or guided practice instead of direct submission-ready work.
- Success:
  - Confirm the changed task or generated artifact.
- Demo readiness:
  - Show database, vector index, API key, screenshots, and required features as pass/warn/fail states.
- Disabled:
  - Disable generation buttons while API calls are running.
- Offline/slow network:
  - Show timeout guidance and allow retry.

## Content voice

- Tone: helpful, concise, encouraging, not exaggerated.
- Terminology:
  - Use "笔记来源", "今日计划", "复习训练", "掌握度", "错题本".
- Microcopy rules:
  - Give actionable suggestions.
  - Avoid vague AI marketing phrases.
  - When sources are missing, say so plainly and ask the user to add notes.

## Implementation constraints

- Framework/styling system:
  - NiceGUI main app with optional custom CSS.
  - Optional Gradio fallback demo if the instructor requires the original guide path.
  - GSAP may be loaded by CDN for light microinteractions and entrance animation.
  - External GSAP skill reference is stored outside C drive at `E:\CodexExternalSkills\gsap-skills`.
- Design-token constraints:
  - Keep colors and spacing centralized once implementation begins.
- Performance constraints:
  - Small local SQLite and Chroma dataset should respond within about 10 seconds for RAG.
- Compatibility constraints:
  - Windows local development and classroom demonstration.
- Test/screenshot expectations:
  - Capture at least five screenshots: dashboard, task parse, RAG answer with citations, review training, weekly report.

## Open questions

- [ ] Whether the final implementation should support PDF upload in addition to Excel.
- [ ] Whether weekly report export should be Markdown, DOCX, or PDF.
- [ ] Whether voice input is required for the final demo or only listed as future work.
