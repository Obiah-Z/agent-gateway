# 多 Agent 协作系统设计与实施计划

日期：2026-07-07

## 1. 背景与目标

当前 Gateway 已经具备多个独立 Agent，包括 `main`、`research`、`wework-main`、`diet-assistant-zhanghaibo`、`ops`、飞书专属 Agent 等。它们通过 `config/agents.json` 定义角色、工具权限、记忆策略和 prompt 目录，通过 `config/bindings.json` 由入站消息路由到单个 Agent。

现阶段的问题是：Agent 之间是孤立的。一次用户请求只能被路由给一个 Agent 执行；如果任务需要多个 Agent 共同完成，只能靠主 Agent 自己在 prompt 中“想象”协作，系统层没有提供可靠的委派、子任务、结果合并、权限隔离和可观测能力。

本计划的目标是设计一套可落地的多 Agent 协作系统，使不同 Agent 能围绕一个用户任务进行受控协作，而不是简单并行聊天或互相调用。系统应满足：

- 主 Agent 可以把任务拆给专属 Agent 或 Worker Agent。
- 子 Agent 拥有独立上下文、独立工具权限和独立执行记录。
- 同一用户会话仍保持顺序一致，不破坏现有 session lane。
- Agent 协作过程可观测、可恢复、可追踪、可限制成本。
- 最终结果仍通过现有可靠投递链路发送给用户。
- 第一阶段优先支持只读、低风险、可验证的协作场景。

## 2. 当前系统基础

当前架构已经具备实现多 Agent 协作的关键底座。

入站侧已经有统一的 `InboundMessage`、路由表、`GatewayDispatcher.dispatch_inbound()` 和 session lane。消息进入后会解析路由，生成 `agent_id` 和 `session_key`，再通过 lane 保证同一会话串行执行。

执行侧已经有 `AgentLoopRunner.run_task_turn()`，它会加载会话历史、组装 prompt、限制工具范围、调用模型、处理工具调用并重写会话历史。这个组件可以继续作为单个 Agent 的执行内核。

后台任务侧已经有 `TaskInstance`、`TaskWorkerRuntime`、任务状态、重试、ack/fail、Redis session claim 续租、RabbitMQ/本地队列适配。这说明协作子任务不需要新造一套 worker 机制，可以复用现有任务运行时。

观测侧已经有 `RuntimeEventStore.record()`，支持记录 `agent.turn.started/completed/failed`、`task.worker.*`、投递、工具、cron 等事件。多 Agent 协作应继续写入这套事件流，而不是另起日志。

投递侧已经有可靠 delivery queue/runtime，Agent 协作的中间阶段和最终结果都应通过统一投递层发送，避免某个 Agent 绕过队列直接向通道发送消息。

## 3. 核心设计原则

多 Agent 协作不能做成“多个 Agent 在一个 session 里自由聊天”。那会带来循环、上下文污染、结果不可控、重复工具调用和权限扩散。Gateway 更适合采用“主控编排 + 专属 Agent 子任务”的模式。

系统层必须明确控制权。默认由入口 Agent 或 deterministic orchestrator 持有控制权，子 Agent 只执行被分配的任务并返回结构化结果。只有在明确配置 handoff 的情况下，才允许把用户后续会话切换给另一个 Agent。

系统层必须明确上下文边界。子 Agent 不应默认拿到主会话全部历史，而是拿到任务目标、必要背景、允许工具、输出格式和截止条件。这样可以减少上下文污染，也能降低模型成本。

系统层必须明确权限边界。子 Agent 的工具权限取其自身 `tool_policy` 与委派任务 `tool_scope` 的交集。即使主 Agent 有某个工具，也不能自动把权限传给子 Agent。

系统层必须有硬性终止条件。每次协作需要限制最大子任务数、最大嵌套深度、最大运行时间、最大工具调用次数、最大重试次数和最大输出长度。不要只依赖 prompt 让模型“不要循环”。

系统层必须可观测。每次委派、子任务开始、子任务完成、合并、失败、降级、handoff 都要写入 runtime events，并关联同一个 `correlation_id`。

## 4. 目标架构

建议新增一个 `AgentCollaborationRuntime`，作为 Dispatcher 和 AgentLoopRunner 之间的协作编排层。

```text
用户消息
  |
  v
ChannelRuntime / Webhook
  |
  v
GatewayDispatcher
  |
  v
Route resolved: agent_id + session_key
  |
  v
AgentCollaborationRuntime
  |
  +-- 单 Agent 快速路径
  |     |
  |     v
  |   AgentLoopRunner.run_task_turn()
  |
  +-- 多 Agent 协作路径
        |
        +-- 创建 CollaborationRun
        +-- 主 Agent / Router 生成 DelegationPlan
        +-- 创建多个 AgentSubtask
        +-- TaskWorkerRuntime 执行子 Agent
        +-- 收集 SubtaskResult
        +-- Merger 汇总结果
        v
      AgentReply
  |
  v
DeliveryRuntime 可靠投递
```

`GatewayDispatcher` 仍负责入站接收、路由解析、session lane 和最终投递。`AgentCollaborationRuntime` 不直接处理通道，不直接发送消息，只负责判断本轮是否需要协作以及如何协作。

## 5. 协作对象模型

### 5.1 CollaborationRun

`CollaborationRun` 表示一次多 Agent 协作流程。它对应一次用户请求或一次后台任务。

建议字段：

| 字段 | 含义 |
|---|---|
| `run_id` | 协作流程 ID |
| `correlation_id` | 贯穿入站、子任务、投递的追踪 ID |
| `root_task_id` | 入口任务 ID，可为空 |
| `root_agent_id` | 入口 Agent |
| `root_session_key` | 入口会话 |
| `trigger_type` | inbound / cron / manual / system |
| `mode` | single / delegate / handoff / review |
| `status` | planning / running / merging / completed / failed / cancelled |
| `max_depth` | 最大委派深度 |
| `max_subtasks` | 最大子任务数量 |
| `created_at` / `updated_at` | 时间戳 |
| `metadata` | 通道、用户、来源等扩展信息 |

第一阶段可以不新增独立数据库表，而是把这些字段放入 `TaskInstance.metadata` 和 runtime events。第二阶段再升级为 PostgreSQL 表。

### 5.2 DelegationPlan

`DelegationPlan` 是主 Agent 或规则路由器生成的协作计划。

建议字段：

| 字段 | 含义 |
|---|---|
| `plan_id` | 计划 ID |
| `run_id` | 所属协作流程 |
| `strategy` | parallel / sequential / handoff / tool_call |
| `subtasks` | 子任务列表 |
| `merge_policy` | all_success / best_effort / first_success / reviewer |
| `fallback_policy` | 子任务失败时如何降级 |
| `requires_user_confirmation` | 是否需要用户确认后执行 |

第一阶段建议不要让模型自由输出任意 JSON。可以先由系统规则生成计划，例如：

- “分析 GitHub 仓库”可委派给 repo analyzer。
- “查资料/调研”可委派给 research。
- “服务器巡检”可委派给 ops。
- “饮食记录/计划”可委派给 diet Agent。
- “需要多视角评审”才启用多个只读子任务。

### 5.3 AgentSubtask

`AgentSubtask` 是一个可执行的子 Agent 任务，可复用 `TaskInstance`。

建议映射：

| AgentSubtask 字段 | 复用 TaskInstance 的位置 |
|---|---|
| `subtask_id` | `TaskInstance.id` |
| `target_agent_id` | `TaskInstance.agent_id` |
| `subtask_type` | `TaskInstance.task_type = agent_subtask` |
| `run_id` | `TaskInstance.metadata.run_id` |
| `parent_task_id` | `TaskInstance.metadata.parent_task_id` |
| `delegation_type` | `TaskInstance.metadata.delegation_type` |
| `context_pack` | `TaskInstance.payload.context_pack` |
| `expected_output_schema` | `TaskInstance.payload.output_schema` |
| `tool_scope` | `TaskInstance.payload.tool_scope` |
| `result` | `TaskInstance.result_preview` 或后续独立结果表 |

这样可以直接复用现有任务队列、worker、重试、幂等和状态恢复能力。

### 5.4 SubtaskResult

子 Agent 不能只返回一段自然语言，否则主 Agent 难以可靠合并。建议第一阶段使用固定结构：

```json
{
  "status": "ok | failed | skipped",
  "agent_id": "research",
  "summary": "结论摘要",
  "evidence": [
    {"type": "url", "title": "来源", "value": "https://..."},
    {"type": "file", "title": "产物", "value": "workspace/reports/..."}
  ],
  "risks": ["不确定点或失败风险"],
  "next_actions": ["建议下一步"],
  "user_visible_text": "可以直接展示给用户的内容"
}
```

## 6. 协作模式设计

### 6.1 单 Agent 快速路径

大部分普通消息仍走现有逻辑：路由到一个 Agent，执行一轮，投递回复。不要为了多 Agent 而把所有请求复杂化。

适合：

- 简单问答。
- 普通个人秘书提醒。
- 饮食记录一类明确属于某个 Agent 的任务。
- 单一工具调用即可完成的任务。

### 6.2 主 Agent 委派子任务

这是第一阶段最推荐的模式。入口 Agent 判断需要外部专业能力时，创建子任务。子任务完成后，主 Agent 或系统 merger 汇总结果。

示例：

```text
用户：帮我分析这个项目能否作为 Gateway 的参考。

root agent: main
subtasks:
  - research: 检索项目背景、star、README 和类似项目
  - repo-analyzer: 分析目录结构、技术栈、可借鉴点
  - reviewer: 判断对 Gateway 的风险和落地成本
merge:
  - 汇总三方结果，形成一份报告
```

第一阶段可以只支持系统规则触发，不开放模型任意创建子任务。

### 6.3 专家 Handoff

Handoff 是会话所有权转移，不是后台子任务。比如用户对 `wework-main` 说“接下来只和饮食助手沟通”，系统可把后续这个 peer 的路由切到 diet Agent，直到用户退出。

Handoff 需要单独设计，因为它会影响后续入站路由。

建议规则：

- 必须记录 `agent.handoff.requested`、`agent.handoff.accepted`、`agent.handoff.completed`。
- 必须有过期时间，例如 24 小时或一次会话。
- 必须允许用户说“退出饮食助手/回到主助手”。
- 不能默认把所有主动 Cron 也一起切过去，Cron 仍按 Agent 自己配置执行。

### 6.4 评审型多 Agent

评审型协作适合代码审查、方案评估、文档审查。多个 Agent 从不同角度读取同一份材料，然后返回结构化意见。

示例角色：

- `research`：事实和来源。
- `ops`：运维风险。
- `reviewer`：实现复杂度和测试风险。
- `main`：最终取舍和用户表达。

第一阶段建议只读，不允许评审子 Agent 写文件。

### 6.5 不建议优先实现自由 Group Chat

多个 Agent 互相发言、自由讨论，看起来很像协作，但对当前 Gateway 风险较高。它会引入循环、成本不可控、共享上下文膨胀、结果难验收和投递混乱。

如果以后需要做“专家会议”，建议也要由 `CollaborationRun` 管控发言轮次、最大回合数、发言者选择和终止条件。

## 7. Agent 间通信机制

Agent 间不要直接互相调用 Python 对象，也不要直接写对方 session。建议统一走任务和事件。

### 7.1 同步调用

同步调用适合低延迟、小任务。例如主 Agent 需要让 research Agent 快速检查一个事实。实现上可以由 `AgentCollaborationRuntime` 直接调用 `AgentLoopRunner.run_task_turn()`，但必须使用独立 `session_key`。

建议子任务 session key 格式：

```text
collab:{run_id}:agent:{agent_id}:subtask:{subtask_id}
```

这样不会污染用户主会话。

### 7.2 异步调用

异步调用适合耗时任务，例如仓库分析、长文档总结、多源调研。实现上创建 `TaskInstance(task_type="agent_subtask")`，交给 `TaskWorkerRuntime` 执行。

异步子任务完成后，由 merger 任务汇总结果。必要时先给用户发送“已收到，正在处理”，最终结果再通过可靠投递发送。

### 7.3 事件通知

所有协作阶段写入 runtime events：

- `agent.collaboration.started`
- `agent.collaboration.plan_created`
- `agent.subtask.enqueued`
- `agent.subtask.started`
- `agent.subtask.completed`
- `agent.subtask.failed`
- `agent.collaboration.merging`
- `agent.collaboration.completed`
- `agent.collaboration.failed`
- `agent.handoff.requested`
- `agent.handoff.accepted`
- `agent.handoff.reverted`

Dashboard 后续可以按 `run_id` 展示一条协作链路。

## 8. 上下文与记忆边界

协作子任务必须使用 `ContextPack`，而不是直接复制主会话全部历史。

`ContextPack` 建议包含：

| 字段 | 含义 |
|---|---|
| `goal` | 子任务目标 |
| `user_request` | 用户原始请求 |
| `background` | 主 Agent 给出的必要背景 |
| `constraints` | 禁止事项、输出长度、是否可联网等 |
| `allowed_sources` | 允许读取的数据范围 |
| `memory_scope` | 可访问的记忆 scope |
| `output_schema` | 输出格式要求 |

记忆访问规则：

- 子 Agent 默认不能写用户长期记忆，除非任务明确授权。
- 研究类、分析类子任务可以读共享记忆，但写入必须通过主 Agent 或显式工具权限。
- 饮食、个人秘书等用户专属 Agent 只能访问自己的 `user_scope`。
- ops Agent 禁止写 memory，避免巡检信息污染长期记忆。

## 9. 权限与安全边界

子 Agent 的实际工具权限应按以下公式计算：

```text
effective_tools = target_agent.tool_policy ∩ delegation.tool_scope ∩ system_safety_policy
```

其中：

- `target_agent.tool_policy` 来自 `config/agents.json`。
- `delegation.tool_scope` 来自委派计划。
- `system_safety_policy` 是系统硬规则，例如只读评审任务禁止 `write_file`、禁止投递、禁止 shell 写操作。

建议第一阶段定义三个安全等级：

| 等级 | 能力 | 用途 |
|---|---|---|
| `readonly` | 读取文件、搜索、查询记忆、联网检索 | 调研、审查、分析 |
| `workspace_write` | 可写 workspace/reports 等产物目录 | 生成报告、图表、总结 |
| `side_effect` | 可投递、写长期记忆、调用外部 API | 需要用户或规则显式授权 |

默认所有子 Agent 都从 `readonly` 开始。

## 10. 状态流转

`CollaborationRun` 状态：

```text
planning -> running -> merging -> completed
                   \-> failed
                   \-> cancelled
```

`AgentSubtask` 状态复用当前 `TaskInstance`：

```text
pending -> running -> completed
                 \-> retrying -> pending
                 \-> failed
                 \-> cancelled
```

合并规则：

- `all_success`：所有子任务成功才合并。
- `best_effort`：部分失败也合并，但必须展示失败项。
- `first_success`：任一子任务成功即可返回。
- `reviewer`：再启动一个 reviewer/merger Agent 对结果做二次整理。

第一阶段建议使用 `best_effort`，因为外部搜索、模型调用和工具调用都可能失败，不能让一个子任务失败导致整轮无回复。

## 11. 数据存储方案

### 11.1 第一阶段：复用 TaskInstance.metadata

为了降低改造风险，第一阶段不新增数据库表，先复用现有任务模型：

```json
{
  "run_id": "collab_xxx",
  "parent_task_id": "task_xxx",
  "root_agent_id": "main",
  "delegation_type": "subtask",
  "target_agent_id": "research",
  "merge_policy": "best_effort",
  "collaboration_depth": 1
}
```

结果可以暂存在 `result_preview` 和 `payload.result`，完整报告写入 workspace/reports。

### 11.2 第二阶段：新增 PostgreSQL 表

当第一阶段稳定后，再新增结构化表：

```text
agent_collaboration_runs
agent_collaboration_subtasks
agent_collaboration_results
agent_handoff_sessions
```

这样 Dashboard 和控制面可以更容易查询协作状态。

### 11.3 本地 JSONL 兜底

协作事件仍写入 runtime events JSONL，数据库不可用时可以保留审计和回放能力。

## 12. 控制面与 Dashboard

控制面建议新增：

- `collaboration.recent`：最近协作流程。
- `collaboration.get`：按 `run_id` 查看详情。
- `collaboration.cancel`：取消未完成协作。
- `handoff.current`：查看当前 peer 是否处于专家接管。
- `handoff.revert`：手动恢复默认路由。

Dashboard 建议新增：

- 最近多 Agent 协作卡片。
- 协作链路时间线。
- 子任务状态列表。
- Agent 贡献摘要。
- 失败原因和降级策略展示。

## 13. 与当前 Agent 的协作案例

### 13.1 研究型任务

用户让 `wework-main` 做市场调研时：

```text
wework-main -> research
research 负责联网检索、来源核验、摘要。
wework-main 负责把结论转成适合用户的行动建议。
```

### 13.2 仓库分析任务

用户发 GitHub 链接：

```text
main/wework-main -> repo-analyzer skill/agent -> research -> main
```

repo analyzer 负责项目结构，research 负责外部信息和竞品，main 负责最终报告。

### 13.3 运维分析任务

用户问服务器空间或系统状态：

```text
main/wework-main -> ops
```

ops 只读采集信息，不删除、不重启、不修改。最终由 main/wework-main 用用户友好语言解释。

### 13.4 饮食个人助理

饮食相关请求可以 handoff 到 `diet-assistant-zhanghaibo`，但只针对绑定用户，不影响企业微信其他用户。

```text
wework-main -> diet-assistant-zhanghaibo
```

如果是“今天吃了什么”，直接 handoff 或路由到 diet；如果是“帮我安排一天计划并提醒我”，可以由 diet 生成计划，wework-main 负责提醒表达。

## 14. 实施阶段

### Phase A：协作协议与事件先行

目标：不改变执行路径，先把模型和事件定义清楚。

任务：

- 新增协作数据模型文档和 Python dataclass。
- 定义 `CollaborationRun`、`DelegationPlan`、`AgentSubtask`、`SubtaskResult`。
- 定义 runtime event 类型。
- 补测试覆盖序列化和事件字段。

完成标准：

- 可以创建一条协作 run 并写入事件。
- 不影响现有单 Agent 执行。

### Phase B：只读子 Agent 执行

目标：支持主 Agent 创建只读子任务，由 worker 执行目标 Agent。

任务：

- 新增 `agent_subtask` handler。
- 子任务使用独立 session key。
- 工具权限按交集裁剪。
- 子任务输出结构化 `SubtaskResult`。
- 子任务只允许 `readonly` 工具。

完成标准：

- main 可委派 research 执行一个只读调研子任务。
- 子任务不会污染 main 的 session 历史。
- Dashboard/事件流能看到子任务开始和完成。

### Phase C：结果合并器

目标：将多个子 Agent 结果汇总为用户可见回复。

任务：

- 新增 deterministic merger。
- 支持 `best_effort` 合并策略。
- 失败子任务必须进入结果说明。
- 最终结果仍通过 `DeliveryRuntime` 投递。

完成标准：

- 三个子任务部分失败时，用户仍收到完整说明。
- 可追踪每段结论来自哪个 Agent。

### Phase D：规则化 Router / Supervisor

目标：让系统根据任务类型自动选择协作策略。

任务：

- 新增协作规则配置，例如 `config/collaboration.json`。
- 支持按意图关键词、Agent 能力、通道、用户 scope 选择子 Agent。
- 高风险任务要求用户确认。

完成标准：

- 调研类任务自动委派 research。
- 运维类任务自动委派 ops。
- 饮食类任务自动 handoff 或路由到 diet。

### Phase E：专家 Handoff

目标：支持用户会话被某个专家 Agent 临时接管。

任务：

- 新增 handoff session 存储。
- 支持过期时间、退出指令和手动恢复。
- 控制面展示当前 handoff 状态。

完成标准：

- 用户可进入/退出 diet Agent。
- handoff 不影响其他用户和其他通道。

### Phase F：协作大屏与恢复

目标：运维面板展示协作链路，并支持失败恢复。

任务：

- Dashboard 增加协作时间线。
- 控制面支持取消/重试协作任务。
- PostgreSQL 表结构化存储协作 run/subtask/result。

完成标准：

- 可以从一次用户消息追踪到所有子 Agent 任务和最终投递。
- Worker 崩溃后，未完成子任务可恢复或标记失败。

## 15. 第一阶段建议切入点

建议先做一个低风险功能：`main/wework-main -> research` 的只读调研委派。

原因：

- research 已存在，工具权限清晰。
- 只读任务风险低。
- 输出容易结构化。
- 不涉及写文件冲突。
- 能快速验证“主 Agent + 子 Agent + 结果合并 + 事件流”的最小闭环。

最小闭环：

```text
用户请求
  -> GatewayDispatcher 路由到 wework-main
  -> CollaborationRuntime 判断需要 research
  -> 创建 agent_subtask
  -> TaskWorkerRuntime 执行 research 独立轮次
  -> 生成 SubtaskResult
  -> Merger 生成最终回复
  -> DeliveryRuntime 投递
```

## 16. 风险与规避

最大风险是循环委派。规避方式是限制最大深度，第一阶段禁止子 Agent 再创建子 Agent。

第二个风险是工具权限扩散。规避方式是使用工具交集和安全等级，子 Agent 默认只读。

第三个风险是上下文污染。规避方式是子任务独立 session key，不写主会话历史，只返回结构化摘要。

第四个风险是结果不可验收。规避方式是强制 `SubtaskResult` schema，合并器必须标注来源、失败和不确定点。

第五个风险是成本膨胀。规避方式是限制子任务数、超时、输出长度和模型配置，默认只在明确规则命中时启用协作。

第六个风险是状态不一致。规避方式是复用现有 TaskStore、Redis session claim、RuntimeEventStore 和可靠投递链路，不让 Agent 之间绕过系统层私下通信。

## 17. 总结

Gateway 的多 Agent 协作不应从“让 Agent 互相聊天”开始，而应从“主控编排、只读委派、结构化结果、可靠合并、全链路观测”开始。

推荐路线是先实现 `CollaborationRun + AgentSubtask + SubtaskResult` 的最小闭环，复用现有 TaskWorker 和 RuntimeEventStore。等只读委派稳定后，再扩展到 handoff、规则化 supervisor、多 Agent 评审和协作大屏。

