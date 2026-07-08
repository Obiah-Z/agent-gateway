# Agent 路由验收清单

这份清单用于验证入口 Agent 是否能把常见用户请求稳定分流到正确的能力 Agent。它不调用大模型，也不触发真实通道投递，只调用内置工具 `classify_task_intent`，并检查目标 Agent 的工具白名单是否覆盖该场景的关键执行工具。

## 运行命令

```bash
python scripts/eval_agent_routing.py
```

输出 JSON：

```bash
python scripts/eval_agent_routing.py --json
```

通过标准：默认样例全部 `PASS`，脚本退出码为 `0`。单条样例需要同时满足三个条件：入口意图正确、推荐 Agent 正确、目标 Agent 的 allowlist 包含该场景声明的关键工具。

## 当前覆盖场景

| 场景 | 期望入口判断 | 期望 Agent | 是否多 Agent 协作 | 关键工具门禁 |
| --- | --- | --- | --- | --- |
| 普通聊天 | `chat` | `main` | 否 | `classify_task_intent`、`format_entry_response` |
| GitHub 仓库普通分析 | `repo-analysis` | `repo-analyzer` | 否 | `compose_github_repo_analysis`、`format_github_repo_analysis` |
| GitHub 仓库阅读路线 | `repo-reading-guide` | `repo-analyzer` | 否 | `github_repo_reading_guide`、`format_github_repo_reading_guide` |
| GitHub 仓库采纳/风险/报告 | `repo-adoption` | `repo-analyzer` | 是 | `plan_github_repo_adoption`、`format_github_repo_adoption_plan` |
| 技术选型和验证计划 | `research-option-validation` | `research` | 是 | `compose_research_option_comparison` |
| 阶段规划 | `planning` | `planner` | 否 | `plan_execution_stage`、`format_execution_stage_plan` |
| Agent 能力目录 | `agent-capabilities` | `main` | 否 | `list_agent_capabilities`、`format_agent_capability_catalog` |
| 运维排障 | `ops` | `ops` | 否 | `ops_readonly_health`、`ops_runtime_diagnostics` |
| 饮食记录 | `diet` | `diet-assistant-zhanghaibo` | 否 | `meal_log_add`、`format_meal_log_entry` |
| 个人待办/复盘 | `personal` | `personal-secretary-zhanghaibo` | 否 | `personal_todo_add`、`personal_review_add` |
| 个人到期提醒 | `personal` | `personal-secretary-zhanghaibo` | 否 | `personal_due_todo_digest_generate`、`format_personal_due_todo_digest` |
| 文档整理 | `document` | `doc-writer` | 否 | `outline_structured_document`、`save_structured_document` |
| 风险审查 | `review` | `reviewer` | 否 | `assess_risk_decision`、`format_risk_decision_assessment` |

## 使用时机

- 新增 Agent 或调整 Agent 职责后运行。
- 新增、删除或迁移 Agent 工具白名单后运行。
- 新增 `classify_task_intent` 关键词或协作路线后运行。
- 修改入口 Agent 的 `IDENTITY.md`、`SOUL.md`、`TOOLS.md` 后运行。
- 用户反馈“这个任务交错 Agent 了”之后，先把失败样例补进脚本，再修规则。

## 维护原则

路由样例应该来自真实使用问题，不要为了测试通过而写过窄的句子。新增能力时优先补一个能代表真实表达的样例，并声明目标 Agent 的关键工具门禁，再决定是否需要新增工具或只调整提示词。
