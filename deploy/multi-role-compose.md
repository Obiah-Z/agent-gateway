# Docker Compose 多角色部署说明

本文档用于把默认单进程 `all` 模式拆成入口、worker、投递、调度和 Dashboard 多个运行角色，更接近最终分布式 lane 运行形态。

## 适用场景

默认 `docker-compose.yml` 会启动一个 `gateway` 容器，并以 `GATEWAY_RUNTIME_ROLES=all` 运行所有模块。这适合本地开发和单机验证。

当你需要验证下面能力时，使用 `docker-compose.roles.yml`：

- 飞书 Webhook 入口只负责验签、去重、标准化和入站任务入队。
- `gateway-worker` 独立消费 `agent_inbound`、Cron、Heartbeat 和命令式后台任务。
- `gateway-delivery` 独立消费可靠出站投递队列。
- `gateway-scheduler` 只负责触发 Cron / Heartbeat，不直接跑 Agent。
- `gateway-dashboard` 独立提供 Dashboard、控制面和观测后台。

## 服务拆分

| 服务 | 角色 | 对应能力 | 是否暴露端口 |
| --- | --- | --- | --- |
| `gateway-api` | `api` | WebSocket 控制面、飞书 Webhook、入站标准化、入站任务 enqueue | `127.0.0.1:8765`、`127.0.0.1:8766` |
| `gateway-worker` | `worker` | 消费 `agent_inbound` / Cron / Heartbeat / 后台任务，执行 AgentLoop 和工具调用 | 不暴露 |
| `gateway-delivery` | `delivery` | 消费 RabbitMQ / PostgreSQL 可靠投递队列并发送到飞书等通道 | 不暴露 |
| `gateway-scheduler` | `scheduler` | 触发 Cron / Heartbeat，把任务写入任务队列 | 不暴露 |
| `gateway-dashboard` | `dashboard` | Dashboard、控制面和观测后台 | `127.0.0.1:8780` |

Redis、PostgreSQL、RabbitMQ 仍由基础 `docker-compose.yml` 提供。

## 启动

```bash
cd ~/Desktop/claw0/gateway
docker compose -f docker-compose.yml -f docker-compose.roles.yml up -d --build
```

查看状态：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml ps
docker compose -f docker-compose.yml -f docker-compose.roles.yml logs -f gateway-api gateway-worker gateway-delivery gateway-scheduler gateway-dashboard
```

这个 overlay 会把基础 `gateway` 服务放入 `single` profile，默认不会启动单进程 `gateway`，只启动拆分后的多角色服务。

如果要回到单进程模式：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml down
docker compose up -d --build
```

## 初始化和验收

首次启动或升级后执行：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-api agent-gateway doctor
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-api agent-gateway postgres-init
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-api agent-gateway postgres-check-schema
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-api agent-gateway lane-doctor
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-api python scripts/smoke_distributed_lane.py --scenario readiness --rabbitmq-url amqp://admin:admin123@rabbitmq:5672/ --redis-url redis://redis:6379/0 --postgres-url postgresql://postgres:postgres@postgres:5432/postgres
```

判定：

- `doctor` 不应出现基础配置和中间件连接 `FAIL`。
- `postgres-check-schema` 应返回 schema 正常。
- `lane-doctor` readiness 应为 ready。
- readiness smoke 应输出 `ready=true` 且失败项为 0。

## 飞书 Webhook

多角色模式下飞书 Webhook 仍由 `gateway-api` 提供：

```text
http://127.0.0.1:8766/webhooks/feishu
```

公网 HTTPS 接入参考 [反向代理与 HTTPS 部署指南](reverse-proxy.md)，反向代理仍转发到宿主机 `127.0.0.1:8766`。

## Worker 扩容注意事项

`docker-compose.roles.yml` 默认只启动一个 `gateway-worker`：

```yaml
GATEWAY_TASK_WORKER_ID: gateway-worker-1
GATEWAY_TASK_WORKER_CONCURRENCY: "4"
```

如果只是短期压测，可以提高单 worker 并发：

```yaml
GATEWAY_TASK_WORKER_CONCURRENCY: "8"
```

如果要长期运行多个 worker，建议复制出多个 worker 服务，并为每个服务配置不同的 `GATEWAY_TASK_WORKER_ID`，例如：

```yaml
gateway-worker-2:
  extends:
    file: docker-compose.roles.yml
    service: gateway-worker
  environment:
    GATEWAY_TASK_WORKER_ID: gateway-worker-2
```

不要让多个长期 worker 共用同一个 `GATEWAY_TASK_WORKER_ID`，否则 lane owner、worker 执行事件和 Dashboard 排障信息会混在一起。

## 出站投递扩容

可以复制多个 `gateway-delivery` 服务，可靠投递事实状态由 PostgreSQL `delivery_entries` 负责，RabbitMQ 负责唤醒和分发。多个 delivery worker 会通过 PostgreSQL reserve 避免重复发送同一条投递。

扩容后检查：

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-dashboard agent-gateway delivery-republish --limit 1000
docker compose -f docker-compose.yml -f docker-compose.roles.yml exec gateway-dashboard agent-gateway lane-doctor
```

## 停止

```bash
docker compose -f docker-compose.yml -f docker-compose.roles.yml down
```

不要在生产环境使用 `down -v`，除非已经完成 [备份与恢复](backup-restore.md)。

## 当前边界

- 这是单机 Compose 多进程拆分，不是跨机器高可用部署。
- 多 worker 长期运行时需要显式配置不同 `GATEWAY_TASK_WORKER_ID`。
- Dashboard 仍默认无内建鉴权，只绑定本机；公网访问必须走 VPN、SSH tunnel、Basic Auth 或后续 Dashboard 鉴权。
- Redis、PostgreSQL、RabbitMQ 仍是单实例中间件；真正高可用需要进一步引入托管服务、主从/集群或云产品。
