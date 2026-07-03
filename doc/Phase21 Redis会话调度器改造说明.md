# Phase 21 Redis 会话调度器改造说明

本文记录 Phase 21 对分布式入站任务执行链路的改造：使用 Redis ready index 和 per-session pending bucket 保证同一会话严格 FIFO，同时让不同会话可以并行执行。

## 1. 改造背景

Phase 20 已经完成 RabbitMQ 入站队列、Redis session lane ownership 和多 worker 执行基础。当系统开启多个 worker、提高 RabbitMQ prefetch 或 worker concurrency 后，旧链路可以保证“同一 session 不并发”，但不能充分保证“同一 session 严格按入队顺序执行”。

旧链路大致如下：

```text
入站消息
  -> TaskStore 创建 task
  -> RabbitMQ 发布 task_id
  -> worker 从 RabbitMQ 取 task_id
  -> TaskStore.reserve_task_id(task_id)
  -> RedisLaneCoordinator 获取 session 锁
  -> Agent 执行
```

这个方案的问题在于，RabbitMQ 只知道消息顺序和分区，但不知道某个 session 当前是否正在执行。多个同 session 任务被预取后，后续任务可能反复 retry 或重新入队，最终顺序容易受重试时间、worker 调度和 broker 投递影响。

典型问题：

```text
A1 -> A2 -> A3 -> B1 -> C1
```

如果 A1 正在执行，A2/A3 不应该占住 worker 或阻塞 B/C。更合理的行为是：A2/A3 留在 A 自己的 pending bucket，worker 继续执行 B1/C1；A1 完成后，A2 才被放行。

## 2. 改造目标

Phase 21 的目标是把“任务分发顺序”从 RabbitMQ 消息顺序，升级为“按 session 调度”：

- 同一 `session_key` 内严格 FIFO，例如 `A1 -> A2 -> A3 -> A4`。
- 不同 session 不互相阻塞，例如 A 正在执行时，B/C 可以并发执行。
- RabbitMQ 继续负责削峰和唤醒 worker，不再直接决定同 session 的执行顺序。
- PostgreSQL / TaskStore 继续作为任务事实状态，Redis 只做热路径调度索引。
- Redis 索引丢失后，可以从 `pending/retrying` 任务事实状态重建。

## 3. 核心数据结构

新增实现位于：

```text
agent_gateway/runtime/tasks/session_scheduler.py
```

Redis 使用三个核心 key：

```text
gateway:tasks:sessions:ready
gateway:tasks:session:{session_key}:pending
gateway:tasks:session:{session_key}:busy
```

含义如下：

| Key | 类型 | 作用 |
| --- | --- | --- |
| `gateway:tasks:sessions:ready` | LIST | 全局可运行 session 索引，只保存 session_key。 |
| `gateway:tasks:session:{session_key}:pending` | LIST | 某个 session 的待执行任务队列，保存轻量 task 引用。 |
| `gateway:tasks:session:{session_key}:busy` | STRING + TTL | 当前 session 正在执行的 owner，包含 worker_id、task_id、session_key、续租时间。 |

pending bucket 中的元素格式：

```text
{task_id}|{task_type}
```

busy owner value 是 JSON：

```json
{
  "version": 1,
  "worker_id": "gateway-worker-1",
  "task_id": "ed9c96a187c546c2",
  "session_key": "agent:main:feishu:feishu-main:direct:ou_xxx",
  "acquired_at": 1782900000.123,
  "renewed_at": 1782900030.456
}
```

## 4. 新执行链路

改造后的入站执行链路如下：

```text
飞书 / WebSocket / 非 CLI 入站消息
  -> ChannelRuntime
  -> TaskQueue.enqueue()
  -> TaskStore / PostgreSQL 写入任务事实状态
  -> RedisSessionReadyScheduler.enqueue()
       -> 写入 session pending bucket
       -> 如果 session 不 busy，把 session 放入 ready index
  -> RabbitMQInboundTaskBroker.publish()
       -> 发布 task_id 轻量唤醒引用
  -> TaskWorkerRuntime
       -> 优先从 Redis scheduler claim session 队首任务
       -> 回到 TaskStore.reserve_task_id() 做事实状态抢占
       -> 执行 handler / Agent / Tool Calling
       -> 执行期间 watchdog 续租 busy owner
       -> 完成后 release busy owner
       -> 如果 pending bucket 仍有任务，把 session 放回 ready index
```

关键变化是：RabbitMQ 消息不再是“必须执行这个 task_id”的命令，而是“有任务到达，可以唤醒 worker”的信号。worker 真正执行哪个任务，由 Redis scheduler 的 `claim_next()` 决定。

## 5. Claim / Release 语义

`claim_next()` 使用 Lua 脚本原子完成：

```text
1. 从 ready index 弹出一个 session_key
2. 检查 session busy key 是否存在
3. 如果 busy，说明该 session 正在执行，把它暂时跳过
4. 如果不 busy，从该 session 的 pending bucket 弹出队首 task
5. 写入 busy owner，设置 TTL
6. 返回 task_id / session_key / owner_value
```

`release()` 同样使用 Lua 脚本原子完成：

```text
1. 校验 busy owner value 是否匹配
2. 匹配才删除 busy key
3. 如果 pending bucket 仍有任务，把 session_key 放回 ready index
4. 不匹配则拒绝释放，避免旧 worker 误删新 owner
```

这样可以解决：

```text
A1 -> A2 -> B1 -> C1
```

执行过程变成：

```text
claim A1，A 变 busy
A2 留在 A pending bucket
worker 继续 claim B1 / C1
A1 release 后，A 重新进入 ready index
下一次才 claim A2
```

## 6. Watchdog 续租

模型调用和工具执行可能耗时较长。如果 busy owner TTL 到期，而任务仍在执行，就可能导致其他 worker 误以为该 session 空闲，从而提前执行同 session 后续任务。

为此，`TaskWorkerRuntime` 在执行 scheduler claim 任务时启动续租协程：

```text
_execute_claimed_task()
  -> 启动 _renew_session_claim_until_cancelled()
  -> 执行真实任务
  -> 取消续租协程
  -> release session claim
```

续租间隔默认取 TTL 的三分之一：

```text
renew_interval = min(60s, ttl / 3)
```

续租成功会记录：

```text
task.scheduler.renewed
```

续租失败会记录：

```text
task.scheduler.renew_failed
```

续租失败不会强行中断当前模型调用，因为外部调用可能已经产生副作用。系统会通过事件和 TTL 机制暴露风险，后续再由恢复策略处理。

## 7. 恢复与观测

Phase 21 增加了控制面和 Dashboard 观测能力。

控制面方法：

```text
tasks.scheduler.status
tasks.scheduler.rebuild
```

`tasks.scheduler.status` 返回：

```text
enabled
namespace
ready_count
ready_sessions
pending_buckets
busy_owners
```

`tasks.scheduler.rebuild` 会从 `pending/retrying` 任务事实状态重建 Redis 索引：

```text
TaskStore / PostgreSQL pending + retrying tasks
  -> RedisSessionReadyScheduler.rebuild()
  -> 重建 pending bucket
  -> 重建 ready index
```

这个操作只恢复调度引用，不会重新执行已完成任务，也不会修改任务事实状态。

Dashboard 后台任务栏现在会展示：

- 会话调度器是否启用。
- 当前 ready session 数。
- pending bucket 样例。
- busy owner 样例。
- “从任务状态重建 Redis 调度索引”按钮。

## 8. 配置开关

新增配置：

```env
GATEWAY_SESSION_READY_SCHEDULER_ENABLED=false
GATEWAY_SESSION_READY_SCHEDULER_NAMESPACE=gateway:tasks
```

默认关闭，原因是要保持单机和旧部署路径兼容。分布式多 worker 严格顺序模式建议开启：

```env
GATEWAY_REDIS_ENABLED=true
GATEWAY_INBOUND_TASK_QUEUE_ENABLED=true
GATEWAY_INBOUND_BROKER=rabbitmq
GATEWAY_SESSION_READY_SCHEDULER_ENABLED=true
```

## 9. 关键代码位置

| 文件 | 作用 |
| --- | --- |
| `agent_gateway/runtime/tasks/session_scheduler.py` | Redis scheduler 核心实现，包含 enqueue、claim_next、release、renew、rebuild、snapshot。 |
| `agent_gateway/runtime/tasks/queue.py` | TaskQueue 入队后写 scheduler，并提供 reserve_session_claim / release_session_claim / renew_session_claim。 |
| `agent_gateway/runtime/tasks/worker.py` | Worker 优先通过 scheduler claim session 队首任务，执行期间续租 busy owner。 |
| `agent_gateway/runtime/execution/control_plane.py` | 暴露 scheduler status 和 rebuild 控制面能力。 |
| `agent_gateway/gateways/control/websocket_server.py` | 暴露 `tasks.scheduler.status` 和 `tasks.scheduler.rebuild` JSON-RPC 方法。 |
| `agent_gateway/monitoring/static/app.js` | Dashboard 展示 session scheduler 状态和重建按钮。 |
| `agent_gateway/config.py` | 增加 scheduler 开关和命名空间配置。 |

## 10. 验证方式

单元测试覆盖：

```bash
./.venv/bin/python -m pytest tests/test_session_scheduler.py tests/test_task_worker.py tests/test_control_plane.py tests/test_gateway_server.py -q
```

全量回归：

```bash
node --check agent_gateway/monitoring/static/app.js
./.venv/bin/python -m pytest tests -q
```

本次完成时的验证结果：

```text
499 passed
```

## 11. 改造后的边界

当前方案已经解决：

- 同 session 严格 FIFO。
- 热点 session 不阻塞其他 session。
- RabbitMQ 只做削峰和唤醒。
- Redis 调度索引可观测、可重建。
- 长任务执行期间 busy owner 可续租。

仍需谨慎处理的边界：

- 如果 worker 卡死但进程仍存活，续租协程可能也受影响，需要依赖 TTL 和后续恢复策略。
- 如果模型调用已经发出，系统不能真正取消外部模型副作用，只能通过幂等和状态恢复降低影响。
- 多机生产部署时，Redis / RabbitMQ / PostgreSQL 本身仍需要高可用部署，不应只依赖单机中间件。
