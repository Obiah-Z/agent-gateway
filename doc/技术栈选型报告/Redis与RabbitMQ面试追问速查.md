# Redis 与 RabbitMQ 面试追问速查

## 1. 总体架构怎么讲

可以这样概括：

> 这个 Gateway 不是简单地把模型 API 包一层，而是把入站接入、Agent 执行、工具调用、出站投递和运维观测拆成多个运行阶段。RabbitMQ 负责异步队列和可靠投递，Redis 负责分布式协调和会话互斥，PostgreSQL 负责事实状态持久化。这样系统既能削峰，也能在多实例部署时避免重复消费和同一会话并发执行。

## 2. 三个中间件分别干什么

| 组件 | 核心职责 | 不适合做什么 |
| --- | --- | --- |
| RabbitMQ | 排队、削峰、分发、ack/nack、死信 | 不适合作为业务事实主存储 |
| Redis | 去重、锁、限流、会话 lane、短 TTL 协调 | 不适合保存完整会话和审计历史 |
| PostgreSQL | 会话、任务、事件、投递、记忆等事实状态 | 不适合承担所有高频临时协调 |

一句话：

```text
RabbitMQ 管任务流动，Redis 管执行资格，PostgreSQL 管事实状态。
```

## 3. 为什么不是只用一个组件

### 3.1 只用 PostgreSQL 的问题

可以保存任务状态，但：

- 需要 worker 高频轮询。
- 去重、限流、锁续租会增加主库压力。
- TTL 过期语义需要额外清理任务。
- 不如 RabbitMQ 的 ack/nack 和 DLQ 直接。

### 3.2 只用 Redis 的问题

可以做锁、去重和简单队列，但：

- 不适合保存完整会话历史。
- 不适合复杂查询和审计。
- 队列语义不如 RabbitMQ 完整。
- 大量业务事实放 Redis 会增加内存成本和治理难度。

### 3.3 只用 RabbitMQ 的问题

可以分发任务，但：

- 不适合查询完整任务状态。
- 不知道当前 session owner 是谁。
- 不适合保存长期历史。
- 不能替代数据库事务和审计。

## 4. 核心链路怎么讲

### 4.1 入站链路

```text
飞书消息
  -> ChannelRuntime 接收
  -> 创建 agent_inbound task
  -> PostgreSQL 保存任务事实
  -> RabbitMQ 按 session_key 分区投递 task_id
  -> TaskWorkerRuntime 消费
  -> Redis 获取 session lane ownership
  -> AgentLoopRunner 调模型 / 工具
  -> 写会话、事件、指标
  -> DeliveryQueue 入队出站消息
```

### 4.2 出站链路

```text
Agent 生成回复
  -> DeliveryQueue 写入投递事实
  -> RabbitMQ 发布 delivery_id
  -> DeliveryRuntime 消费
  -> 调用 Feishu / Telegram / CLI send
  -> 成功 ack，失败 retry 或 dead-letter
```

## 5. 高频追问

### Q1：为什么要做入站队列？

回答：

> 模型调用和工具调用耗时不可控，如果飞书 webhook 入口同步等待 Agent 完成，入口会被慢请求拖住。入站队列把接收和执行拆开，入口只负责快速落任务，后台 worker 慢慢处理，这样可以削峰并支持横向扩展。

### Q2：为什么要做出站可靠投递？

回答：

> 外部通道发送也可能失败，比如飞书接口超时、限流或者 token 刷新失败。如果 Agent 回复生成后直接发送，失败就很难恢复。现在先写 DeliveryQueue，再通过 RabbitMQ 唤醒投递 worker，失败可以重试，超过上限进入死信。

### Q3：怎么处理消息重复？

回答：

> 入口层用 Redis `SET NX EX` 做事件去重，任务层和投递层使用 idempotency_key，状态层通过 PostgreSQL 保存任务和投递状态。RabbitMQ 是至少一次投递，所以系统不能假设消息只来一次，而是通过幂等键和状态检查保证重复消息不会重复执行核心逻辑。

### Q4：怎么处理同一会话并发？

回答：

> 同一会话通过 session_key 映射到 Redis lane。worker 执行前必须获取 lane owner，owner value 包含 worker_id、task_id 和 token。执行过程中续租，释放时校验 token。这样多个 worker 抢同一 session 时只有一个能成功。

### Q5：RabbitMQ 分区后还需要 Redis 锁吗？

回答：

> 需要。RabbitMQ 分区只能降低乱序和并发概率，不能作为最终互斥保证。比如配置多个 consumer、prefetch 大于 1、消息重试或旧 worker 混跑时，仍可能出现同一 session 并发。Redis lane 是最后一道互斥保护。

### Q6：如果 RabbitMQ 挂了怎么办？

回答：

> 因为完整任务事实保存在 PostgreSQL，所以 RabbitMQ 挂了不会导致任务事实丢失。短时间内入站或出站 broker 唤醒会受影响，可以通过数据库状态补偿 publish。生产环境需要 broker 健康检查、积压告警和恢复后的补偿扫描。

### Q7：如果 Redis 挂了怎么办？

回答：

> Redis 挂了会影响去重、限流和 session lane。在单机模式可以降级，但多实例模式不建议无保护继续执行，因为可能发生同会话并发和重复消费。更合理的策略是触发告警，阻断高风险 worker，或者降级到单 worker 模式。

### Q8：如果 PostgreSQL 挂了怎么办？

回答：

> PostgreSQL 是事实状态主存储，影响最大。没有它，任务状态、会话、事件和投递事实都无法可靠落库。可以保留本地 JSONL 兼容作为降级，但生产多实例模式应该把 PostgreSQL 视为强依赖。

## 6. 高并发下如何扩容

### 6.1 入站压力大

优先看：

- RabbitMQ 入站分区队列积压。
- worker 并发数。
- Redis lane 冲突率。
- 模型 API P95 延迟。

扩容方式：

- 增加 task worker 实例。
- 增加入站 partition 数。
- 对入口做 Redis 限流。
- 对热点 session 做单独治理。

### 6.2 出站压力大

优先看：

- delivery queue pending 数量。
- RabbitMQ delivery queue depth。
- Feishu / Telegram API 错误率。
- dead-letter 数量。

扩容方式：

- 增加 delivery worker。
- 调整发送速率限制。
- 按通道拆分投递队列。
- 对失败消息设置退避重试。

### 6.3 同一会话很慢

这是设计上允许的，因为同一 session 必须串行。

可以解释为：

> 系统追求的是不同 session 并发，同一 session 串行。单个热点 session 慢不会破坏其他 session 的并发，但它所在分区可能受影响，所以后续可以演进到更细粒度的 per-session lane。

## 7. 最容易被问到的设计取舍

### 7.1 为什么 RabbitMQ 消息只放 ID？

回答：

> 这是为了把 broker 和业务事实解耦。RabbitMQ 只负责唤醒和分发，完整内容放 PostgreSQL。这样消息堆积时不会在 broker 里堆大量用户上下文，重试和恢复也以数据库状态为准。

### 7.2 为什么不是严格 FIFO？

回答：

> 分布式系统里的严格 FIFO 成本很高。当前通过 session_key 分区和 Redis lane 实现“同会话串行 + 尽量有序”。这已经能解决并发写乱会话的核心问题。后续如果需要更强顺序，可以演进到 per-session lane 调度。

### 7.3 为什么需要死信队列？

回答：

> 有些错误重试没有意义，比如消息格式损坏、通道配置错误、权限缺失。死信队列可以把这类消息隔离出来，避免无限重试阻塞主队列，同时保留排查和人工补偿入口。

### 7.4 Redis 锁安全吗？

回答：

> 当前使用 token 校验和 Lua 脚本释放 / 续租，避免误删其他 worker 的锁。它适合当前这种短租约互斥场景。但如果进入更高等级生产环境，还需要 Redis 高可用、合理 TTL、续租监控和数据库最终幂等约束。

## 8. 简历展开话术

原简历句子：

> 引入 RabbitMQ 作为入站分区队列与出站可靠投递层，实现任务削峰、异步解耦和死信处理，提升系统在高并发场景下的稳定性。基于 Redis 实现事件去重、幂等控制、限流和会话级 lane，解决多实例部署下的重复消费与并发冲突问题，保证同一会话串行执行。

面试展开版：

> 项目一开始是单机 Agent Loop，入站消息会直接进入模型调用，回复也可能直接发到飞书。这个模式在功能上可行，但遇到慢模型、通道失败或并发消息时就不稳定。所以我把链路拆成入站、执行、出站三段。RabbitMQ 负责队列削峰和可靠投递，消息里只放 task_id 或 delivery_id，完整状态落 PostgreSQL。Redis 则负责运行时协调，比如事件去重、限流和 session lane ownership，保证多 worker 下同一个会话不会并发执行。这样系统可以横向扩 worker，同时通过 Redis lane 保持会话串行，通过 RabbitMQ DLQ 保留失败消息的排查和补偿能力。

## 9. 最短回答版

如果面试官只给 30 秒，可以回答：

> RabbitMQ 解决的是异步解耦和可靠任务分发，Redis 解决的是多实例运行时协调。具体来说，入站消息先落任务，再通过 RabbitMQ 分区分发给 worker；worker 执行前用 Redis 获取 session lane，保证同一会话串行；Agent 回复先写投递队列，再通过 RabbitMQ 出站发送，失败可重试或进死信。PostgreSQL 保存事实状态，RabbitMQ 传轻量引用，Redis 做锁、去重和限流。

