# 备份与恢复指南

本文档对应 Phase 20.7.4，用于单机 Docker Compose 或本地部署场景下的数据备份、恢复和升级前保护。

## 数据边界

| 数据 | 位置 | 重要性 | 说明 |
| --- | --- | --- | --- |
| `.env` | 项目根目录 | 高 | 包含模型密钥、飞书密钥、中间件地址和运行开关；只能备份到私有位置。 |
| `config/` | 项目根目录 | 高 | 运行配置、通道配置、Agent 绑定和模型 profile。 |
| `workspace/` | 项目根目录 | 高 | Prompt、Skills、Cron、记忆入口和运行工作区。 |
| `data/` | 项目根目录 | 中到高 | JSONL fallback、审计数据、本地队列和迁移备份。 |
| PostgreSQL | Docker volume `postgres-data` 或外部数据库 | 高 | 会话、任务、事件、错误、指标、记忆索引、投递事实状态和 lane 状态。 |
| RabbitMQ | Docker volume `rabbitmq-data` | 中 | 分发队列、DLQ 和 broker 元数据；事实状态仍以 PostgreSQL / TaskStore 为准。 |
| Redis | Docker volume `redis-data` | 中 | 去重、幂等、限流和 session lane ownership；多数状态有 TTL，但 AOF 可帮助重启后恢复短期状态。 |

## 推荐备份策略

- 每次升级前：备份 `.env`、`config/`、`workspace/`、`data/`，并导出 PostgreSQL。
- 每天：至少做一次 PostgreSQL 逻辑备份和项目运行目录归档。
- 每周：做一次 Docker volume 停机归档，覆盖 PostgreSQL、RabbitMQ 和 Redis 卷。
- 恢复优先级：先恢复 `.env/config/workspace/data`，再恢复 PostgreSQL，最后根据需要恢复 RabbitMQ / Redis。

## 创建备份目录

```bash
cd ~/Desktop/claw0/gateway
export BACKUP_ROOT="$HOME/gateway-backups/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_ROOT"
```

备份目录建议放在项目目录之外，避免被 Docker 构建上下文、git 或清理脚本误处理。

## 备份项目文件

```bash
cd ~/Desktop/claw0/gateway

tar --warning=no-file-changed -czf "$BACKUP_ROOT/gateway-files.tgz" \
  .env \
  config \
  workspace \
  data \
  README.md \
  PROJECT_PLAN.md \
  deploy \
  doc
```

如果只想备份运行必需数据，可以去掉 `README.md`、`PROJECT_PLAN.md`、`deploy` 和 `doc`。

## 备份 PostgreSQL

Docker Compose 模式：

```bash
cd ~/Desktop/claw0/gateway
docker compose exec -T postgres pg_dump -U postgres -d postgres --format=custom > "$BACKUP_ROOT/postgres.dump"
```

本机 PostgreSQL 模式：

```bash
pg_dump "postgresql://postgres:postgres@127.0.0.1:5432/postgres" --format=custom > "$BACKUP_ROOT/postgres.dump"
```

校验备份：

```bash
ls -lh "$BACKUP_ROOT/postgres.dump"
pg_restore --list "$BACKUP_ROOT/postgres.dump" >/dev/null
```

## 备份 RabbitMQ

RabbitMQ 在本项目中不是事实状态源。通常优先依赖 PostgreSQL / TaskStore 重建 pending/retrying 队列：

```bash
docker compose exec gateway agent-gateway delivery-republish --limit 1000
```

如果需要完整保留 RabbitMQ 队列和 broker 元数据，建议停机后归档 Docker volume：

```bash
cd ~/Desktop/claw0/gateway
docker compose stop gateway rabbitmq
docker run --rm \
  -v gateway_rabbitmq-data:/volume:ro \
  -v "$BACKUP_ROOT":/backup \
  alpine tar -czf /backup/rabbitmq-data.tgz -C /volume .
docker compose up -d rabbitmq gateway
```

如果 Compose 项目名不是默认目录名，先用下面命令确认真实 volume 名称：

```bash
docker volume ls | grep rabbitmq
```

## 备份 Redis

Redis 主要保存短期协调状态，通常不作为强一致事实状态源。需要保留时可停机归档 volume：

```bash
cd ~/Desktop/claw0/gateway
docker compose stop gateway redis
docker run --rm \
  -v gateway_redis-data:/volume:ro \
  -v "$BACKUP_ROOT":/backup \
  alpine tar -czf /backup/redis-data.tgz -C /volume .
docker compose up -d redis gateway
```

确认真实 volume 名称：

```bash
docker volume ls | grep redis
```

## 停机备份全部中间件卷

这一步适合升级前或迁移机器前执行，会短暂停止服务：

```bash
cd ~/Desktop/claw0/gateway
docker compose down

for volume in postgres-data rabbitmq-data redis-data; do
  docker run --rm \
    -v "gateway_${volume}:/volume:ro" \
    -v "$BACKUP_ROOT":/backup \
    alpine tar -czf "/backup/${volume}.tgz" -C /volume .
done

docker compose up -d
```

如果 volume 名称不同，先执行：

```bash
docker volume ls | grep gateway
```

## 恢复项目文件

恢复前先停止 Gateway，避免恢复过程中继续写入：

```bash
cd ~/Desktop/claw0/gateway
docker compose stop gateway
tar -xzf "$BACKUP_ROOT/gateway-files.tgz" -C ~/Desktop/claw0/gateway
```

恢复 `.env` 后先检查配置：

```bash
docker compose run --rm gateway agent-gateway doctor
```

## 恢复 PostgreSQL

恢复会覆盖目标库内容。执行前必须确认目标环境正确：

```bash
cd ~/Desktop/claw0/gateway
docker compose stop gateway
docker compose exec -T postgres dropdb -U postgres postgres
docker compose exec -T postgres createdb -U postgres postgres
docker compose exec -T postgres pg_restore -U postgres -d postgres --clean --if-exists < "$BACKUP_ROOT/postgres.dump"
docker compose exec gateway agent-gateway postgres-check-schema
docker compose up -d gateway
```

如果不希望删除整库，可以先恢复到临时数据库做比对：

```bash
docker compose exec -T postgres createdb -U postgres gateway_restore_check
docker compose exec -T postgres pg_restore -U postgres -d gateway_restore_check < "$BACKUP_ROOT/postgres.dump"
docker compose exec -T postgres psql -U postgres -d gateway_restore_check -c "\dt"
```

## 恢复 RabbitMQ / Redis 卷

恢复 Docker volume 前必须停机，并确认目标 volume 名称。下面示例以默认 Compose 项目名 `gateway` 为准。

RabbitMQ：

```bash
cd ~/Desktop/claw0/gateway
docker compose down
docker volume rm gateway_rabbitmq-data
docker volume create gateway_rabbitmq-data
docker run --rm \
  -v gateway_rabbitmq-data:/volume \
  -v "$BACKUP_ROOT":/backup \
  alpine sh -c "tar -xzf /backup/rabbitmq-data.tgz -C /volume"
docker compose up -d
```

Redis：

```bash
cd ~/Desktop/claw0/gateway
docker compose down
docker volume rm gateway_redis-data
docker volume create gateway_redis-data
docker run --rm \
  -v gateway_redis-data:/volume \
  -v "$BACKUP_ROOT":/backup \
  alpine sh -c "tar -xzf /backup/redis-data.tgz -C /volume"
docker compose up -d
```

## 恢复后检查

```bash
cd ~/Desktop/claw0/gateway
docker compose ps
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-check-schema
docker compose exec gateway agent-gateway lane-doctor
docker compose exec gateway python scripts/smoke_distributed_lane.py --scenario readiness --rabbitmq-url amqp://admin:admin123@rabbitmq:5672/ --redis-url redis://redis:6379/0 --postgres-url postgresql://postgres:postgres@postgres:5432/postgres
```

如果 RabbitMQ 被清空或未恢复，使用事实状态重建待投递消息：

```bash
docker compose exec gateway agent-gateway delivery-republish --limit 1000
```

## 禁止操作

- 不要把 `.env`、PostgreSQL dump、RabbitMQ volume 或 Redis volume 上传到公开仓库。
- 不要在生产环境执行 `docker compose down -v`，除非已经确认有可恢复备份。
- 不要在 Gateway 仍在写入时直接复制 PostgreSQL volume；优先使用 `pg_dump` 或停机卷备份。
- 不要把 RabbitMQ 当作唯一事实状态源；投递和任务恢复应优先以 PostgreSQL / TaskStore 为准。
