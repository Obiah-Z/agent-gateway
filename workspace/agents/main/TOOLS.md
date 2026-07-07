# 工具使用

## `classify_task_intent`

用于判断用户请求属于普通聊天，还是更适合交给专用 Agent。

适用场景：

- 用户请求包含 GitHub 仓库、代码库或项目分析。
- 用户要求规划路线、拆任务、写计划。
- 用户要求生成 README、报告、手册或 Markdown 文档。
- 用户要求做风险审查、方案评估或问题排查。
- 用户询问 Docker、Redis、RabbitMQ、PostgreSQL、日志、磁盘或系统运行状态。
- 用户提出个人秘书、饮食、体重、日程、提醒、复盘等长期个人任务。

使用后：

- 如果 `can_answer_directly=true`，可以直接回答。
- 如果推荐了专用 Agent，说明推荐对象和原因，并给出可复制的交接提示。
- 不要把分类结果当成已经完成的执行结果。

## `format_entry_response`

用于把分类结果和委派建议整理成稳定中文回复。

使用规则：

- 对普通聊天，传入 `recommended_agent_id=main` 和 `can_answer_directly=true`。
- 对需要专用 Agent 的任务，传入分类得到的 `intent`、`recommended_agent_id`、`reason`、`context_summary` 和可选 `handoff_prompt`。
- 输出后不要再改写成另一种结构，避免入口回复风格漂移。

## 其他工具

- `memory_search`：只在需要回忆长期背景时使用。
- `memory_write`：只保存长期稳定事实或用户明确要求记住的信息。
- `web_search` / `fetch_url`：用于需要联网核验的事实，不要替代 research 的深度调研职责。
- `read_file` / `list_directory`：只读取 workspace 内用户明确要求查看的文件。
