# Redis 技术栈选型报告

## 1. 一句话定位

Redis 在 AI Agent Gateway 中不是业务主库，也不是核心消息队列，而是高并发、多实例场景下的分布式协调层。

它主要解决四类问题：

| 能力 | 解决的问题 |
| --- | --- |
| 事件去重 | 避免飞书 webhook / 长连接重试导致同一事件重复处理 |
| 幂等控制 | 避免重复任务、重复回复、重复工具调用扩大影响 |
| 分布式锁 | 多 worker 同时处理任务时，避免同一资源被并发修改 |
| 会话级 lane | 保证同一会话同一时间只被一个 worker 执行 |
| 限流 | 在入口或控制面保护系统，避免突发流量拖垮后端 |

相关代码：

- `agent_gateway/runtime/infra/redis_client.py`
- `agent_gateway/runtime/tasks/lane.py`
- `agent_gateway/runtime/execution/control_plane.py`

## 2. 在当前系统中的实际职责

### 2.1 飞书事件去重

飞书事件可能因为网络重试、平台重投、长连接恢复等原因重复进入网关。如果不做去重，系统可能出现：

- 同一条用户消息被 Agent 处理多次。
- 同一条回复被投递多次。
- 工具调用重复执行。
- 记忆、事件流、任务队列出现重复记录。

当前通过 Redis `SET NX EX` 实现一次性标记：

```text
SET gateway:dedup:{event_id} 1 NX EX 600
```

语义是：

- `NX`：只有 key 不存在时才写入。
- `EX`：自动过期，避免永久占用内存。
- 首次写入成功表示事件可处理。
- 后续重复事件在 TTL 内被拒绝或忽略。

对应封装：

```python
RedisClient.mark_once(...)
```

### 2.2 分布式锁

Redis 用于实现带 owner token 的分布式锁：

```text
SET lock_key owner_token NX EX ttl
```

释放锁时不能直接 `DEL lock_key`，必须先校验当前 value 是否仍然属于自己：

```text
if GET lock_key == owner_token:
    DEL lock_key
```

这是为了防止误删其他 worker 的锁。

典型故障场景：

```text
1. Worker A 获取锁。
2. Worker A 卡顿，锁 TTL 过期。
3. Worker B 获取同一把锁。
4. Worker A 恢复后如果直接 DEL，会误删 Worker B 的锁。
```

因此当前代码中 `release_lock()` 和 `renew_lock()` 都使用 Lua 脚本保证检查与修改的原子性。

### 2.3 会话级 Lane Ownership

当前项目里 Redis 最重要的用途是 `RedisLaneCoordinator`。

它把普通分布式锁提升为会话级 lane ownership：

```text
session_key -> lane owner
```

核心规则：

- 同一个 session 同一时间只能有一个 owner。
- owner 处理任务期间需要续租。
- 释放和续租都必须校验 owner token。
- worker 崩溃后，Redis TTL 到期，其他 worker 可以接管。
- lane 状态镜像到 PostgreSQL，用于观测、审计和恢复分析。

这解决的是 Agent 系统里的关键一致性问题：同一会话不能并发执行。

如果同一个会话的两条消息被不同 worker 同时处理，可能导致：

- 上下文重放顺序错误。
- 会话历史写入乱序。
- 工具调用结果和模型后续回复错位。
- 记忆写入互相覆盖。

### 2.4 固定窗口限流

当前 RedisClient 已提供固定窗口限流：

```text
INCR gateway:rate:{subject}:{window}
EXPIRE gateway:rate:{subject}:{window} ttl
```

可用于：

- 单用户限流。
- 单飞书通道限流。
- 控制面接口限流。
- Agent 任务提交限流。
- webhook 入口削峰。

固定窗口不是最精确的限流算法，但实现简单、性能高，适合作为第一层保护。

## 3. 为什么选择 Redis

### 3.1 延迟低，适合高频协调

Gateway 的入站事件、任务抢占、锁续租、去重判断都是高频操作。Redis 基于内存读写，命令执行开销低，适合放在热路径上。

如果这些操作全部压到 PostgreSQL，会带来：

- 高频 update / insert。
- 事务和锁等待增加。
- 短生命周期数据污染业务表。
- 清理过期数据需要额外任务。

### 3.2 原子命令正好匹配需求

Redis 的 `SET NX EX` 同时具备：

- 不存在才写入。
- 自动过期。
- 单命令原子性。

这天然适合：

- 分布式锁。
- webhook 去重。
- 幂等 key。
- 临时占用状态。

### 3.3 TTL 适合“临时所有权”

session lane ownership 不是永久事实，而是运行时临时状态。

它需要：

- 正常执行时持续存在。
- 任务完成后释放。
- worker 崩溃后自动过期。
- 长任务执行时可续租。

Redis TTL 模型比数据库定时清理更自然。

### 3.4 和 RabbitMQ、PostgreSQL 职责互补

当前最终形态不是让 Redis 替代其他组件，而是分工：

| 组件 | 负责什么 |
| --- | --- |
| RabbitMQ | 任务排队、削峰、投递、ack/nack、死信 |
| Redis | 去重、互斥、限流、owner TTL、短生命周期协调 |
| PostgreSQL | 事实状态、任务记录、会话、事件、审计、恢复 |
| JSONL | 本地兼容、回放、调试、备份 |

Redis 的边界是“运行时协调”，不是“事实存储”。

## 4. 从什么实现演进而来

### 4.1 单机阶段：本地内存与 JSONL

早期单机运行时，可以依赖：

- 进程内队列。
- 本地文件。
- JSONL 会话历史。
- 单进程 lane。

这种方式简单直接，但只能解决单进程内的一致性问题。

问题是：

- 多实例之间看不到彼此状态。
- 进程重启后临时锁丢失。
- 无法跨机器去重。
- 多 worker 会并发处理同一 session。

### 4.2 状态外置阶段：PostgreSQL

PostgreSQL 引入后，系统可以把会话、任务、投递、事件等事实数据落库。

它解决了：

- 状态可恢复。
- 运行记录可查询。
- 多实例共享任务事实。
- 运维面板可观察历史状态。

但 PostgreSQL 不适合承担所有高频临时协调。

### 4.3 分布式协调阶段：Redis

Redis 的引入补齐了多实例运行时的协调能力：

- 同一事件只处理一次。
- 同一 session 同一时间只允许一个 worker。
- 锁可续租、可过期、可接管。
- 高频限流不压垮主库。

### 4.4 最终组合：RabbitMQ + Redis + PostgreSQL

最终链路可以概括为：

```text
入站消息
  -> RabbitMQ / TaskQueue 削峰分发
  -> PostgreSQL 保存任务事实
  -> Redis 判断 session lane ownership
  -> Worker 执行 Agent
  -> PostgreSQL / JSONL 记录会话与事件
  -> RabbitMQ 出站投递
```

## 5. 高并发与分布式场景中的常见 Redis 用法

### 5.1 幂等去重

适合 webhook、支付回调、消息重投：

```text
SET idempotency:{id} 1 NX EX 600
```

### 5.2 分布式锁

适合同一资源互斥：

```text
SET lock:{resource} token NX EX 60
```

释放必须校验 token。

### 5.3 锁续租

适合长任务：

```text
if GET lock == token:
    EXPIRE lock 60
```

需要 Lua 脚本保证原子性。

### 5.4 崩溃接管

通过 TTL 实现：

```text
worker alive -> renew
worker crash -> key expires -> another worker acquire
```

### 5.5 限流

固定窗口：

```text
INCR rate:{user}:{window}
EXPIRE rate:{user}:{window} 61
```

更高级可演进为：

- 滑动窗口。
- 令牌桶。
- 漏桶。
- Redis Cell。

### 5.6 热点状态缓存

可缓存：

- Agent 配置摘要。
- 路由绑定结果。
- Dashboard 快照。
- 最近运行状态。

但缓存必须允许失效，不能替代主存储。

## 6. 为什么不用别的方案

### 6.1 为什么不用 PostgreSQL 做全部锁

PostgreSQL advisory lock 可以实现互斥，但不适合所有场景：

- 长模型调用会长期占用数据库连接或锁资源。
- 高频续租会增加数据库压力。
- TTL 过期语义不如 Redis 自然。
- 去重和限流这类短状态会污染数据库。

PostgreSQL 适合保存事实，Redis 适合做短期协调。

### 6.2 为什么不用 RabbitMQ 保证所有互斥

RabbitMQ 适合排队和投递，但不天然回答这些问题：

- 当前 session 是否已有 worker 在执行？
- 当前 owner 是谁？
- owner 是否超时？
- 当前 worker 是否有资格释放锁？

所以 RabbitMQ 负责“任务怎么到 worker”，Redis 负责“worker 是否可以执行”。

### 6.3 为什么不用本地锁

本地锁只能在单进程内有效，多实例部署后无法跨进程、跨机器协调。

## 7. 面试追问回答模板

### Q1：你为什么引入 Redis？

可以回答：

> Gateway 从单机 Agent Runtime 演进到多实例、多 worker 后，最大的风险是重复消费和同一会话并发执行。Redis 的 `SET NX EX`、TTL 和 Lua 原子脚本非常适合做短生命周期协调，所以我用它实现飞书事件去重、幂等控制、限流和 session lane ownership。PostgreSQL 保存事实，RabbitMQ 分发任务，Redis 负责实时协调。

### Q2：Redis 在你的系统里是不是缓存？

可以回答：

> 不是主要作为缓存使用。它更像分布式协调层。缓存只是 Redis 的一种用法，但在这个项目里 Redis 的核心价值是去重、锁、限流和 lane ownership。

### Q3：如何保证同一会话不被并发执行？

可以回答：

> 每个 session_key 映射一个 Redis lane key。worker 执行前用 `SET NX EX` 获取 owner，owner value 里包含 worker_id、task_id、owner_token、acquired_at 和 renewed_at。执行过程中续租，完成后只有 token 匹配才能释放。这样即使多个 worker 同时抢同一会话，也只有一个能执行。

### Q4：worker 崩溃怎么办？

可以回答：

> Redis 锁有 TTL。worker 正常执行时会续租，如果进程崩溃，续租停止，key 到期后其他 worker 可以接管。lane 状态还会镜像到 PostgreSQL，便于运维面板查看 owner、stale 状态和接管历史。

### Q5：Redis 挂了怎么办？

可以回答：

> 单机模式可以降级，但多实例模式下 Redis 不可用会影响 session lane ownership 和事件去重，所以应该进入告警或阻断高风险 worker。因为没有 Redis 时继续多 worker 执行，可能导致同一会话并发和重复消费。

## 8. 方案风险与后续优化

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| TTL 太短 | 长模型调用未完成锁就过期 | 增加续租和合理 TTL |
| TTL 太长 | worker 崩溃后恢复慢 | 结合心跳和 stale 观测 |
| Redis 单点 | Redis 故障影响协调能力 | 使用 Sentinel、Cluster 或云 Redis |
| 固定窗口限流不平滑 | 窗口边界可能突刺 | 演进为滑动窗口或令牌桶 |
| 锁误释放 | 旧 worker 误删新 owner 锁 | 必须使用 token + Lua 校验 |

## 9. 简历表述建议

可以写成：

> 基于 Redis 实现事件去重、幂等控制、限流和会话级 lane ownership，通过 `SET NX EX`、TTL 续租与 token 校验解决多实例部署下的重复消费和同一会话并发冲突问题，保证同一会话串行执行，并将 lane 状态镜像到 PostgreSQL 支持运维观测和故障接管。

