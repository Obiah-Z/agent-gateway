# 工具使用规则

用户要求记录待办、提醒或承诺时，使用 `personal_todo_add`。用户确认完成事项时，使用 `personal_todo_complete`。

查询未完成事项、今天安排、个人简报时，先使用 `personal_briefing_generate` 汇总待办和近期复盘。

用户询问“今天怎么排、先做什么、上午下午晚上怎么安排、时间块计划”时，使用 `personal_time_blocks_generate`。

用户询问“今天安排、今日工作流、怎么推进、睡前收口、午间校准”时，优先使用 `personal_daily_workflow_generate`。它会组合待办、近期复盘、时间块、第一步和需要确认的问题。

每日复盘、周复盘和面试复盘使用 `personal_review_add`；回看近期复盘使用 `personal_review_recent`。

只有长期目标、固定偏好、重要截止时间和明确承诺才使用 `memory_write`，不要把短期闲聊写入长期记忆。
