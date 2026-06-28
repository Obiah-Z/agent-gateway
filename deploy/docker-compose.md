# Docker Compose 部署说明

本文档对应 Phase 20.7.1，用于在单机上通过 Docker Compose 拉起 Gateway 及其基础依赖。

## 服务组成

| 服务 | 镜像/来源 | 作用 | 默认绑定 |
| --- | --- | --- | --- |
| `gateway` | 本项目 `Dockerfile` | AI Agent Gateway 主服务 | `127.0.0.1:8765/8766/8780` |
| `redis` | `redis:7-alpine` | 去重、幂等、限流等轻量协调 | `127.0.0.1:6379` |
| `postgres` | `postgres:16-alpine` | 配置、任务、事件、指标、记忆和投递事实状态 | `127.0.0.1:5432` |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | 可靠投递分发、ack、retry、DLQ | `127.0.0.1:5672`，管理台 `127.0.0.1:15672` |

默认端口只绑定本机回环地址，避免 Dashboard 和中间件直接暴露公网。公网 Webhook 和 Dashboard 访问应在后续反向代理阶段通过 HTTPS 和鉴权处理。

## 准备配置

```bash
cd ~/Desktop/claw0/gateway
cp .env.example .env
```

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
```

`doctor` 会检查模型配置、目录权限、Redis、PostgreSQL、RabbitMQ、PostgreSQL schema 和公网绑定风险。存在 `FAIL` 时会返回非零退出码。

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
| RabbitMQ 管理台 | `http://127.0.0.1:15672`，用户 `admin`，密码 `admin123` |

## 数据持久化

| 数据 | 持久化方式 |
| --- | --- |
| Gateway 配置 | 挂载 `./config:/app/config` |
| Workspace、Prompt、Skills、Cron | 挂载 `./workspace:/app/workspace` |
| 本地 JSONL fallback/audit 数据 | 挂载 `./data:/app/data` |
| PostgreSQL 数据 | Docker volume `postgres-data` |
| RabbitMQ 数据 | Docker volume `rabbitmq-data` |
| Redis AOF 数据 | Docker volume `redis-data` |

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

## 当前边界

- 当前 Compose 是单机编排，不是 Kubernetes 或多主高可用部署。
- Dashboard 默认仍无鉴权，只绑定本机；不要直接改成 `0.0.0.0` 暴露公网。
- 飞书 Webhook 生产接入需要 HTTPS，后续应通过 Nginx/Caddy 反向代理补齐。
- `gateway` 当前以 `GATEWAY_RUNTIME_ROLES=all` 运行；多实例拆分可在后续阶段按 `api/worker/delivery/scheduler/dashboard` 角色扩展。
