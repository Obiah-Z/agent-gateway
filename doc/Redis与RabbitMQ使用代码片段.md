# Redis 与 RabbitMQ 使用代码片段

本文整理系统中 Redis 和 RabbitMQ 的核心使用位置，用于快速理解中间件在 Gateway 中承担的职责。

## 总览

Redis 在系统中主要用于轻量协调能力：事件去重、幂等抢占、分布式锁、锁续租、固定窗口限流和健康检查。它不作为业务数据主存储。

RabbitMQ 在系统中主要用于任务和投递的分布式分发：入站任务 broker、出站投递 broker、分区队列、ACK / NACK / 重入队、死信队列和队列深度观测。RabbitMQ 消息只保存轻量引用，业务状态仍回到 TaskStore / PostgreSQL / 本地 JSONL 中确认。

## 配置入口

源码位置：[agent_gateway/config.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/config.py:62)

```python
redis_enabled: bool = True
redis_url: str = "redis://127.0.0.1:6379/0"
redis_socket_timeout_seconds: float = 1.0
redis_cron_rate_limit_per_minute: int = 0

delivery_broker: str = "rabbitmq"
rabbitmq_url: str = "amqp://admin:admin123@127.0.0.1:5672/"
rabbitmq_exchange: str = "agent_gateway.delivery"
rabbitmq_queue: str = "agent_gateway.delivery.outbound"
rabbitmq_dead_letter_exchange: str = "agent_gateway.delivery.dlx"
rabbitmq_dead_letter_queue: str = "agent_gateway.delivery.dead"

inbound_broker: str = "rabbitmq"
inbound_rabbitmq_url: str = "amqp://admin:admin123@127.0.0.1:5672/"
inbound_rabbitmq_exchange: str = "agent_gateway.inbound"
inbound_rabbitmq_queue_prefix: str = "agent_gateway.inbound.partition"
inbound_rabbitmq_partitions: int = 8
inbound_rabbitmq_prefetch: int = 1
session_ready_scheduler_enabled: bool = True
session_ready_scheduler_namespace: str = "gateway:tasks"
```

运行时从环境变量读取这些配置。当前默认生产形态使用 Redis、PostgreSQL 和 RabbitMQ；如果需要本地降级，可以显式设置 `GATEWAY_REDIS_ENABLED=false`、`GATEWAY_DELIVERY_BROKER=none`、`GATEWAY_INBOUND_BROKER=none`、`GATEWAY_SESSION_READY_SCHEDULER_ENABLED=false`。

源码位置：[agent_gateway/config.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/config.py:201)

```python
redis_enabled=env_bool("GATEWAY_REDIS_ENABLED", True)
redis_url=os.getenv("GATEWAY_REDIS_URL", "redis://127.0.0.1:6379/0")
delivery_broker=os.getenv("GATEWAY_DELIVERY_BROKER", "rabbitmq").strip().lower() or "rabbitmq"
inbound_broker=os.getenv("GATEWAY_INBOUND_BROKER", "rabbitmq").strip().lower() or "rabbitmq"
session_ready_scheduler_enabled=env_bool("GATEWAY_SESSION_READY_SCHEDULER_ENABLED", True)
```

## 应用装配

源码位置：[agent_gateway/app.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/app.py:211)

```python
redis_client = RedisClient(
    enabled=settings.redis_enabled,
    url=settings.redis_url,
    socket_timeout_seconds=settings.redis_socket_timeout_seconds,
)
```

出站投递 broker 在 `GATEWAY_DELIVERY_BROKER=rabbitmq` 时接入 `DeliveryQueue`。

源码位置：[agent_gateway/app.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/app.py:301)

```python
delivery_queue = DeliveryQueue(settings.delivery_queue_dir)
delivery_queue.read_backend = state_bundle.read if hasattr(state_bundle.read, "list") else None
delivery_queue.write_backend = primary_write
if settings.delivery_broker == "rabbitmq":
    delivery_queue.broker = RabbitMQDeliveryBroker(
        url=settings.rabbitmq_url,
        exchange=settings.rabbitmq_exchange,
        queue=settings.rabbitmq_queue,
        dead_letter_exchange=settings.rabbitmq_dead_letter_exchange,
        dead_letter_queue=settings.rabbitmq_dead_letter_queue,
        connect_timeout_seconds=settings.rabbitmq_connect_timeout_seconds,
        enabled=True,
    )
```

入站任务 broker 在 `GATEWAY_INBOUND_BROKER=rabbitmq` 时接入 `LocalTaskQueue`。如果同时启用 `GATEWAY_SESSION_READY_SCHEDULER_ENABLED=true`，应用会创建 `RedisSessionReadyScheduler`，RabbitMQ 消息只负责唤醒 worker，真正执行顺序由 Redis 调度索引决定。

源码位置：[agent_gateway/app.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/app.py:314)

```python
task_broker = None
if settings.inbound_broker == "rabbitmq":
    task_broker = RabbitMQInboundTaskBroker(
        url=settings.inbound_rabbitmq_url,
        exchange=settings.inbound_rabbitmq_exchange,
        queue_prefix=settings.inbound_rabbitmq_queue_prefix,
        dead_letter_exchange=settings.inbound_rabbitmq_dead_letter_exchange,
        dead_letter_queue=settings.inbound_rabbitmq_dead_letter_queue,
        partitions=settings.inbound_rabbitmq_partitions,
        prefetch=settings.inbound_rabbitmq_prefetch,
        connect_timeout_seconds=settings.inbound_rabbitmq_connect_timeout_seconds,
        enabled=True,
    )
session_scheduler = None
if settings.session_ready_scheduler_enabled and redis_client.enabled:
    session_scheduler = RedisSessionReadyScheduler(
        redis_client,
        namespace=settings.session_ready_scheduler_namespace,
        default_ttl_seconds=settings.inbound_session_lock_ttl_seconds,
    )
task_queue = LocalTaskQueue(task_store, broker=task_broker, session_scheduler=session_scheduler)
```

## Redis：基础封装

源码位置：[agent_gateway/runtime/infra/redis_client.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/redis_client.py:52)

`RedisClient` 是系统对 Redis 的薄封装。它提供健康检查、一次性标记、分布式锁、锁续租、锁释放、读取 value 和固定窗口限流。

```python
class RedisClient:
    def health(self) -> RedisHealth:
        if not self.enabled:
            return RedisHealth(enabled=False, ok=True, url=self.url)
        start = time.perf_counter()
        self._get_client().ping()
        latency_ms = (time.perf_counter() - start) * 1000.0
        return RedisHealth(enabled=True, ok=True, url=self.url, latency_ms=round(latency_ms, 3))
```

一次性标记用于幂等和去重，底层使用 Redis `SET NX EX`。

源码位置：[agent_gateway/runtime/infra/redis_client.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/redis_client.py:94)

```python
def mark_once(self, key: str, *, ttl_seconds: int, value: str = "1") -> bool:
    if not self.enabled:
        return True
    return bool(
        self._get_client().set(
            key,
            value,
            nx=True,
            ex=max(1, ttl_seconds),
        )
    )
```

分布式锁也基于 `SET NX EX`，并用 Lua 脚本保证释放和续租时必须匹配 owner token，避免误删或误续租其他 worker 的锁。

源码位置：[agent_gateway/runtime/infra/redis_client.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/redis_client.py:110)

```python
def acquire_lock(self, key: str, *, value: str, ttl_seconds: int) -> bool:
    return bool(
        self._get_client().set(
            key,
            value,
            nx=True,
            ex=max(1, ttl_seconds),
        )
    )

def release_lock(self, key: str, *, value: str) -> bool:
    script = """
    if redis.call("GET", KEYS[1]) == ARGV[1] then
        return redis.call("DEL", KEYS[1])
    end
    return 0
    """
    return bool(self._get_client().eval(script, 1, key, value))
```

固定窗口限流使用 `INCR + EXPIRE`。

源码位置：[agent_gateway/runtime/infra/redis_client.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/redis_client.py:214)

```python
def check_fixed_window_rate_limit(self, key_prefix: str, *, limit: int, window_seconds: int, now: float | None = None) -> RedisRateLimitResult:
    window_id = int(current // safe_window)
    key = f"{key_prefix.rstrip(':')}:{window_id}"
    client = self._get_client()
    count = int(client.incr(key))
    if count == 1:
        client.expire(key, safe_window + 1)
    return RedisRateLimitResult(allowed=count <= safe_limit, key=key, limit=safe_limit, count=count, window_seconds=safe_window)
```

## Redis：Webhook 事件去重

源码位置：[agent_gateway/gateways/feishu/security.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/gateways/feishu/security.py:85)

飞书、企业微信等 Webhook 事件都可能因为平台重试、长连接恢复、Webhook 重放而重复到达。这里用 Redis 记录通用事件去重 key，只有第一次写入成功才继续处理。

```python
class RedisWebhookEventDeduplicator:
    """基于 Redis `SET NX EX` 的通用 Webhook 事件去重器。"""

    def mark_if_new(self, event_id: str, *, now: float | None = None) -> bool:
        if not event_id:
            return True
        client = self.redis_client._get_client()
        return bool(
            client.set(
                f"{self.key_prefix}:{event_id}",
                "1",
                nx=True,
                ex=self.ttl_seconds,
            )
        )
```

Redis 异常时会回退到本地去重器，保证 Webhook 入口不因 Redis 短暂不可用而完全不可用。

源码位置：[agent_gateway/gateways/feishu/security.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/gateways/feishu/security.py:142)

```python
class FallbackWebhookEventDeduplicator:
    def mark_if_new(self, event_id: str, *, now: float | None = None) -> bool:
        try:
            return bool(self.primary.mark_if_new(event_id, now=now))
        except Exception:
            return self.fallback.mark_if_new(event_id, now=now)
```

## Redis：Cron 幂等与限流

源码位置：[agent_gateway/runtime/execution/autonomy.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/execution/autonomy.py:952)

多实例部署时，多个 scheduler 可能同时看到同一个 Cron 到期。系统用 Redis `mark_once` 抢占计划时间窗口，避免同一个 Cron 在同一窗口重复触发。

```python
def _claim_scheduled_run(self, job: CronJob) -> bool:
    if self.redis_client is None or not getattr(self.redis_client, "enabled", False):
        return True
    try:
        return bool(
            self.redis_client.mark_once(
                self._cron_idempotency_key(job),
                ttl_seconds=self._cron_idempotency_ttl(job),
            )
        )
    except Exception:
        return True
```

Cron 自动调度还可以启用跨实例固定窗口限流。

源码位置：[agent_gateway/runtime/execution/autonomy.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/execution/autonomy.py:989)

```python
return self.redis_client.check_fixed_window_rate_limit(
    "gateway:rate:cron",
    limit=limit,
    window_seconds=60,
    now=now,
).to_dict()
```

## Redis：Session Lane 分布式互斥

源码位置：[agent_gateway/runtime/tasks/lane.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/lane.py:76)

`RedisLaneCoordinator` 把普通 Redis 锁提升为 session lane ownership。目标是同一个 session 同一时间只能由一个 worker 执行，避免同一会话并发导致上下文乱序或重复回复。

```python
class RedisLaneCoordinator:
    """基于 Redis 的 session lane ownership 协调器。"""

    def lane_key(self, session_key: str) -> str:
        safe_session = session_key.strip()
        if not safe_session:
            return ""
        return f"{self.namespace}:{safe_session}"

    def acquire(self, session_key: str, *, owner: LaneOwnerToken, ttl_seconds: int, now: float | None = None) -> LaneOwnership | None:
        lane_key = self.lane_key(session_key)
        owner_value = self._encode_owner(...)
        acquired = self.redis_client.acquire_lock(
            lane_key,
            value=owner_value,
            ttl_seconds=max(1, int(ttl_seconds)),
        )
        if not acquired:
            return None
```

## Redis：Session Ready Scheduler

源码位置：[agent_gateway/runtime/tasks/session_scheduler.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/session_scheduler.py:39)

`RedisSessionReadyScheduler` 解决的是“严格顺序调度”问题。它不是业务数据主存储，任务事实状态仍在 TaskStore / PostgreSQL；Redis 只保存热路径索引。

```python
class RedisSessionReadyScheduler:
    """基于 Redis 的 per-session FIFO 调度索引。

    PostgreSQL / TaskStore 仍是任务事实状态；Redis 只保存热路径调度索引：
    每个 session 一个 pending bucket，全局 ready index 只保存“可运行 session”。
    """
```

核心数据结构是三个 Redis key：

```text
gateway:tasks:sessions:ready
gateway:tasks:session:{session_key}:pending
gateway:tasks:session:{session_key}:busy
```

入队时，任务引用会被写入对应 session 的 pending bucket；如果该 session 当前没有 busy owner，则 session key 被放入全局 ready index。

源码位置：[agent_gateway/runtime/tasks/session_scheduler.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/session_scheduler.py:158)

```python
def enqueue(self, task: TaskInstance) -> bool:
    item = self._encode_task_ref(task)
    pending_key = self.pending_key(task.session_key)
    existing = client.lrange(pending_key, 0, -1)
    if item not in set(str(value) for value in existing):
        client.rpush(pending_key, item)
    if task.session_key not in ready_items and not client.exists(self.busy_key(task.session_key)):
        client.rpush(self.ready_key, task.session_key)
    return True
```

claim 阶段通过 Lua 脚本原子完成：从 ready index 取一个 session，检查 busy owner，只弹出该 session 的队首任务，并写入带 TTL 的 busy owner。这样 A session 正在执行时，A2/A3 留在 A 的 pending bucket，B/C session 仍可以继续被其他 worker claim。

源码位置：[agent_gateway/runtime/tasks/session_scheduler.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/session_scheduler.py:177)

```python
def claim_next(self, *, worker_id: str, task_types: Iterable[str] | None = None, ...):
    result = self.redis_client._get_client().eval(
        self.CLAIM_SCRIPT,
        1,
        self.ready_key,
        self.namespace,
        worker_id,
        str(ttl),
        str(current),
        str(max_scan),
        allowed,
    )
```

release 阶段必须校验 owner value。只有当前 owner 匹配时才删除 busy key；如果 pending bucket 仍有任务，再把 session 放回 ready index。

源码位置：[agent_gateway/runtime/tasks/session_scheduler.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/session_scheduler.py:212)

```python
def release(self, claim: SessionTaskClaim) -> bool:
    return bool(
        self.redis_client._get_client().eval(
            self.RELEASE_SCRIPT,
            3,
            self.ready_key,
            claim.busy_key,
            claim.pending_key,
            claim.session_key,
            claim.owner_value,
        )
    )
```

worker 执行 scheduler claim 任务时会启动 watchdog 续租协程。续租成功记录 `task.scheduler.renewed`，续租失败记录 `task.scheduler.renew_failed`，但不会直接中断已经在执行的模型调用或工具调用。

一句话总结：后台协程续期保租约不过期，主协程跑业务；跑完后先停续期协程并确认退出，最后释放租约——整套流程保证分布式任务不重复执行且不泄漏资源。

源码位置：[agent_gateway/runtime/tasks/worker.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/worker.py:351)

```python
async def _execute_claimed_task(self, task: TaskInstance, claim: Any) -> None:
    renew_task = asyncio.create_task(
        self._renew_session_claim_until_cancelled(task, claim),
        name=f"task-session-claim-renew:{task.id}",
    )
    try:
        await self._execute(task)
    finally:
        renew_task.cancel()
        await renew_task
        self.queue.release_session_claim(claim)
```

控制面在 `runtime.status` 中暴露 session scheduler 快照，用于 Dashboard 或排障脚本判断调度索引是否启用、当前 ready session 数量和命名空间。

源码位置：[agent_gateway/runtime/execution/control_plane.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/execution/control_plane.py:1483)

```python
if tasks.get("configured"):
    tasks["persisted_lanes"] = self._session_lane_status()
    tasks["session_scheduler"] = self._session_scheduler_status()
```

控制面还提供独立的 scheduler 状态和重建入口。`tasks.scheduler.status` 返回 ready session、pending bucket 和 busy owner 明细；`tasks.scheduler.rebuild` 从 `pending/retrying` 任务事实状态重建 Redis 索引。

源码位置：[agent_gateway/runtime/execution/control_plane.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/execution/control_plane.py:401)

```python
def session_scheduler_status(self, *, detail: bool = True, limit: int = 20) -> dict[str, Any]:
    return self._session_scheduler_status(detail=detail, limit=limit)

def rebuild_session_scheduler(self, *, limit: int = 5000) -> dict[str, Any]:
    queue = self._require_task_queue()
    tasks = queue.store.list(statuses=["pending", "retrying"], limit=safe_limit)
    rebuilt = int(scheduler.rebuild(tasks))
    return {"ok": True, "rebuilt": rebuilt, "scheduler": self._session_scheduler_status(detail=True)}
```

WebSocket JSON-RPC 方法：

```text
tasks.scheduler.status
tasks.scheduler.rebuild
```

Dashboard 的后台任务栏会展示会话调度器状态，并提供“从任务状态重建 Redis 调度索引”按钮。这个操作只重建调度引用，不会重新执行任务。

入站 Agent 任务处理器会创建这个 coordinator。

源码位置：[agent_gateway/runtime/tasks/handlers.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/handlers.py:56)

```python
class AgentInboundTaskHandler:
    def __init__(..., redis_client: RedisClient | None = None, ...):
        self.redis_client = redis_client
        self.lane_coordinator = lane_coordinator or RedisLaneCoordinator(
            redis_client,
            namespace="gateway:lock:agent_inbound",
            state_repository=state_repository,
        )
```

锁 key 格式如下。

源码位置：[agent_gateway/runtime/tasks/handlers.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/handlers.py:180)

```python
@staticmethod
def _lock_key(task: TaskInstance) -> str:
    session_key = task.session_key.strip()
    if not session_key:
        return ""
    return f"gateway:lock:agent_inbound:{session_key}"
```

## RabbitMQ：出站投递 Broker

源码位置：[agent_gateway/runtime/infra/rabbitmq.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/rabbitmq.py:19)

`RabbitMQDeliveryBroker` 用于出站消息可靠投递。它不会把完整消息体放进 RabbitMQ，而是发布轻量引用，真正的投递状态和消息内容仍由 `DeliveryQueue` / 数据库负责。

```python
class RabbitMQDeliveryBroker:
    """RabbitMQ-backed delivery broker.

    PostgreSQL remains the source of truth. RabbitMQ messages intentionally carry only
    lightweight references so the broker does not retain full outbound message bodies.
    """
```

发布出站投递引用。

源码位置：[agent_gateway/runtime/infra/rabbitmq.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/rabbitmq.py:47)

```python
def publish(self, entry: QueuedDelivery) -> None:
    if not self.enabled:
        return
    channel = self._ensure_channel()
    payload = {
        "delivery_id": entry.id,
        "channel": entry.channel,
        "account_id": str(entry.metadata.get("account_id", "")),
        "correlation_id": str(entry.metadata.get("correlation_id", "")),
        "idempotency_key": str(entry.metadata.get("idempotency_key", "")),
        "published_at": time.time(),
    }
    channel.basic_publish(
        exchange=self.exchange,
        routing_key=self.queue,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,
            message_id=entry.id,
        ),
    )
```

消费端使用显式 ACK / NACK。handler 返回 True 才 ACK，否则 NACK 并 requeue。

源码位置：[agent_gateway/runtime/infra/rabbitmq.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/rabbitmq.py:152)

```python
def consume_once(self, handler) -> bool:
    method, _properties, body = channel.basic_get(queue=self.queue, auto_ack=False)
    if method is None:
        return False
    payload = json.loads(body.decode("utf-8"))
    should_ack = bool(handler(payload))
    if should_ack:
        channel.basic_ack(delivery_tag=method.delivery_tag)
    else:
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
    return True
```

RabbitMQ 拓扑包含主队列和死信队列。

源码位置：[agent_gateway/runtime/infra/rabbitmq.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/rabbitmq.py:221)

```python
def _declare_topology(self, channel: Any) -> None:
    channel.exchange_declare(exchange=self.exchange, exchange_type="direct", durable=True)
    channel.exchange_declare(exchange=self.dead_letter_exchange, exchange_type="direct", durable=True)
    channel.queue_declare(
        queue=self.queue,
        durable=True,
        arguments={"x-dead-letter-exchange": self.dead_letter_exchange},
    )
    channel.queue_bind(exchange=self.exchange, queue=self.queue, routing_key=self.queue)
    channel.queue_declare(queue=self.dead_letter_queue, durable=True)
```

## RabbitMQ：入站任务 Broker

源码位置：[agent_gateway/runtime/infra/rabbitmq.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/rabbitmq.py:242)

`RabbitMQInboundTaskBroker` 用于分布式 worker 消费入站任务。它同样只发送轻量任务引用，任务状态仍回到 TaskStore / PostgreSQL 做校验。

```python
class RabbitMQInboundTaskBroker:
    """RabbitMQ-backed inbound task broker.

    Task storage remains in PostgreSQL/local TaskStore. RabbitMQ carries only a
    lightweight task reference so it can distribute work without owning business
    state or retaining user message bodies.
    """
```

发布入站任务引用时，系统按照 `session_key` 做稳定分区。

源码位置：[agent_gateway/runtime/infra/rabbitmq.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/rabbitmq.py:275)

```python
def publish(self, task: TaskInstance) -> None:
    channel = self._ensure_channel()
    partition = self.partition_for(task.session_key or task.id)
    queue = self.queue_name(partition)
    payload = {
        "task_id": task.id,
        "task_type": task.task_type,
        "session_key": task.session_key,
        "partition": partition,
        "idempotency_key": task.idempotency_key,
        "published_at": time.time(),
    }
    channel.basic_publish(
        exchange=self.exchange,
        routing_key=queue,
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        properties=pika.BasicProperties(delivery_mode=2, message_id=task.id),
    )
```

分区算法是 `sha256(session_key) % partitions`，保证同一 session 稳定进入同一个分区。

源码位置：[agent_gateway/runtime/infra/rabbitmq.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/rabbitmq.py:412)

```python
def partition_for(self, session_key: str) -> int:
    raw = (session_key or "").encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False) % self.partitions
```

每个分区都有自己的队列，并设置 `basic_qos(prefetch_count=self.prefetch)`。

源码位置：[agent_gateway/runtime/infra/rabbitmq.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/infra/rabbitmq.py:419)

```python
def _ensure_channel(self) -> Any:
    parameters = pika.URLParameters(self.url)
    self._connection = pika.BlockingConnection(parameters)
    self._channel = self._connection.channel()
    self._channel.basic_qos(prefetch_count=self.prefetch)
    self._declare_topology(self._channel)
    return self._channel
```

## 任务入队与 RabbitMQ 发布

源码位置：[agent_gateway/runtime/tasks/queue.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/queue.py:21)

任务先写入 TaskStore；如果配置了 session scheduler，则同步写入 Redis pending bucket 和 ready index；如果配置了 broker，再发布轻量引用到 RabbitMQ。重复任务会通过幂等键返回已有任务，并在必要时重新写入调度索引、重新发布唤醒引用。

```python
def enqueue(..., idempotency_key: str = "", ...):
    if idempotency_key:
        existing = self.store.find_by_idempotency_key(...)
        if existing is not None:
            if existing.status in {"pending", "retrying"}:
                self._publish_ready(existing)
            return existing

    task = TaskInstance.create(...)
    created = self.store.create(task)
    self._publish_ready(created)
    return created
```

启用 session scheduler 时，worker 不再按 RabbitMQ payload 中的 `task_id` 直接执行，而是调用 `reserve_session_claim()` 从 Redis ready index 声明一个可执行 session 的队首任务，再回到 TaskStore / PostgreSQL 按 `task_id` 精确预占。未启用 scheduler 时，旧路径仍按 broker payload 的 `task_id` 预占。

源码位置：[agent_gateway/runtime/tasks/queue.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/queue.py:112)

```python
def reserve_session_claim(self, *, worker_id: str, task_types: Iterable[str] | None = None, ...):
    scheduler = self.session_scheduler
    claim = scheduler.claim_next(worker_id=worker_id, task_types=task_types)
    task = self.reserve_task_id(claim.task_id, worker_id=worker_id, task_types=task_types)
    if task is not None:
        return task, claim
    scheduler.release(claim)
```

## Worker 如何消费 RabbitMQ 入站任务

源码位置：[agent_gateway/runtime/tasks/worker.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/worker.py:126)

`TaskWorkerRuntime` 会优先尝试 Redis session scheduler。只有未启用 scheduler 或当前没有可 claim 的任务时，才进入 broker / 本地 reserve 路径。

源码位置：[agent_gateway/runtime/tasks/worker.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/worker.py:74)

```python
async def run_once(self) -> bool:
    scheduler_handled = await self._run_once_from_scheduler()
    if scheduler_handled:
        return True
    broker_handled = await self._run_once_from_broker()
    if broker_handled:
        return True
```

broker 消息到达时，如果 scheduler 已启用，payload 会被视为 worker 唤醒信号。worker 会 claim Redis 中真正可执行的 session 队首任务，然后确认 broker 消息。

源码位置：[agent_gateway/runtime/tasks/worker.py](/home/obiah/Desktop/claw0/gateway/agent_gateway/runtime/tasks/worker.py:159)

```python
if scheduler is not None and getattr(scheduler, "enabled", False):
    claimed = self.queue.reserve_session_claim(
        worker_id=self.worker_id,
        task_types=self.handlers.keys(),
    )
    if claimed is None:
        return True
    task, claim = claimed
    try:
        asyncio.run(self._execute(task))
    finally:
        self.queue.release_session_claim(claim)
    return True
```

## 当前链路总结

入站链路：

```text
飞书 / CLI / WebSocket
  -> 统一入站消息
  -> TaskQueue.enqueue()
  -> TaskStore 持久化任务
  -> RedisSessionReadyScheduler.enqueue(session pending bucket + ready index)
  -> RabbitMQInboundTaskBroker.publish(task_id 唤醒引用)
  -> TaskWorkerRuntime._run_once_from_scheduler()
  -> RedisSessionReadyScheduler.claim_next()
  -> TaskStore.reserve_task_id(claim.task_id)
  -> RedisLaneCoordinator 获取 session 锁
  -> Agent 执行
  -> DeliveryQueue 入队出站消息
```

出站链路：

```text
Agent 回复 / Cron 推送 / Heartbeat 推送
  -> DeliveryQueue 持久化投递记录
  -> RabbitMQDeliveryBroker.publish(delivery_id 引用)
  -> DeliveryRuntime / worker 消费引用
  -> 回查投递记录
  -> 通道发送
  -> 成功 ACK / 失败重试或进入死信队列
```

Redis 解决的是“协调问题”：去重、幂等、限流、session 互斥。

RabbitMQ 解决的是“分发问题”：削峰、异步解耦、分区消费、ACK/NACK、死信和队列观测。
