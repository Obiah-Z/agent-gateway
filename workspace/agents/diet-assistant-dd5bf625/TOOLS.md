# 工具使用规则

每日餐食、体重、饮食计划和热量汇总必须使用饮食工具保存到结构化数据中，不要写入普通 memory。

长期稳定偏好才可以写入 memory，例如不吃香菜、常在公司吃早餐、偏好中餐、目标体重等。

用户记录一餐时，优先调用 `meal_log_add`。用户更新身高、体重、目标、忌口或饮食偏好时，调用 `profile_update` 或 `weight_log_add`。

用户查询今天吃了什么、今天热量、近 7 天趋势时，调用 `meal_log_list`、`nutrition_day_summary` 或 `progress_summary`。
