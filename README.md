# AI Agent Gateway 智能体网关系统

基于 Python 构建的 AI Agent Gateway，用于把多轮对话、工具调用、多通道接入、主动任务执行、可靠投递和运维监控整合到同一个可运行框架中。

当前模型调用链采用 Anthropic Messages API 兼容格式，默认可通过 DeepSeek Anthropic 兼容接口运行，也可以切换到其他兼容服务。

## 核心能力

- Agent Loop：围绕 `stop_reason` 处理模型回复、工具调用和多轮交互。
- Tool Calling：通过 dispatch table 管理文件读写、bash、记忆检索、Web Search 等工具。
- 会话与上下文：基于 JSONL 保存 transcript，支持历史重放和上下文保护。
- 多通道接入：支持 CLI、Telegram、飞书，统一进入 dispatcher。
- 消息路由：基于 `bindings.json` 将不同 channel/account/peer/session 路由到不同 Agent。
- 记忆与技能注入：从 `workspace/` 加载提示词、长期记忆、skills 和 Agent 局部配置。
- 主动任务：支持 Heartbeat、Cron 和 AI Agent 每日简报推送。
- 可靠投递：普通回复、heartbeat、cron 输出先写入本地队列，再由后台 runtime 发送和重试。
- 运维控制面：通过 WebSocket JSON-RPC 和本地 Dashboard 查看健康状态、投递队列、Cron 与运行态。
- 飞书安全接入：支持 challenge、加密事件、签名校验、时间窗校验、事件去重和审计日志。

## 目录结构

```text
gateway/
  agent_gateway/
    core/                   领域层：Agent、消息、路由、ID 规范
    application/            应用层：Loop、dispatcher、control plane、调度编排
    interfaces/             接入层：WebSocket、Feishu HTTP、Feishu 长连接
    channels/               CLI / Telegram / Feishu 通道适配
    delivery/               本地可靠投递队列
    intelligence/           Prompt、记忆、技能发现
    monitoring/             本地运维 Dashboard
    news/                   AI Agent 简报采集与摘要
    sessions/               JSONL 会话存储与上下文保护
    tools/                  工具注册表与内置工具
    app.py                  应用装配与命令入口
    config.py               环境变量、路径与运行参数
  config/                   agents / bindings / channels / profiles 配置
  workspace/                提示词、记忆、技能、heartbeat、cron 和新闻源配置
  tests/                    单元测试与运行链路测试
```

## 快速启动

```bash
cd ~/Desktop/claw0/gateway
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

编辑 `.env`，至少配置模型接口：

```env
ANTHROPIC_API_KEY=你的模型接口密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro
```

启动网关：

```bash
agent-gateway serve
```

默认监听：

```text
WebSocket 控制面: ws://127.0.0.1:8765
飞书 Webhook:   http://127.0.0.1:8766/webhooks/feishu
运维 Dashboard: http://127.0.0.1:8780
```

## 飞书接入

### 长连接快速接入

如果只是本地或单机运行，推荐先使用飞书长连接模式。该模式不需要公网 IP、内网穿透或飞书 Webhook 请求地址，也不需要在网关 `.env` 中维护多组飞书密钥。

前置条件：

```bash
lark-cli config init --new
```

按提示完成飞书应用配置后，打开 `config/channels.json`，将 `feishu-long-local` 改为启用：

```json
{
  "channel": "feishu",
  "account_id": "feishu-long-local",
  "enabled": true,
  "label": "Feishu Long Connection",
  "config": {
    "connection_mode": "long_connection",
    "send_mode": "lark_cli",
    "event_key": "im.message.receive_v1",
    "event_keys": [
      "im.message.receive_v1",
      "im.chat.member.bot.added_v1"
    ],
    "event_identity": "bot",
    "event_command": "lark-cli",
    "lark_cli_command": "lark-cli",
    "lark_cli_identity": "bot",
    "render_mode": "text"
  }
}
```

启动网关后，系统会自动运行：

```bash
lark-cli event consume im.message.receive_v1 --as bot
```

收到的飞书消息会进入 `feishu-long-local` 账号，并通过 `config/bindings.json` 默认路由到 `main` Agent。回复会通过：

```bash
lark-cli im +messages-send --as bot
```

发送回飞书会话。

注意事项：

- 长连接模式仍需要在飞书开放平台为应用开通并订阅 `im.message.receive_v1` 事件。
- 如果你希望群聊里机器人被加入时自动完成接入，还需要订阅 `im.chat.member.bot.added_v1`。
- 发送消息需要应用具备对应 IM 消息发送权限，且机器人在目标会话中可见或已被加入群聊。
- 长连接适合本地开发、个人部署和快速接入；长期生产部署仍建议使用 Webhook 模式。

### 非专业用户扫码绑定

当前第一版的目标不是让用户配置复杂环境变量，而是让用户“扫码后直接进入飞书机器人会话”。

推荐流程：

1. 管理员先完成 `lark-cli config init`。
2. 管理员在飞书开放平台发布并可打开机器人会话，然后把机器人的打开链接填入 `.env` 的 `FEISHU_ONBOARDING_BOT_LINK`。
3. 启动网关后，用户访问 `/onboarding/feishu` 页面扫码。
4. 二维码会优先指向飞书机器人链接。用户在机器人里发第一句话后，网关会自动创建对应 Agent 并完成绑定。

如果你暂时还没有机器人打开链接，系统会自动回退到绑定码模式，用户可以继续用短期口令完成接入。

启动网关后访问：

```text
http://127.0.0.1:8780/onboarding/feishu
```

页面会生成一个可扫码页面和短期绑定码，例如：

```text
绑定 GATEWAY-ABC123
```

用户在飞书中执行以下任一操作：

- 私聊机器人后直接发送第一句话，系统会自动为该用户创建个人飞书 Agent，并绑定 `open_id -> agent_id`。
- 如果使用了绑定码模式，则私聊机器人发送 `绑定 GATEWAY-ABC123`，系统会自动为该用户创建个人飞书 Agent，并绑定 `open_id -> agent_id`。
- 如果开启群聊自动接入，机器人被加入群后也可以自动建群聊 Agent。

绑定完成后，网关会自动写入：

- `config/agents.json`
- `config/bindings.json`
- `workspace/agents/<agent_id>/IDENTITY.md`
- `workspace/agents/<agent_id>/SOUL.md`

该流程适合内部快速试用。绑定码是短期一次性口令，不包含密钥；后续可升级为飞书 OAuth 扫码登录和管理员一键创建应用。

### 你需要准备什么

最少需要这几项：

- 一个飞书开放平台应用。
- 这个应用在本机 `lark-cli` 里完成过 `config init`。
- 机器人的可打开链接，也就是你要填到 `FEISHU_ONBOARDING_BOT_LINK` 的地址。
- 如果你还想保留 webhook 模式，再准备 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_VERIFICATION_TOKEN`、`FEISHU_ENCRYPT_KEY`。
- 如果你要走长连接消息接收，确保应用侧订阅了 `im.message.receive_v1`，以及可选的 `im.chat.member.bot.added_v1`。

当前这版不要求你把 app secret 配进网关长连接路径里；长连接进程依赖的是 `lark-cli` 已登录的本机配置，而不是 `.env` 中的飞书密钥。

### Webhook 生产接入

飞书通道由 `.env` 与 `config/channels.json` 共同控制。密钥从环境变量读取，不写入 JSON 配置。

常用配置：

```env
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_VERIFICATION_TOKEN=xxx
FEISHU_ENCRYPT_KEY=xxx
FEISHU_BOT_OPEN_ID=ou_xxx
FEISHU_WEBHOOK_HOST=0.0.0.0
FEISHU_WEBHOOK_PORT=8766
FEISHU_WEBHOOK_PATH=/webhooks/feishu
```

如果通过公网 IP、域名或内网穿透暴露服务，飞书事件订阅地址填写外部可访问地址：

```text
http://<公网IP或域名>:8766/webhooks/feishu
```

若使用 HTTPS 反向代理，则填写代理后的 HTTPS 地址。

项目已支持多个飞书机器人账号。新增账号时，复制 `config/channels.json` 中的飞书账号配置块，替换 `account_id`、环境变量名和 `webhook_path`，再在 `config/bindings.json` 中增加对应路由即可。

## 主动任务与每日简报

`workspace/CRON.json` 中的 `agent-news-digest` 会每天北京时间 09:30 触发 `research` Agent，整理最近 24 小时内 AI Agent 相关动态，并推送到 `.env` 中配置的主动投递目标。

需要启用：

```env
GATEWAY_WEB_SEARCH_ENABLED=true
TAVILY_API_KEY=你的 Tavily Key
GATEWAY_PROACTIVE_CHANNEL=feishu
GATEWAY_PROACTIVE_ACCOUNT_ID=feishu-main
GATEWAY_PROACTIVE_PEER_ID=飞书 open_id 或 chat_id
GATEWAY_PROACTIVE_AGENT_ID=research
```

手动触发：

```bash
agent-gateway cron-trigger agent-news-digest
```

如果只想入队、不立即发送：

```bash
agent-gateway cron-trigger agent-news-digest --no-flush
```

新闻源配置位于：

```text
workspace/agent-news-sources.json
```

当前支持 RSS、官网 HTML 页面、GitHub Releases 和 arXiv。

## 运维 Dashboard

启动 `agent-gateway serve` 时会默认启动本地 Dashboard：

```text
http://127.0.0.1:8780
```

Dashboard 不依赖 npm、前端构建或外部 CDN，通过 WebSocket JSON-RPC 连接控制面，支持：

- 查看整体健康状态与 `health.check` 明细。
- 查看 agents、bindings、channels、profiles、heartbeat、cron、delivery 的运行态快照。
- 查看 pending / failed 投递队列。
- 对投递消息执行 retry、discard、flush。
- 查看 Cron 任务并手动触发。

相关配置：

```env
GATEWAY_DASHBOARD_ENABLED=true
GATEWAY_DASHBOARD_HOST=127.0.0.1
GATEWAY_DASHBOARD_PORT=8780
GATEWAY_DASHBOARD_REFRESH_INTERVAL_SECONDS=15
```

默认只监听 `127.0.0.1`。当前 Dashboard 已具备投递丢弃、重试和 Cron 触发能力，不建议在未增加 token 鉴权前直接暴露公网。

## 配置文件

- `.env`：运行时密钥、端口、目录、模型参数和通道参数；已被 `.gitignore` 排除，不应提交。
- `config/agents.json`：Agent Manifest、工具策略、记忆策略和提示词策略。
- `config/bindings.json`：channel/account/peer/session 到 Agent 的路由规则。
- `config/channels.json`：CLI、Telegram、飞书通道账号。
- `config/profiles.json`：模型服务 profile。
- `workspace/`：系统提示词、长期记忆、skills、Heartbeat、Cron、新闻源和 Agent 局部提示词。

## 控制面

网关通过 WebSocket JSON-RPC 暴露运行控制能力，主要包括：

- `runtime.status`：运行态快照。
- `health.check`：健康检查。
- `delivery.stats/list/retry/discard/flush`：可靠投递队列运维。
- `cron.list/trigger`：主动任务查看与触发。
- `feishu.onboarding.start/status/list`：飞书扫码绑定会话管理。
- `feishu.long_connection.status`：飞书长连接消费状态。
- `agents.*`、`bindings.*`、`channels.*`、`profiles.*`：运行配置查看、修改、保存和重载。

## 测试

```bash
cd ~/Desktop/claw0/gateway
./.venv/bin/python -m compileall agent_gateway tests
./.venv/bin/python -m pytest tests -q
```

## 当前边界

- 当前是单进程本地运行时，尚未引入数据库、分布式锁或多实例协调。
- Dashboard 默认无鉴权，仅建议本机访问。
- Agent 权限模型已支持工具策略和 capability tags，但仍需要继续增强审计、校验和权限预览。
- 运行态观测已具备 Dashboard 和健康检查接口，但还未接入长期趋势指标和告警渠道。

## 后续方向

- Dashboard token 鉴权与角色分级。
- 运行事件 JSONL 与最近错误视图。
- 指标快照、趋势图和飞书告警。
- Agent 权限预览、配置审计和回滚。
- 多 Agent handoff 与任务实例状态机。
