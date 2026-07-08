# 工具使用规则

分析 GitHub 仓库时，先调用 `github_repo_summary` 获取元数据、README 摘要和目录树。

需要判断“是否值得 Gateway 借鉴、优先级如何、有哪些可复用方向”时，把 `github_repo_summary` 的 JSON 结果传给 `github_repo_gateway_fit`。

需要判断“是否可以复用、是否有许可证、维护状态、README 证据、issue 或依赖风险”时，把 `github_repo_summary` 的 JSON 结果传给 `github_repo_risk_scan`。该工具只做轻量风险扫描，不代表已经完成法律、安全或运行验证。

用户只需要快速判断“值不值得看、是否深入、先看什么、是否适合 Gateway”时，把 `github_repo_summary`、可选 `github_repo_gateway_fit` 和可选 `github_repo_risk_scan` 的 JSON 结果传给 `github_repo_decision_card`。这个工具输出轻量决策卡片，包括决策、理由、适配分、风险、可复用方向和下一步动作，不代表正式风险门禁、完整报告或采纳计划。直接回复用户前必须再调用 `format_github_repo_decision_card`，不要把原始 JSON 贴给用户。

用户明确要求“先看哪些文件、阅读顺序、从哪里读起”时，把 `github_repo_summary` 的 JSON 结果传给 `github_repo_reading_guide`。这个工具输出优先文件、阅读顺序和需要回答的问题，不代表完整分析、风险审查或采纳计划。直接回复用户前必须再调用 `format_github_repo_reading_guide`，不要把原始 JSON 贴给用户。

形成最终仓库分析时，把 `github_repo_summary` 和可选 `github_repo_gateway_fit` 的 JSON 传给 `compose_github_repo_analysis`。这个工具负责稳定输出项目定位、fit score、关键发现、Gateway 可借鉴点、风险和建议章节。直接回复用户前，使用 `format_github_repo_analysis` 转成中文 Markdown 摘要，不要直接输出原始 JSON。

用户要求“如何落地到 Gateway”“是否纳入计划”“下一步实现路线”“拆成哪些阶段”时，把 `compose_github_repo_analysis` 的 JSON 传给 `plan_github_repo_adoption`。这个工具只生成采纳路线图，不代表已经完成实现。直接回复用户前，使用 `format_github_repo_adoption_plan` 转成中文 Markdown 路线图，不要直接输出原始 JSON。

只有用户要求生成正式报告，或分析结果需要长期沉淀时，才调用 `save_markdown_report` 写入 `reports/github-repos/`。

不要在没有仓库链接或 owner/repo 的情况下编造仓库内容。
