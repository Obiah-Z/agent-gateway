# RabbitMQ 技术栈选型报告

## 1. 一句话定位

RabbitMQ 在 AI Agent Gateway 中承担异步任务分发和可靠投递层，核心目标是把入站流量、Agent 执行和出站发送解耦，提升系统在高并发、慢模型调用、外部通道不稳定场景下的稳定性。

它主要解决：

| 能力 | 解决的问题 |
| --- | --- |
| 入站分区队列 | 将外部消息转为后台任务，削峰并支持多 worker 消费 |
| 出站可靠投递 | 回复先入队，再由投递 worker 发送，避免直接阻塞 Agent 执行 |
| 异步解耦 | 入站接收、Agent 推理、外部发送互不直接阻塞 |
| ACK/NACK | 消费成功确认，失败重排或丢入死信 |
| 死信队列 | 不可恢复失败任务进入 DLQ，便于排查和人工处理 |
| 分区能力 | 按 session_key 路由，提升同会话顺序性 |

相关代码：

- `agent_gateway/runtime/infra/rabbitmq.py`
- `agent_gateway/runtime/state/queue.py`
- `agent_gateway/runtime/tasks/worker.py`
- `tests/test_rabbitmq_broker.py`

## 2. 当前项目中的 RabbitMQ 设计边界

当前项目里的 RabbitMQ 有一个非常重要的设计原则：

```text
RabbitMQ 只传递轻量引用，不保存完整业务事实。
```

例如出站投递消息只携带：

```json
{
  "delivery_id": "xxx",
  "channel": "feishu",
  "account_id": "feishu-main",
  "correlation_id": "xxx",
  "idempotency_key": "xxx",
  "published_at": 1782619200.123
}
```

入站任务消息只携带：

```json
{
  "task_id": "xxx",
  "task_type": "agent_inbound",
  "session_key": "agent:feishu:xxx",
  "partition": 3,
  "idempotency_key": "xxx",
  "published_at": 1782619200.123
}
```

完整任务、会话、投递内容仍保存在 PostgreSQL / 本地状态层。

这样做的好处：

- RabbitMQ 堆积时不会保存完整用户消息和上下文，降低敏感数据暴露面。
- 任务状态、重试次数、错误原因仍由 PostgreSQL 统一管理。
- 消费者可以通过 `task_id` / `delivery_id` 回查事实状态。
- RabbitMQ 重投不会导致业务状态丢失。
- Dashboard 和控制面不依赖 RabbitMQ 内部消息体做主查询。

## 3. 出站可靠投递层

早期系统中，Agent 生成回复后可能直接调用通道发送：

```text
AgentLoopRunner
  -> Channel.send()
  -> Feishu / Telegram / CLI
```

这种方式在外部通道慢、失败或限流时会有明显问题：

- Agent 执行线程被发送阻塞。
- 飞书接口失败会直接影响当前轮次。
- 进程崩溃时，已生成但未发送的回复可能丢失。
- 缺少统一重试和死信处理。

引入 RabbitMQ 后，出站链路变为：

```text
AgentLoopRunner
  -> DeliveryQueue.enqueue()
  -> PostgreSQL / JSONL 保存投递事实
  -> RabbitMQDeliveryBroker.publish(delivery_id)
  -> DeliveryRuntime.consume()
  -> Channel.send()
  -> ack / retry / dead-letter
```

这里 RabbitMQ 的作用不是保存完整消息，而是唤醒投递 worker，让 worker 根据 `delivery_id` 读取投递事实并发送。

## 4. 入站分区队列

入站链路中，外部消息进入网关后，如果直接同步执行 Agent，会受到模型调用耗时影响。

慢模型调用会导致：

- webhook 处理时间变长。
- 外部平台可能认为请求超时。
- 多条消息堆积在入口。
- 单个慢会话阻塞其他会话。

引入入站任务队列后，链路变为：

```text
Feishu / Webhook / Telegram
  -> ChannelRuntime 接收
  -> 创建 agent_inbound task
  -> PostgreSQL / TaskStore 保存任务事实
  -> RabbitMQInboundTaskBroker.publish(task_id)
  -> TaskWorkerRuntime 消费
  -> Redis session lane ownership
  -> GatewayDispatcher
  -> AgentLoopRunner
```

### 4.1 按 session 分区

当前 `RabbitMQInboundTaskBroker` 支持基于 `session_key` 计算分区：

```text
partition = sha256(session_key) % partitions
```

同一个 session_key 会稳定进入同一个 partition queue：

```text
agent_gateway.inbound.partition.0
agent_gateway.inbound.partition.1
...
agent_gateway.inbound.partition.N
```

这样做的目标是：

- 同一会话尽量落到同一条队列。
- 不同会话可以分散到不同分区并行处理。
- 减少同一会话被多个 worker 同时抢到的概率。
- 为后续 per-session lane 演进打基础。

### 4.2 与 Redis lane 的关系

RabbitMQ 分区不能完全替代 Redis lane。

原因：

- RabbitMQ 分区只能提升“近似顺序”。
- 如果同一队列多个 consumer 或 prefetch 配置不当，仍可能并发。
- 重试、手动补偿、旧 worker 混跑时仍可能出现同 session 并发。

因此当前推荐关系是：

```text
RabbitMQ 分区负责削峰和分发
Redis lane 负责最终互斥保护
PostgreSQL 负责事实状态落库
```

## 5. 为什么选择 RabbitMQ

### 5.1 它适合可靠任务队列

RabbitMQ 天然提供：

- durable queue。
- persistent message。
- consumer ack。
- nack requeue。
- dead-letter exchange。
- prefetch。
- routing key。
- exchange / queue 拓扑。

这些能力非常适合 Gateway 的任务削峰和可靠投递场景。

### 5.2 它适合处理慢外部依赖

Agent 系统里最慢的部分通常是：

- 模型调用。
- 工具调用。
- 飞书 / Telegram API。
- 网络请求。

RabbitMQ 可以把“接收请求”和“慢处理”拆开，让入口快速返回，后台慢慢消费。

### 5.3 它的 ACK 模型适合可靠投递

出站发送需要明确知道：

- 哪些消息已经发送成功。
- 哪些消息需要重试。
- 哪些消息已经失败到不可恢复。

RabbitMQ 的 ack / nack 能力可以表达消费结果。

当前项目中，RabbitMQ 消息被处理成功后 ack；handler 返回失败时 nack requeue；无法解析或不可恢复时进入 dead-letter。

### 5.4 它比 Redis List / Stream 更适合作为任务 broker

Redis 也能做队列，但 RabbitMQ 在可靠消息方面更完整：

- 原生 dead-letter exchange。
- 成熟的 management UI。
- 消费者 ack 语义清晰。
- prefetch 控制成熟。
- exchange / routing key 拓扑表达力强。

Redis 更适合协调，RabbitMQ 更适合消息分发。

## 6. 从什么实现演进而来

### 6.1 第一阶段：同步通道调用

最早可以直接：

```text
Agent 执行完成 -> channel.send()
```

缺点：

- 外部 API 慢会阻塞主流程。
- 失败后缺少统一重试。
- 进程崩溃可能丢消息。

### 6.2 第二阶段：本地预写队列

之后引入本地 DeliveryQueue：

```text
先写本地磁盘 / JSONL
再由后台 runtime 发送
```

解决了：

- 回复先落盘。
- 失败可以重试。
- 出站发送和 Agent 执行解耦。

但问题是：

- 多实例之间队列不共享。
- worker 扩展能力有限。
- 需要主动轮询。
- 无法充分利用 broker 的 ack/nack 和 DLQ。

### 6.3 第三阶段：PostgreSQL 主存储

投递、任务、事件状态外置到 PostgreSQL：

```text
PostgreSQL 保存事实状态
```

解决：

- 多实例共享状态。
- Dashboard 可查询。
- 任务可恢复。
- 状态迁移清晰。

但数据库不适合作为高频唤醒机制。

### 6.4 第四阶段：RabbitMQ 作为 broker

RabbitMQ 接入后：

```text
PostgreSQL 保存事实
RabbitMQ 传递轻量引用
Worker 消费引用后回查 PostgreSQL
```

这样同时具备：

- 可靠状态。
- 异步唤醒。
- 多 worker 扩展。
- 死信治理。
- 削峰能力。

## 7. 高并发与分布式场景中的常见 RabbitMQ 用法

### 7.1 削峰填谷

流量高峰时，消息先进入队列：

```text
突发 1000 条消息 -> RabbitMQ queue -> worker 按能力消费
```

入口不会因为 worker 瞬间处理不过来而崩溃。

### 7.2 异步解耦

把不同耗时组件拆开：

```text
入口接收
任务执行
模型调用
外部投递
```

每一段都可以单独扩容和限流。

### 7.3 消费确认

消费者处理成功才 ack：

```text
success -> basic_ack
temporary failure -> basic_nack(requeue=True)
permanent failure -> basic_nack(requeue=False) / DLQ
```

### 7.4 死信队列

不可恢复消息进入 DLQ：

```text
main queue -> failed -> dead-letter exchange -> dead-letter queue
```

适合后续排查：

- 哪些消息失败。
- 失败原因是什么。
- 是否需要人工重放。

### 7.5 Prefetch 控制

`prefetch_count` 控制单个消费者一次最多拿多少未 ack 消息。

对于需要顺序性的 session 分区队列，通常建议：

```text
prefetch = 1
```

这样可以降低同一分区内乱序和并发风险。

### 7.6 分区队列

按业务 key 分区：

```text
partition = hash(session_key) % N
```

优点：

- 同一 key 稳定进入同一分区。
- 不同 key 可以并行。
- 分区数量控制整体并发上限。

缺点：

- 热点 key 会拖慢所在分区。
- 分区数量变更会导致 hash 重分布。
- 不等于严格 per-session FIFO，仍需要消费约束。

## 8. 为什么不用其他方案

### 8.1 为什么不用 Redis 做队列

Redis Stream 可以做队列，但在这个项目里 RabbitMQ 更合适：

- RabbitMQ 的 ack/nack、DLQ、prefetch 和管理 UI 更成熟。
- RabbitMQ exchange / routing key 更适合表达入站分区和出站投递拓扑。
- Redis 已经承担锁、去重、限流，继续加重队列职责会让边界混乱。

### 8.2 为什么不用 PostgreSQL 轮询做全部任务分发

PostgreSQL 可以保存任务状态，但不适合高频唤醒：

- worker 需要不断 poll。
- 空轮询浪费数据库资源。
- 高并发下容易把任务调度压力压到主库。
- broker 的 ack/nack 和 DLQ 语义需要额外实现。

更合理的方式是：

```text
PostgreSQL 保存任务事实
RabbitMQ 负责任务唤醒和分发
```

### 8.3 为什么不用 Kafka

Kafka 更适合大规模日志流、事件流和高吞吐顺序追加，但当前 Gateway 的场景更需要：

- 任务级 ack。
- 失败重试。
- 死信队列。
- 小规模多 worker 消费。
- 运维简单。

RabbitMQ 对任务队列更直接，复杂度更低。

## 9. 面试追问回答模板

### Q1：为什么引入 RabbitMQ？

可以回答：

> 因为 Agent Gateway 里模型调用和外部通道发送都可能很慢，如果入口同步执行，会导致 webhook 阻塞、消息堆积和失败不可恢复。所以我引入 RabbitMQ，把入站消息和出站投递都变成异步任务，通过队列削峰、ack/nack 确认和死信队列提升可靠性。

### Q2：RabbitMQ 里存完整消息吗？

可以回答：

> 不存完整业务消息。RabbitMQ 只传 `task_id` 或 `delivery_id` 这种轻量引用，完整任务、会话和投递状态保存在 PostgreSQL。这样可以避免 broker 堆积时暴露完整上下文，也能让任务状态查询、重试和恢复统一依赖事实存储。

### Q3：RabbitMQ 和 Redis 分别解决什么？

可以回答：

> RabbitMQ 解决任务怎么排队、怎么分发、怎么 ack/nack 和进入死信。Redis 解决任务是否允许执行，比如同一 session 是否已有 worker 持有 lane、事件是否重复、是否触发限流。RabbitMQ 管“流动”，Redis 管“协调”。

### Q4：怎么保证同一会话顺序？

可以回答：

> RabbitMQ 入站任务按 `session_key` hash 到固定分区，让同一会话尽量进入同一个 partition queue。同时 Redis session lane 作为最终互斥保护，保证同一会话同一时间只有一个 worker 执行。RabbitMQ 分区负责减少乱序，Redis lane 负责防并发。

### Q5：如果 worker 消费失败怎么办？

可以回答：

> 临时失败可以 nack requeue 或写 task retrying 后重新 publish。不可恢复失败会写入任务失败状态，并发送轻量引用到死信队列。这样失败不会丢失，也不会无限阻塞主队列。

## 10. 方案风险与后续优化

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| 队列积压 | worker 处理能力不足 | 增加 worker、分区、限流和告警 |
| 热点分区 | 某个 session 或分区过热 | 增加 per-session lane 或热点拆分策略 |
| 消息重复 | RabbitMQ 至少一次投递 | 依赖 idempotency_key 和 PostgreSQL 状态去重 |
| 消费乱序 | 多 consumer / prefetch 配置不当 | session 分区队列使用 prefetch=1 |
| DLQ 堆积 | 失败未处理 | Dashboard 展示、Runbook、人工重放工具 |
| Broker 不可用 | 入站和出站 broker 中断 | 本地/数据库状态保底，恢复后补偿 publish |

## 11. 简历表述建议

可以写成：

> 引入 RabbitMQ 作为入站分区队列与出站可靠投递层，采用“PostgreSQL 保存任务事实、RabbitMQ 传递轻量引用”的设计，实现任务削峰、异步解耦、ack/nack 确认与死信处理；入站侧按 session_key 分区以提升同会话顺序性，出站侧通过可靠投递队列降低外部通道失败对 Agent 执行链路的影响。

