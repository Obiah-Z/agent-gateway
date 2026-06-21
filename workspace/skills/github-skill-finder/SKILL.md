---
name: github-skill-finder
description: 从 GitHub 发现热门且仍活跃的 AI Agent Skill、MCP server、工具调用插件、工作流自动化模板、提示词/工具包或个人效率自动化项目，并输出适合飞书推送的中文精选摘要。
invocation: /github-skill-finder
---

# GitHub 热门 Skill 发现

当用户要求寻找 GitHub 上热门 Skill、Agent 插件、MCP 工具、自动化脚本、提示词工具包，或 Cron 定时生成“热门 Skill 发现”时，使用本技能。

## 判断标准

- 优先选择可被 Agent Gateway、Codex、MCP、工具调用、飞书自动化或个人工作流复用的项目。
- 关注 star、fork、最近 push 时间、主题标签、README 描述和落地价值。
- 排除泛泛框架、课程仓库、无维护迹象、纯 demo、主题不清或与 Skill 复用关系弱的项目。
- 输出必须包含仓库链接，不能编造 star、fork、维护状态或功能。

## 输出格式

每条候选按下面结构输出：

- 仓库：owner/repo
- 热度：stars / forks
- 活跃度：最近 push 时间
- 主要方向：MCP / Agent Skill / Tool Calling / Workflow / Prompt Pack / Automation
- 为什么值得关注：1 到 2 句
- 可接入 Gateway 的方式：1 到 2 句
- 优先级：高 / 中 / 低

最后补充“本周可尝试接入项”，最多 3 条。
