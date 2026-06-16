# Gateway Integration Plan

## 目标

将当前代码持续整合为一个可运行、可维护、可扩展的 `gateway/` 项目，并逐步补齐面向生产化的配置控制、运行时自治、可靠投递和多 Agent 编排能力。

## 当前阶段映射

| Session | 已落地模块 | 当前状态 |
| --- | --- | --- |
| `s03_sessions` | `sessions/store.py`, `sessions/context.py` | 已完成 JSONL transcript 持久化、重放与上下文保护 |
| `s04_channels` | `channels/`, `runtime/channel_runtime.py` | 已完成 CLI / Telegram / Feishu 通道抽象与统一入站消费 |
| `s05_gateway_routing` | `router.py`, `runtime/gateway_server.py`, `runtime/dispatcher.py` | 已完成路由解析、WebSocket JSON-RPC 与统一 dispatcher |
| `s06_intelligence` | `intelligence/bootstrap.py`, `intelligence/memory.py`, `intelligence/skills.py` | 已完成提示词分层、记忆召回、技能发现 |
| `s07_heartbeat_cron` | `runtime/autonomy.py` | 已完成 Heartbeat / Cron 主动任务运行时 |
| `s08_delivery` | `delivery/queue.py`, `runtime/delivery_runtime.py` | 已完成可靠出站投递、失败重试、失败落盘 |
| `s09_resilience` | `runtime/resilience.py` | 已完成 profile 轮换、失败分类、overflow 重试骨架 |
| `s10_concurrency` | `runtime/lanes.py`, `runtime/dispatcher.py` | 已完成命名并发车道与 lane 串行执行 |

## 已完成阶段

### Phase 1：骨架与基础链路

- 建立 `agent_gateway/` 包结构与可运行入口
- 接入 WebSocket JSON-RPC 网关
- 完成 Agent / Binding / Session 的基础数据流
- 将会话存储迁移为 JSONL

### Phase 2：配置、上下文与弹性

- 引入 `.env + config/*.json` 文件化配置
- 迁移 transcript 重放和上下文保护
- 接入 resilience runner
- 形成 Anthropic 兼容接口的标准调用链

### Phase 3：记忆、技能与通道

- 接入 `MEMORY.md` 与日记忆写入
- 接入 `SKILL.md` 扫描与提示词注入
- 完成 CLI / Telegram / Feishu 通道适配
- 将通道消息纳入统一调度链

### Phase 4：统一运行时与主动任务

- 完成 dispatcher 统一入口
- Heartbeat 与 Cron 接入同一执行链
- 可靠投递队列接入真实出站路径
- CLI 本地交互节奏修正
- workspace 内收至 `gateway/workspace/`

## 当前状态

### 已完成到可联调阶段

- 已完成配置控制面：
  - bindings / agents / channels / profiles 的运行时增删改、保存、重载
  - `config.source` 原始配置查看
  - 关键约束保护与 env 引用保留
- 已完成 Agent Manifest 基础能力：
  - agent 级 tool policy / memory policy / prompt policy
  - `workspace/agents/<agent_id>/` 局部 prompt 覆盖
  - capability tags 与 agent template 脚手架
- 已完成 Feishu 接入最小闭环：
  - tenant token 获取与消息发送
  - webhook 入站接收
  - challenge 校验响应
  - encrypted payload 解密

### 当前主要缺口

- Feishu 入站仍缺签名校验、时间窗校验、事件幂等去重与审计落盘
- delivery 已具备可靠重试，但缺控制面运维能力与失败人工重放入口
- 运行态观测仍偏弱，主要依赖 `print`，缺统一状态面和结构化日志
- Agent 权限模型仍较粗，只支持 `all / allowlist / denylist`
- 会话、记忆、配置变更缺生命周期治理与审计能力

## 后续阶段

### Phase 7：飞书入站安全与幂等

目标：

- 将飞书接入从“可用”提升到“可稳定运行”
- 降低回调重放、重复消费、恶意请求和误触发的风险

计划项：

1. 增加飞书签名校验与时间窗校验
2. 增加 `event_id` / `message_id` 级别的去重存储
3. 为入站事件增加审计日志与必要元数据落盘
4. 增加按 sender / peer / channel 的基本限流
5. 补齐 Feishu 端到端回调测试与异常路径测试

完成标准：

- 同一飞书事件重复推送不会重复触发 agent 执行
- 非法或过期 webhook 请求会被拒绝
- 能追踪每条飞书入站事件的接收、忽略、执行与投递结果

### Phase 8：投递运维能力

目标：

- 将 delivery 从内部机制升级为可观测、可重放、可人工干预的运行模块

计划项：

1. 增加 `delivery.list_pending`
2. 增加 `delivery.list_failed`
3. 增加 `delivery.retry`
4. 增加 `delivery.delete`
5. 增加 `delivery.stats`
6. 提供失败消息人工重放与错误摘要查看能力

完成标准：

- 可以通过控制面查看 pending / failed 队列
- 可以人工重试失败消息，而无需手动改磁盘文件
- 可以按 channel/account 看到基础成功率和失败率

### Phase 9：运行态观测与统一状态面

目标：

- 降低排障成本，提升系统运行可见性

计划项：

1. 将关键 `print` 替换为结构化日志
2. 增加统一运行态状态接口，聚合：
   - lanes
   - delivery
   - profiles
   - cron
   - heartbeat
   - channels
3. 增加健康检查入口
4. 增加关键指标快照与最近错误摘要

完成标准：

- 排障时可以直接看到当前拥塞、失败、冷却与任务运行状态
- 不再需要主要依赖手工翻日志和磁盘目录定位问题

### Phase 10：Agent 权限模型升级

目标：

- 将多 Agent 运行从“可配置”提升到“可隔离、可审查”

计划项：

1. 从简单 `tool_names` 扩展到 capability + explicit allow + explicit deny
2. 增加 manifest resolved preview
3. 增加 `agents.validate` 接口
4. 加强 schema 级字段校验与错误提示
5. 补齐权限解析与模板生成测试

完成标准：

- 可以明确看到某个 agent 最终拥有的 prompt / memory / skills / tools
- 可以针对高风险 agent 精细限制工具能力

### Phase 11：会话、记忆与配置治理

目标：

- 控制系统长期运行后的数据膨胀、记忆污染和配置误操作风险

计划项：

1. 增加 session 导出、归档、删除与 retention 策略
2. 增加 memory 来源标记、清理与审核入口
3. 增加配置变更审计日志
4. 增加配置快照与回滚能力

完成标准：

- 长期运行后会话与记忆规模可控
- 配置误改后可以回溯与恢复

### Phase 12：多 Agent 编排与任务化运行

目标：

- 将网关从消息响应器扩展为具备协作与任务执行能力的 Agent Runtime

计划项：

1. 增加 agent-to-agent handoff
2. 增加任务实例状态机：
   - pending
   - running
   - retrying
   - done
   - failed
3. 为 cron / heartbeat / 主动任务增加幂等 key 与执行记录
4. 补齐后台任务失败恢复与追踪能力

完成标准：

- 后台任务具备可追踪的执行生命周期
- 多 Agent 协作不再依赖手工 prompt 编排

## 推荐执行顺序

建议按以下优先级推进，而不是并行摊大饼：

1. Phase 7：飞书入站安全与幂等
2. Phase 8：投递运维能力
3. Phase 9：运行态观测与统一状态面
4. Phase 10：Agent 权限模型升级
5. Phase 11：会话、记忆与配置治理
6. Phase 12：多 Agent 编排与任务化运行

## 当前边界说明

- `channels.reload` 对 Telegram / Feishu 这类轮询或网络通道是可用的。
- `CLIChannel` 基于阻塞式 `input()`，运行中热替换只能做到“尽力而为”，不保证无感切换。
- 当前 control plane 仍以单进程内存态协同为主，尚未引入跨进程配置锁或分布式协调。
