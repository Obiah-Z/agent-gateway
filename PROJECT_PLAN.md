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
| Redis | 飞书事件去重、Cron 幂等/限流、session lane ownership、TTL 接管。 |
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
| Redis/PostgreSQL/RabbitMQ 仍是单机中间件示例 | 不是跨机器高可用集群 | 后续基础设施阶段 |

## 9. 后续优先级

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
