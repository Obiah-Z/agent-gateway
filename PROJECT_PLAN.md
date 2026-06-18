# AI Agent Gateway 项目计划

## 项目目标

将 `gateway/` 持续建设为一个可运行、可维护、可扩展的 AI Agent Gateway 智能体网关系统。

系统面向多轮对话、工具调用、多通道接入、主动任务、可靠投递、飞书接入和本地运维监控等场景，目标不是做单一聊天机器人，而是形成一套具备生产化思路的智能体运行框架。

当前项目已经从早期教学型代码片段，演进为分层清晰的工程项目：

- `core/`：领域层，定义 Agent、消息、路由和 ID 规范。
- `application/`：应用层，承载 Agent Loop、dispatcher、control plane、并发车道和主动任务编排。
- `interfaces/`：接入层，承载 WebSocket 控制面、飞书 Webhook 和飞书长连接。
- `channels/`：通道适配层，封装 CLI、Telegram、Feishu 等消息通道。
- `delivery/`：可靠投递队列。
- `intelligence/`：Prompt、记忆和技能注入。
- `monitoring/`：本地 Dashboard 和静态运维页面。
- `news/`：AI Agent 新闻采集、去重和摘要生成。
- `sessions/`：JSONL 会话存储和上下文保护。
- `tools/`：工具注册表与内置工具。

## 当前版本状态

### 已完成能力

| 方向 | 状态 | 说明 |
| --- | --- | --- |
| Agent Loop | 已完成 | 支持 Anthropic Messages API 兼容调用、`stop_reason` 驱动的多轮执行和 tool calling。 |
| Tool Calling | 已完成 | 基于 dispatch table 管理 bash、文件读写、记忆检索、Web Search 等工具。 |
| 会话持久化 | 已完成 | 基于 JSONL 保存 transcript，支持历史重放和上下文保护。 |
| 路由系统 | 已完成 | 基于 `bindings.json` 将 channel/account/peer/session 路由到指定 Agent。 |
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
| Dashboard | 已完成 | 支持本地健康检查、运行态快照、投递队列、Cron 触发和飞书接入状态查看。 |
| 运行事件流 | 已完成 | 支持 runtime event JSONL、`events.tail`、`errors.recent` 和 Dashboard 最近事件/错误视图。 |
| 架构分层 | 已完成 | 已将运行时兼容层移除，主实现迁移到 `core/application/interfaces` 等分层目录。 |

### 当前可运行入口

```bash
cd ~/Desktop/claw0/gateway
source .venv/bin/activate
agent-gateway serve
```

默认入口：

- WebSocket 控制面：`ws://127.0.0.1:8765`
- 飞书 Webhook：`http://127.0.0.1:8766/webhooks/feishu`
- 本地 Dashboard：`http://127.0.0.1:8780`
- 飞书扫码接入页：`http://127.0.0.1:8780/onboarding/feishu`

### 当前验证基线

建议每次进入下一阶段前至少运行：

```bash
cd ~/Desktop/claw0/gateway
./.venv/bin/python -m compileall agent_gateway tests
./.venv/bin/python -m pytest tests -q
```

## 已完成阶段回顾

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

### Phase 8：运维 Dashboard 与运行时状态

- 新增本地 Dashboard 静态页面。
- 支持健康检查、运行态状态、投递队列、Cron 任务和飞书接入状态查看。
- 支持在 Dashboard 中执行投递 retry/discard/flush 和 Cron 手动触发。
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
- 移除旧 `runtime/` 兼容层，避免后续开发继续依赖过期路径。
- README 已同步新的目录结构和运行方式。

### Phase 13：运行事件流与最近错误视图

- 新增 `observability/` 模块和 `RuntimeEventStore`。
- 定义统一 runtime event JSONL schema。
- 接入关键链路事件：
  - inbound received
  - route resolved
  - agent turn started / completed / failed
  - tool call started / completed / failed
  - delivery enqueued / sent / failed
  - cron triggered / completed / failed
  - feishu event accepted / ignored / rejected / error
- 控制面新增 `events.tail` 和 `errors.recent`。
- Dashboard 新增最近事件与最近错误视图。
- 测试覆盖事件存储、控制面入口和投递事件。

## 当前主要边界

- 当前仍是单进程本地运行时，尚未引入数据库、分布式锁或多实例协调。
- Dashboard 默认无鉴权，仅适合本机访问，不应直接暴露公网。
- 配置变更已经能保存和 reload，但配置审计、快照和回滚仍不完整。
- Agent 权限模型已有 tool policy 和 capability tags，但缺少最终权限预览和强校验报告。
- 运行态观测已有 Dashboard、健康检查、最近事件和最近错误，但缺少长期指标、趋势图和告警。
- 飞书长连接依赖本机 `lark-cli` 配置和子进程消费，适合本地/单机部署；生产多实例仍建议优先 Webhook。
- 新闻简报能力已可运行，但来源质量评估、内容去重精度和摘要可解释性仍有提升空间。

## 下一阶段计划

### Phase 12：Dashboard 鉴权与安全边界

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

### Phase 14：指标快照、趋势与告警

目标：

- 从“当前状态可见”升级到“运行趋势可见、异常可通知”。

计划项：

1. 增加定期 metrics snapshot。
2. 记录关键指标：
   - inbound count
   - agent turn latency
   - tool call count / failure count
   - delivery success / failure / retry
   - lane backlog
   - cron success / failure
   - feishu rejected / deduped events
3. Dashboard 增加基础趋势图。
4. 增加飞书告警通道，用于通知连续失败、队列堆积、cron 异常。
5. 增加 `metrics.snapshot` 控制面接口。

完成标准：

- 能看出系统是否正在变慢、失败率是否升高、投递是否堆积。
- 关键故障可以主动推送到指定飞书会话。

### Phase 15：Agent 权限预览与配置治理

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

### Phase 16：会话与记忆治理

目标：

- 控制长期运行后的数据膨胀、记忆污染和上下文质量下降。

计划项：

1. 增加 session list/export/archive/delete。
2. 增加 session retention 策略。
3. 增加 memory 来源标记。
4. 增加 memory review / delete / compact。
5. 增加长期记忆注入前的质量过滤。

完成标准：

- 可以管理长期会话数据，而不是只靠手动删除 JSONL。
- 记忆可追溯、可清理，不会无限污染 prompt。

### Phase 17：多 Agent 协作与任务实例状态机

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

### Phase 18：生产部署形态

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

## 推荐执行顺序

建议按以下优先级推进：

1. Phase 12：Dashboard 鉴权与安全边界
2. Phase 14：指标快照、趋势与告警
3. Phase 15：Agent 权限预览与配置治理
4. Phase 16：会话与记忆治理
5. Phase 17：多 Agent 协作与任务实例状态机
6. Phase 18：生产部署形态

这个顺序的依据是：

- 先补安全边界，避免 Dashboard 和控制面成为风险点。
- 再补指标和告警，降低后续复杂功能的排障成本。
- 然后补配置、权限、会话和记忆治理，提升长期运行质量。
- 最后再做多 Agent 编排和生产部署，避免在可观测性不足的情况下扩大系统复杂度。

## 短期建议

下一步最适合先实现 Phase 12 的最小闭环：

1. 在 `.env.example` 增加 `GATEWAY_DASHBOARD_AUTH_ENABLED` 和 `GATEWAY_DASHBOARD_TOKEN`。
2. 在 Dashboard 静态服务和 WebSocket 控制面增加 token 校验。
3. 在 README 中补充本地访问和公网暴露说明。
4. 增加测试覆盖未授权、授权成功和错误 token 三类路径。

这一阶段改动小、收益高，并且能为后续控制面能力扩展建立安全前提。
