# AI Agent Gateway 项目计划

## 1. 项目定位

`gateway/` 是一个基于 Python 的 AI Agent Gateway 智能体网关系统，目标是把早期代码片段持续演进为可运行、可维护、可扩展的智能体运行框架。

系统面向以下核心场景：

- 多轮对话与 Agent Loop。
- Tool Calling 与外部执行能力。
- 多通道接入与消息路由。
- 会话持久化、上下文管理与记忆注入。
- Heartbeat、Cron、新闻简报等主动任务。
- 可靠投递、失败重试、并发控制与弹性恢复。
- 飞书接入、本地控制面、Dashboard 运维面板。
- 运行事件流、指标快照、趋势与告警。

当前项目应继续坚持“本地优先、结构清晰、可逐步生产化”的路线。短期内不急于引入数据库、分布式锁或复杂部署系统，优先补齐安全边界、配置治理、数据治理和任务状态管理。

## 2. 当前架构

核心目录职责如下：

| 目录 | 职责 |
| --- | --- |
| `agent_gateway/core/` | 领域层，定义 Agent、消息模型、路由和 ID 规范。 |
| `agent_gateway/application/` | 应用层，承载 Agent Loop、Dispatcher、主动任务、控制面、投递运行时、指标和告警运行时。 |
| `agent_gateway/interfaces/` | 外部接口层，承载 WebSocket 控制面、飞书 Webhook 和飞书长连接。 |
| `agent_gateway/channels/` | 通道适配层，封装 CLI、Telegram、Feishu 等消息通道。 |
| `agent_gateway/delivery/` | 可靠投递队列，负责消息预写、重试和失败落盘。 |
| `agent_gateway/intelligence/` | Prompt、记忆、技能和 Agent 局部配置注入。 |
| `agent_gateway/monitoring/` | Dashboard 静态页面和本地运维视图。 |
| `agent_gateway/observability/` | 运行事件、错误、指标和告警存储模型。 |
| `agent_gateway/news/` | AI Agent 新闻采集、去重和摘要生成。 |
| `agent_gateway/sessions/` | JSONL 会话存储和上下文保护。 |
| `agent_gateway/tools/` | 工具注册表和内置工具。 |
| `workspace/` | Prompt、记忆、技能、Cron 和 Agent 局部工作区。 |
| `config/` | agents、bindings、channels、profiles 等静态配置。 |
| `data/` | 会话、投递队列、事件、指标、告警等运行期数据。 |

说明：顶层兼容层 `agent_gateway/agents.py`、`router.py`、`models.py`、`ids.py` 已移除。新代码应直接从 `agent_gateway.core` 或具体子模块导入。

## 3. 当前运行入口

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

进入下一阶段前建议执行：

```bash
cd ~/Desktop/claw0/gateway
./.venv/bin/python -m compileall agent_gateway tests
./.venv/bin/python -m pytest tests -q
```

当前最近验证基线：`168 passed`。

## 4. 已完成能力

| 能力方向 | 状态 | 说明 |
| --- | --- | --- |
| Agent Loop | 已完成 | 支持 Anthropic Messages API 兼容调用、`stop_reason` 驱动的多轮执行和 tool calling。 |
| Tool Calling | 已完成 | 基于 dispatch table 管理 bash、文件读写、记忆检索、Web Search 等工具。 |
| 会话持久化 | 已完成 | 基于 JSONL 保存 transcript，支持历史重放和上下文保护。 |
| 路由系统 | 已完成 | 基于 `bindings.json` 将 channel、account、peer、session 路由到指定 Agent。 |
| 配置控制面 | 已完成 | 支持 agents、bindings、channels、profiles 的查看、修改、保存和 reload。 |
| 记忆与技能 | 已完成 | 支持 `MEMORY.md`、daily memory、`SKILL.md` 扫描和 Agent 局部 prompt 覆盖。 |
| 主动任务 | 已完成 | Heartbeat、Cron 和 AI Agent 每日简报均接入统一执行链。 |
| 可靠投递 | 已完成 | 普通回复、heartbeat、cron 输出先入本地队列，再由后台 runtime 发送、重试和失败落盘。 |
| 并发控制 | 已完成 | 支持命名 lane，保证同一会话或任务维度的串行执行。 |
| 飞书 Webhook | 已完成 | 支持 challenge、加密事件、签名校验、时间窗校验、事件去重和审计日志。 |
| 飞书长连接 | 已完成 | 支持通过 `lark-cli event consume` 消费事件，适合本地开发和单机部署。 |
| 飞书发送 | 已完成 | 支持 SDK/HTTP 发送和 `lark-cli` 发送模式。 |
| 飞书扫码接入 | 已完成 | 支持 `/onboarding/feishu` 页面、绑定码、机器人会话入口和自动创建个人 Agent。 |
| AI Agent 简报 | 已完成 | 支持 RSS、官网 HTML、GitHub Releases、arXiv 等来源采集和每日摘要推送。 |
| Dashboard | 已完成 | 支持健康检查、运行态快照、投递队列、Cron、飞书接入、事件、错误、指标和告警查看。 |
| 运行事件流 | 已完成 | 支持 runtime event JSONL、`events.tail`、`errors.recent` 和 Dashboard 最近链路视图。 |
| 指标与告警 | 已完成 | 支持 metrics snapshot、趋势视图、告警规则、告警历史和飞书告警投递。 |
| 架构分层 | 已完成 | 已移除兼容层，主实现归入 `core/application/interfaces` 等分层目录。 |

## 5. 已完成阶段回顾

### Phase 1：基础工程骨架

- 建立 `agent_gateway/` Python 包结构。
- 建立 `pyproject.toml` 和 `agent-gateway` 命令入口。
- 接入 Anthropic Messages API 兼容调用。
- 建立基础 Agent Loop 和 tool calling 闭环。

### Phase 2：会话、上下文与配置

- 将会话存储改为 JSONL transcript。
- 实现历史重放和上下文保护。
- 引入 `.env`、`config/*.json`、`workspace/` 三层配置与运行资产。
- 建立 profiles、agents、bindings、channels 配置模型。

### Phase 3：多通道、路由与控制面

- 完成 CLI、Telegram、Feishu 通道抽象。
- 完成统一 `InboundMessage` 和 dispatcher。
- 完成 `bindings.json` 驱动的消息路由。
- 接入 WebSocket JSON-RPC 控制面。

### Phase 4：记忆、技能与 Agent Manifest

- 接入 `MEMORY.md` 和 daily memory。
- 接入 `workspace/skills/*/SKILL.md`。
- 支持 `workspace/agents/<agent_id>/` 局部 prompt 覆盖。
- 支持 agent 级 tool policy、memory policy、prompt policy 和 capability tags。

### Phase 5：主动任务与可靠投递

- Heartbeat 和 Cron 接入统一执行链。
- 所有出站消息改为先写入 delivery queue。
- 后台 `DeliveryRuntime` 负责实际发送、重试和失败落盘。
- 控制面支持 delivery stats、list、retry、discard、flush。

### Phase 6：弹性、并发与稳定性

- 引入 resilience runner，支持 profile 轮换、失败分类和 overflow 处理骨架。
- 引入命名 lane，避免同一会话并发踩踏。
- CLI 交互改为等待当前回复处理完成后再放开下一次输入。

### Phase 7：飞书生产化接入

- 完成飞书 Webhook challenge、解密、签名校验、时间窗校验。
- 完成事件去重和审计日志。
- 支持多飞书账号路由。
- 支持飞书卡片渲染、文本分页和投递状态记录。
- 支持 `lark-cli` 发送模式。
- 支持飞书长连接模式，降低本地开发对公网回调地址的依赖。

### Phase 8：Dashboard 与运行态状态

- 新增本地 Dashboard 静态页面。
- 支持健康检查、运行态状态、投递队列、Cron 任务和飞书接入状态查看。
- 支持在 Dashboard 中执行投递 retry、discard、flush 和 Cron 手动触发。
- Dashboard 默认仅监听 `127.0.0.1`，避免未鉴权情况下暴露公网。

### Phase 9：飞书扫码接入与用户 Onboarding

- 新增 `/onboarding/feishu` 页面。
- 支持短期绑定码。
- 支持机器人打开链接扫码进入会话。
- 用户首次私聊机器人后，可自动创建个人 Agent 和路由绑定。
- 支持群聊自动接入的基础配置。

### Phase 10：AI Agent 每日简报

- 新增 `news/` 模块。
- 支持 RSS、HTML、GitHub Releases、arXiv 等来源采集。
- 支持已见条目去重。
- 支持定时生成 AI Agent 相关新闻摘要并通过主动投递链路推送。
- 新增 `workspace/agent-news-sources.json` 作为新闻源配置。

### Phase 11：架构分层重构

- 将领域模型迁移到 `core/`。
- 将应用编排迁移到 `application/`。
- 将外部接入迁移到 `interfaces/`。
- 移除旧 `runtime/` 兼容层。
- 移除 `agent_gateway/agents.py`、`router.py`、`models.py`、`ids.py` 顶层兼容层。
- README 和架构文档已同步新的目录结构和运行方式。

### Phase 13：运行事件流与最近错误视图

- 新增 `observability/` 模块和 `RuntimeEventStore`。
- 定义统一 runtime event JSONL schema。
- 接入关键链路事件：
  - `inbound.received`
  - `route.resolved`
  - `agent.turn.started/completed/failed`
  - `tool.call.started/completed/failed`
  - `delivery.enqueued/sent/failed`
  - `cron.triggered/completed/failed`
  - `feishu.event.accepted/ignored/rejected/error`
- 控制面新增 `events.tail` 和 `errors.recent`。
- Dashboard 新增最近事件、最近错误和最近链路视图。
- 支持按 `correlation_id` 聚合链路。
- `events.tail` 支持 `component`、`status`、`correlation_id`、`agent_id`、`channel`、`job_id`、`delivery_id` 过滤。
- 事件文件按日期轮转，并通过 `GATEWAY_EVENTS_RETENTION_DAYS` 控制保留期。

### Phase 14：指标快照、趋势与告警

- 新增 `agent_gateway/observability/metrics.py`。
- 定义 metrics snapshot JSONL schema。
- 按日期写入 `data/metrics/metrics-YYYY-MM-DD.jsonl`。
- 新增后台 `MetricsRuntime`，采集 delivery、lane、Cron、profiles、事件错误等指标。
- 控制面新增 `metrics.snapshot`、`metrics.tail`、`metrics.summary`。
- Dashboard 新增指标趋势面板。
- 新增 `AlertRule`、`AlertState`、`AlertStore`。
- 新增 `AlertsRuntime`，支持阈值、持续时间、冷却、恢复和通知。
- 内置 delivery backlog、delivery failed、cron failures、profiles unavailable、feishu signature rejected、lane backlog 等规则。
- 告警通知复用可靠投递链路，可发送到指定飞书会话。
- 控制面新增 `alerts.active` 和 `alerts.history`。
- Dashboard 新增当前告警和告警历史视图。
- Dashboard 各列表类面板默认最多展示 6 条，多余内容折叠。

## 6. 当前主要边界

- 当前仍是单进程本地运行时，尚未引入数据库、分布式锁或多实例协调。
- Dashboard 默认无鉴权，仅适合本机访问，不应直接暴露公网。
- 配置变更可以保存和 reload，但配置审计、快照和回滚仍不完整。
- Agent 权限模型已有 tool policy 和 capability tags，但缺少最终权限预览和强校验报告。
- 会话与记忆已经可持久化，但长期运行后的归档、删除、复审和压缩治理仍不足。
- 飞书长连接依赖本机 `lark-cli` 配置和子进程消费，适合本地/单机部署；生产多实例仍建议优先 Webhook。
- 新闻简报能力已可运行，但来源质量评估、内容去重精度和摘要可解释性仍有提升空间。
- 指标与告警已建立本地闭环，但没有外部 TSDB、Prometheus 或集中日志系统。

## 7. 后续路线图

### Phase 15：ChannelRuntime Lane 化与入站背压

状态：进行中，P0 优先级，建议优先于 Dashboard 鉴权推进。

目标：

- 将当前“所有通道入站消息进入单一消费者串行处理”的模型，升级为“统一入口、按会话/Agent 分 lane 并发执行”。
- 避免飞书、CLI、Telegram、Webhook、长连接等通道互相阻塞。
- 保持同一会话内顺序一致，同时允许不同会话、不同 Agent、不同通道并发处理。

背景：

- 当前 `ChannelRuntime` 使用一个全局 `asyncio.Queue` 和一个 `_consume()` 任务。
- 这种设计简单可靠，但一条慢消息会阻塞后续所有消息。
- 典型风险包括：飞书长任务拖慢 CLI、GitHub 仓库分析阻塞普通聊天、Telegram 批量消息影响飞书响应。
- 当前 `restart()` 会先 `stop()` 再替换通道；`stop()` 直接向队列写入 `None` 退出哨兵，存在未消费消息被留在旧队列或旧通道线程继续投递后无人消费的风险。

阶段进展：

1. Phase 15.1：`ChannelRuntime.restart()` graceful drain。已完成。
   - `stop()` 改为先停止接收新消息，再关闭通道并等待旧通道线程退出。
   - 旧线程退出后等待当前入站队列 drain 完成，再投递 consumer 退出哨兵。
   - CLI `completion_event` 在正常处理、失败处理和 restart drain 场景下都会释放。
   - `ingest_external()` 在 runtime 未运行时拒绝入队，避免消息进入无人消费的旧队列。
   - 新增回归测试覆盖：restart 前已入队消息不丢、CLI completion_event 可释放、旧通道线程会被关闭并 join。
2. Phase 15.2：定义入站 lane key 规则。已完成。
   - 优先使用 `agent_id + session_key`。
   - 路由前可临时使用 `channel + account_id + peer_id`。
   - 后台任务、Cron、Heartbeat 与用户实时消息分开 lane。
   - 已新增 `build_preroute_lane_key()` 和 `build_inbound_lane_key()`，并让 `PendingInbound` 暴露路由前 lane key。
   - 已补充路由前 fallback、路由后 Agent/session 优先级和 PendingInbound lane key 测试。
3. Phase 15.3：将 `ChannelRuntime` 从单消费者改为 lane dispatcher。已完成。
   - 全局入站队列只负责接收和粗分发。
   - 每个 lane 内部保持顺序处理。
   - 不同 lane 可以并发执行。
   - 当前按 `PendingInbound.preroute_lane_key` 建立入站 lane worker。
   - 已补充测试覆盖：不同 peer 可并发处理，慢 lane 不阻塞其他 lane；同一 peer/lane 保持串行。
4. Phase 15.4：增加全局并发上限和 per-agent 并发上限。待实现。
   - 例如 `main=2`、`research=1`、`ops=1`。
   - 防止并发过高打爆模型 API 或工具执行资源。
5. Phase 15.5：增加入站背压策略。待实现。
   - 配置最大队列长度。
   - 超过阈值时对低优先级任务延迟或拒绝。
   - 实时用户消息优先于 Cron/Heartbeat。
6. Phase 15.6：增加长任务降级策略。待实现。
   - 超过阈值后先回复“已进入后台处理”。
   - 后续结果通过可靠投递链路补发。
7. Phase 15.7：增加运行指标。待实现。
   - 全局入站队列长度。
   - 每个 lane 的队列长度。
   - 最老消息等待时间。
   - 当前运行 lane 数。
   - 每个 Agent 的并发占用。
8. Phase 15.8：Dashboard 增加入站队列和 lane 视图。待实现。
9. Phase 15.9：控制面增加 `runtime.lanes` 或扩展现有 runtime status，展示当前积压和并发状态。待实现。
10. Phase 15.10：补充测试。进行中。
   - restart 时已入队消息不会丢失。
   - restart 时旧通道线程不会继续向无人消费的旧队列投递消息。
   - 同一 session 串行。
   - 不同 session 并发。
   - 慢任务不阻塞其他 lane。
   - CLI completion_event 仍能正确释放。
   - interceptor 消费消息后不进入 Agent lane。

完成标准：

- 一个长耗时飞书任务不会阻塞 CLI 或其他飞书会话。
- 控制面 reload 通道配置时，已入队消息不会因为 restart 丢失。
- 同一会话内消息顺序仍然稳定。
- Dashboard 能看到入站积压和 lane 运行状态。
- 并发上限可配置，默认保持保守，适合本地单机运行。

### Phase 12：Dashboard 鉴权与安全边界

状态：待实现，高优先级，但排在 Phase 15 之后。

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

状态：待实现，可在 Phase 12 后择机补齐。

目标：

- 把模型调用、fallback、profile 冷却、上下文压缩和错误分类纳入可观测链路。

计划项：

1. 增加 `model.call.started/completed/failed`。
2. 增加 `profile.selected/failed/cooldown`。
3. 增加 `context.compacted`。
4. 记录 profile、model、失败分类、耗时和 fallback 次数。
5. 增加错误分类器：
   - `model_auth_failed`
   - `model_rate_limited`
   - `model_timeout`
   - `tool_failed`
   - `delivery_channel_unavailable`
   - `delivery_invalid_target`
   - `feishu_signature_rejected`
   - `cron_failed`
   - `config_invalid`
6. `errors.recent` 返回错误类型、影响对象和建议操作。
7. Dashboard 最近错误面板展示分类和建议。

完成标准：

- 可以区分模型慢、模型失败、工具慢、投递失败。
- 排查 API key、base_url、限流和上下文溢出时有明确事件依据。
- 用户看到错误后能快速判断该检查模型、工具、通道、飞书还是配置。

### Phase 16：Agent 权限预览与配置治理

状态：待实现。

目标：

- 让多 Agent 配置从“能运行”升级到“可审查、可回滚、可解释”。

计划项：

1. 增加 manifest resolved preview。
2. 增加 `agents.validate` 接口。
3. 增加 Agent 最终权限报告：
   - prompt files
   - memory policy
   - enabled skills
   - allowed tools
   - denied tools
   - capability tags
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

计划项：

1. 增加 agent-to-agent handoff。
2. 增加 task instance 模型：
   - pending
   - running
   - waiting
   - retrying
   - done
   - failed
3. 为 cron、heartbeat、新闻简报和主动任务增加幂等 key。
4. 增加任务执行记录和失败恢复入口。
5. 支持任务级状态在 Dashboard 展示。

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

## 8. 推荐执行顺序

建议接下来按以下顺序推进：

1. Phase 15：ChannelRuntime Lane 化与入站背压。
2. Phase 12：Dashboard 鉴权与安全边界。
3. Phase 13 增强项：模型调用事件与错误分类。
4. Phase 16：Agent 权限预览与配置治理。
5. Phase 17：会话与记忆治理。
6. Phase 18：多 Agent 协作与任务实例状态机。
7. Phase 19：生产部署形态。

排序依据：

- 先解决 ChannelRuntime 全局串行带来的入站拥堵风险，避免多通道互相阻塞。
- 再补 Dashboard 和控制面鉴权，避免管理入口暴露风险。
- 然后补模型调用事件和错误分类，让后续排障更直接。
- 然后做配置、权限、会话和记忆治理，提升长期运行质量。
- 最后推进多 Agent 协作和生产部署，避免在治理能力不足时扩大复杂度。

## 9. 最近一个可执行任务

建议下一步实现 Phase 15 的最小闭环：

1. 先修复 `ChannelRuntime.restart()`：
   - 停止旧通道采集。
   - 等待旧线程退出。
   - drain 已入队消息。
   - drain 后再停止 consumer。
2. 增加 restart 回归测试：
   - 已入队消息在 restart 后仍会处理。
   - CLI `completion_event` 不会卡住。
   - 旧线程退出后不会继续投递到旧队列。
3. 为 `ChannelRuntime` 增加 lane key 计算函数。
4. 保留现有全局入站队列，但将消费阶段改为按 lane 投递到 lane worker。
5. 每个 lane 内保持串行处理，不同 lane 使用 `asyncio.Semaphore` 控制并发。
6. 增加默认全局并发上限，例如 4。
7. CLI 消息继续等待 `completion_event`，确保终端交互节奏不回退。
8. 增加 lane 化测试覆盖：
   - 两个不同 `peer_id` 的消息可以并发。
   - 同一个 `peer_id` 的消息保持顺序。
   - 第一条慢消息不会阻塞另一个 lane。
   - interceptor 消费消息后不进入 Agent 执行。
9. 在 runtime status 中先暴露最小 lane 状态：
   - active lane 数。
   - queued message 数。
   - running task 数。

这一阶段优先解决多通道共用单消费者导致的拥堵问题，收益直接，并且不需要先引入数据库或分布式组件。
