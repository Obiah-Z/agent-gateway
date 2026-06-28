# 分布式 Lane 接管与迁移策略

本文档对应 `20.9.8.5 超时接管与迁移策略`，说明 Gateway 当前如何从 Redis session lock 演进到可观测、可续租、可接管的 per-session lane。

## 1. 当前实现结论

当前 Gateway 的分布式 lane 采用：

```text
Redis key TTL 负责崩溃恢复
Redis lane owner metadata 负责观测
TaskWorkerRuntime reserve 阶段跳过 active lane
AgentInboundTaskHandler 执行阶段获取 / 续租 / 释放 lane ownership
```

这意味着：

- 正常执行时，同一 session 只有一个 owner。
- 长模型调用期间会持续续租，刷新 `renewed_at`。
- worker 崩溃、进程退出或网络断开后，如果没有 release，Redis key 会在 TTL 到期后自动消失。
- TTL 到期后，其他 worker 可以重新 acquire 同一个 session lane，实现接管。

## 2. Lane Key 与 Owner Value

当前 key 仍兼容旧命名：

```text
gateway:lock:agent_inbound:{session_key}
```

value 已升级为 JSON metadata：

```json
{
  "version": 1,
  "session_key": "inbound:feishu:bot-a:user-1",
  "lane_key": "gateway:lock:agent_inbound:inbound:feishu:bot-a:user-1",
  "worker_id": "worker-1",
  "task_id": "task-123",
  "owner_token": "worker-1:task-123",
  "acquired_at": 1782663600.0,
  "renewed_at": 1782663660.0
}
```

兼容策略：

- 新 owner 写 JSON。
- inspect 能解析旧的 `worker_id:task_id` 字符串 value。
- release 和 renew 都使用完整 owner value 做 compare-and-set，避免误释放或误续租其他 worker 的 lane。

## 3. 接管流程

### 3.1 正常执行

```text
worker reserve task
  -> inspect session lane
  -> lane 空闲则 acquire
  -> 执行 AgentLoop
  -> 后台续租 lane
  -> 执行完成 release
```

### 3.2 worker 崩溃

```text
worker acquire lane
  -> worker 崩溃，未 release
  -> renew 停止
  -> Redis TTL 到期
  -> lane key 自动过期
  -> 其他 worker 再次 reserve / acquire
  -> 新 worker 接管 session lane
```

### 3.3 Redis 探测异常

reserve 阶段如果 Redis inspect / exists 异常：

```text
不在 reserve 阶段跳过任务
执行阶段 acquire 失败则进入 retrying
```

这样做是为了避免 Redis 短暂异常导致任务被永久跳过。

## 4. Stale 观测

`RedisLaneCoordinator.inspect()` 会输出：

- `worker_id`
- `task_id`
- `acquired_at`
- `renewed_at`
- `age_seconds`
- `stale`
- `ttl_seconds`
- `legacy`

`stale` 是观测信号，不直接抢占 key。

真正接管仍依赖 Redis TTL 到期后重新 acquire。原因是强行抢占未过期 owner 可能造成双 worker 并发执行同一 session。

## 5. 当前故障注入覆盖

已有自动化测试覆盖：

- 同 session 只能一个 owner。
- 错误 owner 不能 renew / release。
- lane owner value 支持 JSON metadata inspect。
- legacy owner value 可解析。
- 续租会刷新 `renewed_at`。
- owner TTL 到期后其他 worker 可以 acquire。
- worker reserve 阶段遇到 active lane 会跳过。
- worker 崩溃未 release 时，TTL 到期后新 worker 可接管并执行 pending task。

## 6. 与 RabbitMQ 分区的关系

后续接 RabbitMQ 入站分区后，lane 仍然需要保留。

原因：

- RabbitMQ 解决可靠排队和粗粒度分发。
- Lane 解决 session 级执行权。
- 即使同 session 被路由到同一 partition，重投、手动 retry、迁移或误配置并发 consumer 时仍可能出现并发风险。

推荐组合：

```text
RabbitMQ partition queue: 负责消息可靠排队
Redis lane ownership: 负责 session 互斥和接管
PostgreSQL task store: 负责任务状态真源
```

## 7. 后续增强

当前实现已经具备 TTL 接管能力，但还不是完整调度系统。后续可继续增强：

- Worker heartbeat 表：记录 worker 存活、最近心跳、负责 partition / lane 数。
- Lane 状态表：把 active / stale / takeover 历史写入 PostgreSQL，便于审计。
- 热点 session 标记：长时间占用或频繁重试的 session 标记为 hot lane。
- 控制面操作：暂停 lane、释放 stale lane、查看 lane 历史。
- RabbitMQ hybrid worker：broker 唤醒 + task store reserve + lane acquire。

## 8. 当前完成标准

当前阶段完成的核心能力：

```text
worker 崩溃后，lane 不会永久占用；
Redis TTL 到期后，其他 worker 可以接管同一 session；
接管前仍保持同 session 不并发执行。
```
