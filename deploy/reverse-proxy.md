# 反向代理与 HTTPS 部署指南

本文档对应 Phase 20.7.5，用于把飞书 Webhook 从本机 HTTP 升级为公网 HTTPS 入口，同时避免 Dashboard 和控制面裸奔公网。

## 推荐边界

| 入口 | 是否建议公网暴露 | 推荐方式 |
| --- | --- | --- |
| 飞书 Webhook `/webhooks/feishu*` | 可以 | Caddy/Nginx HTTPS 反向代理到 `127.0.0.1:8766` |
| Dashboard `:8780` | 默认不暴露 | 仅本机访问；如必须远程访问，先加 VPN、SSH tunnel、IP 白名单或 Basic Auth |
| WebSocket 控制面 `:8765` | 不建议 | 仅本机或内网访问；控制操作风险高 |
| Redis/PostgreSQL/RabbitMQ | 不暴露 | 只允许 Docker 内部网络或本机回环访问 |

生产建议：

- 只给飞书配置 HTTPS Webhook 域名，例如 `https://gateway.example.com/webhooks/feishu`。
- Dashboard 继续使用 `http://127.0.0.1:8780`，远程查看时用 SSH tunnel。
- 中间件端口不映射公网。
- 如果必须公开 Dashboard，至少加 Basic Auth 和 IP 白名单。

## DNS 与端口前置条件

1. 准备域名，例如 `gateway.example.com`。
2. 将域名 A 记录指向服务器公网 IP。
3. 放通服务器安全组和防火墙端口：
   - `80/tcp`：Let's Encrypt HTTP-01 验证和 HTTP 跳转。
   - `443/tcp`：HTTPS Webhook。
4. 确认 Gateway 仍只绑定本机：

```bash
docker compose ps
curl -i http://127.0.0.1:8766/webhooks/feishu
curl -i http://127.0.0.1:8780
```

Webhook GET 返回 `405 method not allowed` 或类似拒绝信息是正常的，飞书实际会使用 POST。

## 方案 A：Caddy 推荐配置

Caddy 的优点是自动申请和续期证书，适合单机部署。

安装 Caddy 后创建配置：

```bash
sudo mkdir -p /etc/caddy
sudo nano /etc/caddy/Caddyfile
```

示例：

```caddyfile
gateway.example.com {
  encode zstd gzip

  handle /webhooks/feishu* {
    reverse_proxy 127.0.0.1:8766
  }

  handle /healthz {
    respond "ok" 200
  }

  handle {
    respond "not found" 404
  }
}
```

加载配置：

```bash
sudo caddy fmt --overwrite /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl status caddy --no-pager
```

验证：

```bash
curl -i https://gateway.example.com/healthz
curl -i https://gateway.example.com/webhooks/feishu
curl -i https://gateway.example.com/
```

预期：

- `/healthz` 返回 `200 ok`。
- `/webhooks/feishu` 对 GET 返回 `405` 或 Gateway 拒绝信息，说明已转发到 Gateway。
- `/` 返回 `404`，说明 Dashboard 没有被公开。

### Caddy Dashboard 受控访问示例

只有在确实需要公网访问 Dashboard 时才使用。优先使用 SSH tunnel：

```bash
ssh -L 8780:127.0.0.1:8780 user@gateway.example.com
```

如必须通过 HTTPS 访问，可增加独立子域名和 Basic Auth：

```caddyfile
ops.gateway.example.com {
  encode zstd gzip

  basicauth {
    admin $2a$14$替换为caddy_hash_password生成的哈希
  }

  reverse_proxy 127.0.0.1:8780
}
```

生成密码哈希：

```bash
caddy hash-password --plaintext '替换为强密码'
```

注意：Basic Auth 只能作为最低限度保护；更推荐 VPN、Tailscale、WireGuard 或 SSH tunnel。

## 方案 B：Nginx 配置

Nginx 适合已有 Nginx 运维体系的机器。证书可以用 `certbot` 或云厂商证书。

示例 `/etc/nginx/sites-available/agent-gateway.conf`：

```nginx
server {
    listen 80;
    server_name gateway.example.com;

    location /.well-known/acme-challenge/ {
        root /var/www/html;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name gateway.example.com;

    ssl_certificate /etc/letsencrypt/live/gateway.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/gateway.example.com/privkey.pem;

    client_max_body_size 10m;

    location /webhooks/feishu {
        proxy_pass http://127.0.0.1:8766;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 30s;
        proxy_send_timeout 30s;
    }

    location /healthz {
        return 200 "ok\n";
        add_header Content-Type text/plain;
    }

    location / {
        return 404;
    }
}
```

启用：

```bash
sudo ln -s /etc/nginx/sites-available/agent-gateway.conf /etc/nginx/sites-enabled/agent-gateway.conf
sudo nginx -t
sudo systemctl reload nginx
```

## 飞书后台填写方式

在飞书开放平台事件订阅或机器人 Webhook 配置中填写：

```text
https://gateway.example.com/webhooks/feishu
```

如果使用 secondary 路径：

```text
https://gateway.example.com/webhooks/feishu/secondary
```

配置后重点检查：

- 飞书应用的 `Verification Token` 与 `.env` 中一致。
- 如果飞书开启事件加密，必须配置对应 `Encrypt Key`，并确保容器内安装了加密依赖。
- 网关日志出现 `webhook event accepted`，说明入口验签、解析和入队成功。
- 如果出现 `method not allowed`，通常是浏览器或健康检查用 GET 访问 Webhook，不代表飞书 POST 失败。

## Compose 配合方式

保持 `docker-compose.yml` 中 Gateway 端口绑定为本机回环：

```yaml
ports:
  - "127.0.0.1:8765:8765"
  - "127.0.0.1:8766:8766"
  - "127.0.0.1:8780:8780"
```

反向代理运行在宿主机时，访问 `127.0.0.1:8766` 即可。不要为了公网 Webhook 把 Gateway 端口改成 `0.0.0.0:8766:8766`，否则 Dashboard / 控制面误暴露的风险会变高。

## 验收清单

```bash
curl -i https://gateway.example.com/healthz
curl -i https://gateway.example.com/
curl -i https://gateway.example.com/webhooks/feishu
docker compose logs -f gateway
docker compose exec gateway agent-gateway doctor
docker compose exec gateway agent-gateway lane-doctor
```

判定：

- `healthz` 返回 200。
- `/` 返回 404，Dashboard 未暴露。
- `/webhooks/feishu` 可到达 Gateway。
- 飞书发送测试消息后，Gateway 日志出现 `webhook event accepted` 和 `inbound dequeued`。
- `doctor` 不出现公网绑定风险或中间件连接失败。
- `lane-doctor` readiness 仍为 ready，说明 HTTPS 入口没有破坏入站任务队列和 lane 执行链路。

## 常见故障

| 现象 | 常见原因 | 处理 |
| --- | --- | --- |
| 证书申请失败 | DNS 未生效、80 端口未放通、域名指向错误 | 检查 A 记录、安全组和 `curl http://域名` |
| 飞书校验失败 | URL 填错、token 不一致、路径未转发 | 检查 `.env`、飞书后台配置和反向代理 path |
| Gateway 日志没有请求 | 代理没有转发到 `127.0.0.1:8766` | 检查 Caddy/Nginx 配置和本机端口绑定 |
| Dashboard 被公网访问 | 代理把 `/` 转发到了 `8780` | 立即撤销转发，改为 404、Basic Auth 或 SSH tunnel |
| Webhook GET 显示 `method not allowed` | 用浏览器或 curl GET 访问了 POST-only 路径 | 属于正常现象，飞书 POST 才是实际链路 |

## 安全底线

- 不要公开 Redis、PostgreSQL、RabbitMQ 管理端口。
- 不要把控制面 `8765` 直接反代到公网。
- 不要把 Dashboard 裸奔公网。
- 不要在公网日志、截图或 issue 中暴露飞书 token、encrypt key、模型 API key、PostgreSQL dump。
- 所有公网入口都应通过 HTTPS，飞书 Webhook 不应长期依赖裸 HTTP 或临时内网穿透。
