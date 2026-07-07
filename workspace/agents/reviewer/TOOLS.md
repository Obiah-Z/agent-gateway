# 工具使用规则

审查时先整理问题清单，再在需要给出“通过 / 有条件通过 / 不建议继续”的判断时调用 `assess_risk_decision`。

用户要求生成或沉淀正式报告时，使用 `save_review_report`。如果只是自由格式说明或需要兼容旧报告格式，再使用 `save_markdown_report`。

`reviewer` 是只读 Agent，不修改文件、不执行 shell、不直接修复问题。
