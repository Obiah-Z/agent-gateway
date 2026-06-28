# systemd 部署说明

本文档对应 Phase 20.7.3，用于在非 Docker 场景下通过 systemd 托管 AI Agent Gateway。

## 适用场景

- 服务器上已经安装 Python、Redis、PostgreSQL、RabbitMQ。
- 希望 Gateway 随系统启动，并由 systemd 负责失败重启和日志收集。
- 不希望使用 Docker Compose 管理应用进程。

如果希望一并拉起 Redis、PostgreSQL 和 RabbitMQ，优先使用 [Docker Compose 部署说明](docker-compose.md)。

## 文件说明

| 文件 | 说明 |
| --- | --- |
| `deploy/systemd/agent-gateway.service` | systemd service 示例 |
| `deploy/systemd/agent-gateway.env.example` | systemd 环境文件示例 |

示例文件默认项目路径为：

```text
/home/obiah/Desktop/claw0/gateway
```

如果部署到其他路径，需要同步修改 service 中的 `WorkingDirectory`、`ExecStartPre`、`ExecStart` 和环境文件中的目录变量。

## 安装步骤

### 1. 准备 Python 环境

```bash
cd /home/obiah/Desktop/claw0/gateway
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. 准备环境文件

```bash
sudo mkdir -p /etc/agent-gateway
sudo cp deploy/systemd/agent-gateway.env.example /etc/agent-gateway/agent-gateway.env
sudo chmod 600 /etc/agent-gateway/agent-gateway.env
sudo chown root:root /etc/agent-gateway/agent-gateway.env
```

编辑真实配置：

```bash
sudo nano /etc/agent-gateway/agent-gateway.env
```

至少需要填写：

```env
ANTHROPIC_API_KEY=你的模型密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro
```

如启用 PostgreSQL / Redis / RabbitMQ，请确保对应服务已安装并正在运行。

### 3. 初始化 PostgreSQL

```bash
cd /home/obiah/Desktop/claw0/gateway
source .venv/bin/activate
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env postgres-init
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env postgres-check-schema
```

如需回填本地状态：

```bash
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env postgres-migrate-local --dry-run
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env postgres-migrate-local
```

### 4. 安装 service

```bash
sudo cp deploy/systemd/agent-gateway.service /etc/systemd/system/agent-gateway.service
sudo systemctl daemon-reload
sudo systemctl enable agent-gateway
```

启动前先运行 doctor：

```bash
cd /home/obiah/Desktop/claw0/gateway
source .venv/bin/activate
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env doctor
```

启动服务：

```bash
sudo systemctl start agent-gateway
sudo systemctl status agent-gateway
```

## 日志与运维

查看实时日志：

```bash
journalctl -u agent-gateway -f
```

查看最近 200 行：

```bash
journalctl -u agent-gateway -n 200 --no-pager
```

重启服务：

```bash
sudo systemctl restart agent-gateway
```

停止服务：

```bash
sudo systemctl stop agent-gateway
```

## 升级流程

```bash
cd /home/obiah/Desktop/claw0/gateway
git pull
source .venv/bin/activate
pip install -e .
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env doctor
sudo systemctl restart agent-gateway
```

如有数据库 schema 变化：

```bash
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env postgres-init
agent-gateway --env-file /etc/agent-gateway/agent-gateway.env postgres-check-schema
```

## 当前边界

- service 示例默认使用单进程 `GATEWAY_RUNTIME_ROLES=all`。
- Dashboard 默认绑定 `127.0.0.1`，不建议直接暴露公网。
- 飞书 Webhook 生产环境需要 HTTPS，后续应通过 Nginx/Caddy 反向代理暴露。
- `ExecStartPre` 会执行 `agent-gateway doctor`；如果存在 `FAIL`，systemd 会拒绝启动服务。
- Redis、PostgreSQL、RabbitMQ 的安装、备份和高可用不由该 service 管理。
