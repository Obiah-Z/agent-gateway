# Docker Compose 部署说明

本文档用于通过 Docker Compose 启动 AI Agent Gateway 及其依赖，并统一说明三种运行方式：单进程模式、多角色模式、多 Worker 模式。

## 1. 运行模式

| 模式 | Compose 文件 | 适用场景 | 实际启动的 Gateway 服务 |
| --- | --- | --- | --- |
| 单进程模式 | `docker-compose.yml` | 本地开发、最小部署、快速验证 | `gateway`，角色为 `all` |
| 多角色模式 | `docker-compose.yml` + `docker-compose.roles.yml` | 验证入口、Worker、投递、调度、Dashboard 拆分 | `gateway-api`、`gateway-worker`、`gateway-delivery`、`gateway-scheduler`、`gateway-dashboard` |
| 多 Worker 模式 | 再叠加 `docker-compose.workers.yml` | 长任务并行、分布式 lane 验证、吞吐压测 | 多角色服务 + `gateway-worker-1/2/3` |

基础依赖始终由 `docker-compose.yml` 提供：

| 服务 | 镜像/来源 | 作用 | 默认暴露 |
| --- | --- | --- | --- |
| `redis` | `redis:7-alpine` | 去重、幂等、限流、session lane ownership | Compose 内部网络 |
| `postgres` | `postgres:16-alpine` | 配置、任务、事件、指标、记忆、投递事实状态、lane 状态 | Compose 内部网络 |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | 入站/出站 broker、ack、retry、DLQ | Compose 内部网络 |

默认端口只绑定宿主机回环地址 `127.0.0.1`，不要直接暴露公网。飞书 Webhook 的公网 HTTPS 接入参考 [反向代理与 HTTPS 部署指南](reverse-proxy.md)。

## 2. 准备配置

```bash
cd ~/Desktop/claw0/gateway
cp .env.example .env
```

至少确认：

```env
ANTHROPIC_API_KEY=你的模型密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro
```

Compose 会在容器内覆盖中间件地址：

```env
GATEWAY_REDIS_URL=redis://redis:6379/0
GATEWAY_POSTGRES_URL=postgresql://postgres:postgres@postgres:5432/postgres
GATEWAY_RABBITMQ_URL=amqp://admin:admin123@rabbitmq:5672/
```

所以 `.env` 中保留本机 `127.0.0.1` 配置也不影响容器运行。

## 3. 构建加速

当前 `Dockerfile` 已默认使用国内镜像源：

- `apt` 使用清华 Debian 镜像源。
- `pip` 使用清华 PyPI 镜像源。

如果 Docker daemon 配置了 registry mirror，基础镜像拉取会更快。该配置属于宿主机级别，不写入项目仓库。

## 4. 单进程模式

启动：

```bash
docker compose up -d --build
```

查看：

```bash
docker compose ps
docker compose logs -f gateway
```

初始化和验收：

```bash
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-init
docker compose exec gateway agent-gateway postgres-check-schema
docker compose exec gateway python scripts/smoke_distributed_lane.py --scenario readiness --rabbitmq-url amqp://admin:admin123@rabbitmq:5672/ --redis-url redis://redis:6379/0 --postgres-url postgresql://postgres:postgres@postgres:5432/postgres
```

访问：

| 能力 | 地址 |
| --- | --- |
| Dashboard | `http://127.0.0.1:8780` |
| Prometheus metrics | `http://127.0.0.1:8780/metrics` |
| WebSocket 控制面 | `ws://127.0.0.1:8765` |
| 飞书 Webhook | `http://127.0.0.1:8766/webhooks/feishu` |

停止：

```bash
docker compose down
```

## 5. 多角色模式

多角色模式把默认 `gateway=all` 拆成多个进程，更接近最终分布式 lane 架构。

启动：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml up -d --build
```

最终服务：

| 服务 | 角色 | 职责 | 暴露端口 |
| --- | --- | --- | --- |
| `gateway-api` | `api` | 飞书 Webhook、入站标准化、入站任务 enqueue | `127.0.0.1:8766` |
| `gateway-worker` | `worker` | 消费 `agent_inbound` / Cron / Heartbeat / 后台任务，执行 AgentLoop 和工具调用 | 不暴露 |
| `gateway-delivery` | `delivery` | 消费可靠出站投递队列并发送到飞书等通道 | 不暴露 |
| `gateway-scheduler` | `scheduler` | 触发 Cron / Heartbeat，把任务写入队列 | 不暴露 |
| `gateway-dashboard` | `dashboard` | Dashboard、WebSocket 控制面、观测后台 | `127.0.0.1:8765`、`127.0.0.1:8780` |

查看：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml ps
docker compose -f docker-compose.yml -f docker-compose.roles.yml logs -f gateway-api gateway-worker gateway-delivery gateway-scheduler gateway-dashboard
```

初始化和验收：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-api agent-gateway doctor
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-api agent-gateway postgres-init
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-api agent-gateway postgres-check-schema
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-dashboard agent-gateway lane-doctor
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-api python scripts/smoke_distributed_lane.py --scenario readiness --rabbitmq-url amqp://admin:admin123@rabbitmq:5672/ --redis-url redis://redis:6379/0 --postgres-url postgresql://postgres:postgres@postgres:5432/postgres
```

注意：

- 飞书 Webhook 由 `gateway-api` 提供：`http://127.0.0.1:8766/webhooks/feishu`。
- Dashboard 的 WebSocket 控制面由 `gateway-dashboard` 提供：`ws://127.0.0.1:8765`。
- 如果飞书能回复但 Dashboard WebSocket 失败，优先检查 `8765` 是否映射到了 `gateway-dashboard`，而不是 `gateway-api`。

停止：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml down
```

## 6. 多 Worker 模式

多 Worker 模式在多角色模式基础上停用默认 `gateway-worker`，改为启动三个显式命名的 worker：

```text
gateway-worker-1  GATEWAY_TASK_WORKER_ID=gateway-worker-1
gateway-worker-2  GATEWAY_TASK_WORKER_ID=gateway-worker-2
gateway-worker-3  GATEWAY_TASK_WORKER_ID=gateway-worker-3
```

启动：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.roles.yml \
  -f docker-compose.workers.yml \
  up -d --build
```

查看：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.roles.yml \
  -f docker-compose.workers.yml \
  ps
```

查看 worker 日志：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.roles.yml \
  -f docker-compose.workers.yml \
  logs -f gateway-worker-1 gateway-worker-2 gateway-worker-3
```

验收：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.roles.yml \
  -f docker-compose.workers.yml \
  exec gateway-dashboard agent-gateway lane-doctor
```

停止：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.roles.yml \
  -f docker-compose.workers.yml \
  down
```

不要让多个长期 worker 共用同一个 `GATEWAY_TASK_WORKER_ID`，否则 lane owner、worker 执行事件和 Dashboard 排障信息会混在一起。

## 7. Compose 文件组合规则

Compose 会按 `-f` 顺序合并配置，后面的文件覆盖或扩展前面的同名服务。

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml -f docker-compose.workers.yml config --services
```

预期输出包括：

```text
redis
postgres
rabbitmq
gateway-api
gateway-dashboard
gateway-delivery
gateway-scheduler
gateway-worker-1
gateway-worker-2
gateway-worker-3
```

不会启动：

```text
gateway
gateway-worker
```

因为它们分别被放入 `single` / `single-worker` profile，默认不启用。

## 8. RabbitMQ 管理台

默认不暴露 RabbitMQ 管理台。如需临时查看，可新建 `docker-compose.override.yml`：

```yaml
services:
  rabbitmq:
    ports:
      - "127.0.0.1:15672:15672"
```

然后执行：

```bash
docker compose up -d rabbitmq
```

如果宿主机已有 RabbitMQ 占用 `15672`，请换端口，例如：

```yaml
ports:
  - "127.0.0.1:15673:15672"
```

## 9. 数据持久化

| 数据 | 持久化方式 |
| --- | --- |
| Gateway 配置 | 挂载 `./config:/app/config` |
| Workspace、Prompt、Skills、Cron | 挂载 `./workspace:/app/workspace` |
| 本地 JSONL fallback/audit 数据 | 挂载 `./data:/app/data` |
| PostgreSQL 数据 | Docker volume `postgres-data` |
| RabbitMQ 数据 | Docker volume `rabbitmq-data` |
| Redis AOF 数据 | Docker volume `redis-data` |

备份与恢复步骤见 [备份与恢复指南](backup-restore.md)。生产升级前至少备份 `.env`、`config/`、`workspace/`、`data/` 和 PostgreSQL dump。RabbitMQ / Redis 可按停机卷备份保留，也可在恢复后通过事实状态重建短期队列。

## 10. 升级步骤

单进程模式：

```bash
cd ~/Desktop/claw0/gateway
git pull
docker compose up -d --build
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-check-schema
```

多角色或多 Worker 模式：

```bash
cd ~/Desktop/claw0/gateway
git pull
docker compose -f docker-compose.yml -f docker-compose.roles.yml -f docker-compose.workers.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.roles.yml -f docker-compose.workers.yml exec gateway-api agent-gateway doctor
docker compose -f docker-compose.yml -f docker-compose.roles.yml -f docker-compose.workers.yml exec gateway-api agent-gateway postgres-check-schema
```

如果升级涉及数据库 schema，再补跑：

```bash
docker compose exec gateway agent-gateway postgres-init
```

或在多角色模式下：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-api agent-gateway postgres-init
```

## 11. 常见故障

- `docker compose build` 失败：先确认 Dockerfile、基础镜像拉取和镜像源配置。
- `scripts/smoke_distributed_lane.py` 在容器内找不到：确认镜像已重新构建，当前 Dockerfile 会复制 `scripts/` 到 `/app/scripts`。
- `doctor` 提示 `/app/.env` 不存在：通常不影响运行，Compose 已把宿主机 `.env` 注入为环境变量。
- `postgres.schema` drift：执行 `postgres-init` 后再跑 `postgres-check-schema`。
- 飞书 Webhook 没响应：确认飞书应用配置、验签、加密密钥、机器人可见范围和反向代理路径。
- Dashboard WebSocket 失败但飞书能回复：多角色模式下确认 `8765` 映射到 `gateway-dashboard`。
- RabbitMQ 连接失败：容器内地址应使用 `amqp://admin:admin123@rabbitmq:5672/`。
- Redis 或 PostgreSQL 连接失败：Compose 内部应使用服务名 `redis`、`postgres`，不要写 `127.0.0.1`。
- `docker compose down` 没停干净：启动时用了几个 `-f` 文件，停止时也必须带同样的 `-f` 文件。

## 12. 当前边界

- 当前 Compose 是单机编排，不是 Kubernetes 或跨机器高可用部署。
- Dashboard 默认仍无内建鉴权，只绑定本机；不要直接暴露公网。
- 飞书 Webhook 生产接入需要 HTTPS，参考 [反向代理与 HTTPS 部署指南](reverse-proxy.md)。
- Redis、PostgreSQL、RabbitMQ 仍是单实例中间件；真正高可用需要托管服务、主从/集群或云产品。
- 多 Worker 模式能验证分布式 lane 和并行消费，但仍运行在单台 Docker 主机上。
