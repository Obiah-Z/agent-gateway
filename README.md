# AI Agent Gateway 智能体网关系统

AI Agent Gateway 是一个基于 Python 构建的智能体运行网关，用于承载多轮对话、工具调用、多通道接入、主动任务调度、可靠消息投递和运行观测等场景。项目以“稳定运行闭环”为核心目标，将用户输入、Agent 执行、会话持久化、出站投递和运维排障拆分为清晰的运行边界，逐步形成可本地部署、可扩展、可观测的 AI Agent 运行框架。

当前项目采用 Anthropic Messages API 兼容调用方式，默认可接入 DeepSeek Anthropic 兼容接口，也可以切换到其他兼容服务。

## 项目定位

本项目不是单纯的聊天机器人，而是面向个人自动化与智能体运行时的网关系统。它重点解决以下问题：

- 多个消息入口如何统一进入同一套 Agent 执行链路。
- 多轮会话、长期记忆和技能上下文如何稳定注入模型。
- 模型工具调用、结果解析和后续推理如何形成闭环。
- 普通回复、Cron、Heartbeat 等后台任务如何可靠投递到外部平台。
- 出错时如何快速定位是路由、模型、工具、投递还是通道问题。

## 核心能力

- `Agent 执行闭环`：围绕 `stop_reason` 构建模型调用、工具调用、结果回填和多轮交互处理流程。
- `多通道接入`：支持 CLI、Telegram、飞书 Webhook 和飞书长连接，并将不同来源统一抽象为入站消息。
- `消息路由`：基于 `config/bindings.json` 将 channel、account、peer、session 分流到不同 Agent。
- `会话持久化`：使用 JSONL 保存会话历史，支持历史重放、上下文压缩和长期对话保护。
- `工具调用`：通过工具注册表和 dispatch table 封装 bash、文件读写、记忆写入、联网搜索、GitHub 分析等能力。
- `Workspace 扩展`：通过 `SOUL.md`、`TOOLS.md`、`MEMORY.md`、Agent 局部提示词和 `skills/` 注入运行上下文。
- `主动任务`：支持 Heartbeat、全局 Cron、Agent 局部 Cron、新闻采集和技能调度。
- `可靠投递`：普通回复和后台任务统一先写入可靠投递队列；PostgreSQL 作为事实状态表，RabbitMQ 可作为分布式分发层支持多 delivery worker、重试和 DLQ。
- `运维观测`：提供 Dashboard、WebSocket 控制面、运行事件流、最近错误、指标快照和告警视图。

## 消息闭环

系统的核心链路如下：

```text
多通道输入
  -> ChannelRuntime
  -> GatewayDispatcher
  -> CommandQueue / 命名 lane
  -> AgentLoopRunner
  -> SessionStore
  -> DeliveryQueue
  -> PostgreSQL delivery_entries
  -> RabbitMQ broker（可选）
  -> DeliveryRuntime
  -> CLI / 飞书 / Telegram
```

关键设计点：

- 入站消息先进入统一队列，避免各通道直接耦合 Agent 执行逻辑。
- 同一会话使用命名 lane 串行处理，降低会话历史并发写入风险。
- Agent 执行结果先写会话，再写出站投递队列，避免发送失败导致结果丢失。
- 出站投递由后台运行时统一负责，支持失败重试、重放、DLQ 和状态查看。
- 每个关键节点写入 runtime event，Dashboard 可以按事件查看运行链路。

更完整的链路说明见 [消息闭环实现说明](doc/消息闭环实现说明.md)。

## 目录结构

```text
gateway/
  agent_gateway/
    runtime/
      domain/            # 领域模型、消息、路由、事件等核心结构
      execution/         # ChannelRuntime、Dispatcher、Agent Loop、DeliveryRuntime
      state/             # 会话、投递队列、事件、指标、告警等本地状态
      observability/     # 运行观测、指标和告警聚合
    gateways/
      messaging/         # CLI、Telegram 等消息通道
      feishu/            # 飞书 Webhook、长连接和 onboarding
      control/           # WebSocket JSON-RPC 控制面
    ai/
      context/           # Prompt、记忆、技能和上下文装配
      tools/             # 工具注册与工具实现
      news/              # 新闻源采集与摘要
    monitoring/          # 本地 Dashboard
    app.py               # 应用装配入口
    config.py            # 运行配置
    config_loader.py     # 静态配置加载
  config/                # agents、bindings、channels、profiles
  workspace/             # Prompt、记忆、skills、Cron、Heartbeat、新闻源
  data/                  # sessions、delivery queue、events、metrics、alerts
  doc/                   # 项目说明文档
  tests/                 # 自动化测试
```

## 快速开始

### 1. 安装

```bash
cd ~/Desktop/claw0/gateway
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

### 2. 配置模型

至少需要在 `.env` 中配置以下变量：

```env
ANTHROPIC_API_KEY=你的模型接口密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro
```

`.env` 包含密钥和本机运行参数，不应提交到 Git。

### 3. 启动服务

```bash
agent-gateway serve
```

默认监听地址：

```text
WebSocket 控制面: ws://127.0.0.1:8765
飞书 Webhook:   http://127.0.0.1:8766/webhooks/feishu
运维 Dashboard: http://127.0.0.1:8780
```

### 4. 手动触发 Cron

```bash
agent-gateway cron-trigger <job_id>
agent-gateway cron-trigger <job_id> --no-flush
```

`--no-flush` 表示只写入可靠投递队列，不立即刷送。

## Docker Compose 部署

项目提供单机 Docker Compose 编排，用于同时启动 Gateway、Redis、PostgreSQL 和 RabbitMQ。适合新机器快速拉起整套服务：

```bash
cd ~/Desktop/claw0/gateway
cp .env.example .env
docker compose up -d --build
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-init
docker compose exec gateway agent-gateway postgres-check-schema
```

如果本机 Docker 没有 Compose v2 插件，可把 `docker compose` 替换为 `docker-compose`。

默认端口只绑定本机回环地址：

```text
Dashboard:        http://127.0.0.1:8780
Prometheus metrics: http://127.0.0.1:8780/metrics
WebSocket 控制面: ws://127.0.0.1:8765
飞书 Webhook:     http://127.0.0.1:8766/webhooks/feishu
RabbitMQ 管理台:  http://127.0.0.1:15672
```

常见检查：

```bash
docker compose ps
docker compose logs -f gateway
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-check-schema
```

常见故障：

- `doctor` 报 `FAIL`：先按输出修正 `.env`、目录权限、数据库连接或模型配置。
- 飞书 Webhook 没有回复：检查 `FEISHU_WEBHOOK_*`、应用验签、加密密钥和机器人可见范围。
- PostgreSQL 初始化失败：先确认容器内 `postgres` 已就绪，再执行 `postgres-init` 和 `postgres-check-schema`。
- RabbitMQ 无法连接：检查 `GATEWAY_RABBITMQ_URL` 是否仍指向容器服务名 `rabbitmq`。

升级步骤：

```bash
cd ~/Desktop/claw0/gateway
git pull
docker compose up -d --build
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-check-schema
```

详细说明见 [Docker Compose 部署说明](deploy/docker-compose.md)。

## systemd 部署

非 Docker 场景可以使用 systemd 托管 Gateway：

```bash
sudo mkdir -p /etc/agent-gateway
sudo cp deploy/systemd/agent-gateway.env.example /etc/agent-gateway/agent-gateway.env
sudo cp deploy/systemd/agent-gateway.service /etc/systemd/system/agent-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable agent-gateway
```

启动前建议先运行：

```bash
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env doctor
```

常见检查：

```bash
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env doctor
systemctl status agent-gateway
journalctl -u agent-gateway -n 200 --no-pager
```

升级步骤：

```bash
cd ~/Desktop/claw0/gateway
git pull
source .venv/bin/activate
pip install -e .
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env doctor
sudo systemctl restart agent-gateway
```

详细说明见 [systemd 部署说明](deploy/systemd.md)。

## 配置说明

| 文件 | 说明 |
| --- | --- |
| `.env` | 运行时密钥、端口、目录、模型参数和通道参数 |
| `config/agents.json` | Agent 定义、工具策略、记忆策略和提示词策略 |
| `config/bindings.json` | channel/account/peer/session 到 Agent 的路由规则 |
| `config/channels.json` | CLI、Telegram、飞书等通道账号配置 |
| `config/profiles.json` | 模型服务 profile |
| `workspace/` | 系统提示词、长期记忆、skills、Heartbeat、Cron、新闻源和 Agent 局部提示词 |

## PostgreSQL 状态存储与迁移

系统已经支持把配置和运行状态迁移到 PostgreSQL，并默认优先从数据库读取；本地 JSON/JSONL 文件仍保留为审计、回放和兜底来源。

当前已覆盖的数据包括：

- `agents`、`bindings`、`profiles`、`channels`
- `delivery_entries`、`sessions`、`tasks`
- `runtime_events`、`errors`
- `metrics`、`memory_entries`
- `config_audits`
- `feishu_dedup_entries`、`feishu_webhook_events`
- `feishu_onboarding_sessions`
- `channel_offsets`
- `cron_runs`
- `news_items`
- `feishu_card_states`

### 1. 配置连接

`.env` 中的默认 PostgreSQL 配置如下：

```env
GATEWAY_POSTGRES_ENABLED=true
GATEWAY_POSTGRES_URL=postgresql://postgres:postgres@127.0.0.1:5432/postgres
GATEWAY_POSTGRES_CONNECT_TIMEOUT_SECONDS=2.0
```

说明：

- `GATEWAY_POSTGRES_ENABLED=true` 时，运行时默认优先读取和写入 PostgreSQL。
- 如需临时降级，可把 `GATEWAY_POSTGRES_ENABLED=false`，运行时会回到本地文件读写。
- `postgres-migrate-local` 命令会显式写入 PostgreSQL，不依赖 `GATEWAY_POSTGRES_ENABLED`。
- 新环境应先完成建表、schema 检查和回填验证，再启动服务。

### 2. 初始化表结构

```bash
agent-gateway postgres-init --print-sql
agent-gateway postgres-init
agent-gateway postgres-check-schema
```

`--print-sql` 只打印建表 SQL，不构建完整网关应用；不带参数时通过本机 `psql` 执行 `CREATE TABLE IF NOT EXISTS` 和索引初始化。

`postgres-check-schema` 会读取 `information_schema.columns`，检查实库表、列和基础类型是否与当前代码声明一致。旧库如果曾经用早期 schema 初始化过，建议先运行该命令；如果出现 `missing_tables`、`missing_columns` 或 `type_mismatches`，应先处理 schema 漂移，再执行回填或 smoke。

### 3. 迁移本地数据

先 dry-run 预检：

```bash
agent-gateway postgres-migrate-local --dry-run
```

确认扫描数量和错误列表后执行实际回填：

```bash
agent-gateway postgres-migrate-local
```

回填行为：

- 只读取本地文件，不删除、不移动源数据。
- 使用稳定主键和 `upsert`，重复执行不会重复插入配置和运行记录。
- 飞书 Webhook 的旧去重文件和审计文件也会回填到 PostgreSQL，便于多实例共享入站去重状态和集中查询审计记录。
- 飞书扫码接入的旧 onboarding 会话也会回填到 PostgreSQL，运行时会优先使用数据库并保留本地 `sessions.json` 兜底。
- Telegram 轮询 offset 会回填到 PostgreSQL，运行时优先使用数据库 offset 并保留本地 offset 文件兜底。
- Cron 运行记录会回填到 PostgreSQL，运行时优先写入数据库并保留本地 `cron-runs.jsonl` 兜底。
- AI Agent 简报和 GitHub Skill 简报的已采集/已推送条目会回填到 PostgreSQL，运行时优先用 `news_items` 去重并继续保留本地 JSONL 兜底。
- 飞书有状态卡片的分页、展开和收起状态会回填到 PostgreSQL，运行时优先读取数据库并继续保留本地卡片 JSON 兜底。
- 大量 runtime events 和 metrics 使用批量 upsert，避免逐条启动 `psql`。
- 本地文件继续作为保底路径，数据库不可用时读路径会回退到本地。

### 4. 启用数据库优先读取

回填完成后再开启：

```env
GATEWAY_POSTGRES_ENABLED=true
```

数据库优先模式下，控制面和 Dashboard 的会话、任务、投递队列、事件、错误、指标、记忆以及配置读取会优先访问 PostgreSQL；控制面配置保存会先写 PostgreSQL，再写本地 JSON 作为 fallback/audit；记忆召回会优先使用 `memory_entries`，新闻简报去重会优先使用 `news_items`，飞书卡片交互状态会优先使用 `feishu_card_states`，当数据库无数据或读取失败时，仍回退到本地 JSON/JSONL。

控制面 `runtime.status` 会在 PostgreSQL 启用且连通时返回 `postgres.schema`，`health.check` 会额外生成 `postgres.schema` 检查项；如果表结构和当前代码声明不一致，会以 warning 形式提示 schema drift。

### 5. 验证

常用验证命令：

```bash
psql "$GATEWAY_POSTGRES_URL" -c "select count(*) from runtime_events;"
agent-gateway postgres-check-schema
agent-gateway postgres-smoke
GATEWAY_POSTGRES_ENABLED=true agent-gateway serve
```

`postgres-smoke` 会临时开启 PostgreSQL 主存储，写入带唯一 marker 的配置表、会话、任务、运行事件、记忆、指标、告警、投递队列、Telegram offset、Cron 运行记录、新闻简报状态和飞书卡片状态，并同时检查本地 JSON/JSONL fallback 文件是否生成。该命令不调用模型，也不会向外部通道发送消息。

本机实测回填约 1.65 万条配置与运行数据，批量写入约 4.4 秒完成。

状态迁移边界详见 [PostgreSQL状态迁移审计](doc/PostgreSQL状态迁移审计.md)。其中 `workspace/` 下的 Prompt、Skill、Cron 配置和新闻源仍作为可版本化运行资产保留文件形态；数据库主要承载运行状态、审计记录、队列状态和可查询视图。

## 分布式可靠投递队列

可靠投递采用“PostgreSQL 事实状态表 + RabbitMQ 分发层”的混合设计：

- `delivery_entries` 保存完整投递状态、正文、重试次数、下一次重试时间和锁定信息。
- RabbitMQ 只保存 `delivery_id`、channel、account、correlation_id、idempotency_key 等轻量引用，不保存完整消息正文。
- delivery worker 消费 RabbitMQ 消息后，必须回查 PostgreSQL 并通过 `FOR UPDATE SKIP LOCKED` 原子预占记录，避免多 worker 重复发送。
- RabbitMQ 不可用时，`DeliveryRuntime` 会回退到 PostgreSQL/本地文件轮询，并写入 broker warning event。
- RabbitMQ 队列被清空或重启后，可以通过控制面、Dashboard 或 CLI 从事实状态重建投递引用。

启用 RabbitMQ 前，建议先确认 PostgreSQL schema 正常：

```bash
agent-gateway postgres-init
agent-gateway postgres-check-schema
```

`.env` 中开启 broker：

```env
GATEWAY_DELIVERY_BROKER=rabbitmq
GATEWAY_RABBITMQ_URL=amqp://admin:admin123@127.0.0.1:5672/
GATEWAY_RABBITMQ_EXCHANGE=agent_gateway.delivery
GATEWAY_RABBITMQ_QUEUE=agent_gateway.delivery.outbound
GATEWAY_RABBITMQ_DEAD_LETTER_EXCHANGE=agent_gateway.delivery.dlx
GATEWAY_RABBITMQ_DEAD_LETTER_QUEUE=agent_gateway.delivery.dead
GATEWAY_RABBITMQ_CONNECT_TIMEOUT_SECONDS=2.0
```

常用运维命令：

```bash
agent-gateway delivery-republish
```

`delivery-republish` 会重新发布 pending 和 retrying 投递引用到 RabbitMQ，不会复制完整消息正文。Dashboard 的“投递队列”面板也提供“重建队列”按钮，并展示 pending、retrying、failed、broker 队列和 DLQ 状态。

## 分布式入站任务与 Lane

入站分布式链路采用“TaskStore/PostgreSQL 事实状态 + RabbitMQ 轻量任务引用 + Redis session lane ownership”的组合：

- 外部通道接入后先解析、验签、去重和标准化，不在入口层直接长时间执行 Agent。
- 开启入站任务队列后，非 CLI 入站会先写入 `agent_inbound` 任务，再由 task worker 执行。
- `GATEWAY_INBOUND_BROKER=rabbitmq` 时，任务落库后会把 `task_id`、`session_key`、partition 等轻量引用发布到 RabbitMQ 分区队列。
- Redis lane ownership 用 `session_key` 做互斥，保证同一会话同一时间只有一个 worker 进入 AgentLoop；不同 session 可并行执行。
- `GATEWAY_TASK_WORKER_ID` 用于标识当前 worker，建议多实例部署时每个实例使用不同 ID；`GATEWAY_TASK_WORKER_CONCURRENCY` 用于控制单实例 worker 池并发。

典型配置：

```env
GATEWAY_INBOUND_TASK_QUEUE_ENABLED=true
GATEWAY_INBOUND_BROKER=rabbitmq
GATEWAY_INBOUND_RABBITMQ_PARTITIONS=16
GATEWAY_INBOUND_RABBITMQ_PREFETCH=1
GATEWAY_REDIS_ENABLED=true
GATEWAY_INBOUND_SESSION_LOCK_TTL_SECONDS=300
GATEWAY_INBOUND_SESSION_LOCK_RENEW_INTERVAL_SECONDS=0
GATEWAY_TASK_WORKER_ID=worker-1
GATEWAY_TASK_WORKER_CONCURRENCY=4
```

快速验收：

```bash
python scripts/smoke_distributed_lane.py --scenario readiness
```

该命令会检查 Redis、PostgreSQL、RabbitMQ 入站 broker、`agent_inbound` worker、持久 lane 和可靠出站投递是否满足最终分布式 lane 运行条件。

## 飞书接入

项目支持两种飞书接入方式：

- `长连接模式`：适合本地开发或不方便暴露公网 Webhook 的场景，依赖本机已配置好的 `lark-cli`。
- `Webhook 模式`：适合公网部署或通过反向代理接入的场景，需要配置飞书事件回调地址。

Webhook 模式常用环境变量包括：

```env
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_VERIFICATION_TOKEN=
FEISHU_ENCRYPT_KEY=
FEISHU_WEBHOOK_HOST=0.0.0.0
FEISHU_WEBHOOK_PORT=8766
FEISHU_WEBHOOK_PATH=/webhooks/feishu
```

飞书 onboarding 页面用于绑定个人 Agent：

```text
http://127.0.0.1:8780/onboarding/feishu
```

## 主动任务与 Skill

Cron 支持两层配置：

- `workspace/CRON.json`：全局任务。
- `workspace/agents/<agent_id>/CRON.json`：Agent 局部任务。

后台任务默认禁止调用 `memory_write`，避免巡检、新闻简报或定时分析误写长期记忆。项目内置和自定义 Skill 放在：

```text
workspace/skills/
```

明确的长任务命令会先进入后台任务队列，再由 worker 执行。默认后台命令可通过 `.env` 扩展：

```env
GATEWAY_BACKGROUND_INBOUND_COMMANDS=/github-repo-analyzer,/space-advisor
```

当前已经支持的典型自用能力包括：

- 服务器空间巡检，只分析不自动删除。
- GitHub 热门仓库发现和技能灵感推荐。
- GitHub 仓库分析，并将分析结果落地为 Markdown 报告。

新闻源配置文件位于：

```text
workspace/agent-news-sources.json
```

当前支持 RSS、官网 HTML 页面、GitHub Releases 和 arXiv。

## 运维与可观测性

启动 `agent-gateway serve` 后，本地 Dashboard 默认运行在：

```text
http://127.0.0.1:8780
```

Dashboard 主要用于：

- 查看运行健康状态、agents、bindings、channels、profiles、heartbeat、cron 和 delivery 状态。
- 查看 pending / failed 投递队列，并执行 retry、discard、flush。
- 查看最近运行事件、最近错误和最近记忆写入。
- 查看指标快照、趋势变化、当前告警和近期告警历史。

常用 WebSocket JSON-RPC 方法：

| 方法 | 说明 |
| --- | --- |
| `runtime.status` | 查看运行态快照 |
| `health.check` | 执行健康检查 |
| `events.tail` | 查看最近运行事件 |
| `errors.recent` | 查看最近错误、失败或拒绝事件 |
| `memory.recent` | 查看最近写入的 daily memory 记录 |
| `tasks.list/get/cancel/retry` | 后台任务查看、详情、取消和重试 |
| `delivery.stats/list/retry/discard/flush/republish` | 可靠投递队列运维 |
| `cron.list/trigger` | 主动任务查看与触发 |
| `feishu.onboarding.start/status/list` | 飞书绑定会话管理 |
| `feishu.long_connection.status` | 飞书长连接消费状态 |
| `agents.*`、`bindings.*`、`channels.*`、`profiles.*` | 运行配置查看、修改、保存和重载 |

启动前检查：

```bash
agent-gateway doctor
agent-gateway doctor --json
```

`doctor` 不会启动完整网关服务，会检查模型配置、目录权限、Redis、PostgreSQL、RabbitMQ、PostgreSQL schema 和公网绑定风险；存在 `FAIL` 时返回非零退出码，便于 Docker Compose、systemd 或部署脚本提前拦截。

## 测试

```bash
cd ~/Desktop/claw0/gateway
./.venv/bin/python -m compileall agent_gateway tests
./.venv/bin/python -m pytest tests -q
```

## 文档索引

- [项目架构说明](doc/项目架构说明.md)
- [消息闭环实现说明](doc/消息闭环实现说明.md)
- [20.8 压测执行清单](doc/20.8%20压测执行清单.md)
- [Docker Compose 部署说明](deploy/docker-compose.md)
- [Docker Compose 多角色部署说明](deploy/multi-role-compose.md)
- [备份与恢复指南](deploy/backup-restore.md)
- [反向代理与 HTTPS 部署指南](deploy/reverse-proxy.md)
- [项目计划](PROJECT_PLAN.md)

## 当前边界

- 当前主要面向单机本地运行，已接入 Redis 最小协调和 PostgreSQL 状态外置，但完整多实例部署仍在推进中。
- Dashboard 默认无鉴权，仅建议绑定本机或可信网络访问。
- PostgreSQL 已支持配置、运行状态、记忆召回和可靠投递队列的优先读取、主写入、schema 初始化和本地回填；控制面配置变更已改为数据库写入先行，本地 JSON/JSONL 仍保留为审计和兜底。
- 可靠投递队列已支持 PostgreSQL primary storage 与 RabbitMQ 分发层；RabbitMQ 只作为跨 worker 唤醒和分发层，事实状态、人工处理和恢复仍以 PostgreSQL 为准。
- Agent 权限模型已支持工具策略和 capability tags，但仍需继续增强审计、校验和权限预览。
- ChannelRuntime 已完成 lane 化、背压和热重启保护；更细粒度的 per-agent 并发、低优先级延迟队列和长任务后台化仍需继续增强。
