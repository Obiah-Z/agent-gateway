# PostgreSQL 状态外置设计

## 目标

把当前依赖 JSONL 和本地文件的长期状态，逐步迁移到 PostgreSQL，优先解决分页查询、筛选、归档、审计和 Dashboard 聚合困难的问题。

## 保留原则

- JSONL 继续保留作为审计备份和降级路径。
- 迁移过程优先做“读接入”，再做“双写”，最后做“主写切换”。
- 先覆盖可查询列表，再覆盖高频写路径。

## 最小表

### `sessions`

用途：保存会话元数据和摘要。

建议字段：

- `id`
- `agent_id`
- `session_key`
- `channel`
- `account_id`
- `peer_id`
- `title`
- `summary`
- `created_at`
- `updated_at`
- `last_message_at`
- `message_count`
- `metadata`

### `tasks`

用途：保存后台任务实例。

建议字段：

- `id`
- `task_type`
- `source`
- `status`
- `agent_id`
- `session_key`
- `priority`
- `idempotency_key`
- `payload`
- `result_preview`
- `error`
- `retry_count`
- `created_at`
- `updated_at`
- `started_at`
- `finished_at`
- `metadata`

### `runtime_events`

用途：保存运行事件流。

建议字段：

- `event_id`
- `timestamp`
- `type`
- `status`
- `component`
- `message`
- `correlation_id`
- `agent_id`
- `session_key`
- `channel`
- `account_id`
- `peer_id`
- `delivery_id`
- `job_id`
- `error`
- `metadata`

### `errors`

用途：保存错误索引视图或物化副本。

建议字段：

- `id`
- `event_id`
- `timestamp`
- `component`
- `category`
- `severity`
- `message`
- `error`
- `correlation_id`
- `agent_id`
- `session_key`
- `metadata`

### `metrics`

用途：保存指标快照和趋势点。

建议字段：

- `id`
- `timestamp`
- `kind`
- `name`
- `value`
- `labels`
- `window_seconds`
- `metadata`

### `memory_entries`

用途：保存记忆注入和记忆写入记录。

建议字段：

- `id`
- `agent_id`
- `category`
- `content`
- `source_file`
- `created_at`
- `updated_at`
- `metadata`

### `config_audits`

用途：保存配置变更审计。

建议字段：

- `id`
- `entity_type`
- `entity_id`
- `action`
- `before`
- `after`
- `actor`
- `created_at`
- `metadata`

## 索引建议

- `sessions(agent_id, updated_at desc)`
- `sessions(session_key)`
- `tasks(status, updated_at desc)`
- `tasks(agent_id, updated_at desc)`
- `runtime_events(timestamp desc)`
- `runtime_events(correlation_id, timestamp desc)`
- `runtime_events(component, status, timestamp desc)`
- `errors(component, timestamp desc)`
- `metrics(kind, name, timestamp desc)`
- `memory_entries(agent_id, created_at desc)`
- `config_audits(entity_type, entity_id, created_at desc)`

## 接口边界

先定义统一仓储抽象，不直接改业务逻辑：

- `list`
- `get`
- `append`
- `upsert`
- `query`
- `delete`

控制面和 Dashboard 先接 `list/query`。

## 迁移顺序

1. 先接 `runtime_events` 和 `tasks`，因为它们直接影响 Dashboard 排障。
2. 再接 `sessions`，解决会话分页和历史检索。
3. 再接 `memory_entries` 和 `config_audits`，解决治理和审计。
4. 最后接 `metrics` 与 `errors` 的数据库化索引视图。

