# Agent 能力边界总览

本文档记录 Gateway 当前 Agent 分层、主要职责和工具边界，用于后续继续扩展能力时避免职责重叠。

## 平台入口 Agent

`main`、`feishu-entry` 和 `wework-entry` 是入口层。入口层负责普通问答、意图分类、标准交接提示和委派建议，不负责深度执行。

关键工具包括 `classify_task_intent`、`build_agent_handoff_prompt`、`plan_agent_collaboration`、`format_entry_response`、`suggest_agent_delegation` 和 `list_agent_capabilities`。其中 `suggest_agent_delegation` 只表示建议，不代表目标 Agent 已经自动执行；`plan_agent_collaboration` 只生成多 Agent 协作路线，不自动调用任何 Agent。

## 共享能力 Agent

`research` 负责联网检索、来源核验和证据包沉淀。新增的 `compose_research_evidence_pack` 用于把来源、关键事实、不确定点和下游用途整理成可复用材料。

`repo-analyzer` 负责 GitHub 仓库分析、Gateway 适配评估和采纳路线图，不负责正式文档成文和任务执行。

`planner` 负责阶段计划、执行拆解和采纳计划转换。新增后的链路可以把 repo-analyzer 的采纳路线图转换成可执行计划。

`reviewer` 负责风险审查、发布门禁和计划门禁，不直接修改系统或执行高风险动作。

`doc-writer` 负责正式 Markdown 成文。`render_execution_record_markdown` 用于把 planner/reviewer 的结构化结果渲染为执行记录，`render_agent_collaboration_markdown` 用于把入口 Agent 生成的多 Agent 协作路线渲染为正式方案。它只表达路线和交接契约，不代表任何 Agent 已经自动执行。

## 个人 Agent

`personal-secretary-zhanghaibo` 只服务指定企业微信用户，负责待办、复盘、时间块、每日工作流和个人日复盘草稿。`personal_day_review_plan_generate` 只生成草稿，不自动写入待办或复盘。

`diet-assistant-zhanghaibo` 只服务指定企业微信用户，负责餐食、体重、饮食计划、趋势简报和饮食日总结草稿。`diet_day_review_plan_generate` 只读取已有记录生成建议，不自动补记餐食、写体重或生成新计划。

## 运维 Agent

`ops` 负责只读运维诊断。`ops_readonly_health` 采集基础健康信息，`summarize_ops_health` 生成巡检摘要，`ops_runtime_diagnostics` 汇总运行事件、失败投递和告警线索。ops 不执行删除、清空、重启、改权限或改配置。

## 扩展原则

新增能力时优先判断应该落在哪一层：入口层只做分类和交接，共享能力层做通用专业能力，个人 Agent 做用户私有闭环，ops 做只读排障。

新增工具应同时更新 `config/agents.json`、对应 `workspace/agents/*` 提示词和测试。写入类工具必须明确数据范围和用户作用域；草稿类工具必须明确不会自动写入。
