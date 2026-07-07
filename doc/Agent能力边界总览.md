# Agent 能力边界总览

本文档记录 Gateway 当前 Agent 分层、主要职责和工具边界，用于后续继续扩展能力时避免职责重叠。

## 平台入口 Agent

`main`、`feishu-entry` 和 `wework-entry` 是入口层。入口层负责普通问答、意图分类、标准交接提示和委派建议，不负责深度执行。

关键工具包括 `prepare_entry_route_response`、`classify_task_intent`、`build_agent_handoff_prompt`、`plan_agent_collaboration`、`explain_agent_route`、`format_entry_response`、`suggest_agent_delegation` 和 `list_agent_capabilities`。其中 `prepare_entry_route_response` 只把分类、协作路线、路由解释和用户回复组合成入口层准备结果，不执行目标 Agent；`suggest_agent_delegation` 只表示建议，不代表目标 Agent 已经自动执行；`plan_agent_collaboration` 只生成多 Agent 协作路线，不自动调用任何 Agent；`explain_agent_route` 只解释为什么选择某个 Agent 或协作路线，不代表目标 Agent 已经执行。

复杂 GitHub 仓库任务如果同时要求分析、风险、采纳计划或正式报告，入口层应使用 `repo-adoption` 协作路线：repo-analyzer 先产出仓库分析和风险扫描，reviewer 做仓库风险门禁，planner 整合成采纳执行计划，doc-writer 最后成文落盘。

`classify_task_intent` 会把普通仓库理解类请求归为 `repo-analysis`，把包含风险、采纳、引入、复用、计划或报告要求的复杂仓库请求归为 `repo-adoption`，并返回 `requires_collaboration=true` 与 `collaboration_task_type=repo-adoption`，供入口层触发协作路线。

入口层生成 `agent_collaboration_plan` 后，应把该 JSON 作为 `collaboration_plan_json` 传给 `format_entry_response`，向用户展示阶段路线和“尚未自动执行”的边界，而不是只输出单个 Agent 的委派建议。

## 共享能力 Agent

`research` 负责联网检索、来源核验、证据包沉淀和方案选型对比。`compose_research_evidence_pack` 用于把来源、关键事实、不确定点和下游用途整理成可复用材料；`compose_research_option_comparison` 用于把技术选型、方案对比或中间件取舍整理成候选方案、评价维度、推荐项、来源依据和下游动作。

`repo-analyzer` 负责 GitHub 仓库分析、Gateway 适配评估、轻量风险扫描和采纳路线图。`github_repo_risk_scan` 用于检查许可证、维护状态、README 证据、issue 数和依赖文件信号；repo-analyzer 不负责正式文档成文和任务执行。

`planner` 负责阶段计划、执行拆解、采纳计划转换、仓库审查结果整合和协作路线转换。`compose_repo_review_task_plan` 用于把 repo-analyzer 的 `github_repo_analysis`、reviewer 的 `github_repo_risk_gate_review` 和可选 `github_repo_adoption_plan` 合并为可落盘的仓库采纳执行计划；`adapt_collaboration_plan_to_task_plan` 用于把入口 Agent 的 `agent_collaboration_plan` 转成可落盘的阶段计划，明确每一阶段交给哪个 Agent、输入依据、输出和完成标准，但不自动调用任何 Agent。

`reviewer` 负责风险审查、发布门禁、计划门禁、协作路线门禁、证据复用门禁和仓库风险门禁。`review_research_evidence_gate` 用于检查 research 证据包是否具备问题、结论、来源 URL、一手来源、关键事实、不确定点和时效说明；`review_github_repo_risk_gate` 用于检查 repo-analyzer 的 `github_repo_risk_scan` 是否具备明确用途、许可证判断、维护状态、高危阻塞风险和缓解动作；`review_agent_collaboration_gate` 用于检查入口 Agent 生成的多 Agent 协作路线是否具备目标、交接契约、阶段输出、约束和未自动执行声明。reviewer 不直接修改系统或执行高风险动作。

`doc-writer` 负责正式 Markdown 成文。`render_repo_analysis_markdown` 用于把 repo-analyzer 的仓库分析结果渲染为正式报告，`render_github_repo_risk_markdown` 用于把 `github_repo_risk_scan` 渲染为仓库风险扫描文档，并可合并 reviewer 的 `github_repo_risk_gate_review` 门禁结论；`render_research_evidence_markdown` 用于把 research 的证据包渲染为调研证据文档，`render_execution_record_markdown` 用于把 planner/reviewer 的结构化结果渲染为执行记录，`render_agent_collaboration_markdown` 用于把入口 Agent 生成的多 Agent 协作路线渲染为正式方案。它只表达路线和交接契约，不代表任何 Agent 已经自动执行。

## 个人 Agent

`personal-secretary-zhanghaibo` 只服务指定企业微信用户，负责待办、复盘、时间块、每日工作流、个人日复盘草稿和周计划草稿。`personal_day_review_plan_generate` 与 `personal_weekly_plan_generate` 只生成草稿，不自动写入待办、复盘或长期记忆。

`diet-assistant-zhanghaibo` 只服务指定企业微信用户，负责餐食、体重、饮食计划、趋势简报、饮食日总结草稿和周饮食计划草稿。`diet_day_review_plan_generate` 与 `diet_weekly_plan_generate` 只读取已有记录生成建议，不自动补记餐食、写体重或生成新计划。

## 运维 Agent

`ops` 负责只读运维诊断。`ops_readonly_health` 采集基础健康信息，`summarize_ops_health` 生成巡检摘要，`ops_runtime_diagnostics` 汇总运行事件、失败投递和告警线索，`ops_troubleshooting_plan` 把健康摘要和运行诊断整理成只读排障行动清单。ops 不执行删除、清空、重启、改权限或改配置。

## 扩展原则

新增能力时优先判断应该落在哪一层：入口层只做分类和交接，共享能力层做通用专业能力，个人 Agent 做用户私有闭环，ops 做只读排障。

新增工具应同时更新 `config/agents.json`、对应 `workspace/agents/*` 提示词和测试。写入类工具必须明确数据范围和用户作用域；草稿类工具必须明确不会自动写入。
