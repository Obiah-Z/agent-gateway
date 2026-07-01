# PostgreSQL 技术栈选型报告

## 1. 一句话定位

PostgreSQL 在 AI Agent Gateway 中是事实存储主库，负责保存会话、任务、事件、投递、记忆、lane 状态和审计数据。

它解决的核心问题不是“快”，而是：

- 数据可持久化。
- 数据可查询。
- 数据可恢复。
- 数据可审计。
- 多实例共享同一份事实状态。

相关代码：

- `agent_gateway/runtime/state/factory.py`
- `agent_gateway/runtime/state/postgres.py`
- `agent_gateway/runtime/state/migration.py`
- `agent_gateway/runtime/state/queue.py`
- `agent_gateway/runtime/tasks/lane.py`

## 2. 在当前系统里的实际职责

### 2.1 会话事实存储

Gateway 的会话不是单轮消息，而是包含多轮对话、工具调用、上下文重放、记忆注入和通道消息的长链路状态。

PostgreSQL 用于保存：

- session 元信息。
- 对话历史。
- 任务状态。
- 运行事件。
- 命中错误。
- 记忆条目。
- lane owner 状态。

这样做的好处是：

- 进程重启后还能恢复。
- Dashboard 能做历史查询。
- 运维可以追踪状态演变。
- 多实例共享同一份状态事实。

### 2.2 任务与投递主存储

系统中的任务和出站投递采用“事实先落库”的设计。

也就是说：

```text
先写 PostgreSQL
再由 RabbitMQ 唤醒 worker
```

这保证了即使 broker 暂时不可用，任务事实仍然不会丢。

同样，出站投递也是：

```text
Agent 回复 -> DeliveryQueue -> PostgreSQL 保存投递事实 -> RabbitMQ 仅传轻量 delivery_id
```

### 2.3 session lane 状态镜像

Redis 负责实时 lane ownership 协调，但 lane 的状态会镜像到 PostgreSQL：

- 当前 owner 是谁。
- 哪个 session 被哪个 worker 持有。
- lane 是否 stale。
- acquire / renew / release 的历史轨迹。

相关接口：

- `write_session_lane()`
- `write_session_lane_event()`

这意味着 PostgreSQL 不只是“保存最终结果”，还保存了运行过程中的可观测事实。

### 2.4 迁移后备层

系统在本地 JSONL / 文件状态之外，引入 PostgreSQL 作为外置事实层后，还保留了迁移回填路径。

这表示 PostgreSQL 的引入不是一次性替换，而是逐步演进：

```text
本地 JSONL / 文件状态
  -> PostgreSQL 外置事实存储
  -> 继续保留本地回填与恢复能力
```

## 3. 为什么选择 PostgreSQL

### 3.1 它适合保存长期事实

Gateway 中很多状态不是临时协调，而是需要长期保留的事实：

- 哪个 session 做过哪些轮次。
- 哪个任务执行成功或失败。
- 哪条消息什么时候投递。
- 某次 lane ownership 的历史。
- 哪些错误出现过。

这些都非常适合关系型数据库。

### 3.2 它适合做查询和审计

运维和面试里经常会问：

- 某个 session 为什么失败？
- 哪条消息被重试了几次？
- 某个 worker 为什么接管了 lane？
- 哪些任务在某个时间段积压？

这类问题需要查询能力，而不是单纯的键值访问。

PostgreSQL 的价值在于：

- SQL 查询灵活。
- 可做聚合、过滤、排序。
- 可支持审计和报表。
- 可以把运行状态保留下来，供 Dashboard 和控制面读取。

### 3.3 它适合做多实例共享事实中心

在多 worker、多实例部署下，内存和本地文件都不再可靠。

PostgreSQL 作为事实中心可以保证：

- 不同 worker 看同一份任务状态。
- control plane 和 dashboard 看同一份事实。
- 重启后状态可恢复。
- 迁移、回填和审计有统一入口。

### 3.4 它比纯文件存储更适合生产化

JSONL 很适合调试、回放、开发阶段，但在生产里有这些问题：

- 并发写入治理差。
- 查询能力弱。
- 多实例共享困难。
- 迁移和审计成本高。

PostgreSQL 解决的就是“从本地文件原型走向可运维、可查询、可恢复”的问题。

## 4. 从什么实现演进而来

### 4.1 第一阶段：本地 JSONL / 本地目录

早期系统可以把会话、任务、事件写到本地文件。

优点：

- 简单。
- 易调试。
- 方便回放。

缺点：

- 单机依赖重。
- 重启后只依赖文件。
- 多实例不能共享。
- 查询和统计不方便。

### 4.2 第二阶段：本地文件 + 外部持久化后端

系统引入统一的 State Repository 抽象，让上层不直接关心到底是文件还是数据库。

这一步很关键，因为它把“运行逻辑”和“存储实现”分开了。

相关工厂：

```python
build_state_repository(...)
```

当 PostgreSQL 开关关闭时，系统继续使用本地仓储；当开关打开时，切换到 PostgreSQL 仓储骨架。

### 4.3 第三阶段：PostgreSQL 成为事实主库

现在 PostgreSQL 已承担：

- session。
- task。
- event。
- memory。
- alert。
- metric。
- lane state。

它已经不是辅助存储，而是系统事实中心。

## 5. 高并发与分布式场景中的常见 PostgreSQL 用法

### 5.1 事实表

适合存：

- 任务主记录。
- 会话摘要。
- 投递记录。
- 运行事件。
- 错误记录。
- 记忆条目。

特点是写入后长期保留，便于查询和审计。

### 5.2 状态机落库

任务状态通常不是一个值，而是一条演进轨迹：

```text
pending -> running -> retrying -> done / failed
```

PostgreSQL 很适合表达这种状态机转移。

### 5.3 最终一致性记录

在分布式系统里，很多动作是先在运行时协调，再落成事实。

例如：

- Redis 决定谁拥有 lane。
- PostgreSQL 记录 lane owner 的历史。

这样既保留了实时协调能力，也保留了审计能力。

### 5.4 迁移和回填

PostgreSQL 很适合做从本地 JSONL / 文件向外部存储迁移的目标库。

当前代码里有统一的回填入口：

```text
backfill_local_state_to_repository()
```

它把本地配置、会话、任务、投递、事件、记忆、metrics、alerts 等内容逐步导入 PostgreSQL。

### 5.5 高并发查询

在运维侧，PostgreSQL 适合支持：

- 最近失败任务。
- 某 session 的历史记录。
- 某 time range 内的运行事件。
- lane 状态变化。
- 记忆写入历史。

这些查询是 Dashboard 和排障的重要基础。

## 6. 为什么不用别的方案

### 6.1 为什么不用纯 JSONL

JSONL 的优点是简单，但不适合长期生产化：

- 并发写不好治理。
- 不擅长复杂查询。
- 没有标准事务语义。
- 多实例共享困难。

### 6.2 为什么不用 Redis 代替 PostgreSQL

Redis 适合短生命周期协调，不适合保存大量长期事实。

如果把会话、任务、事件全部放 Redis：

- 内存成本高。
- 查询和审计弱。
- 恢复和治理困难。
- 不适合复杂统计和历史追踪。

### 6.3 为什么不用 RabbitMQ 代替 PostgreSQL

RabbitMQ 是任务传递层，不是事实存储层。

如果把业务事实放在 RabbitMQ：

- 消息消费后事实可能消失。
- 不能像数据库一样查询。
- 不适合作为长期状态中心。

所以 PostgreSQL 是事实存储的正确选择。

## 7. 面试追问回答模板

### Q1：为什么这个项目里要引入 PostgreSQL？

可以回答：

> 因为这个 Gateway 不是单轮消息系统，而是一个长生命周期的 Agent 运行框架。它需要保存会话、任务、事件、投递、记忆和 lane 状态。JSONL 适合原型，但不适合生产化查询和多实例共享，所以我把长期事实迁到 PostgreSQL，作为系统的事实中心。

### Q2：PostgreSQL 和 Redis 的区别是什么？

可以回答：

> PostgreSQL 负责保存长期事实，Redis 负责短生命周期协调。比如 session lane 的 owner 由 Redis 控制，但 owner 历史会镜像到 PostgreSQL。一个解决“现在谁能执行”，一个解决“过去发生了什么”。

### Q3：为什么不用 MongoDB 或其他 NoSQL？

可以回答：

> 这个系统的状态天然是结构化的：session、task、event、delivery、lane、memory 都有明确关系。PostgreSQL 更适合做这种强结构、可查询、可审计的数据中心，而且对迁移、回填和约束更友好。

### Q4：PostgreSQL 会不会成为瓶颈？

可以回答：

> 不会让它承担高频临时协调。高频互斥和去重交给 Redis，任务唤醒交给 RabbitMQ，PostgreSQL 保留给事实存储和审计查询。这样数据库压力是可控的。

### Q5：如果 PostgreSQL 挂了怎么办？

可以回答：

> PostgreSQL 是事实主库，影响最大。所以生产里需要高可用和备份。但系统还保留了本地 JSONL 回填路径，至少在恢复阶段可以做数据补偿和迁移。真正上线时，应该把 PostgreSQL 视为强依赖基础设施。

## 8. 风险与后续优化

| 风险 | 说明 | 应对 |
| --- | --- | --- |
| 写入压力 | 任务、事件、lane 状态写入量增大 | 做分表、索引治理和异步写入 |
| 查询膨胀 | 控制面和 Dashboard 查询复杂 | 为常用查询建立索引和摘要表 |
| 迁移复杂 | 从 JSONL 到数据库需要回填 | 保留 backfill 和双写能力 |
| 高并发下锁竞争 | 如果让数据库承担过多协调会有压力 | 继续把互斥放在 Redis |
| 存储膨胀 | 长历史记录不断增长 | 做归档、冷热分层和清理策略 |

## 9. 简历表述建议

可以写成：

> 将会话、任务、事件、投递、记忆和 lane 状态从本地 JSONL 逐步迁移到 PostgreSQL 事实存储，配合统一的 State Repository 抽象实现本地文件与数据库的平滑演进；PostgreSQL 负责长期持久化、查询和审计，Redis 负责短生命周期协调，RabbitMQ 负责异步分发，从而形成可恢复、可观测的生产化运行底座。

