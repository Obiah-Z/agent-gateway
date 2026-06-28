# AI Agent Gateway 项目计划

更新时间：2026-06-27

## 1. 项目定位

`gateway/` 是一个基于 Python 的 AI Agent Gateway 智能体网关系统，目标是把多轮对话、工具调用、多通道接入、主动任务、可靠投递和运行观测整合成一个可本地运行、可持续扩展的智能体运行框架。

当前路线坚持：

- 本地优先：优先保证单机长期稳定运行。
- 结构清晰：按通道、路由、执行、投递、状态、观测拆分边界。
- 逐步生产化：先补齐可靠性、观测、安全和治理，再考虑数据库、分布式和复杂部署。

核心场景：

- 多轮对话与 Agent Loop。
- Tool Calling 与外部执行能力。
- CLI、飞书、Telegram 等多通道接入。
- 会话持久化、上下文管理与记忆注入。
- Heartbeat、Cron、新闻简报等主动任务。
- 可靠投递、失败重试、并发控制与弹性恢复。
- Dashboard、WebSocket 控制面、运行事件流、指标和告警。

## 2. 当前架构

| 目录 | 职责 |
| --- | --- |
| `agent_gateway/runtime/domain/` | 领域模型、Agent 配置、路由、ID 规范和消息结构。 |
| `agent_gateway/runtime/execution/` | Agent Loop、Dispatcher、ChannelRuntime、DeliveryRuntime、Cron/Heartbeat、指标与告警运行时。 |
| `agent_gateway/runtime/state/` | 会话、可靠投递队列、事件、指标、告警等本地状态存储。 |
| `agent_gateway/runtime/observability/` | Runtime events、metrics、alerts 等观测模型。 |
| `agent_gateway/gateways/messaging/` | CLI、Telegram 等消息通道适配。 |
| `agent_gateway/gateways/feishu/` | 飞书 Webhook、长连接、发送通道和 onboarding。 |
| `agent_gateway/gateways/control/` | WebSocket JSON-RPC 控制面。 |
| `agent_gateway/ai/context/` | Prompt、记忆、技能和上下文装配。 |
| `agent_gateway/ai/tools/` | 工具注册表和内置工具。 |
| `agent_gateway/ai/news/` | AI Agent 新闻采集、去重和摘要生成。 |
| `agent_gateway/monitoring/` | Dashboard 静态页面和本地运维视图。 |
| `config/` | agents、bindings、channels、profiles 等静态配置。 |
| `workspace/` | Prompt、记忆、skills、Cron、Heartbeat、新闻源和 Agent 局部工作区。 |
| `data/` | sessions、delivery queue、events、metrics、alerts 等运行期数据。 |
| `tests/` | 自动化测试。 |

说明：旧顶层兼容层 `agent_gateway/agents.py`、`router.py`、`models.py`、`ids.py` 已移除。新代码应直接从具体子模块导入。

## 3. 运行入口与验证基线

本地启动：

```bash
cd ~/Desktop/claw0/gateway
source .venv/bin/activate
agent-gateway serve
```

默认服务：

| 服务 | 默认地址 |
| --- | --- |
| WebSocket 控制面 | `ws://127.0.0.1:8765` |
| 飞书 Webhook | `http://127.0.0.1:8766/webhooks/feishu` |
| Dashboard | `http://127.0.0.1:8780` |
| 飞书扫码接入页 | `http://127.0.0.1:8780/onboarding/feishu` |

常规验证：

```bash
cd ~/Desktop/claw0/gateway
./.venv/bin/python -m compileall agent_gateway tests
./.venv/bin/python -m pytest tests -q
```

最近验证基线：`231 passed`。

## 4. 当前能力基线

| 能力方向 | 状态 | 说明 |
| --- | --- | --- |
| Agent Loop | 已完成 | 支持 Anthropic Messages API 兼容调用、`stop_reason` 驱动的多轮执行和 tool calling。 |
| Tool Calling | 已完成 | 基于 dispatch table 管理 bash、文件读写、记忆检索、Web Search、GitHub 分析等工具。 |
| 会话持久化 | 已完成 | 基于 JSONL 保存 transcript，支持历史重放和上下文保护。 |
| 路由系统 | 已完成 | 基于 `bindings.json` 将 channel、account、peer、session 路由到指定 Agent。 |
| 配置控制面 | 已完成 | 支持 agents、bindings、channels、profiles 的查看、修改、保存和 reload。 |
| 记忆与技能 | 已完成 | 支持 `MEMORY.md`、daily memory、`SKILL.md` 扫描和 Agent 局部 prompt 覆盖。 |
| 主动任务 | 已完成 | Heartbeat、Cron、AI Agent 简报和自用 Skill 可接入统一执行链。 |
| 可靠投递 | 已完成 | 普通回复、heartbeat、cron 输出先入本地队列，再由后台 runtime 发送、重试和失败落盘。 |
| 入站并发 | 基本完成 | ChannelRuntime 已 lane 化，支持全局并发上限、背压、长任务提示和 Dashboard 观测。 |
| 飞书接入 | 已完成 | 支持 Webhook、长连接、签名校验、事件去重、onboarding 和消息发送。 |
| Dashboard | 已完成 | 支持健康检查、运行态、入站队列、投递队列、Cron、事件、错误、记忆、指标和告警查看。 |
| 运行事件流 | 已完成 | 支持 runtime event JSONL、`events.tail`、`errors.recent` 和 Dashboard 最近链路视图。 |
| 指标与告警 | 已完成 | 支持 metrics snapshot、趋势视图、告警规则、告警历史和飞书告警投递。 |

## 5. 阶段状态总览

| 阶段 | 状态 | 主题 |
| --- | --- | --- |
| Phase 1 | 已完成 | 基础工程骨架、包结构、命令入口、基础 Agent Loop。 |
| Phase 2 | 已完成 | 会话、上下文、配置和运行资产。 |
| Phase 3 | 已完成 | 多通道、消息路由和 WebSocket 控制面。 |
| Phase 4 | 已完成 | 记忆、技能和 Agent Manifest。 |
| Phase 5 | 已完成 | 主动任务与可靠投递。 |
| Phase 6 | 已完成 | 弹性、命名 lane 和 CLI 交互稳定性。 |
| Phase 7 | 已完成 | 飞书生产化接入。 |
| Phase 8 | 已完成 | Dashboard 与运行态状态。 |
| Phase 9 | 已完成 | 飞书扫码接入与用户 onboarding。 |
| Phase 10 | 已完成 | AI Agent 每日简报。 |
| Phase 11 | 已完成 | 架构分层和兼容层移除。 |
| Phase 12 | 待实现 | Dashboard 鉴权与安全边界。 |
| Phase 13 | 已完成 | 运行事件流与最近错误视图。 |
| Phase 13 增强 | 待实现 | 模型调用事件与错误分类。 |
| Phase 14 | 已完成 | 指标快照、趋势与告警。 |
| Phase 15 | 基本完成 | ChannelRuntime lane 化、入站背压和可观测性。 |
| Phase 16 | 待实现 | Agent 权限预览与配置治理。 |
| Phase 17 | 待实现 | 会话与记忆治理。 |
| Phase 18 | 待实现 | 多 Agent 协作与任务实例状态机。 |
| Phase 19 | 待实现 | 生产部署形态。 |
| Phase 20 | 进行中 | 高并发、高性能、高可用架构升级；已完成运行角色拆分、Redis 最小协调、后台任务队列、PostgreSQL 状态外置、schema 初始化、本地回填脚手架和状态迁移审计。 |

## 6. 近期完成：Phase 15

### 目标

- 将“所有入站消息单消费者串行处理”升级为“统一入口、按 lane 分发、不同 lane 并发处理”。
- 避免飞书、CLI、Telegram、Webhook、长连接等通道互相阻塞。
- 保持同一 lane 内顺序稳定。
- 在控制面和 Dashboard 中看到入站积压、活跃 lane 和并发状态。

### 已完成内容

| 子阶段 | 状态 | 完成内容 |
| --- | --- | --- |
| 15.1 graceful restart | 已完成 | `ChannelRuntime.stop/restart` 支持停止采集、关闭旧通道、等待旧线程退出、drain 队列后再停止 consumer；CLI `completion_event` 可释放。 |
| 15.2 lane key 规则 | 已完成 | 新增 `build_preroute_lane_key()`、`build_inbound_lane_key()`，`PendingInbound` 暴露路由前 lane key。 |
| 15.3 lane dispatcher | 已完成 | 全局入站队列只做接收和粗分发；每个 lane 独立 worker 串行处理，不同 lane 可并发。 |
| 15.4 全局并发上限 | 已完成 | 新增 `GATEWAY_INBOUND_MAX_CONCURRENT_LANES`，默认 4，通过 `asyncio.Semaphore` 限制同时运行 lane 数。 |
| 15.5 入站背压 | 主目标完成 | 新增 `GATEWAY_INBOUND_MAX_QUEUE_SIZE`、`GATEWAY_INBOUND_MAX_LANE_QUEUE_SIZE`，超限拒绝并提示用户稍后重试；低优先级延迟队列迁移到 Phase 18。 |
| 15.6 长任务提示 | 主目标完成 | 新增 `GATEWAY_INBOUND_LONG_TASK_NOTICE_SECONDS`，超过阈值先发送“继续处理中”提示；真正后台化迁移到 Phase 18。 |
| 15.7 运行指标 | 已完成 | `ChannelRuntime.stats()` 暴露全局队列、lane 队列、运行中任务、活跃 lane、最老等待时间和并发上限。 |
| 15.8 Dashboard 视图 | 已完成 | Dashboard 新增“入站队列与车道”面板，运行态快照新增“入站队列”卡片。 |
| 15.9 控制面状态 | 已完成 | `runtime.status` 新增 `inbound` 字段，`health.check` 增加 `inbound.backlog` 检查。 |
| 15.10 测试 | 已完成 | 覆盖 restart 不丢消息、旧线程退出、同 lane 串行、不同 lane 并发、背压、长任务提示、stats 和控制面状态。 |

### Phase 15 剩余增强

这些增强不阻塞 Phase 15 主目标，建议移动到后续阶段处理：

- `per-agent` 精准并发上限：需要基于路由后的 Agent/session 信息做调度，建议并入 Phase 18 的任务实例状态机。
- 低优先级延迟队列：当前超限策略是拒绝，尚未实现 Cron/Heartbeat 延迟和实时用户消息优先级。
- 真正后台化长任务：当前只是先发“继续处理中”提示，任务仍占用原 lane。释放 lane、后台状态追踪和取消/重试应并入 Phase 18。

## 7. 当前主要边界

- 当前仍是单进程本地运行时，尚未引入数据库、分布式锁或多实例协调。
- Dashboard 默认无鉴权，仅适合本机访问，不应直接暴露公网。
- 配置变更可以保存和 reload，但配置审计、快照和回滚仍不完整。
- Agent 权限模型已有 tool policy 和 capability tags，但缺少最终权限预览和强校验报告。
- 会话与记忆已经可持久化，但长期运行后的归档、删除、复审和压缩治理仍不足。
- 飞书长连接依赖本机 `lark-cli` 配置和子进程消费，适合本地/单机部署；生产多实例仍建议优先 Webhook。
- 指标与告警已建立本地闭环，但没有外部 TSDB、Prometheus 或集中日志系统。
- 当前 JSONL、内存队列和本地文件锁适合单机闭环，不适合多实例共享状态、横向扩容和集中查询。

## 8. 后续路线图

### Phase 12：Dashboard 鉴权与安全边界

状态：待实现，下一阶段建议优先推进。

目标：

- 让 Dashboard 从“本地运维页面”升级为可控暴露的管理入口。
- 避免误把无鉴权控制面暴露到公网。

计划项：

1. 增加 Dashboard 访问 token。
2. 支持从 `.env` 配置 token、是否启用鉴权、允许来源。
3. WebSocket JSON-RPC 增加鉴权握手或请求级 token 校验。
4. 对高风险操作增加二次确认标记，例如 delivery discard、config save、cron trigger。
5. 在 README 和 `.env.example` 中明确公网暴露风险。

完成标准：

- 未携带 token 时无法访问管理数据和控制操作。
- 本机默认体验不被明显破坏。
- 高风险操作在接口层有明确保护。

### Phase 13 增强项：模型调用与错误分类

状态：进行中。

目标：

- 把模型调用、fallback、profile 冷却、上下文压缩和错误分类纳入可观测链路。

计划项：

1. 增加 `model.call.started/completed/failed`。
2. 增加 `profile.selected/failed/cooldown`。
3. 增加 `context.compacted`。
4. 记录 profile、model、失败分类、耗时和 fallback 次数。
5. 增加错误分类器：模型鉴权失败、限流、超时、工具失败、投递目标无效、飞书验签失败、Cron 失败、配置错误。
6. `errors.recent` 返回错误类型、影响对象和建议操作。
7. Dashboard 最近错误面板展示分类和建议。

完成标准：

- 可以区分模型慢、模型失败、工具慢、投递失败。
- 排查 API key、base_url、限流和上下文溢出时有明确事件依据。

### Phase 16：Agent 权限预览与配置治理

状态：进行中。

目标：

- 让多 Agent 配置从“能运行”升级到“可审查、可回滚、可解释”。

计划项：

1. 增加 manifest resolved preview。
2. 增加 `agents.validate` 接口。
3. 增加 Agent 最终权限报告：prompt files、memory policy、enabled skills、allowed tools、denied tools、capability tags。
4. 增加配置变更审计日志。
5. 增加配置快照与回滚能力。

完成标准：

- 修改 Agent 配置前后，可以清楚看到最终能力差异。
- 配置误改后可以定位是谁改了什么，并恢复到旧版本。

### Phase 17：会话与记忆治理

状态：进行中。

目标：

- 控制长期运行后的数据膨胀、记忆污染和上下文质量下降。

计划项：

1. 增加 session list、export、archive、delete。
2. 增加 session retention 策略。
3. 增加 memory 来源标记。
4. 增加 memory review、delete、compact。
5. 增加长期记忆注入前的质量过滤。

完成标准：

- 可以管理长期会话数据，而不是只靠手动删除 JSONL。
- 记忆可追溯、可清理，不会无限污染 prompt。

### Phase 18：多 Agent 协作与任务实例状态机

状态：进行中。

目标：

- 将系统从“多 Agent 可路由”升级到“多 Agent 可协作、任务可追踪”。
- 承接 Phase 15 遗留的 per-agent 并发、低优先级延迟队列和长任务后台化。

计划项：

1. 增加 agent-to-agent handoff。
2. 增加 task instance 模型：pending、running、waiting、retrying、done、failed。
3. 为 cron、heartbeat、新闻简报和主动任务增加幂等 key。
4. 增加任务执行记录和失败恢复入口。
5. 支持任务级状态在 Dashboard 展示。
6. 支持 per-agent 并发上限。
7. 支持低优先级任务延迟和实时消息优先级调度。
8. 支持长任务真正后台化、取消和重试。

完成标准：

- 后台任务具备完整生命周期。
- 多 Agent 协作不再依赖纯 prompt 手工编排。
- 任务失败后可以重试、取消或查看原因。

### Phase 19：生产部署形态

状态：进行中。

目标：

- 明确从本地单机项目走向可部署服务的最小生产路径。

计划项：

1. 增加 systemd service 示例。
2. 增加 Dockerfile / compose 示例。
3. 明确数据目录挂载策略。
4. 增加反向代理示例，特别是飞书 Webhook HTTPS 暴露。
5. 增加备份与恢复说明。
6. 增加启动前配置检查命令。

完成标准：

- 项目可以按文档在新机器上稳定部署。
- 数据、配置、密钥和日志的边界清晰。

### Phase 20：高并发、高性能、高可用架构升级

状态：待实现。

目标：

- 将当前“单进程本地运行时”升级为“可拆分、可横向扩展、可恢复”的生产级运行架构。
- 把入站接入、Agent 执行、后台任务、可靠投递、状态存储和观测能力逐步外置，避免单点阻塞和单机状态瓶颈。
- 在不破坏当前本地优先体验的前提下，为多实例部署、worker 扩容和故障恢复打基础。

#### 中间件选型分析

| 中间件 | 建议优先级 | 主要用途 | 选择原因 | 暂不选择或替代方案 |
| --- | --- | --- | --- | --- |
| Redis | 最高 | 分布式锁、事件去重、限流计数、短期状态缓存、轻量队列 | 接入成本低，Python 生态成熟，适合解决飞书事件去重、Cron 幂等、全局限流和多实例协调；也可作为后续任务队列的过渡层。 | 如果只做单机，当前内存状态够用；如果队列可靠性要求更高，应引入 RabbitMQ。 |
| PostgreSQL | 高 | 会话、任务实例、运行事件、错误、指标快照、配置审计、记忆索引 | 当前 JSONL 适合审计和本地调试，但长期查询、筛选、归档、权限治理和 Dashboard 聚合会越来越困难；PostgreSQL 稳定、通用、便于后续做迁移和备份。 | SQLite 可作为轻量过渡，但多进程写入和远程部署能力弱于 PostgreSQL。 |
| RabbitMQ | 高 | 入站消息队列、后台任务队列、可靠投递队列、死信队列、延迟重试 | 对可靠投递、ack、重试、死信、durable queue、DLX 和消费者扩容支持成熟，适合把 ChannelRuntime、Agent worker、Delivery worker 解耦；本机 Docker 已验证 RabbitMQ 可访问，后续 20.6 优先使用该方案。 | Redis Streams 更轻量，但本项目的可靠投递更看重明确 ack、死信和运维可解释性，因此 RabbitMQ 作为首选。 |
| Celery / Dramatiq | 中 | Cron、Heartbeat、GitHub 分析、服务器巡检、长任务 Skill 的后台执行 | 可以快速把长任务从入站 lane 中剥离，支持 worker 池、重试、任务状态和定时调度；Celery 功能更全，Dramatiq 更轻量。 | 如果希望保持完全自研，可基于 RabbitMQ 写 worker；Redis Streams 只作为轻量备选。 |
| Nginx / Caddy | 中 | HTTPS、反向代理、Dashboard 访问边界、Webhook 公网入口 | 飞书 Webhook 生产环境需要稳定 HTTPS 入口；Caddy 自动证书体验好，Nginx 更通用。 | 本地内网穿透适合测试，不适合长期生产暴露。 |
| Prometheus + Grafana | 中 | 指标采集、趋势图、告警规则和容量评估 | 当前 Dashboard 已有本地指标，但多实例后需要统一指标面；Prometheus 是事实标准，Grafana 展示能力强。 | 早期可继续用本地 metrics JSONL，等多实例前再接入。 |
| Loki / ELK | 低到中 | 集中日志检索、多实例排障 | 当 gateway-api、worker、scheduler 拆开后，本地日志不再方便排障；Loki 与 Grafana 组合轻量。 | 当前已有 runtime events，早期可以先增强事件流，不急于接 ELK。 |
| Docker Compose | 高 | 本地生产化编排、依赖启动、数据卷管理 | 能把 gateway、Redis、PostgreSQL、RabbitMQ、反向代理一次性拉起，便于复现和部署。 | Kubernetes 暂不建议引入，当前项目规模还不需要。 |

#### 目标运行形态

```text
飞书 / Telegram / CLI / Webhook
        ↓
gateway-api / channel runtime
        ↓
message queue
        ↓
agent-worker pool
        ↓
model api / tool runtime / skill runtime
        ↓
delivery queue
        ↓
delivery-worker
        ↓
飞书 / Telegram / WebSocket / CLI
```

#### 子阶段规划

| 子阶段 | 目标 | 主要内容 | 完成标准 |
| --- | --- | --- | --- |
| 20.1 架构边界梳理 | 已完成 | 新增 `GATEWAY_RUNTIME_ROLES`，支持 `all`、`api`、`worker`、`scheduler`、`delivery`、`dashboard`、`control`、`observability` 运行角色；`serve()` 按角色启动控制面、入站、调度器、投递器、Dashboard 和观测后台。 | 默认 `all` 单机模式不变；代码和文档已说明哪些模块未来可独立运行。 |
| 20.2 Redis 最小接入 | 已完成 | 已完成 Redis 配置、客户端封装、健康检查、飞书 Webhook 事件去重、Cron 自动调度幂等 key 和 Cron 跨实例限流。 | 多实例启动时不会重复处理同一飞书事件或重复触发同一 Cron。 |
| 20.3 后台任务队列 | 已完成 | 已新增 `TaskInstance`、本地 `LocalTaskStore`、本地 `LocalTaskQueue` 和 `TaskWorkerRuntime`；Cron/Heartbeat 自动调度已进入任务链路；明确命令式长任务可配置化转入后台执行；控制面和 Dashboard 已支持任务查看、取消和重试；PostgreSQL 开启后任务预占优先使用数据库原子 reserve。 | 用户消息可快速返回“已接收/处理中”，长任务由 worker 后台完成，并可通过控制面和 Dashboard 追踪和干预；多 worker 共享 PostgreSQL 时不会重复抢占同一任务。 |
| 20.4 PostgreSQL 状态外置 | 已完成 | 设计 sessions、tasks、runtime_events、errors、metrics、memory_entries、config_audits 表；保留 JSONL 作为审计备份或降级路径。 | Dashboard 主要列表可从数据库查询，支持分页、筛选和归档。 |
| 20.5 PostgreSQL 初始化与回填 | 已完成 | 增加 schema 初始化命令、本地 JSON/JSONL 回填命令、dry-run 预检、批量 upsert、实库回放校验、README 迁移说明、状态迁移审计，并把可靠投递队列接入 PostgreSQL primary storage。 | 新环境可一键建表；旧本地数据可安全回填；重复执行不会产生重复配置和运行数据；开启 `GATEWAY_POSTGRES_ENABLED=true` 后运行时优先读写 PostgreSQL，本地文件作为兜底和审计；Prompt、Skill、Cron 配置等运行资产继续文件化。 |
| 20.6 分布式可靠队列升级 | 已完成 | 在 PostgreSQL-backed delivery queue 基础上，已新增 RabbitMQ-backed 分发层；PostgreSQL 作为事实状态表，RabbitMQ 作为跨进程唤醒、ack、retry、dead-letter 和削峰层，Redis Streams 保留为轻量备选。 | delivery-worker 可通过 RabbitMQ 分发和 PostgreSQL reserve 横向扩展；失败消息可重试、可进入 DLQ、可在 Dashboard 和控制面处理。 |
| 20.7 生产部署编排 | 进行中 | 已完成 Dockerfile、Docker Compose、基础依赖编排、数据卷和部署说明；后续补启动前检查、systemd、备份恢复、反向代理和 HTTPS。 | 新机器按文档可启动完整依赖和 gateway 服务。 |
| 20.8 统一观测与压测 | 进行中 | 先定义压测指标口径、场景边界和报告格式，再增加 Prometheus metrics endpoint、压测脚本、容量基线、P95 延迟、队列积压、worker 吞吐和错误率指标。 | 能用压测报告说明系统在不同并发下的瓶颈和容量。 |
| 20.9 分布式入站任务顺序与互斥 | 待实现 | 在 `agent_inbound` 入站任务队列化基础上，补齐同 session 互斥执行、近似顺序执行、worker 抢占治理，并为后续 per-session lane 演进打基础。 | 多 worker / 多实例消费入站任务时，同一 session 不并发执行，失败可重试，顺序风险可观测。 |

#### 开展顺序建议

1. 先做 20.1：把进程边界和队列边界设计清楚，避免一开始就把代码改散。
2. 再做 20.2：Redis 的投入最小，但能立即解决多实例去重、Cron 幂等和全局限流。
3. 接着做 20.3：把 Phase 15 遗留的长任务后台化、低优先级任务调度和 per-agent 并发治理接到 task instance。
4. 然后做 20.4：PostgreSQL 接管长期状态，支撑 Dashboard 查询、审计、归档和治理。
5. 再做 20.5：补齐 PostgreSQL schema 初始化和本地数据回填，确保主存储切换不是只停留在代码路径。
6. 然后做 20.6：当任务和状态稳定后，再升级可靠投递队列，避免同时改动执行链路和出站链路。
7. 然后做 20.7 和 20.8：补齐部署、观测和压测，用指标验证高可用和高性能目标是否真实达成。
8. 最后推进 20.9：在入站任务队列化后补齐同 session 互斥、顺序治理和 per-session lane 演进路径。

#### 完成标准

- 支持至少两个 gateway 实例同时运行，入站事件不重复处理。
- 支持多个 agent worker 并发消费任务，长任务不阻塞实时消息入口。
- 支持多个 agent worker 消费入站任务时，同一 session 不会并发执行。
- 支持 delivery worker 水平扩展，投递失败可重试、可死信、可人工处理。
- 关键状态不依赖单机 JSONL，Dashboard 可以分页查询任务、事件、错误和记忆。
- Redis、PostgreSQL、队列和反向代理都有健康检查、配置说明和降级策略。
- 有基础压测结果，能说明当前机器配置下的吞吐、延迟和瓶颈。

#### Phase 20.7 生产部署编排

状态：进行中。

目标：

- 把当前本地手动启动方式升级为可复现的单机部署形态。
- 固定 Redis、PostgreSQL、RabbitMQ、Gateway、Dashboard 的启动、端口、数据卷和健康检查边界。
- 为后续 systemd、反向代理、HTTPS、备份恢复和多角色拆分部署打基础。

| 子阶段 | 状态 | 主要内容 | 完成标准 |
| --- | --- | --- | --- |
| 20.7.1 Docker Compose 基础编排 | 已完成 | 新增 `Dockerfile`、`.dockerignore`、`docker-compose.yml` 和 `deploy/docker-compose.md`；编排 Redis、PostgreSQL、RabbitMQ、Gateway，设置健康检查、数据卷和本机端口绑定。 | 可 `docker compose up -d --build` 拉起依赖和 Gateway；文档说明 schema 初始化、数据卷和访问地址。 |
| 20.7.2 启动前检查命令 | 已完成 | 新增 `agent-gateway doctor` 和 `agent-gateway doctor --json`，检查 `.env`、模型配置、目录权限、Redis、PostgreSQL、RabbitMQ、PostgreSQL schema 和公网绑定风险。 | 启动前能输出 pass/warn/fail；存在 fail 时返回非零退出码。 |
| 20.7.3 systemd 部署方式 | 已完成 | 新增 `deploy/systemd/agent-gateway.service`、`deploy/systemd/agent-gateway.env.example` 和 `deploy/systemd.md`，包含环境文件、doctor 预检查、重启策略、日志查看和升级流程。 | 非 Docker Linux 服务器可用 systemd 托管 Gateway。 |
| 20.7.4 数据卷与备份恢复 | 待实现 | 补齐 `workspace/`、`data/`、PostgreSQL、RabbitMQ、Redis 和 `.env` 的备份/恢复命令。 | 文档提供可执行备份和恢复步骤，明确哪些数据不可丢。 |
| 20.7.5 反向代理与 HTTPS | 待实现 | 增加 Nginx 或 Caddy 示例，支持飞书 Webhook HTTPS 暴露，并限制 Dashboard 访问范围。 | 飞书 Webhook 可通过 HTTPS 访问；Dashboard 不裸奔公网。 |
| 20.7.6 部署文档整合 | 已完成 | 将部署模式、端口表、健康检查、常见故障和升级步骤整合进 README / deploy 文档。 | 新机器可按文档完成部署和基础排障。 |

##### 20.7.1 Docker Compose 基础编排结果

已完成内容：

- 新增 `Dockerfile`，基于 `python:3.12-slim` 构建 Gateway 镜像。
- 新增 `.dockerignore`，避免 `.env`、虚拟环境、运行数据和压测产物进入镜像。
- 新增 `docker-compose.yml`：
  - `redis`：启用 AOF，并绑定 `127.0.0.1:6379`。
  - `postgres`：使用 `postgres:16-alpine`，默认账号 `postgres/postgres`。
  - `rabbitmq`：使用 management 镜像，默认账号 `admin/admin123`。
  - `gateway`：挂载 `config/`、`workspace/`、`data/`，并覆盖容器内中间件地址为服务名。
- 新增 `deploy/docker-compose.md`，说明服务组成、启动命令、schema 初始化、访问地址、数据卷和当前边界。
- README 增加 Docker Compose 快速入口。
- 已通过 `docker compose version` 和 `docker compose config` 验证 Compose v2 可用与配置语法正确。

当前边界：

- Compose 默认单机 `GATEWAY_RUNTIME_ROLES=all`，尚未拆分 api/worker/delivery/scheduler 多服务。
- Dashboard 和中间件端口默认只绑定 `127.0.0.1`，暂不直接支持公网暴露。
- 飞书 Webhook 生产 HTTPS 暴露放到 20.7.5 反向代理阶段。
- Compose 当前不自动执行 `postgres-init`，首次启动后需要手动执行初始化命令。

##### 20.7.2 启动前检查命令结果

已完成内容：

- 新增 `agent_gateway/runtime/diagnostics.py`。
- 新增 CLI：
  - `agent-gateway doctor`
  - `agent-gateway doctor --json`
- `doctor` 不构建完整 Gateway app，不启动入站通道、模型调用或后台任务。
- 当前检查项包括：
  - `.env` 是否存在。
  - `ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL`、`MODEL_ID`。
  - `workspace/`、`data/`、`config/` 是否存在且可写。
  - Redis ping。
  - PostgreSQL `pg_isready`。
  - PostgreSQL schema drift。
  - RabbitMQ broker stats。
  - RabbitMQ 开启时 PostgreSQL 是否启用。
  - Dashboard 绑定公网地址风险。
  - 飞书 Webhook 外部绑定但未配置 encrypt key 的风险。
- 文本输出按 `PASS/WARN/FAIL` 展示；JSON 输出适合部署脚本和 CI 读取。
- 存在 `FAIL` 时退出码为 `1`。

使用示例：

```bash
agent-gateway doctor
agent-gateway doctor --json
```

Docker Compose 初始化建议：

```bash
docker compose up -d --build
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-init
docker compose exec gateway agent-gateway postgres-check-schema
```

当前边界：

- `doctor` 是启动前轻量检查，不替代运行时 `health.check`。
- RabbitMQ 检查会声明/探测队列拓扑，但不会发布业务消息。
- 飞书检查当前只做配置风险提示，不主动访问飞书 OpenAPI。

##### 20.7.3 systemd 部署方式结果

已完成内容：

- 新增 `deploy/systemd/agent-gateway.service`。
- 新增 `deploy/systemd/agent-gateway.env.example`。
- 新增 `deploy/systemd.md`。
- service 默认：
  - `WorkingDirectory=/home/obiah/Desktop/claw0/gateway`
  - `EnvironmentFile=/etc/agent-gateway/agent-gateway.env`
  - `ExecStartPre=agent-gateway --env-file ... doctor`
  - `ExecStart=agent-gateway --env-file ... serve`
  - `Restart=on-failure`
  - 日志进入 journald。
- README 增加 systemd 部署入口。

使用示例：

```bash
sudo mkdir -p /etc/agent-gateway
sudo cp deploy/systemd/agent-gateway.env.example /etc/agent-gateway/agent-gateway.env
sudo cp deploy/systemd/agent-gateway.service /etc/systemd/system/agent-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable agent-gateway
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env doctor
sudo systemctl start agent-gateway
```

当前边界：

- service 示例默认单进程 `GATEWAY_RUNTIME_ROLES=all`。
- Redis、PostgreSQL、RabbitMQ 需要由系统包、Docker 或其他方式单独托管。
- 飞书 Webhook HTTPS 暴露仍放到 20.7.5 反向代理阶段。

#### Phase 20.8 统一观测与压测

状态：进行中。

目标：

- 用统一指标证明系统在不同并发、不同队列后端和不同外部依赖条件下的容量上限。
- 区分网关自身瓶颈、模型 API 瓶颈、飞书发送瓶颈、PostgreSQL/RabbitMQ/Redis 中间件瓶颈。
- 生成可复现的 Markdown 压测报告，避免只凭 Dashboard 主观判断性能。

##### 20.8.1 指标口径定义

状态：已完成。

压测分层：

| 层级 | 目标 | 说明 |
| --- | --- | --- |
| 本地闭环压测 | 测网关自身调度能力 | 使用 mock agent / mock channel，绕开真实模型和外部平台，重点观察入站 lane、任务队列、投递队列和状态写入。 |
| 队列链路压测 | 测 PostgreSQL + RabbitMQ 可靠投递能力 | 只发送轻量投递消息，验证 reserve、ack、retry、DLQ、队列积压和多 delivery worker 消费。 |
| 真实链路压测 | 测端到端用户体验 | 使用真实模型、真实飞书发送或 Webhook，低并发逐步提升，观察外部 API 延迟、限流和失败率。 |

核心指标：

| 指标 | 口径 | 主要来源 |
| --- | --- | --- |
| 入站吞吐 `inbound_rps` | 每秒成功进入 dispatcher 的消息数 | `inbound.received` runtime events、压测客户端统计。 |
| 端到端延迟 `e2e_ms` | 从压测客户端发起到收到最终回复/投递完成的耗时 | 压测客户端本地计时；真实链路可关联 `correlation_id`。 |
| Agent 执行延迟 `agent_turn_ms` | `agent.turn.started` 到 `agent.turn.completed/failed` 的耗时 | runtime events 中 `duration_ms`。 |
| 工具调用延迟 `tool_call_ms` | 工具 started 到 completed/failed 的耗时 | `tool.call.*` runtime events。 |
| 投递延迟 `delivery_ms` | `delivery.enqueued` 到 `delivery.sent/failed` 的耗时 | runtime events、delivery id。 |
| P50/P95/P99 | 各类延迟分位数 | 压测脚本聚合。 |
| 错误率 `error_rate` | failed/rejected/error 数量占总请求比例 | runtime events、压测客户端。 |
| 入站积压 `inbound_backlog` | 全局队列、lane 队列和运行中 lane 数 | `runtime.status.inbound`、metrics snapshot。 |
| 投递积压 `delivery_backlog` | pending、retrying、failed、DLQ 数量 | `delivery.stats`、RabbitMQ stats。 |
| worker 吞吐 `worker_tps` | task worker / delivery worker 每秒完成量 | task stats、delivery events。 |
| 中间件状态 | Redis、PostgreSQL、RabbitMQ 是否健康 | `health.check`、broker stats、PostgreSQL schema check。 |

压测场景矩阵：

| 场景 | 并发建议 | 目标 | 是否调用真实外部服务 |
| --- | --- | --- | --- |
| `mock-local` | 1、4、8、16、32 | 找网关本地调度上限和入站 lane 瓶颈 | 否 |
| `delivery-local` | 1、4、8、16 | 找 PostgreSQL + 本地投递轮询瓶颈 | 否 |
| `delivery-rabbitmq` | 1、4、8、16、32 | 找 RabbitMQ 分发、reserve 和 ack 瓶颈 | 否 |
| `inbound-rabbitmq` | 1、4、8、16、32 | 找 RabbitMQ 入站分区、task_id 预占、session lane ownership、TaskWorkerRuntime 和热点 session 瓶颈 | 否 |
| `feishu-webhook` | 1、2、4、8 | 验证飞书入站和出站真实稳定性 | 是 |
| `model-real` | 1、2、4 | 验证真实模型 profile、fallback、限流和上下文开销 | 是 |

报告固定结构：

```text
# AI Agent Gateway 压测报告

## 基本信息
- 时间：
- 机器：
- Git commit：
- Python 版本：
- 运行角色：
- Redis / PostgreSQL / RabbitMQ 配置：

## 场景配置
- 场景：
- 并发：
- 请求数：
- 是否真实模型：
- 是否真实飞书：

## 结果摘要
- 成功数 / 失败数 / 错误率：
- 吞吐：
- E2E P50 / P95 / P99：
- Agent P50 / P95 / P99：
- Delivery P50 / P95 / P99：
- 最大入站积压：
- 最大投递积压：

## 瓶颈判断
- 主要瓶颈：
- 证据：
- 建议：

## 原始指标摘要
- runtime.status：
- delivery.stats：
- metrics.summary：
- errors.recent：
```

验收标准：

- 后续压测脚本必须按上述指标命名和报告结构输出。
- mock 场景和真实外部服务场景必须分开，不能把模型/飞书延迟误判为网关本地瓶颈。
- 每次压测必须记录 Git commit、运行角色和 Redis/PostgreSQL/RabbitMQ 开关。

##### 20.8 后续子阶段

| 子阶段 | 状态 | 主要内容 | 完成标准 |
| --- | --- | --- | --- |
| 20.8.2 压测脚本 MVP | 已完成 | 新增 `scripts/load_test_gateway.py`，支持 `mock-local` 场景、并发、请求数、模拟 Agent/Delivery 延迟、JSON/Markdown 输出。 | 能跑 `mock-local` 并生成报告到 `workspace/reports/load-tests/`。 |
| 20.8.3a 本地投递队列压测 | 已完成 | `delivery-local` 场景已接入真实 `DeliveryQueue`、`DeliveryRuntime` 和 mock channel，测量本地文件投递队列的单 worker flush 吞吐。 | 能生成本地投递队列报告，展示最大投递积压、吞吐和投递 P95。 |
| 20.8.3b RabbitMQ 投递链路压测 | 已完成 | 新增 `delivery-rabbitmq` 场景，覆盖 RabbitMQ 分发、DeliveryQueue reserve、DeliveryRuntime consume、ack 和 broker stats。 | 已能对比 `delivery-local` 和 `delivery-rabbitmq` 的吞吐、积压和 P95。 |
| 20.8.3c RabbitMQ 入站任务压测 | 已完成 | 新增 `inbound-rabbitmq` 场景，覆盖 RabbitMQ 入站分区、轻量 task_id 引用、`LocalTaskQueue.reserve_task_id()`、`TaskWorkerRuntime` hybrid consume、worker 池、session 分布参数、本地 lane ownership 探针和真实 Redis lane ownership 模式；压测 worker 使用独立 broker 连接，避免共享 pika channel。 | 能生成入站 broker 报告，展示最大入站积压、吞吐、P95、分区数、命中分区数、最大活跃 lane、同 session 最大并发、Redis 健康状态和消费后 broker 积压；本机 RabbitMQ + Redis smoke 已验证 `max_same_session_concurrency=1` 且消费后 broker 积压为 0。 |
| 20.8.4 真实链路压测 | 部分完成 | 已新增 `model-real` 和 `feishu-send-real` 场景，必须显式 `--allow-real-external` 才会调用真实模型或发送真实飞书消息；飞书 Webhook 入站压测仍待补齐。 | 已能低并发验证真实模型、上下文装配、AgentLoopRunner 延迟和飞书出站发送延迟；后续补真实飞书入站链路。 |
| 20.8.5 Prometheus metrics endpoint | 已完成 | Dashboard HTTP 服务新增 `/metrics`，输出 Prometheus text exposition，覆盖 metrics 可用性、窗口样本数、投递积压、入站 lane、事件错误、Cron 和模型 profile 指标。 | Prometheus 可 scrape，指标名稳定。 |
| 20.8.6 容量基线报告 | 已完成 | 新增 `scripts/build_capacity_baseline.py`，可汇总 load-test JSON，按场景生成容量基线 Markdown，包含吞吐、P95、错误率、积压和瓶颈判断。 | `workspace/reports/capacity-baseline.md` 下有可复现 Markdown 报告。 |
| 20.8.7 边界测试矩阵 | 待实现 | 将 20.8 的压测从“单场景验证”升级为“边界定位矩阵”，按本地闭环、队列链路、真实模型、飞书链路、故障注入和资源上限分层执行。 | 能明确说明系统在不同并发、不同后端和不同故障条件下的容量边界与退化方式。 |

##### 20.8.2 压测脚本 MVP 结果

已完成内容：

- 新增 `scripts/load_test_gateway.py`，作为 Phase 20.8 的独立压测入口。
- 当前支持 `mock-local` 场景，不调用真实模型、飞书、PostgreSQL、Redis 或 RabbitMQ。
- 支持参数：
  - `--requests`
  - `--concurrency`
  - `--agent-delay-ms`
  - `--delivery-delay-ms`
  - `--report-dir`
  - `--basename`
- 输出 JSON 和 Markdown 两份报告，默认目录为 `workspace/reports/load-tests/`。
- 报告结构遵循 20.8.1 定义，包含 Git commit、Python 版本、机器信息、吞吐、P50/P95/P99、错误率和瓶颈判断。

使用示例：

```bash
python scripts/load_test_gateway.py --scenario mock-local --requests 100 --concurrency 8
```

当前边界：

- `mock-local` 只用于建立本地压测和报告生成基线，不能代表真实模型或飞书链路性能。
- 队列链路压测将在 20.8.3 中接入 PostgreSQL / RabbitMQ 真实投递路径。

##### 20.8.3a 本地投递队列压测结果

已完成内容：

- `scripts/load_test_gateway.py` 新增 `delivery-local` 场景。
- 该场景使用真实 `DeliveryQueue`、`DeliveryRuntime` 和 mock channel，不调用真实模型、飞书、PostgreSQL、Redis 或 RabbitMQ。
- 本地文件队列作为 fallback/audit 路径，按单 delivery worker 测量；多 worker 并发消费留给 RabbitMQ 场景验证。
- 报告会记录最大投递积压、吞吐、Delivery P50/P95/P99 和本地队列临时目录。

使用示例：

```bash
python scripts/load_test_gateway.py --scenario delivery-local --requests 100 --concurrency 1 --delivery-delay-ms 0
```

本机 smoke：

```text
requests=20 concurrency=4 delivery_delay_ms=0
success=20 failed=0 throughput_rps≈1172 e2e_p95_ms≈0.853
```

当前边界：

- `delivery-local` 的有效投递 worker 固定为 1；`--concurrency` 当前只作为报告参数保留，不用于并发扫描本地文件队列。
- 本场景不代表 PostgreSQL/RabbitMQ 多 worker 能力；下一步 20.8.3b 需要接入 RabbitMQ 分发路径。

##### 20.8.3b RabbitMQ 投递链路压测结果

已完成内容：

- `scripts/load_test_gateway.py` 新增 `delivery-rabbitmq` 场景。
- 该场景使用真实 RabbitMQ broker、真实 `DeliveryQueue`、真实 `DeliveryRuntime` 和 mock channel，不调用真实模型、飞书、PostgreSQL 或 Redis。
- 为避免污染真实数据库，脚本内置轻量 `InMemoryDeliveryBackend` 作为 `delivery_entries` 事实状态后端，RabbitMQ 只保存 `delivery_id` 等轻量引用。
- 压测专用队列与正式运行队列隔离：
  - exchange：`agent_gateway.delivery.load_test`
  - queue：`agent_gateway.delivery.load_test.outbound`
  - DLX：`agent_gateway.delivery.load_test.dlx`
  - DLQ：`agent_gateway.delivery.load_test.dead`
- 报告会记录 RabbitMQ publish 后和 consume 后的 broker stats、最大投递积压、吞吐、Delivery P50/P95/P99。

使用示例：

```bash
python scripts/load_test_gateway.py --scenario delivery-rabbitmq --requests 100 --concurrency 8 --delivery-delay-ms 0
```

本机 smoke：

```text
requests=20 concurrency=4 delivery_delay_ms=0
success=20 failed=0 throughput_rps≈1801 e2e_p95_ms≈0.555
```

当前边界：

- `delivery-rabbitmq` 当前验证的是 RabbitMQ 分发路径和 DeliveryRuntime broker consume 能力，不验证真实飞书发送能力。
- 当前场景没有连接真实 PostgreSQL；后续如需验证 PostgreSQL `reserve_delivery()` 的锁竞争和 SQL 成本，需要新增 `delivery-postgres-rabbitmq` 或在本场景增加显式开关。
- 压测产物默认写入 `workspace/reports/load-tests/`，该目录已作为生成物忽略，不纳入版本库。

##### 20.8.4a 真实模型压测结果

已完成内容：

- `scripts/load_test_gateway.py` 新增 `model-real` 场景。
- 该场景复用真实 `build_application().runner.run_turn()`，会走真实 Agent 配置、Prompt 装配、会话写入、ResilienceRunner 和模型 API。
- 为避免误消耗 API quota 或触发限流，运行时必须显式添加 `--allow-real-external`，否则脚本直接拒绝执行。
- 该场景不发送飞书消息，不压测出站投递队列，只用于分离真实模型 API、上下文装配和 AgentLoopRunner 的耗时。
- 报告会把 `uses_real_model=true`、`uses_real_feishu=false` 明确写入 JSON / Markdown，避免与本地 mock 场景混淆。

使用示例：

```bash
python scripts/load_test_gateway.py \
  --scenario model-real \
  --allow-real-external \
  --requests 3 \
  --concurrency 1 \
  --agent-id main \
  --prompt "请用一句中文回复 pong，不要调用工具。"
```

当前边界：

- `model-real` 会真实调用模型，建议从 `requests=1 concurrency=1` 开始，避免误判限流或产生额外费用。
- 该场景生成真实会话记录，建议使用独立 `--session-prefix` 标记压测会话，便于后续清理。
- 飞书 Webhook 入站和飞书发送出站还未接入真实链路压测，后续可增加 `feishu-webhook` 或 `feishu-send-real` 场景。

##### 20.8.4b 飞书真实发送压测结果

已完成内容：

- `scripts/load_test_gateway.py` 新增 `feishu-send-real` 场景。
- 该场景复用真实 `build_application().channel_manager.get("feishu", account_id).send()`，会走当前配置的飞书发送方式、token 刷新、lark-cli 或 OpenAPI 出站链路。
- 为避免误刷飞书消息，运行时必须显式添加 `--allow-real-external`。
- 为避免误发到默认对象，运行时必须同时传入 `--feishu-account-id` 和 `--feishu-peer-id`。
- 该场景不调用模型，不经过 Agent Loop，只用于分离飞书出站 API、lark-cli、token 刷新和平台限流的耗时。
- 报告会把 `uses_real_model=false`、`uses_real_feishu=true` 明确写入 JSON / Markdown。

使用示例：

```bash
python scripts/load_test_gateway.py \
  --scenario feishu-send-real \
  --allow-real-external \
  --requests 5 \
  --concurrency 1 \
  --feishu-account-id feishu-main \
  --feishu-peer-id ou_xxx \
  --message-text "AI Agent Gateway 飞书发送压测。"
```

当前边界：

- `feishu-send-real` 会真实发送飞书消息，建议从 `requests=1 concurrency=1` 开始。
- 该场景不测试飞书 Webhook 入站、事件验签、去重和路由，后续需要单独增加 `feishu-webhook` 场景。
- 该场景不经过可靠投递队列；如需测“入队到飞书发送”的真实出站链路，应新增基于 `DeliveryQueue + DeliveryRuntime + FeishuChannel` 的场景。

##### 20.8.5 Prometheus metrics endpoint 结果

已完成内容：

- Dashboard HTTP 服务新增 `GET /metrics`。
- `/metrics` 输出 Prometheus text exposition 格式，content type 为 `text/plain; version=0.0.4`。
- 指标来源复用控制面 `metrics_summary(limit=60)`，不新增额外采集线程。
- 当前暴露指标包括：
  - `gateway_metrics_configured`
  - `gateway_metrics_available`
  - `gateway_metrics_window_samples`
  - `gateway_delivery_*`
  - `gateway_lanes_*`
  - `gateway_events_*`
  - `gateway_cron_*`
  - `gateway_profiles_*`
- Dashboard 未注入控制面时，`/metrics` 返回 `503` 和最小不可用指标，避免误以为 scrape 正常。

使用示例：

```bash
curl http://127.0.0.1:8780/metrics
```

当前边界：

- `/metrics` 依赖本地 metrics snapshot；刚启动且尚未采集时会显示 `gateway_metrics_available 0`。
- 当前暴露的是摘要型 gauge，尚未输出请求级 histogram、counter 和 label 化的 per-agent/per-channel 指标。
- Prometheus/Grafana 部署配置和容量基线报告放到 20.8.6 或 Phase 20.7 生产编排中继续补齐。

##### 20.8.6 容量基线报告结果

已完成内容：

- 新增 `scripts/build_capacity_baseline.py`。
- 脚本读取 `workspace/reports/load-tests/*.json`，自动筛选合法压测报告。
- 同一场景多份报告会按成功数、吞吐和请求数择优，避免基线表过长。
- 输出 `workspace/reports/capacity-baseline.md`，包含：
  - 基本环境信息。
  - 场景、请求数、并发、成功/失败、错误率、吞吐。
  - E2E / Agent / Delivery P95。
  - 最大投递积压。
  - 分场景瓶颈判断。
  - 使用边界和原始报告路径。

使用示例：

```bash
python scripts/build_capacity_baseline.py
```

建议基线采集顺序：

```bash
python scripts/load_test_gateway.py --scenario mock-local --requests 100 --concurrency 8 --basename baseline-mock-local
python scripts/load_test_gateway.py --scenario delivery-local --requests 100 --concurrency 1 --delivery-delay-ms 0 --basename baseline-delivery-local
python scripts/load_test_gateway.py --scenario delivery-rabbitmq --requests 100 --concurrency 8 --delivery-delay-ms 0 --basename baseline-delivery-rabbitmq
python scripts/build_capacity_baseline.py
```

当前边界：

- 容量基线报告只汇总已有 JSON，不负责自动执行所有压测命令。
- 真实模型和飞书场景需要显式 `--allow-real-external`，不建议纳入默认自动基线。
- 报告是单次或少量样本的容量快照，不等同 SLA；严谨结论需要多轮重复压测和固定机器负载。

##### 20.8.7 边界测试矩阵建议

目标不是把数字跑到最大，而是找出系统在哪一步开始退化。

测试分层：

| 层级 | 目的 | 推荐场景 |
| --- | --- | --- |
| 本地闭环 | 看网关自身调度和 lane 并发极限 | `mock-local` |
| 队列链路 | 看可靠投递、ack、retry、DLQ 和 worker 吞吐 | `delivery-local`、`delivery-rabbitmq` |
| 真实模型 | 看 AgentLoopRunner、上下文装配和外部模型 API 延迟 | `model-real` |
| 真实飞书 | 看通道发送、Webhook、长连接和回包延迟 | `feishu-send-real`，后续补 `feishu-webhook-real` |
| 故障注入 | 看失败场景的退化是否可控 | 模型超时、RabbitMQ 不可用、PostgreSQL 不可用、Redis 不可用、飞书验签失败 |
| 资源上限 | 看 CPU / 内存 / 磁盘 / 队列积压的临界点 | 逐步提高并发并观察 `P95`、错误率和积压 |

建议执行顺序：

1. 先跑 `mock-local` 找到纯调度瓶颈。
2. 再跑 `delivery-local` 和 `delivery-rabbitmq` 对比本地队列和 broker 队列的损耗。
3. 再跑 `model-real` 看真实模型的上限。
4. 再跑 `feishu-send-real` 看外部通道投递边界。
5. 最后做故障注入和资源上限测试，确认系统是否能稳定降级而不是直接崩溃。

建议输出结论：

- 哪个场景先出现 `P95` 激增。
- 哪个组件先出现队列积压。
- 哪个外部依赖先触发失败率上升。
- 在什么并发点开始出现明显退化。
- 哪些错误可以重试恢复，哪些错误需要人工介入。

#### Phase 20.9 分布式入站任务顺序与互斥

状态：待实现。

目标：

- 在 `GATEWAY_INBOUND_TASK_QUEUE_ENABLED=true` 后，保证非 CLI 入站消息进入 `agent_inbound` 任务队列后，多 worker / 多实例消费时不会并发执行同一 session。
- 把当前 `ChannelRuntime` 的进程内 lane 思路，逐步迁移到任务执行层，形成可分布式演进的 per-session lane。
- 先解决同 session 互斥执行，再解决更强的顺序执行和 lane ownership。

当前入站策略：

```text
默认实时路径：
外部入站 -> ChannelRuntime -> preroute lane -> Dispatcher -> AgentLoopRunner

任务化路径：
外部入站 -> ChannelRuntime -> preroute lane -> agent_inbound task -> TaskWorkerRuntime -> AgentInboundTaskHandler -> Dispatcher -> AgentLoopRunner
```

当前边界：

- `ChannelRuntime` lane 只保护入站落任务前的入口阶段。
- `TaskWorkerRuntime` 已支持 session-aware reserve，可在 reserve 前跳过已被 Redis 锁保护的 session。
- PostgreSQL `FOR UPDATE SKIP LOCKED` 能避免同一 task 被重复抢占，并已支持 `blocked_session_keys` 排除热点 session。
- `AgentInboundTaskHandler` 已具备 Redis session lock、token-safe release、长任务续租和锁冲突 retry。
- 当前能力已经能防止同 session 并发执行，并已具备 lane ownership、worker metadata、续租、inspect 和 TTL 接管能力。
- RabbitMQ 入站 broker 已开始接入，当前形态是：TaskStore/PostgreSQL 作为事实状态，RabbitMQ 只承载 `task_id` 等轻量引用并按 `session_key` 分区，worker 优先消费 broker 消息，失败时保留本地/数据库轮询兜底。
- 最终目标是形成完整分布式 per-session lane：RabbitMQ 负责可靠排队，Redis/PostgreSQL 负责 lane 归属和状态，worker 池负责执行，Dashboard 负责队列、分区、lane owner 和延迟观测。

技术选型文档：

- [分布式入站任务顺序与互斥技术选型](doc/分布式入站任务顺序与互斥技术选型.md)

子阶段规划：

| 子阶段 | 状态 | 主要内容 | 完成标准 |
| --- | --- | --- | --- |
| 20.9.1 当前入站任务链路审计 | 已完成 | 已梳理默认实时路径、任务化路径、`ChannelRuntime` lane、`TaskWorkerRuntime` reserve 和 `AgentInboundTaskHandler` 执行边界。 | 明确当前只做到入站任务化，还没有 task worker 层的 session 互斥和顺序保证。 |
| 20.9.2 Redis session lock MVP | 已完成 | 为 `agent_inbound` 任务执行增加 Redis 分布式锁，lock key 基于 `session_key`；获取不到锁或 Redis 锁不可用时进入 retrying。 | 多 worker 同时消费时，同一 session 同一时间最多一个任务进入 AgentLoopRunner。 |
| 20.9.3 锁安全与续租 | 已完成 | 锁 value 使用 `worker_id + task_id`，释放和续租均校验 value；补 TTL 与续租间隔配置，任务执行期间后台续租。 | worker 崩溃可自动释放锁，长模型调用不会因锁过期导致误并发。 |
| 20.9.4 session-aware reserve | 已完成 | `TaskWorkerRuntime` 在 reserve 前收集已被 Redis 锁保护的 session，`LocalTaskQueue` 和 PostgreSQL 原子 reserve 均支持跳过 blocked session，减少拿到任务后再 retry 的抖动。 | 热点 session 不会导致大量任务反复 running/retrying，不同 session 仍可并行推进。 |
| 20.9.5 观测与控制面 | 已完成 | `runtime.status.tasks.session_locks` 暴露被锁 session 数、累计跳过次数和最近样例；Dashboard 中文展示后台任务锁观测；事件流记录 `agent_inbound.session_locked_skipped`。 | 排查入站延迟时能区分模型慢、worker 少、锁冲突和 session 热点。 |
| 20.9.6 故障注入与压测 | 已完成 | 增加同 session 多消息、多 worker 抢占、Redis 探测异常、续租失败、锁跳过事件去重等自动化测试。 | 能证明同 session 不并发执行；Redis 故障时降级策略明确。 |
| 20.9.7 RabbitMQ session 分区评估 | 已完成 | 已输出 [RabbitMQ 入站 Session 分区评估](doc/RabbitMQ入站Session分区评估.md)，评估 `hash(session_key) % N` 分区队列、轻量引用消息体、prefetch=1、hybrid worker 和迁移风险。 | 结论是暂不替换当前 Redis lock + PostgreSQL task 主链路，RabbitMQ 入站分区作为中期演进方向。 |
| 20.9.8 per-session task lane 设计 | 已完成 | 已实现分布式 lane ownership、owner metadata、续租、inspect、TTL 接管和迁移策略文档。 | 从 Redis lock 演进到可观测、可续租、可接管的 per-session lane。 |
| 20.9.8.1 最终 lane 目标落盘 | 已完成 | 根据最终形态说明，明确 RabbitMQ 负责可靠排队，Redis/PostgreSQL 负责 lane 归属与状态，worker 池执行，同 session 串行、不同 session 并行。 | PROJECT_PLAN 明确最终架构，不再把 RabbitMQ 分区和 lane ownership 混为一层。 |
| 20.9.8.2 RedisLaneCoordinator MVP | 已完成 | 抽象 lane ownership API：acquire、renew、release、inspect，owner token 使用 `worker_id + task_id`，兼容当前 Redis lock。 | 单元测试证明同 session 只能一个 owner，续租/释放均 token-safe。 |
| 20.9.8.3 AgentInboundTaskHandler 接入 lane coordinator | 已完成 | handler 内部已从直接调用 Redis lock 迁移到 `RedisLaneCoordinator`，保留现有 key namespace、错误消息和事件行为。 | 现有 20.9.2-20.9.6 测试继续通过。 |
| 20.9.8.4 Worker heartbeat 与 lane metadata | 已完成 | lane ownership value 已增加 worker_id、task_id、acquired_at、renewed_at，续租会刷新 renewed_at；worker blocked session 样例和事件 metadata 已包含 lane owner inspect 信息。 | runtime.status 能看到 active lane owner 和最近续租时间。 |
| 20.9.8.5 超时接管与迁移策略 | 已完成 | 已输出 [分布式 Lane 接管与迁移策略](doc/分布式Lane接管与迁移策略.md)，并验证 owner TTL 过期后的接管、worker 崩溃未 release 后的 pending task 重入。 | 故障注入证明 worker 崩溃后 lane 可由其他 worker 接管。 |
| 20.9.9 RabbitMQ 入站 broker MVP | 已完成 | 新增 `RabbitMQInboundTaskBroker`，按 `hash(session_key) % partitions` 发布轻量任务引用；消息体只包含 task_id、task_type、session_key、partition、idempotency_key 和 published_at，不包含用户正文或 payload。 | 同 session 稳定进入同一分区；RabbitMQ 不保存完整入站消息；broker 支持 ack/nack、DLQ topology、stats 和 purge。 |
| 20.9.10 task_id 精确预占 | 已完成 | `LocalTaskQueue.reserve_task_id()` 和 PostgreSQL `reserve_task_id()` 支持按 broker 消息中的 task_id 原子预占，过滤 task_type、状态和 blocked session。 | 过期 broker 消息不会重复执行已完成任务；worker 不会因为 broker 唤醒而抢到其他 session 的任务。 |
| 20.9.11 worker hybrid consume | 已完成 | `TaskWorkerRuntime.run_once()` 优先消费 RabbitMQ 入站分区消息，预占成功后执行 handler；无 broker 消息时回退原有 PostgreSQL/本地轮询。 | RabbitMQ 可作为分布式唤醒/分发层；broker 不可用或发布失败时任务仍保留在 TaskStore 等待轮询消费。 |
| 20.9.12 入站 broker 观测与压测 | 已完成 | 已将入站 RabbitMQ broker stats 暴露到 `runtime.status.tasks.broker`，Dashboard 后台任务卡片展示入站 Broker 开关、分区数、prefetch、总积压、死信和前 6 个分区积压；Prometheus 已输出 `gateway_tasks_*` broker 积压、死信、分区和最大分区积压指标；broker 消费已记录 `task.broker.acked/requeued/discarded` 事件；`inbound-rabbitmq` 压测场景可验证 partition/worker/session 分布、本地 lane 探针和真实 Redis lane ownership。 | 能用压测报告说明不同 partition/worker/concurrency 下的入站吞吐、积压和热点 session；已完成 RabbitMQ + Redis lane smoke，证明同 session 串行且 broker 积压可清零。 |
| 20.9.13 worker identity 与并发配置 | 已完成 | 新增 `GATEWAY_TASK_WORKER_ID` 和 `GATEWAY_TASK_WORKER_CONCURRENCY`，应用装配时传入 `TaskWorkerRuntime`，并同步给 `AgentInboundTaskHandler` 的 lane owner metadata。 | 多 worker / 多实例部署时，每个 worker 的 Redis lane owner、事件和 Dashboard 样例可区分；单实例 worker 池并发可按机器容量调整。 |
| 20.9.14 故障注入补强 | 已完成 | 已补重复 RabbitMQ 入站消息幂等验证：已完成 task 的重复 broker 消息会 ack/discard，不会再次执行 handler；已补 PostgreSQL/主存储 `reserve_task_id` 短暂异常时的本地 TaskStore fallback 验证；新增 `scripts/smoke_distributed_lane.py`，用于真实 RabbitMQ + Redis lane 快速验收；本机 inbound smoke 已验证 8/8 成功、`max_same_session_concurrency=1`、broker 积压和 DLQ 为 0；本机 TTL takeover smoke 已验证旧 worker 持有 lane、新 worker TTL 前被阻塞、TTL 后成功接管且 owner metadata 切换；本机 broker-unavailable smoke 已验证 broker publish 失败后任务仍 pending，worker 通过 polling fallback 执行到 done；本机 primary-unavailable smoke 已验证主存储 `reserve_task_id` 异常时，worker 仍能按 broker task_id 通过本地 TaskStore fallback 执行到 done 并 ack broker payload；本机 worker-crash 集成 smoke 已验证旧 worker 持有 lane 后崩溃不释放，新 worker TTL 前不抢跑、TTL 后通过 `TaskWorkerRuntime` 接管并执行任务到 `done`，handler 只调用一次且 lane 最终释放。 | 已形成可复现故障注入清单，覆盖 broker 不可用、主存储短暂不可用、TTL 接管、worker crash 和重复消息幂等。 |
| 20.9.15 PostgreSQL lane 状态基础 | 已完成 | 新增 `session_lanes` PostgreSQL 状态表，记录 session_key、lane_key、worker_id、task_id、owner_token、state、ttl_seconds、acquired_at、renewed_at、updated_at 和 metadata；`RedisLaneCoordinator` acquire/renew/release 会可选双写 `write_session_lane` / `release_session_lane`，应用装配在 PostgreSQL 写仓储启用时注入该状态仓储；`runtime.status.tasks.persisted_lanes` 和 Dashboard 后台任务卡片已展示最近持久 lane owner。 | Redis 仍作为快速互斥路径，PostgreSQL 可审计最近 lane owner、续租和释放状态；写库失败不会影响 Redis lane 主路径；运维面板能看到最近 session lane owner。 |

推荐落地顺序：

1. 已完成 20.9.12，把 RabbitMQ 入站 broker 的分区、积压、ack/nack、DLQ 和延迟暴露到 Dashboard / Prometheus。
2. 已补入站 broker 压测场景，验证不同 partitions、worker 数和 session 分布下的吞吐边界。
3. 已完成故障注入：RabbitMQ 不可用、消息重复、worker crash、lane owner TTL 过期、PostgreSQL 短暂不可用。
4. 已新增 PostgreSQL `session_lanes` 持久状态基础，并接入 `runtime.status` 与 Dashboard；后续可继续做更完整的 lane owner 历史、恢复和人工接管视图。

阶段完成标准：

- 开启 `GATEWAY_INBOUND_TASK_QUEUE_ENABLED=true` 且运行多个 worker 时，同一 session 不会并发执行多个 `agent_inbound`。
- Redis 可用时优先使用分布式 lane ownership；Redis 不可用时有明确降级策略和 warning event。
- RabbitMQ 入站 broker 开启后，入站任务先落 TaskStore，再发布轻量 task_id 引用；发布失败不会丢任务，worker 仍可通过轮询兜底。
- Dashboard 能看到入站任务积压、锁等待和热点 session。
- 压测报告能说明同 session 多消息场景下的吞吐、P95 和顺序风险。

#### 当前实现说明

- `GATEWAY_RUNTIME_ROLES=all` 仍是默认值，保持原来的单进程全量启动体验。
- `api` 角色启动入站通道、飞书 Webhook 和长连接消费。
- `delivery` 角色启动出站可靠投递后台。
- `scheduler` 角色启动 Heartbeat 和 Cron。
- `dashboard` 角色启动 Dashboard，并自动包含控制面和观测后台。
- `control` 角色只启动 WebSocket JSON-RPC 控制面。
- `observability` 角色启动 metrics 和 alerts 后台采集。
- `worker` 角色已接入任务队列，可消费 Cron task 和明确命令式长任务；PostgreSQL 开启后 reserve 使用 `FOR UPDATE SKIP LOCKED` 原子预占，本地文件仍作为兜底和审计；后续可替换为 Redis/RabbitMQ backend。
- Redis 已增加 `GATEWAY_REDIS_ENABLED`、`GATEWAY_REDIS_URL`、`GATEWAY_REDIS_SOCKET_TIMEOUT_SECONDS` 配置。
- Redis 当前只接入基础设施层和健康检查；默认关闭，不影响本地单机运行。
- Redis 开启但不可用时，`health.check` 会返回 `redis.ping` warning，不会直接阻塞网关启动。
- 飞书 Webhook 事件去重已支持 Redis `SET NX EX`；Redis 不可用时回退本地 JSONL 去重状态。
- Cron 自动调度已支持 Redis 幂等 key；手动 `cron-trigger` 不受幂等限制，便于主动重跑。
- Redis 已提供固定窗口限流工具；当前先接入 Cron 自动调度限流，默认关闭，后续模型调用和通道发送可复用。
- 后台任务控制面已提供 `tasks.list`、`tasks.get`、`tasks.cancel`、`tasks.retry`，可用于排查和人工处理 pending、retrying、failed、cancelled 等任务状态。
- Dashboard 已增加“后台任务”视图，最多默认展示 6 条，可折叠展开，并支持对 pending/running/retrying 任务执行取消、对 failed/cancelled 任务执行重试。
- Heartbeat 自动调度已改为创建 `heartbeat` task instance，由 `TaskWorkerRuntime` 后台执行；手动触发仍保持同步执行，便于控制面立即返回结果。
- 后台长任务命令已支持 `GATEWAY_BACKGROUND_INBOUND_COMMANDS` 配置，默认保留 `/github-repo-analyzer,/space-advisor`，新增命令不再需要修改 `ChannelRuntime` 源码。

#### Phase 20.2 Redis 子阶段

| 子阶段 | 状态 | 主要内容 |
| --- | --- | --- |
| 20.2.1 基础设施接入 | 已完成 | 新增 Redis 配置、连接封装、ping 健康检查、控制面状态和测试。 |
| 20.2.2 飞书事件去重 | 已完成 | 飞书 Webhook 去重已接入 Redis `SET NX EX` 后端，并保留本地去重作为 fallback；长连接去重后续可按需要补齐。 |
| 20.2.3 Cron 幂等 key | 已完成 | Scheduler 触发 Cron 前写入 Redis 幂等 key，避免多实例重复触发；Redis 不可用时回退当前单机行为。 |
| 20.2.4 全局限流 | 已完成 | 新增 Redis `INCR + EXPIRE` 固定窗口限流工具，并先接入 Cron 自动调度；模型调用和通道发送限流留到后续专项阶段。 |

#### Phase 20.3 后台任务队列子阶段

| 子阶段 | 状态 | 主要内容 |
| --- | --- | --- |
| 20.3.1 任务模型与本地存储 | 已完成 | 新增 `TaskInstance`，支持 pending、running、retrying、done、failed、cancelled 状态；新增本地 JSON 文件任务存储。 |
| 20.3.2 本地任务队列接口 | 已完成 | 增加 enqueue、reserve、ack、retry、fail、cancel 和 stats 抽象，先用本地 backend 实现。 |
| 20.3.3 Worker 运行时 | 已完成 | 增加 `TaskWorkerRuntime`，支持 handler 注册、并发 worker loop、成功 ack、失败 fail、异常 retry 和 stats。 |
| 20.3.4 Cron/Heartbeat 任务化 | 已完成 | Cron 自动调度已改为创建 `cron` task instance，Heartbeat 自动调度已改为创建 `heartbeat` task instance，二者均由 `TaskWorkerRuntime` 执行；手动触发保留同步执行。 |
| 20.3.5 长任务 Skill 任务化 | 部分完成 | 明确命令式长任务 `/github-repo-analyzer`、`/space-advisor` 已先入 `agent_inbound` task，再由 `TaskWorkerRuntime` 走原 dispatcher 链路后台执行并投递结果；更通用的 tool-call 级后台化留到后续阶段。 |
| 20.3.6 任务控制面接口 | 已完成 | 新增 `tasks.list`、`tasks.get`、`tasks.cancel`、`tasks.retry`，并将 `task_queue` 注入控制面；列表默认只返回 payload preview，详情可按需返回完整 payload。 |
| 20.3.7 Dashboard 任务视图 | 已完成 | 在运维面板增加最近后台任务视图，最多展示 6 条，多余折叠；展示任务类型、状态、Agent、来源、时间、错误和取消/重试操作入口。 |
| 20.3.8 Heartbeat 任务化 | 已完成 | 将 Heartbeat 自动调度从 scheduler 直接执行迁移为 enqueue 后由 worker 执行，避免调度器承担实际任务执行。 |
| 20.3.9 后台命令配置化 | 已完成 | 将 `/github-repo-analyzer`、`/space-advisor` 等后台命令从硬编码迁移到 `GATEWAY_BACKGROUND_INBOUND_COMMANDS` 环境变量，支持逗号分隔扩展。 |
| 20.3.10 PostgreSQL 任务原子预占 | 已完成 | `LocalTaskQueue.reserve()` 在 PostgreSQL 写仓储可用时优先调用 `reserve_task()`；数据库侧使用 `UPDATE ... FOR UPDATE SKIP LOCKED` 原子选择并标记 running，避免多 worker 重复消费同一任务。 |

#### Phase 20.4 PostgreSQL 状态外置子阶段

| 子阶段 | 状态 | 主要内容 |
| --- | --- | --- |
| 20.4.1 状态边界与表设计 | 已完成 | 明确 sessions、tasks、runtime_events、errors、metrics、memory_entries、config_audits 的最小字段、主键、时间列、索引和保留策略；保留 JSONL 作为回退和审计。 |
| 20.4.2 仓储接口草案 | 已完成 | 定义状态仓储抽象，先不替换业务写入，只约束 list/get/append/upsert/query/delete 的统一接口。 |
| 20.4.3 只读仓储统一入口 | 已完成 | 先把 Dashboard / 控制面读取统一接到 `StateReadRepository`，本地 JSONL / 内存存储先作为默认后端；后续切换 PostgreSQL 时不改上层调用。 |
| 20.4.4 PostgreSQL 只读后端 | 已完成 | 为 sessions、tasks、runtime_events、errors、metrics、memory_entries、config_audits 提供 PostgreSQL 只读实现，Dashboard 按配置切换。 |
| 20.4.4.1 仓储查询映射 | 已完成 | 补齐各表主键、排序列、过滤字段和只读查询骨架，确保 read path 的 SQL 形态稳定。 |
| 20.4.4.2 后端切换开关 | 已完成 | `GATEWAY_POSTGRES_ENABLED` 开关可切到 PostgreSQL 只读仓库，默认仍返回本地仓库。 |
| 20.4.4.3 只读结果对齐 | 已完成 | 把 PostgreSQL 返回结构进一步对齐本地仓库，减少 control plane / Dashboard 适配成本。 |
| 20.4.4.3.1 错误视图对齐 | 已完成 | PostgreSQL `errors` 输出对齐 `RuntimeEventStore.recent_errors` 的事件形态，避免控制面重复适配。 |
| 20.4.4.3.2 记忆视图对齐 | 已完成 | PostgreSQL `memory_entries` 输出对齐 `MemoryStore.recent_entries` 的摘要形态，保持 Dashboard 视图一致。 |
| 20.4.5 双写与迁移脚手架 | 已完成 | 为会话、任务、事件和记忆补齐迁移备份脚手架，主链路仍保留 JSONL，新增独立 migration 备份目录用于后续切换和回放验证。 |
| 20.4.6 PostgreSQL 写入后端草案 | 已完成 | 已补齐 PostgreSQL 写入仓储骨架，并在状态仓储装配中显式暴露 `write` 接口；默认仍保持本地 JSONL 主链路。 |
| 20.4.7 PostgreSQL 通用写入接口 | 已完成 | 为 PostgreSQL 写后端补齐通用 `append/upsert/delete/query` 实现，先保证写入骨架和 SQL 形态可用，再做表级细化。 |
| 20.4.8 PostgreSQL 写入可执行化 | 已完成 | 修正写后端 SQL 参数展开与返回形态，让通用写路径具备最小可执行能力，并保持现有测试通过。 |
| 20.4.9 sessions 表级写入适配 | 已完成 | 先把 sessions 的 append/upsert/delete 拆成表级适配入口，作为后续 tasks、events、memory 写入切换的模板。 |
| 20.4.10 tasks 表级写入适配 | 已完成 | 把 tasks 的 append/upsert/delete 拆成表级适配入口，继续推进 PostgreSQL 作为主存储的可迁移性。 |
| 20.4.11 runtime_events 表级写入适配 | 已完成 | 把 runtime_events 的 append/upsert/delete 拆成表级适配入口，确保观测链路也能切到 PostgreSQL。 |
| 20.4.12 memory_entries 表级写入、统计与召回适配 | 已完成 | 把 memory_entries 的 append/upsert/delete 拆成表级适配入口；`MemoryStore.hybrid_search()` 和 `get_stats()` 已优先从 PostgreSQL `memory_entries` 构造检索块和统计结果，数据库不可用或无数据时再回退本地 MEMORY.md 和 daily JSONL。 |
| 20.4.13 config_audits 表级写入适配 | 已完成 | 把 config_audits 的 append/upsert/delete 拆成表级适配入口，补齐配置审计的数据库主存储。 |
| 20.4.14 控制面配置审计接入 | 已完成 | 控制面在保存 agents / bindings / profiles / channels，以及 set/remove agent 时，都会同步写入 config_audits；主配置文件仍保留 JSON 作为落盘和回放来源。 |
| 20.4.15 主写入口双写接入 | 已完成 | SessionStore、LocalTaskStore、RuntimeEventStore、MemoryStore 的 backup sink 已接入复合迁移层，可同时镜像到 PostgreSQL 写仓储和本地 migration 目录，主链路保留 JSONL 兜底。 |
| 20.4.16 PostgreSQL 读路径优先 | 已完成 | Session、task、event、memory、metrics、alerts 的读取现在可优先走 PostgreSQL 读后端，文件系统仍作为兜底回退。 |
| 20.4.17 观测数据写入镜像 | 已完成 | MetricsStore 和 AlertStore 现在会把写入镜像到 PostgreSQL 备份层，同时继续保留本地 JSONL 作为保底。 |
| 20.4.18 配置表读写适配 | 已完成 | PostgreSQL 写仓储补齐 agents、bindings、profiles、channels 表级 upsert/delete；读仓储支持四类配置表查询，内部 key 字段在控制面读取时自动清理。 |
| 20.4.19 控制面配置数据库优先 | 已完成 | 控制面 `get_source/reload/save/set/remove` 已支持数据库优先读取；配置保存和 set/remove 路径已改为 PostgreSQL 写入先行，本地 JSON 文件作为 fallback/audit；profiles 和 channels 保持 `_env` 环境变量解析语义。 |
| 20.4.20 启动阶段配置数据库优先 | 已完成 | `build_application()` 通过临时控制面加载 agents、bindings、profiles、channels，启动时优先使用 PostgreSQL 配置，数据库无数据或不可用时回退 JSON 文件。 |

#### Phase 20.5 PostgreSQL 初始化与回填子阶段

| 子阶段 | 状态 | 主要内容 |
| --- | --- | --- |
| 20.5.1 schema 初始化 SQL | 已完成 | 新增 `build_postgres_schema_sql()`，可根据 `POSTGRES_STATE_TABLES` 生成 agents、bindings、profiles、channels、delivery_entries、sessions、tasks、runtime_events、errors、metrics、memory_entries、config_audits、feishu_dedup_entries、feishu_webhook_events、feishu_onboarding_sessions、channel_offsets、cron_runs、news_items、feishu_card_states 建表和索引 SQL。 |
| 20.5.2 schema 初始化命令 | 已完成 | 新增 `agent-gateway postgres-init`；`--print-sql` 只打印 SQL，不构建完整应用；默认通过本机 `psql` 执行初始化。 |
| 20.5.3 本地回填 dry-run | 已完成 | 新增 `backfill_local_state_to_repository()` 和 `agent-gateway postgres-migrate-local --dry-run`，可扫描本地配置、会话、任务、投递、飞书 Webhook 去重/审计、飞书 onboarding 会话、飞书卡片状态、Telegram offset、Cron 运行记录、新闻简报状态、事件、指标、告警和记忆并输出计数，不写数据库。 |
| 20.5.4 本地数据回填 | 已完成 | `postgres-migrate-local` 可把本地 JSON/JSONL 回填到 PostgreSQL 写仓储；配置表使用自然键，运行数据使用稳定主键，飞书审计使用内容 hash 生成稳定主键，便于重复执行。 |
| 20.5.5 回填测试保护 | 已完成 | 增加 schema 初始化、CLI 入口、dry-run 和代表性运行数据回填测试，确保迁移命令不误启动完整网关。 |
| 20.5.6 实库回放校验 | 已完成 | 已在本机 PostgreSQL 执行 `postgres-init`、`postgres-migrate-local --dry-run` 和实际回填；约 1.65 万条本地数据通过批量 upsert 在约 4.4 秒完成写入，抽样确认 agents、sessions、tasks、runtime_events、metrics、memory_entries 行数和读仓储查询正常。 |
| 20.5.7 迁移文档 | 已完成 | README 已补充 PostgreSQL 配置、schema 初始化、dry-run、实际回填、重复执行语义、本地文件兜底和启用数据库优先读取的操作说明。 |
| 20.5.8 可靠投递队列 PostgreSQL 主存储 | 已完成 | 新增 delivery_entries 表；DeliveryQueue 支持 PostgreSQL 读写 backend，enqueue/fail/move_to_failed/retry/ack/discard 优先写数据库并保留本地 JSON 文件兜底；本地 pending/failed 队列可通过迁移命令回填。 |
| 20.5.9 PostgreSQL 主存储 smoke 验收 | 已完成 | 新增并增强 `agent-gateway postgres-smoke`，不调用模型和外部通道，直接验证配置表、会话、任务、事件、记忆、指标、告警、投递队列、Telegram offset、Cron 运行记录、新闻简报状态和飞书卡片状态能写入 PostgreSQL，并确认本地 JSON/JSONL fallback 文件仍会生成。 |
| 20.5.10 飞书 Webhook 状态 PostgreSQL 化 | 已完成 | 新增 `feishu_dedup_entries` 和 `feishu_webhook_events`；Webhook 去重链路支持 Redis -> PostgreSQL -> 本地文件多级兜底，审计日志优先写 PostgreSQL 并保留本地 JSONL；迁移命令可回填旧 `seen-events.jsonl` 和 `events.jsonl`。 |
| 20.5.11 飞书 onboarding 会话 PostgreSQL 化 | 已完成 | 新增 `feishu_onboarding_sessions`；扫码/绑定会话读取优先 PostgreSQL，写入优先 PostgreSQL 并继续写本地 `sessions.json` 作为兜底；迁移命令可回填旧 onboarding 会话文件。 |
| 20.5.12 Telegram offset PostgreSQL 化 | 已完成 | 新增 `channel_offsets`；Telegram 轮询 offset 读取优先 PostgreSQL，写入优先 PostgreSQL 并继续写本地 offset 文件作为兜底；迁移命令可回填旧 `channel-state/telegram/offset-*.txt`。 |
| 20.5.13 Cron 运行记录 PostgreSQL 化 | 已完成 | 新增 `cron_runs`；CronService 运行记录优先写 PostgreSQL，并继续写本地 `workspace/cron/cron-runs.jsonl` 作为兜底；迁移命令可回填旧 Cron 运行日志。 |
| 20.5.14 新闻简报状态 PostgreSQL 化 | 已完成 | 新增 `news_items`；AI Agent 简报和 GitHub Skill 简报的 collected/seen 状态读取优先 PostgreSQL、写入优先 PostgreSQL，并继续写本地 JSONL 作为兜底；迁移命令可回填旧 `data/news-digest` 和 `data/github-skill-digest`。 |
| 20.5.15 飞书卡片状态 PostgreSQL 化 | 已完成 | 新增 `feishu_card_states`；飞书有状态卡片分页、展开和收起状态读取优先 PostgreSQL，写入优先 PostgreSQL 并继续写本地卡片 JSON 作为兜底；迁移命令可回填旧 `data/channel-state/feishu/*/cards/*.json`。 |
| 20.5.16 状态迁移审计与 smoke 收口 | 已完成 | 新增 `doc/PostgreSQL状态迁移审计.md`，明确数据库主存储、文件 fallback/audit、Prompt/Skill/配置资产的边界；`postgres-smoke` 增补 agents、bindings、profiles、channels 配置表写入和读回校验。 |
| 20.5.17 schema drift 预检命令 | 已完成 | 新增 `agent-gateway postgres-check-schema`，基于 `information_schema.columns` 检查实库表、列和基础类型是否与当前代码声明一致，提前暴露旧库 schema 漂移问题。 |
| 20.5.18 控制面 schema 健康检查 | 已完成 | `runtime.status` 在 PostgreSQL 启用且连通时返回 `postgres.schema`，`health.check` 增加 `postgres.schema` 检查项，schema drift 会以 warning 形式暴露到控制面和 Dashboard。 |
| 20.5.19 默认主存储切换 | 已完成 | `.env.example` 已把 `GATEWAY_POSTGRES_ENABLED=true` 作为默认示例，本机 `.env` 已切到 PostgreSQL 主存储；应用构建验证读仓储、写仓储、SessionStore 和 TaskStore 均使用 PostgreSQL 后端，本地文件继续作为 fallback/audit。 |

#### Phase 20.6 分布式可靠队列升级

状态：已完成。

目标：

- 把当前 `DeliveryRuntime` 的“单实例轮询 pending 队列”升级为“多 delivery worker 可并发消费、可确认、可重试、可死信”的分布式可靠投递链路。
- 保留 PostgreSQL `delivery_entries` 作为事实状态表，确保 Dashboard、控制面、人工 retry/discard 和迁移审计仍有统一查询来源。
- 新增 broker-backed 分发层，用于跨进程唤醒 worker、削峰、消费组协调和失败恢复，避免多个 worker 扫描同一批 pending 记录。
- 保持本地单机默认体验不变：未启用 broker 时继续使用 PostgreSQL/文件队列轮询。

##### 队列中间件选型

| 方案 | 适用定位 | 优点 | 风险与取舍 | 本项目建议 |
| --- | --- | --- | --- | --- |
| PostgreSQL `FOR UPDATE SKIP LOCKED` | 可靠状态表 + 最小多 worker 预占 | 已接入 PostgreSQL；一致性强；便于 Dashboard 查询、人工处理和审计；部署依赖少。 | 高频投递下会增加数据库压力；不适合作为高吞吐消息 broker。 | 作为 20.6 的事实状态表和 fallback，先补齐原子 reserve。 |
| RabbitMQ | 生产级可靠队列、ack、DLX、延迟重试 | 投递语义成熟；支持 durable queue、manual ack、dead-letter exchange、消费者扩容；本机 Docker 已验证 `127.0.0.1:5672` 和 `127.0.0.1:15672` 可访问。 | 新增运维依赖；需要补 Python AMQP 客户端、连接恢复和本地开发文档。 | 作为 20.6 首选 broker MVP。 |
| Redis Streams | 轻量消息 broker、消费组、pending entries | 已有 Redis 基础设施；接入成本低；适合本地/小规模多 worker；支持 consumer group、XACK、XPENDING、XCLAIM。 | 持久化、死信、延迟重试和运维语义弱于 RabbitMQ；需要处理 stream 与 PostgreSQL 状态一致性。 | 降级为轻量备选，不作为 20.6 默认实现。 |

选型结论：

- 第一阶段不直接把队列事实状态迁到 RabbitMQ，而是采用“PostgreSQL 状态表 + RabbitMQ 消费通知”的混合模式。
- `DeliveryQueue.enqueue()` 仍先写 `delivery_entries`，再向 broker publish 一个轻量消息 `{delivery_id, channel, account_id, idempotency_key}`。
- worker 消费 broker 消息后，必须回查 PostgreSQL 并原子 reserve 该 delivery，发送成功后 ack broker 并删除/标记 PostgreSQL 记录。
- broker 不可用时，系统降级为当前轮询模式；PostgreSQL 不可用时，继续保留本地文件 fallback，但不承诺多实例一致性。

本机开发 RabbitMQ 连接基线：

```text
GATEWAY_DELIVERY_BROKER=rabbitmq
GATEWAY_RABBITMQ_URL=amqp://admin:admin123@127.0.0.1:5672/
GATEWAY_RABBITMQ_MANAGEMENT_URL=http://127.0.0.1:15672
GATEWAY_RABBITMQ_USER=admin
GATEWAY_RABBITMQ_PASSWORD=admin123
```

##### 目标链路

```text
GatewayDispatcher.deliver_reply / deliver_text
        ↓
DeliveryQueue.enqueue()
        ↓
PostgreSQL delivery_entries pending
        ↓
Broker publish delivery_id
        ↓
DeliveryWorker consumer group
        ↓
reserve delivery entry
        ↓
channel.send()
        ↓
ack / retry / dead-letter
        ↓
runtime events + Dashboard
```

##### 子阶段拆解

| 子阶段 | 状态 | 主要内容 | 完成标准 |
| --- | --- | --- | --- |
| 20.6.1 当前投递链路审计 | 已完成 | 已梳理 `DeliveryQueue`、`DeliveryRunner`、`DeliveryRuntime`、控制面和 Dashboard 对 pending/failed 的读写依赖；确认 broker 接入前必须保持 enqueue、stats、retry、discard、flush 和事件流兼容。 | 已输出接口边界说明；明确 broker 接入点不能破坏现有 retry/discard。 |
| 20.6.2 Delivery backend 抽象 | 已完成 | 已定义 `DeliveryQueueBackend` / `DeliveryBroker` 协议，并新增 `NoopDeliveryBroker`；`DeliveryQueue` 继续保留 PostgreSQL/本地文件事实状态路径，同时在 enqueue、ack、retry、dead_letter、discard 上预留 broker 分发钩子。 | 本地文件、PostgreSQL 和未来 RabbitMQ broker 的职责边界已拆清；现有投递、控制面和测试行为保持兼容。 |
| 20.6.3 PostgreSQL 原子预占 | 已完成 | 已为 `delivery_entries` 增加 `locked_by`、`locked_at` 字段和索引，新增 `PostgresWriteRepository.reserve_delivery(worker_id, now)`，使用 `FOR UPDATE SKIP LOCKED` 原子抢占 pending/retrying 到期消息；`DeliveryQueue.reserve()` 优先走数据库原子预占，`DeliveryRunner` 已改为按 reserve 消费。 | PostgreSQL backend 可用时，多个 delivery worker 不会通过普通 pending 扫描重复抢占同一条 delivery；本地 fallback 仍保持单机扫描行为。 |
| 20.6.4 RabbitMQ broker MVP | 已完成 | 已新增 RabbitMQ producer/consumer MVP：enqueue 后向 durable queue publish 轻量 `{delivery_id}` 引用；`DeliveryRuntime` 优先 basic_get broker 消息，回查并 reserve PostgreSQL 后发送，成功后 ack；无 broker 消息时回退 PostgreSQL/本地轮询。 | 启用 RabbitMQ 后，delivery worker 不再只依赖 1 秒轮询；RabbitMQ 分发与 PostgreSQL reserve 已形成最小闭环，并通过本机 RabbitMQ smoke 验证。 |
| 20.6.5 retry 与 dead-letter 语义 | 已完成 | 已统一 `pending/retrying/failed` 状态；可重试失败写入 `retrying` 并保留 `retry_count/next_retry_at`，到期后由 `publish_due_retries()` 重新 publish 到 RabbitMQ；超过上限或永久失败进入 `failed` 并发布轻量 DLQ 引用。 | 临时失败按退避重试；超过上限进入 failed/DLQ；控制面可看到 pending/retrying/failed、retry_ready 和 RabbitMQ/DLQ 队列深度。 |
| 20.6.6 幂等投递保护 | 已完成 | 已为 outgoing message 增加 `idempotency_key`，显式 key 优先，否则由 channel/to/text/kind/correlation_id 派生；`DeliveryQueue.enqueue()` 会软查询 pending/retrying/failed 中相同 key 并复用 delivery_id，`force_delivery=true` 可强制重发。 | 进程重启、broker 重投、人工 retry 和重复入队不易造成重复投递；必要时允许显式强制重发。 |
| 20.6.7 控制面与 Dashboard 适配 | 已完成 | 控制面 `delivery.stats` 已增加 broker、retrying、retry_ready、DLQ 队列深度；`delivery.list` 支持 pending/retrying/failed/all；Dashboard 已展示待投递/等待重试/失败、DLQ 数量，并提供“重建队列”操作入口。 | 运维面板能区分数据库积压、broker 积压、等待重试、发送失败和 DLQ。 |
| 20.6.8 Redis Streams 可选后端评估 | 已移出主线 | RabbitMQ 已作为 20.6 主 broker 方案落地；Redis Streams 不再作为分布式可靠投递升级的完成标准，仅保留为轻量部署备选增强。 | 后续如果确实需要轻量 broker，再在独立阶段评估 `GATEWAY_DELIVERY_BROKER=redis`。 |
| 20.6.9 迁移与降级策略 | 已完成 | 已新增控制面 `delivery.republish`、Dashboard“重建队列”按钮和 CLI `agent-gateway delivery-republish`；可从 PostgreSQL/本地事实状态重新 publish pending/retrying 到 broker；RabbitMQ 不可用时 `DeliveryRuntime` 回退轮询并写 `delivery.broker.failed` warning event。 | RabbitMQ 清空或重启后，可从事实状态恢复投递队列；broker 故障能在事件流中看到。 |
| 20.6.10 并发与压测验收 | 已完成 | 已增加多 runner 不重复消费测试、幂等重复投递保护测试、broker 故障 fallback 测试、RabbitMQ publish/consume/stats smoke 和全量测试回归；正式压测脚本后续可并入 20.8 统一观测与压测。 | 当前测试能证明多 worker reserve 不重复、RabbitMQ 分发可用、故障可回退；全量测试 347 passed。 |
| 20.6.11 旧库 schema drift 收口 | 已完成 | `postgres-init` 已在建表 SQL 后、建索引前追加幂等迁移，自动为旧 `delivery_entries` 表补齐 `locked_by`、`locked_at`；`locked_at` 已归类为 `DOUBLE PRECISION` 时间字段。 | 本机旧库执行 `agent-gateway postgres-init` 成功，随后 `agent-gateway postgres-check-schema` 返回 `ok: True`。 |

##### 关键设计约束

- 不把 RabbitMQ 当作唯一事实来源。RabbitMQ 负责分发、ack、重试路由和 DLQ，PostgreSQL 负责状态、查询、人工干预和恢复。
- broker message 只放轻量引用，不放完整正文，避免 RabbitMQ/Redis 中长期保存敏感消息正文。
- 任何 worker 发送前必须回查并 reserve PostgreSQL 记录，避免 broker 重投或多 worker 并发造成重复发送。
- 成功发送后先更新 PostgreSQL 状态，再 ack broker；如果 ack broker 失败，下一次重投会因为 PostgreSQL 状态已完成而被安全跳过。
- retry 调度基于 PostgreSQL `next_retry_at`，broker 只负责到期消息的再次分发。
- 本地文件队列继续作为 fallback/audit，但多实例 HA 只在 PostgreSQL + broker 开启时承诺。

##### 20.6.1 当前投递链路审计结果

当前链路：

```text
GatewayDispatcher.deliver_reply / deliver_text
        ↓
DeliveryQueue.enqueue()
        ↓
PostgreSQL delivery_entries pending + 本地 JSON 文件 fallback/audit
        ↓
DeliveryRuntime.flush_once()
        ↓
DeliveryRunner.run_once()
        ↓
DeliveryQueue.pending_entries() 全量扫描
        ↓
channel.send()
        ↓
ack / fail / move_to_failed
```

现有写入点：

- 普通回复通过 `GatewayDispatcher.deliver_reply()` 入队，写入 `delivery.enqueued` 事件。
- 主动任务、Cron、Heartbeat、告警等通过 `GatewayDispatcher.deliver_text()` 入队，写入 `delivery.enqueued` 事件。
- `DeliveryQueue.enqueue()` 负责生成 `delivery_id`，优先写 PostgreSQL `delivery_entries`，再写本地 pending JSON 文件作为 fallback/audit。

现有消费点：

- `DeliveryRuntime` 后台每秒调用 `flush_once()`，内部通过 `DeliveryRunner.run_once()` 扫描 pending 队列。
- `DeliveryRunner.run_once()` 当前使用 `DeliveryQueue.pending_entries()` 全量读取 pending 记录，并按 `next_retry_at` 判断是否可重试。
- 发送成功后调用 `DeliveryQueue.ack()` 删除主存储记录和本地 pending 文件。
- 发送失败但未超过上限时调用 `DeliveryQueue.fail()` 增加 `retry_count` 并设置 `next_retry_at`。
- 超过重试上限或永久失败时调用 `DeliveryQueue.move_to_failed()`，进入 failed 队列等待人工处理。

控制面与 Dashboard 依赖：

- `delivery.stats` 依赖 `DeliveryQueue.pending_entries()`、`failed_entries()`、`retry_ready` 和 `oldest_pending_at`。
- `delivery.retry` 依赖 `DeliveryQueue.retry_now()`，需要支持 pending/failed 记录立即重试。
- `delivery.discard` 依赖 `DeliveryQueue.discard()`，需要支持 pending/failed/any 删除。
- `delivery.flush` 依赖 `DeliveryRuntime.flush_once()` 和 `pending_count()`，RabbitMQ 化后仍应保留为“触发投递推进”的兼容入口。
- Dashboard 依赖上述控制面接口展示积压、失败、重试和人工丢弃操作。

PostgreSQL 状态现状：

- `delivery_entries` 已作为可靠投递主存储，可按 `state=pending|failed` 查询。
- 当前 `DeliveryQueue` 只区分 pending/failed，没有正式 running/locked/retrying/dead-letter 状态。
- 当前 PostgreSQL 读路径按 `enqueued_at ASC` 返回队列记录，适合展示和单机扫描，但不适合作为多 worker 并发消费依据。

主要风险：

- `DeliveryRunner.run_once()` 当前是全量扫描 pending，没有 reserve/lock；多个 delivery worker 同时运行时可能重复发送同一条消息。
- `ack()` 当前是删除记录语义；RabbitMQ 化后需要明确“已发送记录是否删除、归档或保留事件”的策略。
- `fail()` 当前直接回写 pending 并依赖下一轮轮询；RabbitMQ 化后需要补“到期 retry 重新 publish”的调度机制。
- `flush_delivery()` 当前语义是手动轮询发送；RabbitMQ 化后不能删除该接口，应改为触发 broker 唤醒/重建，并在 broker 不可用时走本地 fallback flush。
- RabbitMQ message 不能承载完整正文，只能承载 `delivery_id` 等轻量引用，避免敏感消息正文进入 broker 长期留存。

后续实现边界：

- 20.6.2 需要先拆出状态存储与 broker 分发边界，避免 RabbitMQ 代码直接侵入 `GatewayDispatcher` 和控制面。
- 20.6.3 必须先实现 PostgreSQL 原子 reserve，否则 20.6.4 即使接入 RabbitMQ，也无法证明多 worker 不重复发送。
- `DeliveryQueue` 对外兼容接口至少要保留 `enqueue()`、`pending_entries()`、`failed_entries()`、`retry_now()`、`discard()`、`ack()`、`fail()`、`move_to_failed()`。
- 新增 broker 后，Dashboard 和控制面应继续从 PostgreSQL 查询事实状态，RabbitMQ 只提供 queue depth、consumer、DLQ 等运行指标。

##### 20.6.2 Delivery backend 抽象结果

已完成内容：

- 新增 `DeliveryQueueBackend` 协议，描述可靠投递事实状态存储最小接口：`list()`、`get()`、`upsert()`、`delete()`。
- 新增 `DeliveryBroker` 协议，描述 broker 分发层接口：`publish()`、`ack()`、`retry()`、`dead_letter()`、`discard()`、`stats()`。
- 新增 `NoopDeliveryBroker`，作为默认 broker，保持未启用 RabbitMQ 时的本地单机行为不变。
- `DeliveryQueue` 保留 `read_backend` / `write_backend` 兼容字段，现有 PostgreSQL 和测试用内存 backend 不需要大改。
- `DeliveryQueue.enqueue()` 在事实状态落盘后调用 `broker.publish()`；broker 失败不会影响 PostgreSQL/本地文件队列落盘。
- `DeliveryQueue.ack()`、`fail()`、`move_to_failed()`、`retry_now()`、`discard()` 分别预留 `broker.ack()`、`retry()`、`dead_letter()`、`publish()`、`discard()` 钩子。
- 控制面 `delivery.stats` 已增加 `broker` 摘要字段，当前默认返回 `backend=none`、`enabled=false`。

兼容性结论：

- 当前没有接入真实 RabbitMQ 网络调用，运行行为仍是 PostgreSQL/本地文件队列 + `DeliveryRuntime` 轮询。
- `GatewayDispatcher` 不感知 broker，仍只调用 `DeliveryQueue.enqueue()`。
- 控制面和 Dashboard 仍从 `DeliveryQueue.pending_entries()` / `failed_entries()` 获取事实状态。
- 已通过 `tests/test_delivery_runtime.py` 和投递控制面相关测试，证明 broker 抽象未破坏现有投递、重试、失败和人工处理路径。

后续实现边界：

- 20.6.3 应在 `DeliveryQueueBackend` / PostgreSQL 写仓储侧补 `reserve_delivery()`，而不是让 RabbitMQ 直接决定一条消息是否可发送。
- 20.6.4 的 RabbitMQ message 只需要携带 `delivery_id` 等轻量引用，消费时必须回查并 reserve PostgreSQL 事实记录。

##### 20.6.3 PostgreSQL 原子预占结果

已完成内容：

- `delivery_entries` schema 增加 `locked_by`、`locked_at` 字段，并增加 `idx_delivery_entries_locked_by_locked_at` 索引。
- `PostgresWriteRepository._normalize_delivery_entry()` 已补齐 `locked_by`、`locked_at` 归一化，保证写入结构和 schema 一致。
- 新增 `PostgresWriteRepository.reserve_delivery(worker_id, now)`，通过 `UPDATE ... FROM (SELECT ... FOR UPDATE SKIP LOCKED LIMIT 1)` 原子选择并标记一条可发送记录。
- `reserve_delivery()` 只抢占 `state in ('pending', 'retrying')` 且 `next_retry_at <= now` 的记录，抢占后设置 `state='running'`、`locked_by`、`locked_at` 和 `updated_at`。
- `DeliveryQueue.reserve(worker_id, now)` 优先调用主存储 `reserve_delivery()`；主存储不可用或未实现时，回退本地 pending 扫描。
- `DeliveryRunner.run_once()` 已从全量 `pending_entries()` 扫描改为循环调用 `DeliveryQueue.reserve()`，直到没有可发送消息。

兼容性结论：

- PostgreSQL backend 可用时，多 delivery worker 通过 `FOR UPDATE SKIP LOCKED` 避免重复抢占同一条消息。
- 未启用 PostgreSQL 或数据库不可用时，仍保持原本本地文件队列扫描语义，不影响单机运行。
- 当前尚未接入 RabbitMQ；20.6.3 只解决“谁有权发送这条 delivery”的事实状态正确性。

验证：

- `python -m compileall agent_gateway tests` 通过。
- `pytest tests/test_delivery_runtime.py tests/test_postgres_state.py tests/test_control_plane.py::test_control_plane_manages_delivery_queue tests/test_gateway_server.py::test_gateway_server_exposes_delivery_control_methods -q` 通过，35 passed。

注意事项：

- 旧 PostgreSQL 表可通过 `agent-gateway postgres-init` 幂等补齐 `delivery_entries.locked_by/locked_at`，补列后再运行 `agent-gateway postgres-check-schema` 应返回 `ok: True`。
- RabbitMQ 消费者在 20.6.4 中必须仍然回查并 reserve PostgreSQL，不能只凭 RabbitMQ message 直接发送。

##### 20.6.4 RabbitMQ broker MVP 结果

已完成内容：

- `pyproject.toml` 增加 `pika` 依赖，作为同步 AMQP 客户端。
- `GatewaySettings` 增加 RabbitMQ 配置：`GATEWAY_DELIVERY_BROKER`、`GATEWAY_RABBITMQ_URL`、exchange、queue、DLX、DLQ 和连接超时。
- 新增 `RabbitMQDeliveryBroker`，负责声明 durable exchange、durable queue、dead-letter exchange、dead-letter queue。
- `RabbitMQDeliveryBroker.publish()` 只发布轻量引用：`delivery_id`、`channel`、`account_id`、`correlation_id`、`published_at`，不发布 outbound 正文。
- `RabbitMQDeliveryBroker.consume_once()` 使用 `basic_get(auto_ack=False)` 消费单条消息，handler 成功后 `basic_ack`，handler 失败后 `basic_nack(requeue=True)`。
- `DeliveryRuntime.flush_once()` 已改为优先消费一条 broker 消息；broker 未启用、无消息或不可用时回退原有 `DeliveryRunner.run_once()` 轮询。
- broker 消息处理时，`DeliveryRuntime` 只从 RabbitMQ 读取 `delivery_id`，随后调用 `DeliveryQueue.reserve(worker_id, delivery_id=...)` 回查并原子预占 PostgreSQL 事实记录，reserve 不到则 ack 跳过旧消息。
- `build_application()` 在 `GATEWAY_DELIVERY_BROKER=rabbitmq` 时装配 `RabbitMQDeliveryBroker`；默认仍是 no-op broker。

验证：

- `pytest tests/test_rabbitmq_broker.py tests/test_config_loader.py tests/test_delivery_runtime.py tests/test_postgres_state.py -q` 通过，45 passed。
- `python -m compileall agent_gateway tests` 通过。
- 本机 Docker RabbitMQ 使用 `amqp://admin:admin123@127.0.0.1:5672/` 完成真实 publish/consume/ack smoke；管理 API 最终确认 `agent_gateway.delivery.outbound` 队列消息数为 0。

当前边界：

- 20.6.4 是 RabbitMQ 最小闭环，不包含完整延迟重试调度、DLQ 人工恢复和队列重建命令。
- `basic_get` 适合当前 MVP 和低吞吐场景；后续如果要提升吞吐，可在 20.6.10 或生产化阶段改为 `basic_consume` 长连接 worker。
- RabbitMQ 只负责分发和唤醒；重复发送保护仍依赖 PostgreSQL `reserve_delivery()`。

##### 20.6.5 retry 与 dead-letter 语义结果

已完成内容：

- `DeliveryQueue.fail()` 现在把可重试失败写为 `state='retrying'`，同时更新 `retry_count`、`last_error` 和 `next_retry_at`。
- 新增 `DeliveryQueue.retrying_entries()` 和 `get_retrying()`，用于控制面统计、手动 retry 和到期重发。
- 新增 `DeliveryQueue.publish_due_retries()`，把到期 `retrying` 记录改回 `pending` 并重新 publish 到 RabbitMQ。
- `DeliveryRuntime.flush_once()` 在 broker 空闲时会先调用 `publish_due_retries()`，再尝试消费 broker；如果仍没有 broker 消息，则回退本地轮询。
- `DeliveryQueue.move_to_failed()` 保持 failed 事实状态，并调用 `broker.dead_letter()` 发布轻量 DLQ 引用。
- `delivery.stats` 已增加 `retrying`、`oldest_retrying_at`，`retry_ready` 现在基于 retrying 队列计算。
- `RabbitMQDeliveryBroker.stats()` 已通过 RabbitMQ passive queue declare 暴露 `messages`、`consumers`、`dead_letter_messages`、`dead_letter_consumers`。
- `retry_now()` 支持 pending、retrying、failed 三类记录，人工重试会立即重新 publish。

验证：

- `pytest tests/test_delivery_runtime.py tests/test_rabbitmq_broker.py tests/test_control_plane.py::test_control_plane_manages_delivery_queue -q` 通过，20 passed。
- `pytest tests/test_postgres_state.py tests/test_config_loader.py -q` 通过，29 passed。
- 本机 RabbitMQ stats smoke 通过，能读取 outbound queue 和 dead-letter queue 深度。

当前边界：

- DLQ 中仍只保存轻量引用，完整错误和正文以 PostgreSQL `delivery_entries` / runtime events 为准。
- 延迟重试由 `DeliveryRuntime.flush_once()` 主动扫描到期 retrying 并重新 publish，不依赖 RabbitMQ delayed message 插件。
- 后续 20.6.7 需要把这些新字段在 Dashboard 上更清晰展示。

##### 20.6.6 幂等投递保护结果

已完成内容：

- `DeliveryQueue.enqueue()` 现在会为每条出站消息写入 `metadata.idempotency_key`。
- 上游显式提供 `idempotency_key` 时优先使用；未提供时由 `channel`、`to`、`text`、`kind`、`correlation_id` 派生 SHA-256。
- 入队前会在 pending、retrying、failed 三类未完成记录中软查询相同 `idempotency_key`；命中时复用原 `delivery_id` 并重新 publish broker 引用。
- 支持 `metadata.force_delivery=true` 跳过去重，显式强制重发。
- RabbitMQ 轻量消息和 headers 已携带 `idempotency_key`，便于排障和后续消费者侧观测。

验证：

- 覆盖重复入队复用、强制重发、派生稳定 key、RabbitMQ lightweight payload 携带 key。
- `pytest tests/test_delivery_runtime.py tests/test_rabbitmq_broker.py -q` 通过，22 passed。
- `pytest tests/test_control_plane.py::test_control_plane_manages_delivery_queue tests/test_postgres_state.py tests/test_config_loader.py -q` 通过，30 passed。

当前边界：

- 当前采用软去重查询，暂未给 PostgreSQL 增加唯一约束，避免误伤允许强制重发的场景。
- 如果未来需要强一致幂等，可增加 `idempotency_key` 独立列和部分唯一索引，但需要先设计 force delivery 的绕过策略。

##### 20.6.7/20.6.9 控制面、Dashboard 与恢复入口阶段结果

已完成内容：

- `delivery.stats` 增加 `retrying`、`oldest_retrying_at`、`broker`，其中 RabbitMQ broker stats 包含 outbound queue 和 DLQ 深度。
- `delivery.list` 支持 `state=retrying` 和 `state=all`。
- 新增控制面方法 `republish_deliveries()`，可从事实状态重新发布 pending/retrying 引用到 broker。
- WebSocket JSON-RPC 新增 `delivery.republish`。
- `DeliveryQueue.republish_pending()` 与 `publish_due_retries()` 可作为 RabbitMQ 队列清空后的恢复基础。
- Dashboard 投递筛选新增“等待重试”，顶部指标显示“待投递 / 等待重试 / 失败”，问题摘要显示 DLQ 数量。
- Dashboard 投递面板新增“重建队列”按钮，可调用 `delivery.republish`。
- CLI 新增 `agent-gateway delivery-republish`，可在不打开 Dashboard 的情况下重建 RabbitMQ 投递引用。
- RabbitMQ consume 异常时，`DeliveryRuntime` 会写入 `delivery.broker.failed` warning event，并回退 PostgreSQL/本地轮询。

验证：

- `pytest tests/test_monitoring_static.py tests/test_control_plane.py::test_control_plane_manages_delivery_queue tests/test_gateway_server.py::test_gateway_server_exposes_delivery_control_methods -q` 通过，14 passed。
- `pytest tests/test_delivery_runtime.py tests/test_rabbitmq_broker.py tests/test_postgres_state.py tests/test_config_loader.py -q` 通过，51 passed。
- `pytest tests/test_app_cli.py::test_delivery_republish_cli_republishes_without_serving -q` 通过。
- `python -m compileall agent_gateway tests` 通过。

待完成内容：

- 20.6.10 还需要补并发验收测试和完整回归。

##### 20.6.10 并发与验收结果

已完成内容：

- 新增测试覆盖两个 `DeliveryRuntime` / `DeliveryRunner` 共享同一 PostgreSQL-like backend 时不会重复发送同一条 delivery。
- 新增幂等重复入队测试，覆盖显式 `idempotency_key`、派生 key 和 `force_delivery` 强制重发。
- 新增 broker consume 故障 fallback 测试，确认 RabbitMQ 异常时仍会回退 PostgreSQL/本地轮询，并记录 warning event。
- 新增 RabbitMQ broker publish、consume、nack、DLQ、stats 单元测试。
- 使用本机 Docker RabbitMQ 完成真实 publish/consume/ack/stats smoke。

验证：

- `pytest tests -q` 通过，347 passed。
- `python -m compileall agent_gateway tests` 通过。

阶段结论：

- 20.6 已形成“PostgreSQL 事实状态 + RabbitMQ 分发 + Dashboard/控制面/CLI 恢复”的可运行闭环。
- Redis Streams 备选后端不再阻塞 20.6；当前主方案以 RabbitMQ 为准，Redis Streams 如需实现应单独进入后续增强阶段。
- 正式高并发压测脚本和 P95/吞吐报告建议并入 Phase 20.8 统一观测与压测，而不是继续塞在 20.6。

##### 20.6.11 旧库 schema drift 收口结果

已完成内容：

- `build_postgres_schema_sql()` 现在同时生成建表 SQL 和保守的旧库迁移 SQL。
- 迁移 SQL 会在建表后、建索引前执行，避免旧表缺失新列时先创建 `locked_by/locked_at` 索引导致初始化失败。
- 已为旧 `delivery_entries` 表追加幂等补列：
  - `locked_by TEXT NOT NULL DEFAULT ''`
  - `locked_at DOUBLE PRECISION NOT NULL DEFAULT 0`
- `_column_type()` 已将 `locked_at` 归类为浮点时间字段，避免全新建库时被错误创建为 `TEXT`。
- 已补测试覆盖迁移 SQL 内容和执行顺序。

验证：

- `pytest tests/test_postgres_state.py -q` 通过，24 passed。
- `python -m compileall agent_gateway/runtime/state/postgres.py tests/test_postgres_state.py` 通过。
- 本机旧库执行 `agent-gateway postgres-init` 成功。
- 本机旧库执行 `agent-gateway postgres-check-schema` 返回 `{'ok': True, 'missing_tables': [], 'missing_columns': {}, 'type_mismatches': {}}`。

##### 建议实施顺序

1. 先做 20.6.1 和 20.6.2，冻结接口边界，避免 RabbitMQ 代码直接侵入业务链路。
2. 再做 20.6.3，用 PostgreSQL 原子 reserve 解决“多 worker 是否会重复发送”的核心正确性问题。
3. 然后做 20.6.4 和 20.6.5，用 RabbitMQ 打通 durable queue、manual ack、retry 和 failed/DLQ。
4. 接着做 20.6.6 和 20.6.7，把幂等、状态视图和运维面板补齐。
5. 最后完成恢复命令、schema drift 收口和验收；Redis Streams 仅作为后续可选增强，不阻塞 RabbitMQ 主方案。

## 9. 推荐执行顺序

建议接下来按以下顺序推进：

1. Phase 12：Dashboard 鉴权与安全边界。
2. Phase 13 增强项：模型调用事件与错误分类。
3. Phase 16：Agent 权限预览与配置治理。
4. Phase 17：会话与记忆治理。
5. Phase 18：多 Agent 协作与任务实例状态机。
6. Phase 20.6：可靠投递队列升级。
7. Phase 19 / Phase 20.7-20.8：生产部署、统一观测和压测。

排序依据：

- Phase 15 已基本解决入站拥堵风险，下一步应优先补 Dashboard 和控制面鉴权。
- 模型调用事件和错误分类能继续提升排障效率。
- 配置、权限、会话和记忆治理决定长期运行质量。
- Phase 18 的任务实例状态机是 Phase 20 后台任务队列化的前置基础。
- Redis 和任务队列可以先于完整生产部署落地，因为它们直接解决多实例去重、长任务阻塞和队列削峰问题。
- PostgreSQL、可靠队列、统一观测和压测放在后半段，避免数据库和队列同时改造造成排障复杂度过高。
