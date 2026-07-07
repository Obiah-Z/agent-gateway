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

## `build_agent_handoff_prompt`

用于把入口 Agent 到专用 Agent 的交接信息整理成标准文本。

使用规则：

- 分类结果推荐专用 Agent 时优先使用。
- `user_goal` 保留用户原始目标，不要改写成泛化任务。
- `context_summary` 写清关键上下文、平台、用户身份或已知输入。
- `constraints` 写明不做事项、权限边界、时间范围或落盘限制。
- `expected_output` 写清目标 Agent 应该产出什么。
- 生成的文本可作为 `format_entry_response` 的 `handoff_prompt`。

## `plan_agent_collaboration`

用于为复杂任务生成多 Agent 协作路线。

使用规则：

- 任务需要两个以上 Agent 串联时使用，例如仓库分析后生成计划、计划审查后写文档、调研后成文。
- 该工具只输出 `handoff_sequence`，不会自动调用任何 Agent。
- 每个阶段完成后，上一阶段结构化输出应作为下一阶段 `upstream_result`。
- 给用户说明时必须强调这是协作路线，不代表已经执行。

## 其他工具

- `memory_search`：只在需要回忆长期背景时使用。
- `memory_write`：只保存长期稳定事实或用户明确要求记住的信息。
- `web_search` / `fetch_url`：用于需要联网核验的事实，不要替代 research 的深度调研职责。
- `read_file` / `list_directory`：只读取 workspace 内用户明确要求查看的文件。
