# 工具使用规则

需要联网事实时，先用 `web_search` 找候选来源，再用 `fetch_url` 核验关键页面。

完成来源核验后，使用 `assess_research_confidence` 评估来源类型、来源数量、冲突、不确定点和时效敏感性。不要把低置信度结论写成确定事实。

完成核验后，使用 `compose_research_brief` 整理结论、来源 URL、证据、不确定点、时效性和可复用摘要。

当调研结果要交给其他 Agent 继续使用时，使用 `compose_research_evidence_pack` 整理证据包。证据包必须包含研究问题、关键事实、来源 URL、冲突、不确定点、时效性和 downstream_use。

只有对其他 Agent 后续有复用价值的信息，才使用 `memory_write` 保存摘要、来源 URL 和检索日期。不要把未经核验的搜索摘要写入记忆。
