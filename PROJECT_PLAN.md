# AI Agent Gateway 项目计划

更新时间：2026-06-30

## 1. 项目定位

AI Agent Gateway 是一个基于 Python 的智能体网关系统，目标是把多轮对话、工具调用、多通道接入、主动任务、可靠投递、运行观测和分布式执行治理整合成一个可本地运行、可逐步生产化的 Agent 运行框架。

当前建设原则：

- `本地优先`：单机可直接运行、调试和恢复。
- `边界清晰`：入站、路由、执行、状态、投递、调度和观测拆开。
- `可靠闭环`：消息先入队、任务可追踪、投递可重试、错误可定位。
- `渐进生产化`：保留 JSONL fallback，同时引入 Redis、PostgreSQL、RabbitMQ 和 Docker Compose。

## 2. 当前运行形态

```text
飞书 / CLI / WebSocket
  -> Inbound Gateway
  -> 统一 InboundMessage
  -> RabbitMQ 入站分区队列
  -> Task Worker 池
  -> Redis/PostgreSQL session lane ownership
  -> AgentLoop / Tool Calling / Memory / Skills
  -> Delivery Queue
  -> RabbitMQ / PostgreSQL reliable outbound
  -> 飞书 / CLI / Telegram
```

核心中间件职责：

| 组件 | 职责 |
| --- | --- |
| Redis | 飞书事件去重、Cron 幂等/限流、session lane ownership、TTL 接管、per-session pending bucket 和 ready index。 |
| PostgreSQL | 会话、任务、事件、错误、指标、记忆索引、投递事实状态、lane 状态与历史。 |
| RabbitMQ | 入站任务分区分发、出站投递唤醒、ack/nack、DLQ、削峰。 |
| JSONL / 本地文件 | 本地优先 fallback、审计备份、迁移回放。 |
| Dashboard / Control Plane | 运行状态、事件、错误、任务、投递、lane doctor、readiness 和恢复入口。 |

## 3. 代码结构

| 路径 | 职责 |
| --- | --- |
| `agent_gateway/runtime/domain/` | 领域模型、Agent 配置、路由、ID 和消息结构。 |
| `agent_gateway/runtime/execution/` | Agent Loop、Dispatcher、ChannelRuntime、DeliveryRuntime、Cron/Heartbeat、控制面。 |
| `agent_gateway/runtime/tasks/` | 后台任务、TaskWorkerRuntime、Redis lane coordinator、入站任务处理。 |
| `agent_gateway/runtime/state/` | 本地状态、PostgreSQL repository、迁移与 schema。 |
| `agent_gateway/runtime/infra/` | Redis、RabbitMQ、PostgreSQL 等基础设施客户端。 |
| `agent_gateway/runtime/observability/` | Runtime events、metrics、alerts。 |
| `agent_gateway/gateways/feishu/` | 飞书 Webhook、长连接、发送通道和 onboarding。 |
| `agent_gateway/gateways/messaging/` | CLI、Telegram 等通道适配。 |
| `agent_gateway/gateways/control/` | WebSocket JSON-RPC 控制面。 |
| `agent_gateway/ai/` | Prompt、上下文、记忆、技能、工具、新闻简报。 |
| `agent_gateway/monitoring/` | Dashboard 静态页面和 `/metrics`。 |
| `config/` | agents、bindings、channels、profiles 等配置。 |
| `workspace/` | Prompt、skills、memory、Cron、Heartbeat、运行工作区。 |
| `data/` | 本地 sessions、events、metrics、alerts、fallback 队列。 |
| `deploy/` | Docker Compose、systemd、反向代理、备份恢复和多角色部署说明。 |
| `doc/` | 架构说明、压测清单、分布式 lane Runbook 和专题设计文档。 |

## 4. 运行入口

本地启动：

```bash
cd ~/Desktop/claw0/gateway
source .venv/bin/activate
agent-gateway serve
```

Docker Compose 单进程模式：

```bash
docker compose up -d --build
```

Docker Compose 多角色模式：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml up -d --build
```

常用地址：

| 服务 | 地址 |
| --- | --- |
| WebSocket 控制面 | `ws://127.0.0.1:8765` |
| 飞书 Webhook | `http://127.0.0.1:8766/webhooks/feishu` |
| Dashboard | `http://127.0.0.1:8780` |
| Prometheus metrics | `http://127.0.0.1:8780/metrics` |

## 5. 当前能力基线

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Agent Loop | 已完成 | 支持 Anthropic Messages API 兼容调用、`stop_reason` 多轮执行和 tool calling。 |
| Tool Calling | 已完成 | 支持 bash、文件读写、记忆、Web Search、GitHub 仓库分析等工具。 |
| 会话与上下文 | 已完成 | JSONL 持久化、历史重放、Prompt/Memory/Skills 装配。 |
| 多通道接入 | 已完成 | CLI、飞书 Webhook、飞书长连接、Telegram 适配基础。 |
| 路由系统 | 已完成 | `bindings.json` 将 channel/account/peer/session 路由到 Agent。 |
| 主动任务 | 已完成 | Heartbeat、Cron、新闻简报、自用 Skills 可进入统一任务链。 |
| 可靠投递 | 已完成 | PostgreSQL 事实状态 + RabbitMQ 分发 + retry/DLQ + Dashboard/CLI 恢复。 |
| 入站任务队列 | 已完成 | 非 CLI 入站可落 `agent_inbound` task，入口快速 ack。 |
| 分布式 lane | 已完成 | RabbitMQ 入站分区 + Redis/PostgreSQL lane ownership + TTL 接管 + readiness smoke。 |
| 状态外置 | 已完成 | PostgreSQL schema、初始化、回填、双写/fallback、schema drift 检查。 |
| 运维观测 | 已完成 | Dashboard、events、errors、metrics、alerts、lane doctor、Prometheus `/metrics`。 |
| 部署编排 | 已完成 | Dockerfile、Compose、Compose 多角色、systemd、备份恢复、HTTPS 反代文档。 |
| 压测基线 | 已完成 | `load_test_gateway.py`、`run_capacity_matrix.py`、容量基线报告生成。 |

## 6. 阶段状态总览

| 阶段 | 状态 | 主题 |
| --- | --- | --- |
| Phase 1-11 | 已完成 | 工程骨架、Agent Loop、多通道、记忆技能、主动任务、可靠投递、飞书接入、Dashboard、架构分层。 |
| Phase 12 | 待实现 | Dashboard 鉴权与控制面安全边界。 |
| Phase 13 | 已完成 | 运行事件流、最近错误、最近记忆写入、事件/错误控制面。 |
| Phase 13 增强 | 待实现 | 模型调用事件、profile fallback、上下文压缩和错误分类。 |
| Phase 14 | 已完成 | 指标快照、趋势视图、告警规则和告警投递。 |
| Phase 15 | 已完成 | ChannelRuntime lane 化、入站背压、热重启保护和入站观测。 |
| Phase 16 | 待实现 | Agent 权限预览、配置校验、审计、快照和回滚。 |
| Phase 17 | 待实现 | 会话归档、删除、导出、记忆审查、压缩和污染治理。 |
| Phase 18 | 部分完成 | 后台任务队列已完成；多 Agent handoff、per-agent 并发和任务优先级仍待增强。 |
| Phase 19 | 已完成 | Docker Compose、systemd、数据卷、HTTPS、备份恢复、启动前检查。 |
| Phase 20 | 已完成 | Redis/PostgreSQL/RabbitMQ 分布式执行基础、可靠队列、分布式 lane、部署与压测闭环。 |
| Phase 21 | 已完成 | Redis ready index + per-session pending bucket，保证同一 session 严格 FIFO，同时不同 session 并行。 |

## 7. Phase 20 完成摘要

Phase 20 已把系统从“单进程本地运行时”升级为“可拆分、可横向扩展、可恢复”的生产化运行框架。

已完成能力：

| 子阶段 | 状态 | 结果 |
| --- | --- | --- |
| 20.1 运行角色拆分 | 已完成 | 支持 `all/api/worker/scheduler/delivery/dashboard/control/observability`。 |
| 20.2 Redis 最小接入 | 已完成 | 事件去重、Cron 幂等/限流、健康检查、lane ownership。 |
| 20.3 后台任务队列 | 已完成 | `TaskInstance`、TaskStore、TaskWorkerRuntime、任务控制面和 Dashboard。 |
| 20.4 PostgreSQL 状态外置 | 已完成 | sessions、tasks、events、errors、metrics、memory、config、lane 等表。 |
| 20.5 初始化与回填 | 已完成 | `postgres-init`、schema check、local migration、回填审计。 |
| 20.6 分布式可靠投递 | 已完成 | PostgreSQL `delivery_entries` + RabbitMQ broker + retry/DLQ + republish。 |
| 20.7 生产部署编排 | 已完成 | Compose、Compose 多角色、systemd、备份恢复、Caddy/Nginx HTTPS 文档。 |
| 20.8 统一观测与压测 | 已完成 | Prometheus `/metrics`、压测脚本、容量基线、边界矩阵。 |
| 20.9 分布式入站 lane | 已完成 | RabbitMQ 入站 broker、Redis/PostgreSQL lane、TTL 接管、lane doctor、readiness smoke。 |

最终验收命令：

```bash
./.venv/bin/python scripts/smoke_distributed_lane.py --scenario readiness --lane-ttl-seconds 30
```

最近验收结果：

```text
ready=true
passed=8
failed=0
```

注意：readiness smoke 需要项目 `.venv` 或 Docker 镜像环境。系统 Python 若未安装 `redis` / `pika` 会失败。

## 8. 当前主要边界

| 边界 | 影响 | 建议归属 |
| --- | --- | --- |
| Dashboard 默认无内建鉴权 | 不应直接暴露公网 | Phase 12 |
| 控制面高风险操作缺少统一鉴权和二次确认 | 误操作风险 | Phase 12 |
| 模型调用事件和错误分类仍不够细 | 排查模型限流/鉴权/超时不够直观 | Phase 13 增强 |
| Agent 最终权限缺少预览和 diff | 多 Agent 配置审查成本高 | Phase 16 |
| 会话与记忆长期治理不足 | 数据膨胀、记忆污染 | Phase 17 |
| 多 Agent handoff 仍偏 prompt 编排 | 协作任务缺少强状态机 | Phase 18 |
| 多 worker 长期部署需显式配置不同 `GATEWAY_TASK_WORKER_ID` | 运维观测可能混淆 | 后续部署增强 |
| Redis session ready scheduler 需要显式开启 | 默认仍兼容旧路径；分布式多 worker 严格顺序模式需要 `GATEWAY_SESSION_READY_SCHEDULER_ENABLED=true` | 运维配置 |
| Scheduler 续租失败后不强行中断当前模型调用 | 当前会记录续租失败事件并等待 TTL/后续恢复，自动抢占正在执行的外部调用仍需谨慎设计 | 后续恢复增强 |
| Redis/PostgreSQL/RabbitMQ 仍是单机中间件示例 | 不是跨机器高可用集群 | 后续基础设施阶段 |

## 9. 后续优先级

### P0：严格会话顺序与 Redis 调度索引

Phase 21：Redis ready index + per-session pending bucket。

目标：

- 同一 `session_key` 内任务严格按创建顺序执行，例如 `A1 -> A2 -> A3 -> A4`。
- 不同 session 互不阻塞，例如 A 正在执行时，B/C 的队首任务仍可被其他 worker 并发执行。
- RabbitMQ 继续承担入口削峰和 worker 唤醒，不再直接决定同一 session 的执行顺序。
- PostgreSQL / TaskStore 继续作为任务事实状态，Redis 只保存热路径调度索引，支持从数据库重建。

建议技术方案：

- Redis `LIST` 保存每个 session 的 pending bucket：`gateway:session:{session}:pending`。
- Redis `ZSET` 或 `LIST` 保存全局 ready index：`gateway:sessions:ready`。
- Redis `STRING` 保存 busy owner：`gateway:session:{session}:busy`，value 包含 `worker_id/task_id/owner_token`，带 TTL。
- Redis Lua 脚本原子完成 `claim_next`：取 ready session、检查 busy、弹出队首 task、设置 busy owner。
- Redis Lua 脚本原子完成 `release_session`：校验 owner、删除 busy、若 pending 非空则重新放回 ready index。
- PostgreSQL 提供 rebuild 入口：按 `created_at` 扫描 `pending/retrying` 任务，重建 Redis pending bucket 和 ready index。

子任务：

| 子阶段 | 状态 | 内容 | 完成标准 |
| --- | --- | --- | --- |
| 21.1 调度数据结构设计 | 已完成 | 定义 Redis key、value、Lua 脚本输入输出、状态迁移和重建规则。 | `tests/test_session_scheduler.py` 覆盖 claim/release/rebuild。 |
| 21.2 SessionScheduler 接口 | 已完成 | 新增 `RedisSessionReadyScheduler`，封装 enqueue、claim_next、release、renew、rebuild。 | fake Redis 单测验证严格 FIFO。 |
| 21.3 入站 enqueue 接入 | 已完成 | TaskStore 创建任务后写入 session pending bucket；ready index 只放 session，不放 task。 | `LocalTaskQueue` 已接入 scheduler，再发布 RabbitMQ 唤醒。 |
| 21.4 Worker claim 改造 | 已完成 | 启用 scheduler 后，worker 通过 scheduler claim session 队首任务；RabbitMQ payload 作为唤醒消息。 | 相关 worker 单测验证 scheduler 优先于直接 reserve。 |
| 21.5 锁 TTL 与续租治理 | 已完成 | busy owner 带 TTL，release/renew 必须校验 owner；worker 执行期间启动 scheduler claim watchdog 定期续租。 | 慢任务测试覆盖执行期间续租；续租失败记录 `task.scheduler.renew_failed`。 |
| 21.6 恢复与观测 | 已完成 | Control Plane / WebSocket 暴露 scheduler status 和 rebuild；Dashboard 展示 ready session、pending bucket、busy owner，并提供重建按钮。 | Redis 调度索引丢失后可从 pending/retrying 任务事实状态重建。 |

Redis 锁过期后果与治理：

- 当前实现里，Redis lane lock 是“执行前互斥锁”。如果模型调用超过 TTL 且续租失败，锁会自然过期。
- 锁过期后，A1 仍可能在 worker-1 中执行，但 A2 可能被 worker-2 抢到并开始执行，导致同一 session 并发。
- 可能后果包括上下文读取乱序、会话历史写入交错、重复回复、工具副作用重复、最终消息投递顺序不符合用户发送顺序。
- Phase 21 要把 busy owner 作为调度状态，并在执行期间持续续租；release 时必须校验 owner token，防止旧 worker 释放新 owner。
- 如果 busy owner 过期，只允许通过可观测的接管流程恢复：记录 `session.owner.stale` 事件，必要时由 recovery/rebuild 决定是否重新放行队首任务。

完成标准：

- 在 `GATEWAY_TASK_WORKER_CONCURRENCY=4`、`GATEWAY_INBOUND_RABBITMQ_PREFETCH=4` 下，同一 session 的 `A1/A2/A3/A4` 仍严格按顺序完成。
- A session 长任务运行时，B/C session 的队首任务可以并发执行，不被 A 堵塞。
- 模型调用超过默认 TTL 时，续租保持 busy owner 有效；续租失败会产生明确事件和 Dashboard 告警。
- Redis 调度索引丢失后，可以从 PostgreSQL pending/retrying 任务重建。

### P0：安全边界

1. Phase 12：Dashboard 访问 token。
2. WebSocket JSON-RPC 鉴权握手或请求级 token。
3. 高风险操作二次确认：delivery discard、config save、cron trigger、lane recovery execute。
4. README / `.env.example` 标注公网暴露风险。

完成标准：

- 未授权请求无法读取 Dashboard 管理数据。
- 未授权请求无法调用控制面写操作。
- 本机开发默认体验不被明显破坏。

### P1：模型与错误可观测性

1. `model.call.started/completed/failed`。
2. `profile.selected/failed/cooldown`。
3. `context.compacted`。
4. 统一错误分类：模型鉴权、限流、超时、工具失败、投递失败、飞书验签、Cron、配置错误。
5. Dashboard 最近错误展示分类、影响对象和建议操作。

完成标准：

- 能快速区分模型慢、模型失败、工具失败、投递失败和通道失败。

### P1：配置治理与权限预览

1. `agents.validate`。
2. Agent manifest resolved preview。
3. 最终权限报告：prompt、memory policy、skills、allowed tools、denied tools、capability tags。
4. 配置变更审计、快照和回滚。

完成标准：

- 配置变更前后能看到能力差异。
- 配置误改可定位、可回滚。

### P2：会话与记忆治理

1. session list/export/archive/delete。
2. session retention 策略。
3. memory 来源标记、review、delete、compact。
4. 长期记忆注入前质量过滤。

完成标准：

- 长期运行后可以控制数据膨胀和记忆污染。

### P2：多 Agent 协作增强

1. agent-to-agent handoff。
2. per-agent 并发上限。
3. 低优先级任务延迟队列。
4. 长任务真正后台化、取消和重试。

完成标准：

- 后台任务和多 Agent 协作具备明确生命周期，不依赖纯 prompt 手工编排。

## 10. 常用验收命令

基础回归：

```bash
cd ~/Desktop/claw0/gateway
./.venv/bin/python -m compileall agent_gateway tests scripts
./.venv/bin/python -m pytest tests -q
```

分布式 lane readiness：

```bash
./.venv/bin/python scripts/smoke_distributed_lane.py --scenario readiness --lane-ttl-seconds 30
```

Docker Compose 配置校验：

```bash
docker compose config --quiet
docker compose -f docker-compose.yml -f docker-compose.roles.yml config --quiet
```

多角色服务列表：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml config --services
```

压测矩阵 dry-run：

```bash
./.venv/bin/python scripts/run_capacity_matrix.py --dry-run --include-external
```

容量基线生成：

```bash
./.venv/bin/python scripts/build_capacity_baseline.py
```

## 11. 文档索引

| 文档 | 说明 |
| --- | --- |
| [README.md](README.md) | 项目总览、运行方式、配置说明。 |
| [deploy/docker-compose.md](deploy/docker-compose.md) | Docker Compose 单进程、多角色、多 Worker 部署。 |
| [docker-compose.roles.yml](docker-compose.roles.yml) | 多角色 Compose overlay。 |
| [docker-compose.workers.yml](docker-compose.workers.yml) | 多 Worker Compose overlay。 |
| [deploy/backup-restore.md](deploy/backup-restore.md) | 备份与恢复。 |
| [deploy/reverse-proxy.md](deploy/reverse-proxy.md) | Caddy/Nginx HTTPS 反向代理。 |
| [doc/20.8 压测执行清单.md](doc/20.8%20压测执行清单.md) | 压测矩阵、QPS 口径、容量边界。 |
| [doc/分布式Lane运维Runbook.md](doc/分布式Lane运维Runbook.md) | 分布式 lane 运维、诊断和恢复。 |
| [doc/分布式入站任务顺序与互斥技术选型.md](doc/分布式入站任务顺序与互斥技术选型.md) | 分布式入站 lane 技术选型。 |
| [doc/消息闭环实现说明.md](doc/消息闭环实现说明.md) | 从用户输入到落盘投递的完整链路说明。 |
| [doc/项目架构说明.md](doc/项目架构说明.md) | 架构分层与模块说明。 |
