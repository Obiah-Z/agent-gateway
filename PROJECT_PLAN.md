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
| Phase 20 | 进行中 | 高并发、高性能、高可用架构升级；已完成运行角色拆分、Redis 最小协调和后台任务队列基础闭环。 |

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

状态：待实现。

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

状态：待实现。

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

状态：待实现。

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
| RabbitMQ | 中高 | 入站消息队列、后台任务队列、可靠投递队列、死信队列、延迟重试 | 对可靠投递、ack、重试、死信和消费者扩容支持成熟，适合把 ChannelRuntime、Agent worker、Delivery worker 解耦。 | Redis Streams 更轻量，适合先做 MVP；但 RabbitMQ 在投递语义和运维可解释性上更强。 |
| Celery / Dramatiq | 中 | Cron、Heartbeat、GitHub 分析、服务器巡检、长任务 Skill 的后台执行 | 可以快速把长任务从入站 lane 中剥离，支持 worker 池、重试、任务状态和定时调度；Celery 功能更全，Dramatiq 更轻量。 | 如果希望保持完全自研，可基于 RabbitMQ/Redis Streams 写 worker，但开发成本更高。 |
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
| 20.3 后台任务队列 | 已完成 | 已新增 `TaskInstance`、本地 `LocalTaskStore`、本地 `LocalTaskQueue` 和 `TaskWorkerRuntime`；Cron/Heartbeat 自动调度已进入任务链路；明确命令式长任务可配置化转入后台执行；控制面和 Dashboard 已支持任务查看、取消和重试。 | 用户消息可快速返回“已接收/处理中”，长任务由 worker 后台完成，并可通过控制面和 Dashboard 追踪和干预。 |
| 20.4 PostgreSQL 状态外置 | 提升长期查询和治理能力 | 设计 sessions、tasks、runtime_events、errors、metrics、memory_entries、config_audits 表；保留 JSONL 作为审计备份或降级路径。 | Dashboard 主要列表可从数据库查询，支持分页、筛选和归档。 |
| 20.5 可靠投递队列升级 | 支持多 worker 投递和死信处理 | 将本地 delivery queue 抽象为接口，新增 Redis Streams 或 RabbitMQ backend；支持 ack、retry、dead-letter、idempotency key。 | delivery-worker 可水平扩展，失败消息不会丢失，可在 Dashboard 中重试或丢弃。 |
| 20.6 生产部署编排 | 形成可复现部署形态 | 增加 Dockerfile、Compose、数据卷、反向代理、HTTPS、启动检查和备份恢复说明。 | 新机器按文档可启动完整依赖和 gateway 服务。 |
| 20.7 统一观测与压测 | 用数据验证性能提升 | 增加 Prometheus metrics endpoint、压测脚本、容量基线、P95 延迟、队列积压、worker 吞吐和错误率指标。 | 能用压测报告说明系统在不同并发下的瓶颈和容量。 |

#### 开展顺序建议

1. 先做 20.1：把进程边界和队列边界设计清楚，避免一开始就把代码改散。
2. 再做 20.2：Redis 的投入最小，但能立即解决多实例去重、Cron 幂等和全局限流。
3. 接着做 20.3：把 Phase 15 遗留的长任务后台化、低优先级任务调度和 per-agent 并发治理接到 task instance。
4. 然后做 20.4：PostgreSQL 接管长期状态，支撑 Dashboard 查询、审计、归档和治理。
5. 再做 20.5：当任务和状态稳定后，再升级可靠投递队列，避免同时改动执行链路和出站链路。
6. 最后做 20.6 和 20.7：补齐部署、观测和压测，用指标验证高可用和高性能目标是否真实达成。

#### 完成标准

- 支持至少两个 gateway 实例同时运行，入站事件不重复处理。
- 支持多个 agent worker 并发消费任务，长任务不阻塞实时消息入口。
- 支持 delivery worker 水平扩展，投递失败可重试、可死信、可人工处理。
- 关键状态不依赖单机 JSONL，Dashboard 可以分页查询任务、事件、错误和记忆。
- Redis、PostgreSQL、队列和反向代理都有健康检查、配置说明和降级策略。
- 有基础压测结果，能说明当前机器配置下的吞吐、延迟和瓶颈。

#### 当前实现说明

- `GATEWAY_RUNTIME_ROLES=all` 仍是默认值，保持原来的单进程全量启动体验。
- `api` 角色启动入站通道、飞书 Webhook 和长连接消费。
- `delivery` 角色启动出站可靠投递后台。
- `scheduler` 角色启动 Heartbeat 和 Cron。
- `dashboard` 角色启动 Dashboard，并自动包含控制面和观测后台。
- `control` 角色只启动 WebSocket JSON-RPC 控制面。
- `observability` 角色启动 metrics 和 alerts 后台采集。
- `worker` 角色已接入本地任务队列，可消费 Cron task 和明确命令式长任务；后续可替换为 Redis/RabbitMQ/PostgreSQL backend。
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

#### Phase 20.4 PostgreSQL 状态外置子阶段

| 子阶段 | 状态 | 主要内容 |
| --- | --- | --- |
| 20.4.1 状态边界与表设计 | 已完成 | 明确 sessions、tasks、runtime_events、errors、metrics、memory_entries、config_audits 的最小字段、主键、时间列、索引和保留策略；保留 JSONL 作为回退和审计。 |
| 20.4.2 仓储接口草案 | 已完成 | 定义状态仓储抽象，先不替换业务写入，只约束 list/get/append/upsert/query/delete 的统一接口。 |
| 20.4.3 只读仓储统一入口 | 进行中 | 先把 Dashboard / 控制面读取统一接到 `StateReadRepository`，本地 JSONL / 内存存储先作为默认后端；后续切换 PostgreSQL 时不改上层调用。 |
| 20.4.4 PostgreSQL 只读后端 | 进行中 | 为 sessions、tasks、runtime_events、errors、metrics、memory_entries、config_audits 提供 PostgreSQL 只读实现，Dashboard 按配置切换。 |
| 20.4.4.1 仓储查询映射 | 已完成 | 补齐各表主键、排序列、过滤字段和只读查询骨架，确保 read path 的 SQL 形态稳定。 |
| 20.4.4.2 后端切换开关 | 已完成 | `GATEWAY_POSTGRES_ENABLED` 开关可切到 PostgreSQL 只读仓库，默认仍返回本地仓库。 |
| 20.4.4.3 只读结果对齐 | 进行中 | 把 PostgreSQL 返回结构进一步对齐本地仓库，减少 control plane / Dashboard 适配成本。 |
| 20.4.4.3.1 错误视图对齐 | 已完成 | PostgreSQL `errors` 输出对齐 `RuntimeEventStore.recent_errors` 的事件形态，避免控制面重复适配。 |
| 20.4.4.3.2 记忆视图对齐 | 已完成 | PostgreSQL `memory_entries` 输出对齐 `MemoryStore.recent_entries` 的摘要形态，保持 Dashboard 视图一致。 |
| 20.4.5 双写与迁移脚手架 | 待实现 | 逐步把会话、任务、事件和记忆接入数据库主存储，保留 JSONL 双写和回放能力。 |

## 9. 推荐执行顺序

建议接下来按以下顺序推进：

1. Phase 12：Dashboard 鉴权与安全边界。
2. Phase 13 增强项：模型调用事件与错误分类。
3. Phase 16：Agent 权限预览与配置治理。
4. Phase 17：会话与记忆治理。
5. Phase 18：多 Agent 协作与任务实例状态机。
6. Phase 20.4.1-20.4.2：先完成状态边界、表设计和仓储接口草案。
7. Phase 19：生产部署形态。
8. Phase 20.4-20.7：PostgreSQL 状态外置、可靠队列升级、统一观测和压测。

排序依据：

- Phase 15 已基本解决入站拥堵风险，下一步应优先补 Dashboard 和控制面鉴权。
- 模型调用事件和错误分类能继续提升排障效率。
- 配置、权限、会话和记忆治理决定长期运行质量。
- Phase 18 的任务实例状态机是 Phase 20 后台任务队列化的前置基础。
- Redis 和任务队列可以先于完整生产部署落地，因为它们直接解决多实例去重、长任务阻塞和队列削峰问题。
- PostgreSQL、可靠队列、统一观测和压测放在后半段，避免数据库和队列同时改造造成排障复杂度过高。
