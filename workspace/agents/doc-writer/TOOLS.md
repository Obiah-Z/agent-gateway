# 工具使用规则

生成正式文档前，先使用 `outline_structured_document` 明确文档类型、目标读者、章节结构、材料摘要和缺失材料。

收到 `compose_github_repo_analysis` 输出的 `github_repo_analysis` JSON 时，使用 `render_repo_analysis_markdown` 渲染为正式 Markdown。用户要求落盘时，再把渲染结果传给 `save_markdown_report`，category 使用 `github-repos`。

README、方案、复盘和技术报告优先使用 `save_structured_document` 落盘。自由格式或非标准结构文档才使用 `save_markdown_report` 或 `write_file`。

`doc-writer` 不负责事实核验，不要把委派摘要当成事实来源；材料不足时必须先说明缺口。
