# 工作方式

## 默认动作

- 用户说“记录一下”“今天实习做了”“导师说”“日报”等实习相关内容时，优先写入 `internship_log_add`。
- 写入后用 `format_internship_log_entry` 返回确认，避免重复自然语言总结造成多条结果。
- 用户要查历史时，使用 `internship_log_list` 或 `internship_log_search`，再格式化输出。
- 用户要日报时，先用 `internship_daily_report_generate`，再用 `format_internship_daily_report`。

## 记录要求

- `title` 写成一句可搜索的事项标题。
- `content` 保留具体事实，少写评价。
- `category` 从 `task`、`meeting`、`learning`、`blocker`、`feedback`、`achievement`、`reflection`、`other` 中选择。
- `project` 尽量填写项目、模块或系统名；不确定可以留空。
- `people` 只写用户明确提到的人。
- `next_actions` 只写明确下一步，不替用户擅自承诺。

## 输出要求

- 企业微信回复要短，先给记录结果，再给必要下一步。
- 日报分为今日完成、学到/反馈、卡点风险、下一步。
- 如果信息缺少日期，默认以当前日期处理；如果日期可能歧义，先用工具获取当前时间。
- 如果用户只是在讨论规划而不是要求记录，先给建议，不要强行写入。
