# RabbitMQ 入站 Session 分区评估

本文档对应 `PROJECT_PLAN.md` 中的 `20.9.7 RabbitMQ session 分区评估`，用于判断 Gateway 是否应把 `agent_inbound` 入站任务从当前 `PostgreSQL/JSONL task + Redis session lock` 模式升级为 RabbitMQ 分区队列。

## 1. 当前结论

当前不建议立刻替换现有入站任务主链路。

推荐路线是：

```text
短期继续使用 PostgreSQL/JSONL task 主存储 + Redis session lock
中期引入 RabbitMQ 作为入站任务分区唤醒层
长期演进到 per-session task lane
```

原因：

- 当前 `20.9.2 ~ 20.9.6` 已经解决同 session 并发执行风险。
- RabbitMQ 分区队列能提升顺序性和 worker 分发效率，但会引入拓扑、重平衡、死信、幂等和消费并发控制复杂度。
- 现有出站 RabbitMQ 实现已经采用“轻量引用 + PostgreSQL 主存储”的模式，入站如果接 RabbitMQ，也应保持同一原则。

## 2. 当前入站链路

当前非 CLI 入站链路：

```text
Feishu / Telegram / Webhook
  -> ChannelRuntime
  -> LocalTaskQueue.enqueue(agent_inbound)
  -> LocalTaskStore / PostgreSQL task 状态
  -> TaskWorkerRuntime.reserve()
  -> AgentInboundTaskHandler
  -> Redis session lock
  -> GatewayDispatcher
  -> AgentLoopRunner
  -> DeliveryQueue
```

当前已经具备：

- `agent_inbound` 任务持久化。
- worker reserve 阶段跳过已锁 session。
- 执行阶段 Redis session lock 互斥。
- 锁 value 使用 `worker_id + task_id`。
- 长任务锁续租。
- runtime status / Dashboard / event stream 可观测。
- 故障注入测试覆盖同 session 抢占、Redis 探测异常、续租失败和事件去重。

## 3. RabbitMQ 分区队列目标

RabbitMQ 入站分区不应该替代任务状态存储，而应该解决两个问题：

1. 更高效地把任务分发到 worker，减少轮询。
2. 让同一 session 固定进入同一分区，提升近似 FIFO 能力。

目标拓扑：

```text
agent_gateway.inbound exchange
  routing_key = inbound.partition.{hash(session_key) % N}

agent_gateway.inbound.partition.0
agent_gateway.inbound.partition.1
...
agent_gateway.inbound.partition.N-1
```

同一 `session_key` 始终路由到同一个 partition queue。

## 4. 推荐消息体

RabbitMQ 消息只携带轻量引用，不携带完整用户消息和上下文。

```json
{
  "task_id": "a1b2c3d4e5f6",
  "task_type": "agent_inbound",
  "session_key": "inbound:feishu:bot-a:user-1",
  "partition": 3,
  "idempotency_key": "feishu:event:xxx",
  "published_at": 1782619200.123
}
```

主数据仍保留在 task store：

- PostgreSQL 是生产主存储。
- JSONL / 本地文件作为开发和迁移兼容层。
- RabbitMQ 消费者拿到 `task_id` 后再从 task store 读取任务。

这样做的好处：

- RabbitMQ 不保存完整消息体，降低消息堆积时的数据暴露和内存压力。
- 任务状态、重试次数、错误原因仍由 task store 统一管理。
- Dashboard 和控制面仍从 task store 看任务状态，不依赖 broker 内部状态。

## 5. 消费模型

建议每个 partition queue 配置：

```text
prefetch_count = 1
单 partition 单 active consumer
```

如果一个 partition 有多个 consumer 并发消费，即使 RabbitMQ 队列本身 FIFO，也可能因为不同任务处理耗时不同而破坏 session 顺序。

更稳妥的模型：

```text
worker 进程可以绑定多个 partition
每个 partition 内部串行消费
不同 partition 并行执行
```

示例：

```text
worker-a: partition 0, 1
worker-b: partition 2, 3
worker-c: partition 4, 5
```

## 6. 与 Redis session lock 的关系

即使引入 RabbitMQ 分区，也不建议马上移除 Redis session lock。

原因：

- 部署初期可能存在旧 worker 和新 worker 混跑。
- 重投递、手动 retry、分区调整期间仍可能出现同 session 并发风险。
- RabbitMQ 的顺序依赖消费参数，如果误配 `prefetch > 1` 或同队列多 consumer，仍可能破坏互斥。

推荐策略：

```text
RabbitMQ 分区负责减少乱序和减少抢锁
Redis session lock 继续作为最后一道互斥保护
```

等分区消费模型稳定后，再评估是否降低 Redis 锁依赖。

## 7. 失败与重试策略

### 消费成功

```text
1. RabbitMQ consumer 收到 task_id
2. 从 task store reserve 指定 task
3. 执行 AgentInboundTaskHandler
4. task 状态写 done / failed / retrying
5. RabbitMQ ack
```

### 可重试失败

建议：

```text
task store 写 retrying + next_retry_at
RabbitMQ ack 当前消息
由 retry scheduler 重新 publish
```

不建议直接 `basic_nack(requeue=True)` 无限回队头，否则热点失败任务会阻塞同 partition 后续任务。

### 不可恢复失败

建议：

```text
task store 写 failed
RabbitMQ ack
必要时 publish lightweight dead-letter reference
```

## 8. 分区数量选择

初始建议：

```text
GATEWAY_INBOUND_RABBITMQ_PARTITIONS=8
```

选择依据：

- 当前个人/小规模部署不需要太多分区。
- 8 个分区足以让不同 session 并行。
- 后续可扩到 16 或 32，但扩容会涉及 hash 重分布。

分区数量一旦上线，不建议频繁变化。否则同一个 session 的新旧消息可能落到不同分区，破坏顺序假设。

## 9. 配置建议

未来如果实现，建议新增配置：

```env
GATEWAY_INBOUND_BROKER=none
GATEWAY_INBOUND_RABBITMQ_URL=amqp://admin:admin123@rabbitmq:5672/
GATEWAY_INBOUND_RABBITMQ_EXCHANGE=agent_gateway.inbound
GATEWAY_INBOUND_RABBITMQ_QUEUE_PREFIX=agent_gateway.inbound.partition
GATEWAY_INBOUND_RABBITMQ_PARTITIONS=8
GATEWAY_INBOUND_RABBITMQ_PREFETCH=1
GATEWAY_INBOUND_RABBITMQ_ENABLED=false
```

注意：

- 不复用出站 delivery exchange，避免入站和出站队列语义混淆。
- 入站 broker 开关应独立于 `GATEWAY_DELIVERY_BROKER`。
- 默认关闭，先灰度。

## 10. 推荐实现步骤

### 10.1 定义 broker 协议

新增 `InboundTaskBroker` 抽象：

```python
class InboundTaskBroker:
    def publish(task: TaskInstance) -> None: ...
    def consume_once(partition: int, handler) -> bool: ...
    def stats() -> dict: ...
```

### 10.2 实现 RabbitMQ 入站 broker

新增类似 `RabbitMQDeliveryBroker` 的实现，但使用独立 exchange 和 partition queue。

关键点：

- 发布轻量 task reference。
- routing key 基于 `hash(session_key) % partitions`。
- durable exchange / queue。
- `prefetch_count=1`。
- DLQ 独立配置。

### 10.3 接入 enqueue

`LocalTaskQueue.enqueue()` 成功写入 task store 后，再 publish 到 inbound broker。

失败策略：

- task store 写入成功但 broker publish 失败时，任务仍保留 pending。
- worker 仍可通过现有 polling 兜底消费。
- event stream 记录 broker publish failed。

### 10.4 接入 worker

新增 worker 模式：

```text
polling mode: 当前 reserve 轮询
broker mode: RabbitMQ consume_once + task_id reserve
hybrid mode: 优先 broker，无消息时 polling 兜底
```

推荐先做 hybrid mode。

### 10.5 增加观测

Dashboard 展示：

- inbound broker 是否启用。
- partition 数量。
- 每个 partition ready / unacked。
- oldest task age。
- DLQ 数量。
- fallback polling 次数。

## 11. 风险点

| 风险 | 说明 | 缓解 |
| --- | --- | --- |
| 分区热点 | 某个 session 消息很多，会拖慢所在 partition | 热点 session 单独 lane / 优先级治理 |
| 扩容重分布 | partition 数变化导致同 session 路由变化 | 分区数上线后固定，必要时做迁移窗口 |
| 队头阻塞 | 失败任务反复 requeue 阻塞后续任务 | 当前消息 ack，依赖 task retry scheduler 重发 |
| 误配置并发 | 同一 partition 多 consumer 或 prefetch > 1 破坏顺序 | 默认 prefetch=1，控制面健康检查告警 |
| broker 与 task store 不一致 | task 已写入但消息未发布，或消息存在但 task 不存在 | polling fallback，消费者跳过缺失 task |
| 复杂度增加 | 入站链路从 task store 变成 task store + broker | 先 hybrid mode，保留现有路径 |

## 12. 最终评估

当前阶段结论：

```text
RabbitMQ session 分区值得作为中期演进方向，但不应立即替换现有 Redis lock + PostgreSQL task 链路。
```

建议进入下一阶段前先满足：

- 当前 Redis lock 路径稳定运行一段时间。
- Dashboard 能证明瓶颈来自 task polling 或 session 锁冲突。
- 实际部署出现多 worker 扩展需求。
- 压测显示 broker 唤醒能明显降低延迟或提高吞吐。

因此 `20.9.7` 的产出是：

```text
保留现有实现。
明确 RabbitMQ 入站分区方案。
后续如进入实现，采用 lightweight reference + PostgreSQL task source of truth + hybrid worker。
```
