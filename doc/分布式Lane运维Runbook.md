# 分布式 Lane 运维 Runbook

本文档用于排查和恢复 Gateway 分布式入站 lane 链路。目标是把 RabbitMQ、Redis、PostgreSQL、TaskWorkerRuntime、控制面和 Dashboard 串成一套可执行操作流程。

适用场景：

- 飞书或其他非 CLI 入站消息已接收，但迟迟没有回复。
- RabbitMQ 入站分区队列持续积压。
- Dashboard 出现过期 Lane、被锁会话或后台任务重试。
- worker 崩溃后，同一 session 的后续消息无法继续处理。
- 需要人工释放 PostgreSQL 中残留的 stale lane owner。

## 1. 架构边界

最终分布式 lane 链路：

```text
外部通道
  -> Inbound Gateway
  -> agent_inbound task
  -> PostgreSQL / TaskStore 事实状态
  -> RabbitMQ 入站分区轻量引用
  -> TaskWorkerRuntime worker 池
  -> Redis session lane ownership
  -> AgentLoopRunner
  -> DeliveryQueue
  -> RabbitMQ / PostgreSQL / 本地 fallback 出站投递
```

职责边界：

| 组件 | 职责 | 不负责 |
| --- | --- | --- |
| RabbitMQ | 轻量 task_id 引用分发、分区、ack/nack、DLQ、削峰 | 不保存完整入站消息正文，不作为任务事实状态 |
| Redis | session lane ownership、TTL、续租、快速互斥 | 不保存长期执行记录 |
| PostgreSQL | tasks、runtime_events、session_lanes、session_lane_events 等事实状态和审计 | 不负责高频消息分发 |
| TaskWorkerRuntime | 消费任务、reserve task、调用 handler、记录 worker 生命周期 | 不直接绕过 lane ownership |
| 控制面 / Dashboard | 查询状态、预检恢复、受控释放、审计回看 | 不自动强制抢占未过期 Redis owner |

## 2. 推荐配置

本地或单机 Docker Compose：

```bash
GATEWAY_POSTGRES_ENABLED=true
GATEWAY_REDIS_ENABLED=true
GATEWAY_INBOUND_TASK_QUEUE_ENABLED=true
GATEWAY_INBOUND_BROKER=rabbitmq
GATEWAY_INBOUND_RABBITMQ_PARTITIONS=16
GATEWAY_INBOUND_RABBITMQ_PREFETCH=1
GATEWAY_INBOUND_SESSION_LOCK_TTL_SECONDS=300
GATEWAY_INBOUND_SESSION_LOCK_RENEW_INTERVAL_SECONDS=0
GATEWAY_TASK_WORKER_ID=worker-1
GATEWAY_TASK_WORKER_CONCURRENCY=4
```

多实例部署要求：

- 每个 worker 必须使用不同 `GATEWAY_TASK_WORKER_ID`。
- `GATEWAY_INBOUND_RABBITMQ_PREFETCH` 建议保持 `1`，避免单 consumer 预取多条同分区消息导致顺序观察复杂化。
- `GATEWAY_INBOUND_RABBITMQ_PARTITIONS` 初始可用 `16`，热点 session 明显时再扩到 `32` 或 `64`。
- PostgreSQL 必须先执行 schema 初始化并通过检查。

## 3. 启动前检查

Docker Compose：

```bash
docker compose ps
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-check-schema
```

本地运行：

```bash
source .venv/bin/activate
agent-gateway doctor
agent-gateway postgres-check-schema
agent-gateway lane-doctor
```

依赖连通性：

```bash
redis-cli -u redis://127.0.0.1:6379/0 ping
pg_isready -h 127.0.0.1 -p 5432 -U postgres -d postgres
rabbitmq-diagnostics -q ping
```

如果 RabbitMQ 在容器中：

```bash
docker compose exec rabbitmq rabbitmq-diagnostics -q ping
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers
```

## 4. 快速验收

每次修改分布式 lane 链路后，至少执行：

```bash
python scripts/smoke_distributed_lane.py \
  --scenario inbound \
  --requests 8 \
  --concurrency 4 \
  --session-count 2 \
  --rabbitmq-partitions 4 \
  --lane-ttl-seconds 5
```

验证 Redis TTL 接管：

```bash
python scripts/smoke_distributed_lane.py \
  --scenario ttl-takeover \
  --lane-ttl-seconds 2
```

验证 worker 崩溃接管：

```bash
python scripts/smoke_distributed_lane.py \
  --scenario worker-crash \
  --lane-ttl-seconds 2
```

验证 PostgreSQL lane 状态：

```bash
python scripts/smoke_distributed_lane.py \
  --scenario postgres-lane \
  --lane-ttl-seconds 2
```

结果至少应满足：

- `status` 为 `ok`。
- 同 session 最大并发为 `1`。
- RabbitMQ 入站队列消费后积压为 `0`。
- DLQ 为 `0`。
- `postgres-lane` 的 `history_count` 大于等于 `2`。

## 5. Dashboard 排查顺序

打开 Dashboard：

```text
http://127.0.0.1:8780
```

优先查看“后台任务”卡片：

1. `入站Broker` 是否已启用。
2. `Broker总积压` 是否持续增加。
3. `Broker死信` 是否大于 0。
4. `被锁会话` 是否长期大于 0。
5. `过期Lane` 是否大于 0。
6. `最近owner` 是否停留在同一个 worker。
7. `最近Lane历史` 是否只有 acquired/renewed，没有 released。
8. `恢复建议`、`恢复预检`、`恢复审计` 是否有可处理项。

判断方式：

| 现象 | 优先判断 |
| --- | --- |
| Broker 积压增加，worker 没运行 | worker 角色未启动或 `GATEWAY_RUNTIME_ROLES` 未包含 `worker` |
| Broker 积压增加，worker 运行中 | PostgreSQL reserve、Redis lane 或模型执行可能卡住 |
| 被锁会话长期存在 | session 正在执行长任务，或 Redis owner 未过期 |
| 过期 Lane 存在 | PostgreSQL 中有 stale owner，需要确认 Redis TTL 和 worker 状态 |
| DLQ 大于 0 | RabbitMQ broker 消费或 handler 发生不可恢复失败 |

## 5.1 Lane Doctor

`lane-doctor` 是只读诊断命令，会汇总健康检查、入站 broker 积压、Redis/PostgreSQL 状态、持久 lane、stale lane、恢复预检、恢复审计和最近 worker 执行事件。

文本输出：

```bash
agent-gateway lane-doctor
```

JSON 输出：

```bash
agent-gateway lane-doctor --json --limit 20
```

安全边界：

- 不启动 Gateway 服务。
- 不消费 RabbitMQ 消息。
- 不释放 lane。
- 不修改 Redis key。
- 不写入 PostgreSQL 恢复状态。

适合用于部署后验收和故障现场第一轮只读诊断。

## 6. 控制面查询

控制面 WebSocket 地址默认：

```text
ws://127.0.0.1:8765
```

运行状态：

```json
{"jsonrpc":"2.0","id":1,"method":"status","params":{}}
```

健康检查：

```json
{"jsonrpc":"2.0","id":2,"method":"health.check","params":{}}
```

查询当前持久 lane owner：

```json
{"jsonrpc":"2.0","id":3,"method":"tasks.lanes","params":{"state":"owned","limit":20}}
```

查询 lane owner 历史：

```json
{"jsonrpc":"2.0","id":4,"method":"tasks.lanes.history","params":{"limit":20}}
```

查询 worker 执行事件：

```json
{"jsonrpc":"2.0","id":5,"method":"tasks.executions","params":{"limit":20}}
```

查询 lane recovery 审计事件：

```json
{"jsonrpc":"2.0","id":6,"method":"tasks.lanes.recovery.events","params":{"limit":20}}
```

## 7. Stale Lane 恢复流程

### 7.1 先看建议

```json
{"jsonrpc":"2.0","id":10,"method":"tasks.lanes.recovery","params":{"limit":20}}
```

返回内容会包含：

- `session_key`
- `worker_id`
- `task_id`
- `owner_token`
- `expired_seconds`
- `release_params`

### 7.2 再做 dry-run 预检

```json
{"jsonrpc":"2.0","id":11,"method":"tasks.lanes.recovery.plan","params":{"limit":20}}
```

只允许继续处理满足这些条件的项：

- 有 `session_key`。
- 有 `owner_token`。
- `action` 是 `release_session_lane`。
- `force` 是 `false`。
- 已确认对应 worker 不再执行该 session。

### 7.3 显式确认后执行

默认不会释放：

```json
{"jsonrpc":"2.0","id":12,"method":"tasks.lanes.recovery.execute","params":{"limit":20}}
```

真正执行必须显式传入：

```json
{"jsonrpc":"2.0","id":13,"method":"tasks.lanes.recovery.execute","params":{"limit":20,"execute":true}}
```

安全边界：

- 执行路径逐条复用 `tasks.lanes.release`。
- 默认只释放 stale lane。
- `owner_token` 不匹配会失败。
- 不会直接删除 Redis key。
- Redis TTL 到期后才允许真实运行时接管执行权。

### 7.4 查看审计

```json
{"jsonrpc":"2.0","id":14,"method":"tasks.lanes.recovery.events","params":{"limit":20}}
```

事件类型：

| 类型 | 含义 |
| --- | --- |
| `session_lane.recovery.dry_run` | 生成过恢复 dry-run |
| `session_lane.recovery.released` | 某条 stale lane 已释放 |
| `session_lane.recovery.release_failed` | 某条 lane 释放失败 |
| `session_lane.recovery.completed` | 批量恢复执行完成 |

## 8. PostgreSQL 排查

使用环境变量：

```bash
export GATEWAY_POSTGRES_URL="postgresql://postgres:postgres@127.0.0.1:5432/postgres"
```

查看当前 owned lane：

```bash
psql "$GATEWAY_POSTGRES_URL" -c "
select session_key, worker_id, task_id, state, ttl_seconds, renewed_at, updated_at
from session_lanes
where state = 'owned'
order by updated_at desc
limit 20;"
```

查看 lane 历史：

```bash
psql "$GATEWAY_POSTGRES_URL" -c "
select session_key, worker_id, task_id, event, ttl_seconds, occurred_at
from session_lane_events
order by occurred_at desc
limit 20;"
```

查看后台任务：

```bash
psql "$GATEWAY_POSTGRES_URL" -c "
select id, task_type, status, agent_id, session_key, locked_by, locked_at, updated_at
from tasks
order by updated_at desc
limit 20;"
```

查看 lane recovery 审计事件：

```bash
psql "$GATEWAY_POSTGRES_URL" -c "
select type, status, session_key, message, created_at, metadata
from runtime_events
where type like 'session_lane.recovery.%'
order by created_at desc
limit 20;"
```

禁止直接执行：

```bash
delete from session_lanes where state = 'owned';
```

原因：直接删表会绕过 owner_token、stale 校验和审计事件。

## 9. Redis 排查

查看 lane key：

```bash
redis-cli -u redis://127.0.0.1:6379/0 --scan --pattern 'gateway:lock:agent_inbound:*'
```

查看 TTL：

```bash
redis-cli -u redis://127.0.0.1:6379/0 ttl 'gateway:lock:agent_inbound:<session_key>'
```

查看 owner value：

```bash
redis-cli -u redis://127.0.0.1:6379/0 get 'gateway:lock:agent_inbound:<session_key>'
```

处理原则：

- TTL 正常递减，说明 owner 未续租，等待过期后接管。
- TTL 持续刷新，说明 worker 仍在续租，不应人工释放 PostgreSQL owner。
- 不要手动 `DEL` Redis lane key，除非已经确认进程停止且接受并发风险。

## 10. RabbitMQ 排查

查看队列：

```bash
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers
```

入站分区队列通常形如：

```text
agent_gateway.inbound.partition.0
agent_gateway.inbound.partition.1
...
```

关注：

- 某个 partition `messages` 持续增加：可能有热点 session 或该 partition consumer 不工作。
- `consumers` 为 0：worker 没有消费该队列。
- DLQ 有消息：broker 消费或 task reserve/handler 可能失败。

RabbitMQ 管理台：

```text
http://127.0.0.1:15672
```

默认账号：

```text
admin / admin123
```

## 11. 常见故障处置

### 11.1 飞书消息已进入网关但无回复

1. Dashboard 看 `入站Broker`、`Broker总积压`、`被锁会话`。
2. 控制面查 `tasks.executions`。
3. 控制面查 `tasks.lanes` 和 `tasks.lanes.history`。
4. 如果存在 stale lane，按第 7 节做恢复预检。
5. 如果模型调用失败，查看最近错误和 `task.worker.failed` 事件。

### 11.2 同一 session 一直阻塞

1. 查 Redis TTL 是否还在续租。
2. 查 PostgreSQL `session_lanes` 的 `renewed_at`。
3. 查 `task.worker.started/completed/failed` 是否有对应 task。
4. TTL 已过期且 PostgreSQL 仍 owned 时，执行 recovery dry-run。
5. dry-run 正常且确认 worker 已退出后，再 `execute=true`。

### 11.3 RabbitMQ 积压但 Redis 没锁

可能原因：

- worker 未启动。
- worker 没包含 `worker` runtime role。
- RabbitMQ URL 指向错误。
- task store reserve 失败。

检查：

```bash
agent-gateway doctor
docker compose logs -f gateway
docker compose exec rabbitmq rabbitmqctl list_queues name messages consumers
```

### 11.4 Redis 不可用

影响：

- 多实例下 session 互斥能力下降。
- Cron 幂等、飞书去重、限流可能降级。

处理：

1. 先恢复 Redis。
2. 检查 worker 是否产生 `retrying`。
3. 不建议在 Redis 不可用时扩大 worker 并发。

### 11.5 PostgreSQL 不可用

影响：

- 任务事实状态、runtime events、session_lanes 和审计视图不可用。
- 多 worker 原子 reserve 失去主存储保障。

处理：

1. 恢复 PostgreSQL。
2. 执行 `agent-gateway postgres-check-schema`。
3. 查 `tasks` 是否存在长时间 running/retrying。
4. 不建议在 PostgreSQL 不可用时执行批量恢复。

## 12. 禁止操作

- 不要把完整用户消息正文写入 RabbitMQ。
- 不要绕过控制面直接批量删除 `session_lanes`。
- 不要在 Redis TTL 未过期且 worker 仍续租时释放 PostgreSQL owner。
- 不要把 `GATEWAY_TASK_WORKER_ID` 配成多个实例相同值。
- 不要把 `GATEWAY_INBOUND_RABBITMQ_PREFETCH` 调高后仍宣称严格 session 顺序。

## 13. 最小恢复检查单

1. `agent-gateway doctor` 无 FAIL。
2. `agent-gateway postgres-check-schema` 通过。
3. Dashboard 后台任务卡片无持续增长的 Broker 积压。
4. `tasks.lanes` 中 stale owner 数量为 0，或已完成 recovery 审计。
5. `tasks.lanes.recovery.events` 能看到本次 dry-run / released / completed。
6. `smoke_distributed_lane.py --scenario inbound` 通过。
7. `smoke_distributed_lane.py --scenario postgres-lane` 通过。
