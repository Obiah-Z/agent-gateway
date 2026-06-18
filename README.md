# AI Agent Gateway 智能体网关系统

AI Agent Gateway 是一个基于 Python 的智能体运行网关，用于把多轮对话、工具调用、多通道接入、主动任务调度、可靠消息投递和运维观测整合到同一个可运行框架中。

项目当前以单机本地运行时为主，采用 Anthropic Messages API 兼容调用格式，默认可接入 DeepSeek Anthropic 兼容接口，也可以切换到其他兼容服务。整体目标不是只做一个聊天机器人，而是沉淀一个具备生产化思路的 Agent Runtime：可路由、可恢复、可观测、可扩展。

## 功能概览

| 模块 | 说明 |
| --- | --- |
| Agent Loop | 围绕 `stop_reason` 实现模型调用、工具调用、多轮闭环和结果落盘。 |
| Tool Calling | 通过工具注册表和 dispatch table 管理文件、bash、记忆、联网搜索等工具能力。 |
| 会话管理 | 基于 JSONL 保存 transcript，支持历史重放、上下文保护和会话隔离。 |
| 多通道接入 | 支持 CLI、Telegram、飞书 Webhook、飞书长连接等入口。 |
| 消息路由 | 通过 `config/bindings.json` 将 channel/account/peer/session 路由到不同 Agent。 |
| 记忆与 Prompt | 从 `workspace/` 加载身份、人格、工具说明、长期记忆、skills 和 Agent 局部提示词。 |
| 主动任务 | 支持 Heartbeat、全局 Cron、Agent 局部 Cron 和 AI Agent 每日简报。 |
| 可靠投递 | 普通回复、Heartbeat、Cron 输出先写入本地队列，再由后台 runtime 发送和重试。 |
| 运维控制面 | 通过 WebSocket JSON-RPC 和本地 Dashboard 查看健康状态、投递队列、Cron、事件链路和最近记忆写入。 |
| 飞书安全接入 | 支持 challenge、加密事件、签名校验、时间窗校验、事件去重和审计日志。 |

## 架构目录

```text
gateway/
  agent_gateway/
    core/                   领域层：Agent、消息、路由、ID 规范
    application/            应用层：Agent Loop、dispatcher、control plane、后台调度
    interfaces/             接入层：WebSocket、Feishu HTTP、Feishu 长连接
    channels/               CLI / Telegram / Feishu 通道适配
    delivery/               本地可靠投递队列
    intelligence/           Prompt 装配、记忆、技能发现
    monitoring/             本地运维 Dashboard
    news/                   AI Agent 新闻采集与摘要生成
    observability/          运行事件 JSONL、链路追踪、最近错误视图
    sessions/               JSONL 会话存储与上下文保护
    tools/                  工具注册表与内置工具
    app.py                  应用装配与命令入口
    config.py               环境变量、路径与运行参数
  config/                   agents / bindings / channels / profiles 配置
  workspace/                提示词、记忆、skills、Heartbeat、Cron、新闻源配置
  tests/                    单元测试与运行链路测试
```

## 快速启动

### 1. 安装

```bash
cd ~/Desktop/claw0/gateway
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

### 2. 配置模型接口

编辑 `.env`，至少配置以下变量：

```env
ANTHROPIC_API_KEY=你的模型接口密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro
```

### 3. 启动网关

```bash
agent-gateway serve
```

默认监听地址：

```text
WebSocket 控制面: ws://127.0.0.1:8765
飞书 Webhook:   http://127.0.0.1:8766/webhooks/feishu
运维 Dashboard: http://127.0.0.1:8780
```

## 核心配置

| 文件 | 用途 |
| --- | --- |
| `.env` | 运行时密钥、端口、目录、模型参数和通道参数。该文件已被 `.gitignore` 排除，不应提交。 |
| `config/agents.json` | Agent Manifest、工具策略、记忆策略和提示词策略。 |
| `config/bindings.json` | channel/account/peer/session 到 Agent 的路由规则。 |
| `config/channels.json` | CLI、Telegram、飞书通道账号。 |
| `config/profiles.json` | 模型服务 profile。 |
| `workspace/` | 系统提示词、长期记忆、skills、Heartbeat、Cron、新闻源和 Agent 局部提示词。 |

## 飞书接入

项目支持两种飞书接入方式：长连接和 Webhook。开发、本地运行或个人部署建议优先使用长连接；需要公网稳定接入时再使用 Webhook。

### 长连接模式

长连接模式不需要公网 IP、内网穿透或飞书 Webhook 回调地址。事件接收和消息发送依赖本机已配置好的 `lark-cli`。

初始化飞书 CLI：

```bash
lark-cli config init --new
```

启用 `config/channels.json` 中的 `feishu-long-local` 账号：

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

启动网关后，系统会自动消费飞书事件：

```bash
lark-cli event consume im.message.receive_v1 --as bot
```

回复会通过以下命令发送回飞书会话：

```bash
lark-cli im +messages-send --as bot
```

注意事项：

- 飞书开放平台需要为应用开通并订阅 `im.message.receive_v1`。
- 如果希望机器人被加入群聊时自动接入，还需要订阅 `im.chat.member.bot.added_v1`。
- 发送消息需要应用具备对应 IM 权限，且机器人在目标会话中可见。

### Webhook 模式

Webhook 模式适合公网部署或通过反向代理接入飞书事件订阅。密钥从环境变量读取，不写入 JSON 配置。

常用 `.env` 配置：

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

飞书事件订阅地址填写外部可访问地址：

```text
http://<公网IP或域名>:8766/webhooks/feishu
```

如果使用 HTTPS 反向代理，则填写代理后的 HTTPS 地址。

项目支持多个飞书机器人账号。新增账号时，复制 `config/channels.json` 中的飞书账号配置块，替换 `account_id`、环境变量名和 `webhook_path`，再在 `config/bindings.json` 中增加对应路由。

### 用户扫码绑定

项目提供轻量级飞书接入页面，适合内部试用或给非技术用户快速绑定个人 Agent。

推荐流程：

1. 管理员完成 `lark-cli config init`。
2. 管理员在飞书开放平台发布机器人，并将机器人的打开链接填入 `.env` 的 `FEISHU_ONBOARDING_BOT_LINK`。
3. 启动网关后，用户访问 `/onboarding/feishu` 页面扫码。
4. 用户在机器人里发送第一句话后，网关自动创建个人 Agent 并完成绑定。

访问地址：

```text
http://127.0.0.1:8780/onboarding/feishu
```

如果暂时没有机器人打开链接，系统会回退到绑定码模式，例如：

```text
绑定 GATEWAY-ABC123
```

绑定完成后，网关会自动写入：

- `config/agents.json`
- `config/bindings.json`
- `workspace/agents/<agent_id>/IDENTITY.md`
- `workspace/agents/<agent_id>/SOUL.md`

绑定码是短期一次性口令，不包含密钥。后续可以升级为飞书 OAuth 扫码登录和管理员一键创建应用。

## 主动任务

Cron 支持两层配置：

- `workspace/CRON.json`：全局任务，适合系统健康检查、全局提醒、通用主动任务。
- `workspace/agents/<agent_id>/CRON.json`：某个 Agent 自己的任务，适合研究简报、个人助理日程、专项巡检等场景。

Agent 局部 Cron 如果没有显式配置 `target.agent_id`，会默认使用目录名作为执行 Agent。例如 `workspace/agents/research/CRON.json` 中的任务会默认交给 `research` Agent 执行。运行时会把局部任务 ID 展示为 `<agent_id>:<job_id>`，例如：

```text
research:agent-news-digest
```

Cron 后台任务默认禁止调用 `memory_write`，避免系统巡检、新闻简报等后台输出污染长期记忆。需要长期保留的系统观察应通过明确的前台指令或后续专门的审计流程写入。

当前 `workspace/agents/research/CRON.json` 中的 `agent-news-digest` 会每天北京时间 09:30 触发 `research` Agent，整理最近 24 小时内 AI Agent 相关动态，并推送到 `.env` 中配置的主动投递目标。

启用每日简报需要配置：

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
agent-gateway cron-trigger research:agent-news-digest
```

只入队、不立即发送：

```bash
agent-gateway cron-trigger research:agent-news-digest --no-flush
```

如果某个 `job_id` 在所有 Cron 文件中唯一，也可以继续使用短 ID 触发；存在重名时应使用完整 ID。

新闻源配置：

```text
workspace/agent-news-sources.json
```

当前支持 RSS、官网 HTML 页面、GitHub Releases 和 arXiv。

## 运维 Dashboard

启动 `agent-gateway serve` 后，默认会启动本地 Dashboard：

```text
http://127.0.0.1:8780
```

Dashboard 不依赖 npm、前端构建或外部 CDN，通过 WebSocket JSON-RPC 连接控制面。

主要能力：

- 查看整体健康状态与 `health.check` 明细。
- 查看 agents、bindings、channels、profiles、heartbeat、cron、delivery 的运行态快照。
- 查看 pending / failed 投递队列，并执行 retry、discard、flush。
- 查看 Cron 任务，按全局任务和 Agent 局部任务分组展示，并支持手动触发。
- 查看最近运行事件、最近错误和按 `correlation_id` 聚合的链路。
- 查看最近写入的 daily memory 记录，用于排查记忆污染或误写入。

相关配置：

```env
GATEWAY_DASHBOARD_ENABLED=true
GATEWAY_DASHBOARD_HOST=127.0.0.1
GATEWAY_DASHBOARD_PORT=8780
GATEWAY_DASHBOARD_REFRESH_INTERVAL_SECONDS=15
GATEWAY_EVENTS_RETENTION_DAYS=14
```

运行事件按日期写入：

```text
data/events/runtime-events-YYYY-MM-DD.jsonl
```

`events.tail` 会跨最近事件文件读取数据，过期文件会按 `GATEWAY_EVENTS_RETENTION_DAYS` 自动清理。

默认 Dashboard 只监听 `127.0.0.1`。由于当前面板具备投递丢弃、重试和 Cron 触发能力，不建议在未增加 token 鉴权前直接暴露公网。

## 控制面接口

网关通过 WebSocket JSON-RPC 暴露运行控制能力。常用方法如下：

| 方法 | 说明 |
| --- | --- |
| `runtime.status` | 查看运行态快照。 |
| `health.check` | 执行健康检查。 |
| `events.tail` | 查看最近运行事件，支持按 component/status/correlation_id/agent/channel/job/delivery 过滤。 |
| `errors.recent` | 查看最近错误、失败或拒绝事件，支持按 component/correlation_id 过滤。`method not allowed` 等防御性拒绝不会进入错误列表。 |
| `memory.recent` | 查看最近写入的 daily memory 记录。 |
| `delivery.stats/list/retry/discard/flush` | 可靠投递队列运维。 |
| `cron.list/trigger` | 主动任务查看与触发。 |
| `feishu.onboarding.start/status/list` | 飞书扫码绑定会话管理。 |
| `feishu.long_connection.status` | 飞书长连接消费状态。 |
| `agents.*`、`bindings.*`、`channels.*`、`profiles.*` | 运行配置查看、修改、保存和重载。 |

## 测试

```bash
cd ~/Desktop/claw0/gateway
./.venv/bin/python -m compileall agent_gateway tests
./.venv/bin/python -m pytest tests -q
```

## 当前边界

- 当前是单进程本地运行时，尚未引入数据库、分布式锁或多实例协调。
- Dashboard 默认无鉴权，仅建议本机访问。
- Agent 权限模型已支持工具策略和 capability tags，但还需要继续增强审计、校验和权限预览。
- 运行态观测已具备 Dashboard、健康检查、最近事件、最近错误和最近记忆写入视图，但还未接入长期趋势指标和告警渠道。

## 后续方向

- Dashboard token 鉴权与角色分级。
- 指标快照、趋势图和飞书告警。
- Agent 权限预览、配置审计和回滚。
- 多 Agent handoff 与任务实例状态机。
- 多实例部署、锁协调和集中式持久化。
