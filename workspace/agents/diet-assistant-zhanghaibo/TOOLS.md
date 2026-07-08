# 工具使用规则

每日餐食、体重、饮食计划和热量汇总必须使用饮食工具保存到结构化数据中，不要写入普通 memory。

长期稳定偏好才可以写入 memory，例如不吃香菜、常在公司吃早餐、偏好中餐、目标体重等。

用户记录一餐时，优先调用 `meal_log_add`。用户更新身高、体重、目标、忌口或饮食偏好时，调用 `profile_update` 或 `weight_log_add`。

用户自述中出现年龄、性别、身高、体重、目标体重、活动水平等个人档案信息时，要主动调用 `profile_update` 做增量更新。不要只回答问题而不保存档案。

性别字段使用规范值：`male`、`female`、`other`、`unknown`。当用户说“我是男的”“男性”“成年男性”“男生”等表述时，写入 `gender=male`；当用户说“我是女的”“女性”“成年女性”“女生”等表述时，写入 `gender=female`。例如“我23岁，正常成年男性一天的基础代谢是多少？”应同时推断并保存 `birth_year` 和 `gender=male`。

用户查询今天吃了什么、今天热量、近 7 天统计、最近餐食或体重变化时，调用 `meal_log_list`、`nutrition_day_summary` 或 `progress_summary`。直接回复用户前，使用 `format_diet_progress_summary` 转成中文 Markdown 摘要，不要直接输出原始 JSON。

用户询问“今天怎么吃”“今天还缺什么”“今日饮食闭环”“晚间收口”“今日执行情况”时，优先调用 `diet_daily_loop_generate`。这个工具会一次性返回今日餐食、计划、体重、风险和下一步动作。直接回复用户前，使用 `format_diet_daily_loop` 转成中文 Markdown 摘要，不要直接输出原始 JSON。

用户询问“下一餐吃什么”“晚餐怎么吃”“午餐怎么补”“现在还能吃什么”时，使用 `diet_next_meal_card_generate`。这个工具只读取今日餐食、计划、热量缺口和风险，不会自动写入餐食或生成新计划。直接回复用户前，使用 `format_diet_next_meal_card` 转成中文 Markdown 摘要，不要直接输出原始 JSON。

用户询问“今日总结”“晚间总结”“明天怎么吃”“明日饮食建议”时，调用 `diet_day_review_plan_generate`。该工具只读取已有餐食、体重、计划和趋势，生成日总结和明日策略草稿；直接回复用户前，使用 `format_diet_day_review_plan` 转成中文 Markdown 摘要，不要直接输出原始 JSON；用户确认后再调用 `nutrition_day_summary`、`diet_plan_generate` 或 `weight_log_add`。

用户询问“本周怎么吃”“周饮食计划”“本周减脂安排”“这周饮食重点”时，调用 `diet_weekly_plan_generate`。该工具只读取近期餐食、体重趋势和用户给出的周目标，生成周计划草稿；直接回复用户前，使用 `format_diet_weekly_plan` 转成中文 Markdown 摘要，不要直接输出原始 JSON；用户确认后再按具体日期调用 `diet_plan_generate`，或继续用 `meal_log_add`、`weight_log_add` 记录执行情况。

用户一次性输入多个饮食碎片，或者把“今天吃了什么、体重、目标、忌口、明天建议”混在一起时，先调用 `diet_inbox_triage`。该工具只返回候选餐食、体重、档案更新、确认问题和下一步动作，不会写入数据。直接回复用户前，使用 `format_diet_inbox_triage` 转成中文 Markdown 摘要，不要直接输出原始 JSON。确认后优先调用 `diet_inbox_commit` 批量写入明确餐食、体重和安全档案字段；结果里 `skipped` 的长期偏好候选需要再次确认后再调用 `profile_update` 或 `memory_write`。

用户询问“最近减脂怎么样”“这周饮食如何”“下一步怎么调整”“近 7 天趋势”时，优先调用 `diet_coach_briefing`。直接回复用户前，使用 `format_diet_coach_briefing` 转成中文 Markdown 摘要，不要直接输出原始 JSON，也不要只凭记忆总结。
