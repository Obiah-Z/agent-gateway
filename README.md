# AI Agent Gateway

AI Agent Gateway 是一个基于 Python 的智能体运行网关，用于承载多轮对话、工具调用、多通道接入、主动任务调度、可靠消息投递和运行观测。系统采用“入口层、任务队列、分布式 lane、Worker 池、可靠投递、状态存储、观测面板”分层设计，适合个人自动化、本地智能体运行时和可扩展 Agent 服务编排。

当前模型接入使用 Anthropic Messages API 兼容协议，默认可接入 DeepSeek Anthropic 兼容接口，也可以切换到其他兼容服务。

## 核心能力

- `多通道入口`：支持 CLI、飞书 Webhook、飞书长连接、Telegram 等消息入口，并统一转换为入站消息模型。
- `Agent 执行闭环`：围绕模型 `stop_reason` 处理模型回复、工具调用、工具结果回填和多轮推理。
- `分布式 Lane`：按 `session_key` 获取 Redis lane ownership，保证同一会话串行执行，不同会话并行执行。
- `后台 Worker 池`：入站消息、Cron、Heartbeat 和长任务进入任务队列，由 worker 消费执行。
- `可靠出站投递`：回复先写入投递事实状态，再由 delivery worker 发送到飞书等通道，支持重试、死信和重建队列。
- `状态外置`：PostgreSQL 承载会话、任务、事件、错误、指标、记忆索引、投递状态和 lane 状态；本地 JSON/JSONL 保留为审计与兜底。
- `可观测运维`：Dashboard、WebSocket 控制面、Prometheus metrics、运行事件流、最近错误、任务面板、lane doctor 和 readiness smoke。
- `Workspace 扩展`：通过 Markdown Prompt、Memory、Skill、Cron 配置和 Agent 局部配置扩展系统行为。

## 架构概览

```text
飞书 / CLI / Webhook / Telegram
        ↓
gateway-api / ChannelRuntime
        ↓
agent_inbound task
        ↓
RabbitMQ 入站分区 broker
        ↓
gateway-worker pool
        ↓
Redis session lane ownership
        ↓
AgentLoopRunner / ToolRegistry / Skill Runtime
        ↓
SessionStore / PostgreSQL
        ↓
DeliveryQueue / PostgreSQL delivery_entries
        ↓
RabbitMQ 出站 broker
        ↓
gateway-delivery
        ↓
飞书 / CLI / Telegram
```

关键约束：

- 入口层只做解析、验签、去重、标准化和入队，避免 Webhook 超时。
- RabbitMQ 负责可靠排队、分区、ack、nack、DLQ 和削峰。
- Redis 负责 session lane ownership、TTL、续租、去重和幂等。
- PostgreSQL 负责事实状态、审计、查询和恢复。
- Worker 执行 AgentLoop，长模型调用期间续租 lane；worker 崩溃后由 TTL 释放并接管。

## 快速开始

### 本地运行

```bash
cd ~/Desktop/claw0/gateway
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

至少配置：

```env
ANTHROPIC_API_KEY=你的模型接口密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro
```

启动前检查并运行：

```bash
agent-gateway doctor
agent-gateway postgres-init
agent-gateway postgres-check-schema
agent-gateway serve
```

默认访问地址：

| 能力 | 地址 |
| --- | --- |
| Dashboard | `http://127.0.0.1:8780` |
| Prometheus metrics | `http://127.0.0.1:8780/metrics` |
| WebSocket 控制面 | `ws://127.0.0.1:8765` |
| 飞书 Webhook | `http://127.0.0.1:8766/webhooks/feishu` |

### Docker Compose 单进程模式

```bash
cd ~/Desktop/claw0/gateway
cp .env.example .env
docker compose up -d --build
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-init
docker compose exec gateway agent-gateway postgres-check-schema
```

执行 readiness 验收：

```bash
docker compose exec gateway python scripts/smoke_distributed_lane.py --scenario readiness --rabbitmq-url amqp://admin:admin123@rabbitmq:5672/ --redis-url redis://redis:6379/0 --postgres-url postgresql://postgres:postgres@postgres:5432/postgres
```

### Docker Compose 多角色模式

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml up -d --build
```

服务拆分：

| 服务 | 职责 |
| --- | --- |
| `gateway-api` | 飞书 Webhook、入站标准化、入站任务 enqueue |
| `gateway-worker` | 消费任务、获取 lane、执行 AgentLoop 和工具调用 |
| `gateway-delivery` | 消费可靠投递队列并发送出站消息 |
| `gateway-scheduler` | 触发 Cron / Heartbeat |
| `gateway-dashboard` | Dashboard、WebSocket 控制面、观测后台 |

### Docker Compose 多 Worker 模式

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.roles.yml \
  -f docker-compose.workers.yml \
  up -d --build
```

该模式会启动：

```text
gateway-worker-1
gateway-worker-2
gateway-worker-3
```

每个 worker 都有独立 `GATEWAY_TASK_WORKER_ID`，便于 lane owner、任务事件和 Dashboard 排障。

查看最终服务：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.roles.yml \
  -f docker-compose.workers.yml \
  config --services
```

停止时必须使用相同的 Compose 文件组合：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.roles.yml \
  -f docker-compose.workers.yml \
  down
```

完整说明见 [Docker Compose 部署说明](deploy/docker-compose.md)。

## 配置文件

| 路径 | 说明 |
| --- | --- |
| `.env` | 密钥、端口、中间件、模型、通道和运行开关 |
| `config/agents.json` | Agent 定义、工具策略、记忆策略和提示词策略 |
| `config/bindings.json` | channel/account/peer/session 到 Agent 的绑定规则 |
| `config/channels.json` | CLI、飞书、Telegram 等通道账号配置 |
| `config/profiles.json` | 模型服务 profile |
| `workspace/` | Prompt、Memory、Skills、Cron、Heartbeat、新闻源和运行工作区 |
| `data/` | 本地 JSONL 审计、fallback 状态和运行文件 |

## 运行角色

`GATEWAY_RUNTIME_ROLES` 控制当前进程启动哪些运行边界：

| 角色 | 说明 |
| --- | --- |
| `all` | 单进程启动所有能力，适合本地开发和最小部署 |
| `api` | 启动入站通道和 Webhook，不启动 Agent worker |
| `worker` | 消费后台任务和 `agent_inbound`，执行 AgentLoop |
| `delivery` | 消费可靠投递队列并发送出站消息 |
| `scheduler` | 触发 Cron / Heartbeat，并写入任务队列 |
| `dashboard` | 启动 Dashboard、控制面和观测后台 |
| `control` | 仅启动 WebSocket JSON-RPC 控制面 |
| `observability` | 启动指标和告警采集后台 |

## 飞书接入

Webhook 模式推荐用于稳定部署：

```env
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_VERIFICATION_TOKEN=
FEISHU_ENCRYPT_KEY=
FEISHU_WEBHOOK_HOST=0.0.0.0
FEISHU_WEBHOOK_PORT=8766
FEISHU_WEBHOOK_PATH=/webhooks/feishu
```

飞书后台填写：

```text
https://你的域名/webhooks/feishu
```

公网 HTTPS 建议通过 Caddy 或 Nginx 反向代理到宿主机 `127.0.0.1:8766`。Dashboard 和控制面默认只绑定本机，不建议裸奔公网。详见 [反向代理与 HTTPS 部署指南](deploy/reverse-proxy.md)。

## Skill 与主动任务

Skill 位于：

```text
workspace/skills/
```

Cron 支持：

```text
workspace/CRON.json
workspace/agents/<agent_id>/CRON.json
```

明确的长任务命令可以进入后台任务队列，由 worker 执行：

```env
GATEWAY_BACKGROUND_INBOUND_COMMANDS=/github-repo-analyzer,/space-advisor
```

典型自用能力包括：

- 服务器空间巡检，只分析不自动删除。
- GitHub 热门仓库发现和技能灵感推荐。
- GitHub 仓库分析，并将结果落地为 Markdown 报告。
- 新闻源采集与定期摘要。

## 运维命令

启动前检查：

```bash
agent-gateway doctor
agent-gateway doctor --json
```

PostgreSQL：

```bash
agent-gateway postgres-init
agent-gateway postgres-check-schema
agent-gateway postgres-migrate-local --dry-run
agent-gateway postgres-migrate-local
```

分布式 lane：

```bash
agent-gateway lane-doctor
python scripts/smoke_distributed_lane.py --scenario readiness
```

可靠投递：

```bash
agent-gateway delivery-republish
```

Cron：

```bash
agent-gateway cron-trigger <job_id>
agent-gateway cron-trigger <job_id> --no-flush
```

## Dashboard 与控制面

Dashboard 默认运行在：

```text
http://127.0.0.1:8780
```

常用 JSON-RPC 方法：

| 方法 | 说明 |
| --- | --- |
| `runtime.status` | 运行态快照 |
| `health.check` | 健康检查 |
| `events.tail` | 最近运行事件 |
| `errors.recent` | 最近错误、失败或拒绝事件 |
| `memory.recent` | 最近记忆写入 |
| `tasks.list/get/cancel/retry` | 后台任务查看、详情、取消和重试 |
| `tasks.lanes.*` | session lane 查询、诊断、恢复预检和审计 |
| `delivery.stats/list/retry/discard/flush/republish` | 可靠投递队列运维 |
| `cron.list/trigger` | 主动任务查看与触发 |
| `agents.*`、`bindings.*`、`channels.*`、`profiles.*` | 运行配置查看、修改、保存和重载 |

## 压测与容量基线

安全矩阵 dry-run：

```bash
python scripts/run_capacity_matrix.py --dry-run
```

执行安全矩阵并生成基线：

```bash
python scripts/run_capacity_matrix.py
python scripts/build_capacity_baseline.py
```

真实模型和飞书场景必须显式开启：

```bash
python scripts/run_capacity_matrix.py --dry-run --include-external
```

压测报告输出：

```text
workspace/reports/load-tests/*.json
workspace/reports/load-tests/*.md
workspace/reports/capacity-baseline.md
```

详见 [20.8 压测执行清单](doc/20.8%20压测执行清单.md)。

## 测试

```bash
cd ~/Desktop/claw0/gateway
./.venv/bin/python -m compileall agent_gateway tests scripts
./.venv/bin/python -m pytest tests -q
```

## 文档

- [项目架构说明](doc/项目架构说明.md)
- [消息闭环实现说明](doc/消息闭环实现说明.md)
- [Docker Compose 部署说明](deploy/docker-compose.md)
- [备份与恢复指南](deploy/backup-restore.md)
- [反向代理与 HTTPS 部署指南](deploy/reverse-proxy.md)
- [分布式 Lane 运维 Runbook](doc/分布式Lane运维Runbook.md)
- [20.8 压测执行清单](doc/20.8%20压测执行清单.md)
- [项目计划](PROJECT_PLAN.md)

## 安全说明

- `.env`、数据库 dump、RabbitMQ/Redis volume 备份不能提交到公开仓库。
- Dashboard 默认无内建鉴权，只建议绑定本机或可信内网。
- WebSocket 控制面不要直接暴露公网。
- 飞书 Webhook 生产环境应使用 HTTPS 反向代理。
- RabbitMQ、PostgreSQL、Redis 不应暴露公网。
