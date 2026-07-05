# 键名与 ID 命名规范

本文档统计 Gateway 中已经设计和使用的关键 ID、key、token、queue 名称。它们分布在会话路由、入站调度、Redis lane、RabbitMQ、投递队列、运行事件和 Cron 幂等控制中，用于保证多通道、多 worker、多实例运行时的可追踪、可去重和可恢复。

## 总览

| 名称 | 格式 | 示例 | 主要用途 |
| --- | --- | --- | --- |
| `task_id` | `uuid4.hex[:16]` | `ed9c96a187c546c2` | 后台任务主键，RabbitMQ 入站消息只传轻量 task 引用。 |
| `delivery_id` | `uuid4.hex[:12]` | `a8ffdd16aeb7` | 出站投递主键，用于可靠投递、重试和控制面查询。 |
| `owner_token` | `{worker_id}:{task_id}` | `local-worker:ed9c96a187c546c2` | Redis lane owner 身份，释放和续租时必须匹配。 |
| `session_key` | `agent:{agent_id}:...` | `agent:main:feishu:feishu-main:direct:ou_xxx` | 会话隔离键，用于上下文、任务和 lane 串行。 |
| `preroute_lane_key` | `inbound:{channel}:{account_id}:{peer_id}` | `inbound:feishu:feishu-main:ou_xxx` | 路由前入站粗分组，避免未知 Agent 阶段消息乱序。 |
| `inbound_lane_key` | `agent:{agent_id}:session:{session_key}` | `agent:main:session:agent:main:direct:ou_xxx` | 路由后入站 lane key，保证同 Agent 会话串行。 |
| `redis_lane_key` | `{namespace}:{session_key}` | `gateway:lock:agent_inbound:inbound:feishu:feishu-main:ou_xxx` | Redis 中实际持有的 session lane key。 |
| `session_ready_index` | `{namespace}:sessions:ready` | `gateway:tasks:sessions:ready` | Redis session scheduler 的全局可运行 session 索引。 |
| `session_pending_bucket` | `{namespace}:session:{session_key}:pending` | `gateway:tasks:session:agent:main:...:pending` | Redis session scheduler 中某个 session 的待执行任务队列。 |
| `session_busy_owner` | `{namespace}:session:{session_key}:busy` | `gateway:tasks:session:agent:main:...:busy` | Redis session scheduler 中某个 session 当前队首任务的 busy owner。 |
| `delivery_idempotency_key` | 显式 key 或 `sha256(seed)` | `d4e5...` | 出站投递幂等，避免同一回复重复发送。 |
| `cron_idempotency_key` | `gateway:cron:{job_id}:{schedule_slot}` | `gateway:cron:system-ping:2026-07-02T10:00:00+00:00` | Cron 分布式幂等，避免多实例重复触发。 |
| `webhook_event_dedup_key` | `gateway:webhook:event:{account_id}:{event_id}` | `gateway:webhook:event:feishu-main:evt-redis-dedup-1` | 通用 Webhook 事件去重，覆盖飞书、企业微信等平台重复回调。 |
| `correlation_id` | `{prefix}_{uuid4.hex[:16]}` | `evt_a1b2c3d4e5f60708` | 串联一次入站、Agent、工具、投递和事件链路。 |
| `rabbitmq_delivery_queue` | 配置值 | `agent_gateway.delivery.outbound` | 出站投递 RabbitMQ 主队列。 |
| `rabbitmq_inbound_partition_queue` | `{queue_prefix}.{partition}` | `agent_gateway.inbound.partition.3` | 入站任务按 session 分区后的 RabbitMQ 队列。 |

## 任务 ID

后台任务由 `TaskInstance.create()` 创建，主键格式是：

```text
task_id = uuid.uuid4().hex[:16]
```

示例：

```text
ed9c96a187c546c2
```

`task_id` 是任务事实表、任务本地文件、RabbitMQ 入站轻量消息、worker 生命周期事件之间的核心关联字段。RabbitMQ 入站消息不携带完整用户消息，只携带 `task_id`，worker 消费后再回到 TaskStore / PostgreSQL 校验状态并预占任务。

## 投递 ID

出站投递由 `DeliveryQueue.enqueue()` 创建，主键格式是：

```text
delivery_id = uuid.uuid4().hex[:12]
```

示例：

```text
a8ffdd16aeb7
```

`delivery_id` 用于可靠投递链路。Agent 回复不会直接调用通道发送，而是先进入 DeliveryQueue，再由 RabbitMQ 或本地投递 runtime 处理。RabbitMQ 出站消息也只携带轻量 `delivery_id`，完整投递内容仍保存在 PostgreSQL / 本地队列中。

## Owner Token

`owner_token` 定义在 `LaneOwnerToken.value`，格式是：

```text
owner_token = {worker_id}:{task_id}
```

示例：

```text
local-worker:ed9c96a187c546c2
gateway-worker-1:ed9c96a187c546c2
```

它用于标识当前 session lane 的持有者。Redis lane 的释放、续租和替换都必须校验当前 value，避免旧 worker 卡顿恢复后误删新 worker 的锁。

Redis lane value 当前默认写入 JSON metadata，但 `owner_token` 仍然保留字符串格式，便于错误信息、历史兼容和 Dashboard 展示。典型 metadata 包含：

```json
{
  "version": 1,
  "session_key": "inbound:feishu:feishu-main:ou_xxx",
  "lane_key": "gateway:lock:agent_inbound:inbound:feishu:feishu-main:ou_xxx",
  "worker_id": "gateway-worker-1",
  "task_id": "ed9c96a187c546c2",
  "owner_token": "gateway-worker-1:ed9c96a187c546c2",
  "acquired_at": 1782900000.123,
  "renewed_at": 1782900030.456
}
```

## Session Key

`session_key` 由 `build_session_key()` 生成，负责定义会话隔离边界。不同 `dm_scope` 会生成不同粒度的 key。

按账号、通道和 peer 隔离：

```text
agent:{agent_id}:{channel}:{account_id}:direct:{peer_id}
```

示例：

```text
agent:main:feishu:feishu-main:direct:ou_7d7ecc3911714f2cf6905b0d7215ca5e
```

按通道和 peer 隔离：

```text
agent:{agent_id}:{channel}:direct:{peer_id}
```

只按 peer 隔离：

```text
agent:{agent_id}:direct:{peer_id}
```

没有 peer 时回退到主会话：

```text
agent:{agent_id}:main
```

`session_key` 是上下文重放、会话持久化、入站任务、lane ownership 和 Dashboard 追踪的关键字段。

## 入站 Lane Key

入站 lane 有两个阶段。

路由前，`ChannelRuntime` 还不知道目标 Agent 和最终 session，因此使用 `build_preroute_lane_key()` 生成临时 lane：

```text
inbound:{channel}:{account_id}:{peer_id}
```

示例：

```text
inbound:feishu:feishu-main:ou_7d7ecc3911714f2cf6905b0d7215ca5e
```

路由后，如果已经得到 `agent_id` 和 `session_key`，则使用 `build_inbound_lane_key()`：

```text
agent:{agent_id}:session:{session_key}
```

示例：

```text
agent:main:session:agent:main:feishu:feishu-main:direct:ou_7d7ecc3911714f2cf6905b0d7215ca5e
```

路由前 key 解决“入口阶段粗串行”，路由后 key 解决“Agent 会话严格串行”。

## Redis Lane Key

Redis 中实际使用的 lane key 由 `RedisLaneCoordinator.lane_key()` 生成：

```text
redis_lane_key = {namespace}:{session_key}
```

默认 `agent_inbound` 命名空间：

```text
gateway:lock:agent_inbound
```

示例：

```text
gateway:lock:agent_inbound:inbound:feishu:feishu-main:ou_7d7ecc3911714f2cf6905b0d7215ca5e
```

压测和 smoke 场景会使用独立命名空间，例如：

```text
gateway:smoke:lane:{session_key}
gateway:load-test:lane:{session_key}
```

这个 key 的作用是跨 worker、跨实例保证同一个 session 同一时间只有一个 owner。

## Redis Session Scheduler Key

Redis session scheduler 使用独立命名空间，默认是：

```text
gateway:tasks
```

全局 ready index 保存“当前可以被 worker claim 的 session”，而不是保存任务 ID：

```text
gateway:tasks:sessions:ready
```

每个 session 有自己的 pending bucket，里面保存轻量任务引用：

```text
gateway:tasks:session:{session_key}:pending
```

示例：

```text
gateway:tasks:session:agent:main:feishu:feishu-main:direct:ou_xxx:pending
```

pending bucket 中的元素格式是：

```text
{task_id}|{task_type}
```

示例：

```text
ed9c96a187c546c2|agent_inbound
```

某个 session 正在执行队首任务时，会写入 busy owner：

```text
gateway:tasks:session:{session_key}:busy
```

busy owner value 是 JSON，至少包含：

```json
{
  "version": 1,
  "worker_id": "gateway-worker-1",
  "task_id": "ed9c96a187c546c2",
  "session_key": "agent:main:feishu:feishu-main:direct:ou_xxx",
  "acquired_at": 1782900000.123,
  "renewed_at": 1782900000.123
}
```

`release` 和 `renew` 必须校验完整 owner value，不能只按 key 删除。这样可以避免旧 worker 卡顿恢复后释放新 worker 的 busy owner。

调度语义如下：A session 被 claim 后，A2/A3 留在 A 的 pending bucket，A 不会再次进入 ready index；B/C session 如果不 busy，仍然可以进入 ready index 并被其他 worker 执行。A1 完成并 release 后，如果 A 的 pending bucket 非空，A 才重新进入 ready index。

如果 Redis 调度索引丢失，可以通过控制面 `tasks.scheduler.rebuild` 从 `pending/retrying` 任务事实状态重建这些 key。重建只恢复调度引用，不改变 TaskStore / PostgreSQL 中的任务事实状态，也不会重新执行已完成任务。

## 投递幂等 Key

出站投递幂等 key 由 `DeliveryQueue._build_idempotency_key()` 生成。上游显式传入 `metadata.idempotency_key` 时优先使用；没有显式 key 时，系统基于以下 seed 计算 SHA-256：

```json
{
  "channel": "feishu",
  "to": "ou_xxx",
  "text": "回复内容",
  "kind": "agent_reply",
  "correlation_id": "evt_a1b2c3d4e5f60708"
}
```

最终格式：

```text
sha256(json_seed)
```

示例：

```text
4d967d2c7e4d6bb8f63cb0f6c6b7d4c9f7f3ef6e6a4b7f8a9d2a3e4c5f607182
```

它用于避免同一条回复被重复入队或重复发送。需要强制重复发送时，可以在 metadata 中设置 `force_delivery`。

## Cron 幂等 Key

Cron 使用 Redis 做分布式幂等，key 格式是：

```text
gateway:cron:{safe_job_id}:{schedule_slot}
```

`safe_job_id` 会把 job id 里的空格替换为 `_`。示例：

```text
gateway:cron:system-ping:2026-07-02T10:00:00+00:00
gateway:cron:health-check:2026-07-02T12:00:00+00:00
```

这个 key 用于避免多实例 scheduler 在同一个计划时间重复触发同一个 cron job。

## Webhook 事件去重 Key

飞书、企业微信等 Webhook 事件会经过 Redis 去重，测试中可见 key 格式：

```text
gateway:webhook:event:{account_id}:{event_id}
```

示例：

```text
gateway:webhook:event:feishu-main:evt-redis-dedup-1
```

这个 key 用于处理平台 webhook 重试、长连接重连、重复推送等场景。首次 `SET NX EX` 成功时才处理事件，重复事件会被忽略。

## Correlation ID

运行事件使用 `correlation_id` 串起一次完整链路，格式是：

```text
{prefix}_{uuid4.hex[:16]}
```

默认前缀是 `evt`，示例：

```text
evt_a1b2c3d4e5f60708
feishu_1a2b3c4d5e6f7081
```

生成时会清理 prefix，只保留小写字母、数字、`-` 和 `_`。`correlation_id` 会被写入运行事件、投递 metadata 和部分 broker payload，用于从 Dashboard 中追踪一次入站到出站的完整链路。

## RabbitMQ 队列名

出站投递默认配置：

```text
exchange = agent_gateway.delivery
queue = agent_gateway.delivery.outbound
dead_letter_exchange = agent_gateway.delivery.dlx
dead_letter_queue = agent_gateway.delivery.dead
```

入站任务默认配置：

```text
exchange = agent_gateway.inbound
queue_prefix = agent_gateway.inbound.partition
dead_letter_exchange = agent_gateway.inbound.dlx
dead_letter_queue = agent_gateway.inbound.dead
```

入站分区队列名由 `RabbitMQInboundTaskBroker.queue_name()` 生成：

```text
{queue_prefix}.{partition}
```

示例：

```text
agent_gateway.inbound.partition.0
agent_gateway.inbound.partition.1
agent_gateway.inbound.partition.7
```

分区号由 `partition_for(session_key)` 计算：

```text
partition = int.from_bytes(sha256(session_key)[:8], "big") % partitions
```

这个规则保证同一个 `session_key` 在分区数量不变时总是进入同一个 RabbitMQ partition queue。

## 命名设计原则

这些 key 的设计遵循三个原则。第一，能从名称看出作用域，例如 `gateway:cron:*` 用于 Cron 幂等，`gateway:lock:agent_inbound:*` 用于入站 session lane。第二，跨系统传递时只传轻量引用，例如 RabbitMQ 只传 `task_id` 或 `delivery_id`，完整事实仍在 PostgreSQL / 本地状态层。第三，涉及分布式 ownership 的 key 必须包含可校验 token，例如 `owner_token={worker_id}:{task_id}`，释放和续租时必须匹配，不能只靠 key 存在与否判断执行权。
