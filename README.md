# AI Agent Gateway

> 面向多通道智能体任务调度、可靠投递和分布式执行治理的后端网关平台。

![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-session%20scheduler-DC382D?style=flat-square&logo=redis&logoColor=white)
![RabbitMQ](https://img.shields.io/badge/RabbitMQ-reliable%20queue-FF6600?style=flat-square&logo=rabbitmq&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-state%20store-4169E1?style=flat-square&logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)
![WeCom](https://img.shields.io/badge/WeCom-personal%20secretary-07C160?style=flat-square&logo=wechat&logoColor=white)

AI Agent Gateway 是一个基于 Python 构建的智能体运行网关。它把企业微信、飞书、CLI、Telegram 等入口统一成后台任务，将 Agent 执行、工具调用、会话状态、可靠投递、定时任务和运行观测拆成清晰的后端模块，用 Redis、RabbitMQ、PostgreSQL 支撑多 worker 场景下的去重、顺序、重试、恢复和可观测性。

`多通道入口` · `Agent Loop` · `个人秘书` · `会话调度` · `可靠投递` · `运维观测`

## 目录

- [核心特性](#核心特性)
- [架构预览](#架构预览)
- [快速启动](#快速启动)
- [Docker 部署](#docker-部署)
- [关键配置](#关键配置)
- [核心机制](#核心机制)
- [Dashboard 与控制面](#dashboard-与控制面)
- [压测与验证](#压测与验证)
- [文档索引](#文档索引)
- [安全说明](#安全说明)

## 核心特性

| 能力 | 说明 |
| --- | --- |
| 🔌 多通道接入 | 支持企业微信、飞书 Webhook、飞书长连接、CLI、Telegram 等入口，统一转换为 `InboundMessage`。 |
| 🤖 Agent 执行闭环 | 支持模型调用、`stop_reason` 处理、tool calling、多轮工具结果回填和会话写入。 |
| 🧑‍💼 企业微信个人秘书 | `wework-main` 提供个人计划、午间校准、晚间复盘、睡前收口和周计划/周复盘等秘书型能力。 |
| 👷 Worker 任务执行 | 入站消息、Cron、Heartbeat 和长任务进入统一任务队列，由 worker 池并发消费。 |
| 🧭 会话级严格顺序 | Redis ready index + per-session pending bucket 保证同一 session FIFO，不同 session 并行。 |
| 📮 可靠出站投递 | 回复先落事实状态，再由投递 worker 发送，支持 retry、DLQ、flush、republish 和 discard。 |
| 🗄️ 状态外置与兜底 | PostgreSQL 保存任务、会话、事件、投递、指标和 lane 状态；本地 JSONL 保留审计和 fallback。 |
| ⏱️ 主动任务体系 | 支持 Heartbeat、全局 Cron、Agent 局部 Cron、新闻简报和自定义 Skill 定时执行。 |
| 📊 运维观测 | Dashboard、WebSocket JSON-RPC、Prometheus metrics、事件流、最近错误、任务面板、lane doctor。 |
| 🧩 Workspace 扩展 | 使用 Markdown 管理 Prompt、Memory、Skills、Cron 和运行报告，方便个人自动化扩展。 |

## 架构预览

```text
┌──────────────────────────────────────────────────────────────────────┐
│                              外部入口                                │
│   企业微信 Webhook / 飞书 Webhook / 飞书长连接 / CLI / Telegram       │
│                              WebSocket                                │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                       ChannelRuntime / gateway-api                    │
│       验签 · 解密 · 去重 · 标准化 · 快速 ACK · 创建后台任务             │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         任务事实状态与入站削峰                       │
│         PostgreSQL tasks / LocalTaskStore + RabbitMQ task wakeup       │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         Redis 会话调度器                              │
│   ready index 只放 session_key · pending bucket 保存 session 内任务     │
│   busy owner + TTL + watchdog 续租保证同一 session 串行                 │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                         gateway-worker pool                           │
│         claim session 队首任务 · 执行 AgentLoop · Tool Calling          │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                           可靠出站投递                                │
│        DeliveryQueue + PostgreSQL delivery_entries + RabbitMQ broker   │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│                              外部通道                                │
│                    企业微信 / 飞书 / CLI / Telegram                    │
└──────────────────────────────────────────────────────────────────────┘

旁路观测：Dashboard / WebSocket Control Plane / Prometheus / Runtime Events / Lane Doctor
```

### 中间件职责

| 组件 | 在系统中的职责 |
| --- | --- |
| Redis | 飞书事件去重、Cron 幂等/限流、session lane ownership、会话调度 ready index、pending bucket、busy owner TTL。 |
| RabbitMQ | 入站削峰和 worker 唤醒、出站投递唤醒、ACK/NACK、DLQ、队列积压观测。 |
| PostgreSQL | 任务、会话、事件、错误、指标、记忆索引、投递状态、lane 状态和配置事实状态。 |
| JSONL / 本地文件 | 本地开发、审计备份、fallback、迁移回放和 workspace 产物落地。 |
| Dashboard / Control Plane | 查看运行状态、任务、投递、事件、错误、scheduler、lane doctor，并执行恢复操作。 |

## 快速启动

### 1. 安装

本地开发环境推荐使用虚拟环境，避免污染系统 Python。

```bash
cd ~/Desktop/claw0/gateway
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

至少配置模型接口：

```env
ANTHROPIC_API_KEY=你的模型接口密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro
```

### 2. 初始化与检查

```bash
agent-gateway doctor
agent-gateway postgres-init
agent-gateway postgres-check-schema
```

### 3. 本地启动

```bash
agent-gateway serve
```

默认地址：

| 服务 | 地址 |
| --- | --- |
| Dashboard | `http://127.0.0.1:8780` |
| Prometheus metrics | `http://127.0.0.1:8780/metrics` |
| WebSocket 控制面 | `ws://127.0.0.1:8765` |
| 飞书 Webhook | `http://127.0.0.1:8766/webhooks/feishu` |
| 企业微信 Webhook | `http://127.0.0.1:8766/webhooks/wework` |

## Docker 部署

### 单进程模式

适合快速验证和最小部署：一个 gateway 服务带起完整运行链路。

```bash
docker compose up -d --build
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-init
docker compose exec gateway agent-gateway postgres-check-schema
```

### 多角色模式

适合长期运行：把入口、worker、投递、调度和 Dashboard 拆成不同服务。

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml up -d --build
```

| 服务 | 职责 |
| --- | --- |
| `gateway-api` | 飞书 Webhook、入站标准化、任务入队。 |
| `gateway-worker` | 消费后台任务，执行 AgentLoop 和工具调用。 |
| `gateway-delivery` | 消费可靠投递队列并发送出站消息。 |
| `gateway-scheduler` | 触发 Cron / Heartbeat。 |
| `gateway-dashboard` | Dashboard、控制面和观测后台。 |

### 多 Worker 模式

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.roles.yml \
  -f docker-compose.workers.yml \
  up -d --build
```

停止时必须使用同一组 Compose 文件：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.roles.yml \
  -f docker-compose.workers.yml \
  down
```

完整说明见 [Docker Compose 部署说明](deploy/docker-compose.md)。

## 关键配置

### 运行角色

`GATEWAY_RUNTIME_ROLES` 控制当前进程启动哪些 runtime：

| 角色 | 说明 |
| --- | --- |
| `all` | 单进程启动所有能力。 |
| `api` | 启动通道入口和 Webhook。 |
| `worker` | 消费后台任务并执行 Agent。 |
| `delivery` | 消费可靠投递队列。 |
| `scheduler` | 触发 Cron / Heartbeat。 |
| `dashboard` | 启动 Dashboard 和观测面板。 |
| `control` | 启动 WebSocket JSON-RPC 控制面。 |
| `observability` | 启动指标和告警采集。 |

### 分布式任务与会话调度

```env
GATEWAY_REDIS_ENABLED=true
GATEWAY_POSTGRES_ENABLED=true
GATEWAY_INBOUND_TASK_QUEUE_ENABLED=true
GATEWAY_INBOUND_BROKER=rabbitmq
GATEWAY_SESSION_READY_SCHEDULER_ENABLED=true
GATEWAY_SESSION_READY_SCHEDULER_NAMESPACE=gateway:tasks
GATEWAY_TASK_WORKER_CONCURRENCY=4
GATEWAY_INBOUND_RABBITMQ_PREFETCH=4
```

### 飞书 Webhook

```env
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_VERIFICATION_TOKEN=
FEISHU_ENCRYPT_KEY=
FEISHU_WEBHOOK_HOST=0.0.0.0
FEISHU_WEBHOOK_PORT=8766
FEISHU_WEBHOOK_PATH=/webhooks/feishu
```

飞书后台事件订阅地址示例：

```text
https://你的域名/webhooks/feishu
```

生产环境建议通过 Caddy 或 Nginx 做 HTTPS 反向代理，详见 [反向代理与 HTTPS 部署指南](deploy/reverse-proxy.md)。

### 企业微信自建应用

企业微信通过 `wework-main` 通道接入，支持自建应用回调 URL 验证、加密 XML 文本消息入站、`MsgId` 去重、应用文本消息出站、`access_token` 缓存和过期重试。

```env
WEWORK_CORP_ID=
WEWORK_AGENT_ID=
WEWORK_SECRET=
WEWORK_CALLBACK_TOKEN=
WEWORK_ENCODING_AES_KEY=
```

企业微信后台回调地址示例：

```text
https://你的域名/webhooks/wework
```

如果直接使用端口穿透，地址形态为：

```text
http://你的公网地址:8766/webhooks/wework
```

企业微信出站发送需要把 Gateway 的出口公网 IP 加入企业微信后台可信 IP，否则会返回 `errcode=60020`。

默认企业微信消息会路由到 `wework-main` Agent。该 Agent 被配置为个人秘书，不主动报告系统运行状态，主要负责个人计划、提醒、复盘和行动项闭环。

## 核心机制

### 入站任务链路

```text
外部消息
  -> ChannelRuntime
  -> TaskQueue.enqueue()
  -> TaskStore / PostgreSQL 写入任务事实状态
  -> RedisSessionReadyScheduler 写入 session pending bucket
  -> RabbitMQ 发布 task_id 唤醒引用
  -> TaskWorkerRuntime claim session 队首任务
  -> AgentLoopRunner
  -> DeliveryQueue
```

### Redis 会话调度器

Phase 21 引入 Redis ready index + per-session pending bucket：

```text
gateway:tasks:sessions:ready
gateway:tasks:session:{session_key}:pending
gateway:tasks:session:{session_key}:busy
```

设计要点：

- `ready index` 只存可以执行的 `session_key`。
- `pending bucket` 保存某个 session 内的待执行任务。
- `busy owner` 表示当前 session 正在被哪个 worker 执行，并带 TTL。
- worker claim 只弹出某个 session 的队首任务。
- A session 正在执行时，A2/A3 留在 A 自己的 bucket 中，不会堵住 B/C。
- Redis 索引丢失后，可通过 `tasks.scheduler.rebuild` 从 `pending/retrying` 任务事实状态重建。

详见 [Phase21 Redis 会话调度器改造说明](doc/Phase21%20Redis会话调度器改造说明.md)。

### 可靠出站投递

```text
Agent 回复
  -> DeliveryQueue
  -> PostgreSQL delivery_entries
  -> RabbitMQ outbound broker
  -> gateway-delivery
  -> 外部通道
```

出站发送失败不会丢失回复，投递状态可以 retry、discard、flush 或 republish。

### Workspace 扩展

| 路径 | 说明 |
| --- | --- |
| `workspace/IDENTITY.md` | 系统身份提示词。 |
| `workspace/SOUL.md` | 行为规则与风格约束。 |
| `workspace/MEMORY.md` | 长期记忆入口。 |
| `workspace/CRON.json` | 全局定时任务。 |
| `workspace/agents/wework-main/` | 企业微信个人秘书的专属提示词与 Cron。 |
| `workspace/skills/` | 自定义 Skill。 |
| `workspace/reports/` | 运行报告、仓库分析、压测报告等产物。 |

### 企业微信个人秘书

`workspace/agents/wework-main/CRON.json` 内置了个人秘书类定时任务，默认投递到企业微信 `wework-main`：

| 任务 | 时间 | 目的 |
| --- | --- | --- |
| 今日计划 | 工作日 08:30 | 给出当天 3 个重点、第一步动作和一个待确认问题。 |
| 午间校准 | 工作日 12:30 | 检查上午进展，调整下午优先级。 |
| 晚间复盘 | 工作日 18:30 | 引导记录完成项、卡点和明天第一步。 |
| 睡前收口 | 工作日 22:30 | 确认明天第一件事、准备材料和承诺事项。 |
| 周计划 | 周一 09:00 | 整理本周 3 个重点和第一步动作。 |
| 周复盘 | 周五 17:30 | 梳理本周完成、未完成和下周第一步。 |

该 Agent 默认启用 `memory_search` 和 `memory_write`，但只应保存长期有价值的信息，例如长期目标、明确承诺、固定偏好和重要截止时间。

## Dashboard 与控制面

Dashboard 默认地址：

```text
http://127.0.0.1:8780
```

常用 JSON-RPC 方法：

| 方法 | 说明 |
| --- | --- |
| `runtime.status` | 运行态快照。 |
| `health.check` | 健康检查。 |
| `events.tail` | 最近运行事件。 |
| `errors.recent` | 最近错误、失败或拒绝事件。 |
| `memory.recent` | 最近记忆写入。 |
| `tasks.list/get/cancel/retry` | 后台任务查看、详情、取消和重试。 |
| `tasks.scheduler.status` | Redis 会话调度器状态。 |
| `tasks.scheduler.rebuild` | 从任务事实状态重建 Redis 调度索引。 |
| `tasks.lanes.*` | session lane 查询、诊断、恢复预检和审计。 |
| `delivery.stats/list/retry/discard/flush/republish` | 可靠投递队列运维。 |
| `cron.list/trigger` | 主动任务查看与触发。 |
| `agents.* / bindings.* / channels.* / profiles.*` | 运行配置查看、修改、保存和重载。 |

## 压测与验证

### 基础测试

```bash
./.venv/bin/python -m compileall agent_gateway tests scripts
./.venv/bin/python -m pytest tests -q
```

### 分布式 readiness

```bash
python scripts/smoke_distributed_lane.py --scenario readiness
```

Docker 环境：

```bash
docker compose exec gateway python scripts/smoke_distributed_lane.py \
  --scenario readiness \
  --rabbitmq-url amqp://admin:admin123@rabbitmq:5672/ \
  --redis-url redis://redis:6379/0 \
  --postgres-url postgresql://postgres:postgres@postgres:5432/postgres
```

### 容量基线

```bash
python scripts/run_capacity_matrix.py --dry-run
python scripts/run_capacity_matrix.py
python scripts/build_capacity_baseline.py
```

压测产物：

```text
workspace/reports/load-tests/*.json
workspace/reports/load-tests/*.md
workspace/reports/capacity-baseline.md
```

详见 [20.8 压测执行清单](doc/20.8%20压测执行清单.md)。

## 文档索引

| 文档 | 说明 |
| --- | --- |
| [项目计划](PROJECT_PLAN.md) | 阶段状态、能力基线和后续优先级。 |
| [项目架构说明](doc/项目架构说明.md) | 目录结构和主要模块职责。 |
| [Phase21 Redis 会话调度器改造说明](doc/Phase21%20Redis会话调度器改造说明.md) | 会话级 FIFO 调度、恢复和观测说明。 |
| [Redis 与 RabbitMQ 使用代码片段](doc/Redis与RabbitMQ使用代码片段.md) | 中间件使用位置和关键代码。 |
| [键名与 ID 命名规范](doc/键名与ID命名规范.md) | Redis key、任务 ID、投递 ID、session key 规范。 |
| [Docker Compose 部署说明](deploy/docker-compose.md) | 单进程、多角色、多 worker 部署。 |
| [企业微信通道接入调研](doc/企业微信通道接入调研.md) | 企业微信 Key、回调、文本闭环和后续扩展边界。 |
| [备份与恢复指南](deploy/backup-restore.md) | 数据卷、PostgreSQL、workspace 和恢复流程。 |
| [反向代理与 HTTPS 部署指南](deploy/reverse-proxy.md) | Caddy/Nginx 暴露 Webhook 和 Dashboard。 |
| [20.8 压测执行清单](doc/20.8%20压测执行清单.md) | QPS、吞吐、延迟和容量边界测试。 |

## 安全说明

- `.env`、数据库 dump、RabbitMQ / Redis / PostgreSQL volume 备份不能提交到公开仓库。
- Dashboard 默认无内建鉴权，只建议绑定本机或可信内网。
- WebSocket 控制面不要直接暴露公网。
- 飞书 Webhook 生产环境应使用 HTTPS 反向代理。
- 企业微信自建应用的 `Secret`、`Token`、`EncodingAESKey` 必须只放在 `.env` 或密钥管理系统中。
- 企业微信应用消息发送需要可信 IP，变更部署出口后要同步更新企业微信后台配置。
- RabbitMQ、PostgreSQL、Redis 不应暴露公网。
