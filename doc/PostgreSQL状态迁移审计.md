# PostgreSQL 状态迁移审计

更新时间：2026-06-28

## 审计结论

当前网关的核心运行状态已经完成 PostgreSQL 主存储适配；本地 JSON、JSONL 和 Markdown 文件继续保留为配置源、Prompt 资产、审计副本、回放来源和降级路径。

这意味着后续不应盲目把所有文件读写迁移到数据库。`workspace/` 下的提示词、Skill、Cron 配置和新闻源属于可编辑运行资产，继续文件化更利于本地开发、Git 管理和人工审查；`data/` 下的运行状态在开启 `GATEWAY_POSTGRES_ENABLED=true` 后优先读写 PostgreSQL，本地文件作为 fallback/audit。

## 已数据库化的运行状态

| 状态类别 | PostgreSQL 表 | 运行时策略 | 本地文件角色 |
| --- | --- | --- | --- |
| Agent 配置 | `agents` | 控制面保存和启动加载优先使用数据库 | `config/agents.json` 保留为 fallback/audit |
| 路由绑定 | `bindings` | 控制面保存和启动加载优先使用数据库 | `config/bindings.json` 保留为 fallback/audit |
| 模型 Profile | `profiles` | 控制面保存和启动加载优先使用数据库 | `config/profiles.json` 保留为 fallback/audit |
| 通道账号 | `channels` | 控制面保存和启动加载优先使用数据库 | `config/channels.json` 保留为 fallback/audit |
| 可靠投递队列 | `delivery_entries` | enqueue、retry、ack、discard 优先写数据库 | `data/delivery-queue/` 保留为兜底队列文件 |
| 会话历史 | `sessions` | 会话读写优先使用数据库 | `data/sessions/**/*.jsonl` 保留为审计和回放 |
| 后台任务 | `tasks` | 任务创建、预占、完成、失败优先使用数据库 | `data/tasks/*.json` 保留为 fallback |
| 运行事件 | `runtime_events` | 事件写入和 Dashboard 查询优先使用数据库 | `data/events/*.jsonl` 保留为审计 |
| 最近错误 | `errors` | 告警和错误视图优先使用数据库 | `data/alerts/*.jsonl` 保留为审计 |
| 指标快照 | `metrics` | 指标写入和趋势查询优先使用数据库 | `data/metrics/*.jsonl` 保留为审计 |
| 记忆条目 | `memory_entries` | 记忆写入、最近记忆、统计和召回优先使用数据库 | `workspace/memory/daily/*.jsonl` 保留为回放 |
| 配置审计 | `config_audits` | 控制面配置变更写入数据库 | 本地配置文件保留最终快照 |
| 飞书事件去重 | `feishu_dedup_entries` | Redis 优先，PostgreSQL 次级兜底，本地文件最终兜底 | `data/feishu-webhook/dedup/*.jsonl` 保留为降级 |
| 飞书 Webhook 审计 | `feishu_webhook_events` | Webhook 入站审计优先写数据库 | `data/feishu-webhook/events.jsonl` 保留为审计 |
| 飞书 onboarding | `feishu_onboarding_sessions` | 扫码绑定会话优先读写数据库 | `data/feishu-onboarding/sessions.json` 保留为兜底 |
| 通道 offset | `channel_offsets` | Telegram 等消费 offset 优先读写数据库 | `data/channel-state/*/offset-*.txt` 保留为兜底 |
| Cron 运行记录 | `cron_runs` | Cron 运行结果优先写数据库 | `workspace/cron/cron-runs.jsonl` 保留为审计 |
| 新闻简报状态 | `news_items` | collected/seen 状态优先读写数据库 | `data/*-digest/*.jsonl` 保留为兜底 |
| 飞书卡片状态 | `feishu_card_states` | 卡片分页、展开和收起状态优先读写数据库 | `data/channel-state/feishu/*/cards/*.json` 保留为兜底 |

## 仍应文件化的资产

| 文件范围 | 类型 | 不迁移原因 |
| --- | --- | --- |
| `workspace/IDENTITY.md`、`SOUL.md`、`TOOLS.md`、`BOOTSTRAP.md`、`AGENTS.md`、`USER.md` | Prompt 资产 | 需要人工编辑、Git diff、按 Agent 覆盖和快速回滚 |
| `workspace/skills/*/SKILL.md` | Skill 定义 | Skill 本质是可版本化能力说明和本地脚本入口 |
| `workspace/CRON.json`、`workspace/HEARTBEAT.md` | 主动任务配置 | 仍作为本地可审查配置源，运行记录已进入数据库 |
| `workspace/news/*.json` 等新闻源配置 | 数据源配置 | 属于采集源定义，不是运行结果 |
| `.env`、`.env.example` | 环境变量配置 | 包含密钥引用、端口和部署差异，不应入数据库主存储 |
| `pyproject.toml`、README、项目文档 | 工程资产 | 属于代码仓库内容 |

## 故意保留的文件写入

| 代码路径 | 作用 | 审计判断 |
| --- | --- | --- |
| `read_file`、`write_file` 内置工具 | 允许 Agent 在 workspace 内读写用户授权文件 | 用户工作区能力，不属于网关运行状态 |
| `materialize_agent_template()` | 创建 Agent 局部 Prompt 模板 | 生成可人工修改的运行资产 |
| 飞书 onboarding 模板创建 | 为新 Agent 初始化 `IDENTITY.md`、`SOUL.md` | 生成 workspace 资产 |
| JSONL fallback 写入 | 数据库不可用时保留可恢复副本 | 生产降级和审计需要 |

## 验收覆盖

当前 `agent-gateway postgres-smoke` 已覆盖：

- 配置表：`agents`、`bindings`、`profiles`、`channels`
- 核心运行表：`sessions`、`tasks`、`runtime_events`、`memory_entries`
- 观测表：`metrics`、`errors`
- 出站投递：`delivery_entries`
- 通道状态：`channel_offsets`、`feishu_card_states`
- 主动任务和内容状态：`cron_runs`、`news_items`
- 本地 fallback：会话、任务、事件、记忆、指标、告警、投递、Telegram offset、Cron、新闻状态和飞书卡片状态文件

辅助验证：

```bash
agent-gateway postgres-init
agent-gateway postgres-migrate-local --dry-run
agent-gateway postgres-smoke
```

代码回归：

```bash
./.venv/bin/python -m compileall agent_gateway tests
./.venv/bin/python -m pytest tests -q
```

## 后续建议

1. Phase 20.6 再处理出站投递队列的分布式 backend，不再继续扩大 PostgreSQL 表范围。
2. Phase 17 做会话和记忆治理时，优先基于 PostgreSQL 增加归档、删除、复审和压缩能力。
3. Phase 12 做 Dashboard 鉴权前，不建议把数据库管理能力暴露到公网。
4. 如果后续引入 RabbitMQ 或 Redis Streams，`delivery_entries` 仍可作为审计表和人工重试视图。
