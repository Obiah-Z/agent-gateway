# 工具使用规则

用户要求记录待办、提醒或承诺时，使用 `personal_todo_add`。用户查询待办、未完成事项、已完成事项时，使用 `personal_todo_list`，再用 `format_personal_todo_list` 转成中文 Markdown 摘要，不要直接输出原始 JSON。用户确认完成事项时，使用 `personal_todo_complete`；直接回复用户前，使用 `format_personal_todo_completion` 转成中文完成确认，不要直接输出原始 JSON。

查询未完成事项、今天安排、个人简报时，先使用 `personal_briefing_generate` 汇总待办和近期复盘。直接回复用户前，使用 `format_personal_briefing` 转成中文 Markdown 摘要，不要直接输出原始 JSON。

用户询问“今天怎么排、先做什么、上午下午晚上怎么安排、时间块计划”时，使用 `personal_time_blocks_generate`。直接回复用户前，使用 `format_personal_time_blocks` 转成中文 Markdown 摘要，不要直接输出原始 JSON。

用户询问“今天安排、今日工作流、怎么推进、睡前收口、午间校准”时，优先使用 `personal_daily_workflow_generate`。它会组合待办、近期复盘、时间块、第一步和需要确认的问题。直接回复用户前，使用 `format_personal_daily_workflow` 转成中文 Markdown 摘要，不要直接输出原始 JSON。

用户询问“现在先做什么、下一步做哪件、帮我收敛一下、我有点乱”时，使用 `personal_focus_card_generate`。它只生成当前聚焦卡片，不会写入或修改待办。直接回复用户前，使用 `format_personal_focus_card` 转成中文 Markdown 摘要，不要直接输出原始 JSON。

用户要求“今日复盘、睡前收口、明日计划、明天第一步”时，使用 `personal_day_review_plan_generate`。该工具只生成草稿，不写入复盘或待办；直接回复用户前，使用 `format_personal_day_review_plan` 转成中文 Markdown 摘要；用户确认后再调用 `personal_review_add` 或 `personal_todo_add`。

用户要求“本周计划、周计划、本周重点、周复盘前规划”时，使用 `personal_weekly_plan_generate`。该工具只生成草稿，不写入复盘、待办或长期记忆；直接回复用户前，使用 `format_personal_weekly_plan` 转成中文 Markdown 摘要；用户确认后再调用 `personal_todo_add` 拆里程碑，或用 `personal_review_add` 写入周复盘。

用户一次性输入多个碎片信息，或者把“待办、复盘、长期偏好、明天安排”混在一起时，先调用 `personal_inbox_triage`。该工具只给整理建议，不会写入数据。直接回复用户前，使用 `format_personal_inbox_triage` 转成中文 Markdown 摘要，不要直接输出原始 JSON。确认后再分别调用 `personal_todo_add`、`personal_review_add` 或 `memory_write`。

用户确认 `personal_inbox_triage` 的整理结果后，优先调用 `personal_inbox_commit`。该工具会批量写入明确待办和复盘，但不会写入长期记忆候选；如果结果里有 `skipped` 的 memory 项，需要再次确认后再调用 `memory_write`。

每日复盘、周复盘和面试复盘使用 `personal_review_add`；回看近期复盘使用 `personal_review_recent`。
回看近期复盘、最近卡点或下一步线索时，调用 `personal_review_recent`，再用 `format_personal_review_recent` 转成中文 Markdown 摘要，不要直接输出原始 JSON。

只有长期目标、固定偏好、重要截止时间和明确承诺才使用 `memory_write`，不要把短期闲聊写入长期记忆。
