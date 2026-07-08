# Agent 路由验收清单

这份清单用于验证入口 Agent 是否能把常见用户请求稳定分流到正确的能力 Agent。它不调用大模型，也不触发真实通道投递，只调用内置工具 `classify_task_intent`。

## 运行命令

```bash
python scripts/eval_agent_routing.py
```

输出 JSON：

```bash
python scripts/eval_agent_routing.py --json
```

通过标准：默认样例全部 `PASS`，脚本退出码为 `0`。

## 当前覆盖场景

| 场景 | 期望入口判断 | 期望 Agent | 是否多 Agent 协作 |
| --- | --- | --- | --- |
| 普通聊天 | `chat` | `main` | 否 |
| GitHub 仓库普通分析 | `repo-analysis` | `repo-analyzer` | 否 |
| GitHub 仓库阅读路线 | `repo-reading-guide` | `repo-analyzer` | 否 |
| GitHub 仓库采纳/风险/报告 | `repo-adoption` | `repo-analyzer` | 是 |
| 技术选型和验证计划 | `research-option-validation` | `research` | 是 |
| 阶段规划 | `planning` | `planner` | 否 |
| Agent 能力目录 | `agent-capabilities` | `main` | 否 |
| 运维排障 | `ops` | `ops` | 否 |
| 饮食记录 | `diet` | `diet-assistant-zhanghaibo` | 否 |
| 个人待办/复盘 | `personal` | `personal-secretary-zhanghaibo` | 否 |
| 文档整理 | `document` | `doc-writer` | 否 |
| 风险审查 | `review` | `reviewer` | 否 |

## 使用时机

- 新增 Agent 或调整 Agent 职责后运行。
- 新增 `classify_task_intent` 关键词或协作路线后运行。
- 修改入口 Agent 的 `IDENTITY.md`、`SOUL.md`、`TOOLS.md` 后运行。
- 用户反馈“这个任务交错 Agent 了”之后，先把失败样例补进脚本，再修规则。

## 维护原则

路由样例应该来自真实使用问题，不要为了测试通过而写过窄的句子。新增能力时优先补一个能代表真实表达的样例，再决定是否需要新增工具或只调整提示词。
