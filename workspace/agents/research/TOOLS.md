# 工具使用规则

需要联网事实时，先用 `web_search` 找候选来源，再用 `fetch_url` 核验关键页面。

完成核验后，使用 `compose_research_brief` 整理结论、来源 URL、证据、不确定点、时效性和可复用摘要。

只有对其他 Agent 后续有复用价值的信息，才使用 `memory_write` 保存摘要、来源 URL 和检索日期。不要把未经核验的搜索摘要写入记忆。
