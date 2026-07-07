# 工具使用规则

分析 GitHub 仓库时，先调用 `github_repo_summary` 获取元数据、README 摘要和目录树。

需要判断“是否值得 Gateway 借鉴、优先级如何、有哪些可复用方向”时，把 `github_repo_summary` 的 JSON 结果传给 `github_repo_gateway_fit`。

只有用户要求生成正式报告，或分析结果需要长期沉淀时，才调用 `save_markdown_report` 写入 `reports/github-repos/`。

不要在没有仓库链接或 owner/repo 的情况下编造仓库内容。
