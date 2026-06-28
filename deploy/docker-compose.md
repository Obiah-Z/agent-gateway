# Docker Compose 部署说明

本文档对应 Phase 20.7.1，用于在单机上通过 Docker Compose 拉起 Gateway 及其基础依赖。

## 服务组成

| 服务 | 镜像/来源 | 作用 | 默认绑定 |
| --- | --- | --- | --- |
| `gateway` | 本项目 `Dockerfile` | AI Agent Gateway 主服务 | `127.0.0.1:8765/8766/8780` |
| `redis` | `redis:7-alpine` | 去重、幂等、限流等轻量协调 | 仅 Compose 内部网络 |
| `postgres` | `postgres:16-alpine` | 配置、任务、事件、指标、记忆和投递事实状态 | 仅 Compose 内部网络 |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | 可靠投递分发、ack、retry、DLQ | 仅 Compose 内部网络 |

默认只把 Gateway 的控制面、Webhook 和 Dashboard 绑定到本机回环地址。Redis、PostgreSQL 和 RabbitMQ 不映射到宿主机端口，避免和本机已安装服务冲突，也避免中间件被误暴露。公网 Webhook 访问应通过 [反向代理与 HTTPS 部署指南](reverse-proxy.md) 配置；Dashboard 默认不要公网暴露。

## 准备配置

```bash
cd ~/Desktop/claw0/gateway
cp .env.example .env
```

## 构建加速

当前 `Dockerfile` 已默认切换为国内镜像源：

- `apt` 使用清华镜像源
- `pip` 使用清华 PyPI 镜像源

如果你本机的 Docker daemon 也配置了 registry mirror，那么基础镜像拉取会更快。这个属于宿主机级别配置，不写入项目仓库。

至少修改：

```env
ANTHROPIC_API_KEY=你的模型密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro
```

Compose 会覆盖容器内中间件地址：

```env
GATEWAY_REDIS_URL=redis://redis:6379/0
GATEWAY_POSTGRES_URL=postgresql://postgres:postgres@postgres:5432/postgres
GATEWAY_RABBITMQ_URL=amqp://admin:admin123@rabbitmq:5672/
```

因此 `.env` 中保留本机 `127.0.0.1` 配置也不影响容器运行。

## 启动依赖和网关

```bash
docker compose up -d --build
```

如果本机 Docker 没有 Compose v2 插件，可使用旧命令：

```bash
docker-compose up -d --build
```

查看状态：

```bash
docker compose ps
docker compose logs -f gateway
```

如需校验 Compose 语法，可以运行：

```bash
docker compose config
```

注意：`docker compose config` 会展开 `.env` 中的真实密钥，不要把完整输出粘贴到公开渠道。

首次启动后初始化 PostgreSQL schema：

```bash
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-init
docker compose exec gateway agent-gateway postgres-check-schema
docker compose exec gateway python scripts/smoke_distributed_lane.py --scenario readiness --rabbitmq-url amqp://admin:admin123@rabbitmq:5672/ --redis-url redis://redis:6379/0 --postgres-url postgresql://postgres:postgres@postgres:5432/postgres
```

`doctor` 会检查模型配置、目录权限、Redis、PostgreSQL、RabbitMQ、PostgreSQL schema 和公网绑定风险。存在 `FAIL` 时会返回非零退出码。
`readiness` smoke 会进一步确认入站任务队列、RabbitMQ 入站 broker、Redis lane ownership、PostgreSQL lane 状态、worker handler 和可靠出站投递是否满足最终分布式 lane 运行条件。

如需把已有本地 JSON/JSONL 状态回填到数据库：

```bash
docker compose exec gateway agent-gateway postgres-migrate-local --dry-run
docker compose exec gateway agent-gateway postgres-migrate-local
```

## 访问地址

| 能力 | 地址 |
| --- | --- |
| Dashboard | `http://127.0.0.1:8780` |
| Prometheus metrics | `http://127.0.0.1:8780/metrics` |
| WebSocket 控制面 | `ws://127.0.0.1:8765` |
| 飞书 Webhook | `http://127.0.0.1:8766/webhooks/feishu` |
| RabbitMQ 管理台 | 默认不暴露；如需临时查看，可添加本地 override 暴露 `15672` |

## 健康检查

启动后建议先跑一遍下面的检查：

```bash
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-init
docker compose exec gateway agent-gateway postgres-check-schema
docker compose exec gateway python scripts/smoke_distributed_lane.py --scenario readiness --rabbitmq-url amqp://admin:admin123@rabbitmq:5672/ --redis-url redis://redis:6379/0 --postgres-url postgresql://postgres:postgres@postgres:5432/postgres
```

判定规则：

- `doctor` 通过，说明基础配置、目录权限、依赖连接和 schema 前置条件正常。
- `postgres-init` 负责初始化表结构，不会删除已有数据。
- `postgres-check-schema` 用于确认表和列没有明显漂移，适合升级后核对。
- `readiness` smoke 通过，说明当前 Compose 环境已满足分布式 lane 最终形态的关键运行条件。

如果 `doctor` 失败，先看输出里的 `FAIL` 项，再决定是修 `.env`、修目录权限，还是先启动中间件。

临时暴露 RabbitMQ 管理台时，可以新建 `docker-compose.override.yml`：

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

如果宿主机已经有 RabbitMQ 占用 `15672`，请改用其它宿主机端口，例如 `"127.0.0.1:15673:15672"`。

## 数据持久化

| 数据 | 持久化方式 |
| --- | --- |
| Gateway 配置 | 挂载 `./config:/app/config` |
| Workspace、Prompt、Skills、Cron | 挂载 `./workspace:/app/workspace` |
| 本地 JSONL fallback/audit 数据 | 挂载 `./data:/app/data` |
| PostgreSQL 数据 | Docker volume `postgres-data` |
| RabbitMQ 数据 | Docker volume `rabbitmq-data` |
| Redis AOF 数据 | Docker volume `redis-data` |

备份与恢复步骤见 [备份与恢复指南](backup-restore.md)。生产升级前至少备份 `.env`、`config/`、`workspace/`、`data/` 和 PostgreSQL dump；RabbitMQ / Redis 可按停机卷备份保留，也可在恢复后通过事实状态重建短期队列。

## 停止和清理

停止服务：

```bash
docker compose down
```

停止并删除中间件数据卷：

```bash
docker compose down -v
```

`down -v` 会删除 PostgreSQL、RabbitMQ 和 Redis 数据，生产环境不要随意执行。

## 常见故障

- `docker compose build` 失败：先确认 Dockerfile 是否已修复，特别是 `heredoc`、基础镜像拉取和镜像源配置。
- `agent-gateway doctor` 报数据库失败：确认容器内的 `postgres` 已就绪，再重新执行 `postgres-init` 和 `postgres-check-schema`。
- 飞书 Webhook 没有响应：确认飞书应用配置、验签、加密密钥、机器人可见范围和 webhook 路径。
- RabbitMQ 连接失败：确认 `GATEWAY_RABBITMQ_URL=amqp://admin:admin123@rabbitmq:5672/`，而不是宿主机地址。
- Redis 或 PostgreSQL 连接不上：Compose 内部应使用服务名 `redis`、`postgres`，不要写 `127.0.0.1`。

## 升级步骤

```bash
cd ~/Desktop/claw0/gateway
git pull
docker compose up -d --build
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway postgres-check-schema
```

如果升级涉及数据库 schema，再补跑：

```bash
docker compose exec gateway agent-gateway postgres-init
```

## 当前边界

- 当前 Compose 是单机编排，不是 Kubernetes 或多主高可用部署。
- Dashboard 默认仍无鉴权，只绑定本机；不要直接改成 `0.0.0.0` 暴露公网。
- 飞书 Webhook 生产接入需要 HTTPS，参考 [反向代理与 HTTPS 部署指南](reverse-proxy.md)；Dashboard 默认不要裸奔公网。
- `gateway` 当前以 `GATEWAY_RUNTIME_ROLES=all` 运行；多实例拆分可按 `api/worker/delivery/scheduler/dashboard` 角色扩展。拆出多个 worker 时，为每个实例配置不同的 `GATEWAY_TASK_WORKER_ID`，并按机器容量调整 `GATEWAY_TASK_WORKER_CONCURRENCY`。
