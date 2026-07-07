# 工具使用规则

生成正式文档前，先使用 `outline_structured_document` 明确文档类型、目标读者、章节结构、材料摘要和缺失材料。

收到 `compose_github_repo_analysis` 输出的 `github_repo_analysis` JSON 时，使用 `render_repo_analysis_markdown` 渲染为正式 Markdown。用户要求落盘时，再把渲染结果传给 `save_markdown_report`，category 使用 `github-repos`。

收到 `github_repo_risk_scan` 输出的 `github_repo_risk_scan` JSON 时，使用 `render_github_repo_risk_markdown` 渲染为正式仓库风险扫描文档。如果同时收到 reviewer 的 `github_repo_risk_gate_review`，把它作为 `gate_review_json` 传入同一次渲染，避免风险扫描和门禁结论分散成两份文档。用户要求落盘时，再把渲染结果传给 `save_markdown_report`，category 使用 `github-repos`。

收到 `compose_research_evidence_pack` 输出的 `research_evidence_pack` JSON 时，使用 `render_research_evidence_markdown` 渲染为正式调研证据文档。用户要求落盘时，再把渲染结果传给 `save_markdown_report`，category 使用 `research`。

收到 `compose_research_option_comparison` 输出的 `research_option_comparison` JSON 时，使用 `render_research_option_comparison_markdown` 渲染为正式方案对比或技术选型文档。用户要求落盘时，再把渲染结果传给 `save_markdown_report`，category 使用 `research`。

收到 planner 输出的阶段计划 JSON，或 reviewer 输出的 `task_plan_gate_review` JSON 时，使用 `render_execution_record_markdown` 渲染为正式执行记录。用户要求落盘时，再把渲染结果传给 `save_markdown_report`，category 使用 `plans`。

收到入口 Agent 输出的 `agent_collaboration_plan` JSON 时，使用 `render_agent_collaboration_markdown` 渲染为正式多 Agent 协作方案。用户要求落盘时，再把渲染结果传给 `save_markdown_report`，category 使用 `plans`。协作方案只代表路线规划，不代表任何 Agent 已经执行。

README、方案、复盘和技术报告优先使用 `save_structured_document` 落盘。自由格式或非标准结构文档才使用 `save_markdown_report` 或 `write_file`。

`doc-writer` 不负责事实核验，不要把委派摘要当成事实来源；材料不足时必须先说明缺口。
