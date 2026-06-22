# AI Agent Gateway 智能体网关系统

AI Agent Gateway 是一个基于 Python 的智能体运行网关，用于统一承载多轮对话、工具调用、多通道接入、主动任务调度、可靠消息投递与运行观测。项目当前以单机本地运行时为主，采用 Anthropic Messages API 兼容调用方式，默认可接入 DeepSeek Anthropic 兼容接口，也可切换到其他兼容服务。

## 项目概述

系统当前具备以下核心能力：

- 多轮 Agent 执行闭环，围绕 `stop_reason` 驱动模型调用、工具调用和结果落盘
- 工具调用体系，支持 bash、文件读写、记忆、联网搜索等能力
- 基于 JSONL 的会话存储、上下文保护与长期记忆写入
- 多通道接入，支持 CLI、Telegram、飞书 Webhook、飞书长连接
- 基于 `config/bindings.json` 的消息路由与多 Agent 分流
- Heartbeat、全局 Cron、Agent 局部 Cron、新闻简报等主动任务
- 本地可靠投递队列、失败重试、投递重放
- WebSocket JSON-RPC 控制面与本地 Dashboard 运维面板
- 运行事件、指标快照、告警状态与近期错误视图

## 架构

项目目录结构如下：

```text
gateway/
  agent_gateway/
    runtime/
      domain/
      execution/
      state/
      observability/
    gateways/
      messaging/
      feishu/
      control/
    ai/
      context/
      tools/
      news/
    monitoring/
    app.py
    config.py
    config_loader.py
  config/
  workspace/
  data/
  tests/
```

各模块职责如下：

- `agent_gateway/runtime/`
  运行内核，负责领域模型、消息路由、执行闭环、会话状态、可靠投递、事件、指标和告警。

- `agent_gateway/gateways/`
  外部接入层，负责 CLI、Telegram、飞书、Webhook、长连接和 WebSocket 控制面协议。

- `agent_gateway/ai/`
  智能能力层，负责 Prompt 装配、记忆、技能、工具注册、联网搜索和新闻采集摘要。

- `agent_gateway/monitoring/`
  本地运维面板静态资源与服务端。

- `config/`
  静态配置目录，包含 agents、bindings、channels、profiles。

- `workspace/`
  工作区目录，包含系统 Prompt、Agent 局部提示词、记忆、技能、Heartbeat、Cron 和新闻源配置。

- `data/`
  运行数据目录，保存 sessions、delivery-queue、events、metrics、alerts 等状态文件。

## 启动

### 安装

```bash
cd ~/Desktop/claw0/gateway
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

### 模型配置

至少配置以下环境变量：

```env
ANTHROPIC_API_KEY=你的模型接口密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro
```

### 运行命令

```bash
agent-gateway serve
agent-gateway cron-trigger <job_id>
agent-gateway cron-trigger <job_id> --no-flush
```

- `serve`
  启动网关服务，包括控制面、通道接入、主动任务运行时和本地 Dashboard。

- `cron-trigger`
  手动触发指定 Cron 任务；`--no-flush` 表示只入队、不立即刷送。

默认监听地址：

```text
WebSocket 控制面: ws://127.0.0.1:8765
飞书 Webhook:   http://127.0.0.1:8766/webhooks/feishu
运维 Dashboard: http://127.0.0.1:8780
```

## 配置与接入

### 核心配置文件

| 文件 | 用途 |
| --- | --- |
| `.env` | 运行时密钥、端口、目录、模型参数和通道参数 |
| `config/agents.json` | Agent 定义、工具策略、记忆策略、提示词策略 |
| `config/bindings.json` | channel/account/peer/session 到 Agent 的路由规则 |
| `config/channels.json` | CLI、Telegram、飞书通道账号 |
| `config/profiles.json` | 模型服务 profile |
| `workspace/` | 系统提示词、长期记忆、skills、Heartbeat、Cron、新闻源和 Agent 局部提示词 |

### 飞书接入

项目支持两种飞书接入方式：

- 长连接模式：适合本地开发或无需公网回调的场景
- Webhook 模式：适合公网部署或经反向代理接入的场景

长连接模式依赖本机已配置好的 `lark-cli`。Webhook 模式通过 `.env` 提供 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_VERIFICATION_TOKEN`、`FEISHU_ENCRYPT_KEY` 等参数，并使用 `FEISHU_WEBHOOK_HOST/PORT/PATH` 暴露回调地址。

项目同时提供飞书 onboarding 页面，用于绑定个人 Agent。访问地址：

```text
http://127.0.0.1:8780/onboarding/feishu
```

### 主动任务

Cron 支持两层配置：

- `workspace/CRON.json`
  全局任务。

- `workspace/agents/<agent_id>/CRON.json`
  Agent 局部任务。

Cron 后台任务默认禁止调用 `memory_write`，避免后台巡检或新闻简报污染长期记忆。新闻源配置文件位于：

```text
workspace/agent-news-sources.json
```

当前支持 RSS、官网 HTML 页面、GitHub Releases 和 arXiv。

## 运维

### Dashboard

启动 `agent-gateway serve` 后，默认会启动本地 Dashboard：

```text
http://127.0.0.1:8780
```

Dashboard 通过 WebSocket JSON-RPC 连接控制面，主要用于：

- 查看健康检查、agents、bindings、channels、profiles、heartbeat、cron、delivery 状态
- 查看 pending / failed 投递队列并执行 retry、discard、flush
- 查看最近运行事件、最近错误、最近记忆写入
- 查看指标快照、趋势变化、当前告警和近期告警历史

### 控制面接口

常用 WebSocket JSON-RPC 方法如下：

| 方法 | 说明 |
| --- | --- |
| `runtime.status` | 查看运行态快照 |
| `health.check` | 执行健康检查 |
| `events.tail` | 查看最近运行事件 |
| `errors.recent` | 查看最近错误、失败或拒绝事件 |
| `memory.recent` | 查看最近写入的 daily memory 记录 |
| `delivery.stats/list/retry/discard/flush` | 可靠投递队列运维 |
| `cron.list/trigger` | 主动任务查看与触发 |
| `feishu.onboarding.start/status/list` | 飞书绑定会话管理 |
| `feishu.long_connection.status` | 飞书长连接消费状态 |
| `agents.*`、`bindings.*`、`channels.*`、`profiles.*` | 运行配置查看、修改、保存和重载 |

### 测试

```bash
cd ~/Desktop/claw0/gateway
./.venv/bin/python -m compileall agent_gateway tests
./.venv/bin/python -m pytest tests -q
```

### 当前边界

- 当前为单进程本地运行时，尚未引入数据库、分布式锁或多实例协调
- Dashboard 默认无鉴权，仅建议本机访问
- 当前以本地 JSONL / 文件状态为主，尚未接入集中式数据库、消息系统或多实例共享状态
- Agent 权限模型已支持工具策略和 capability tags，但仍需继续增强审计、校验和权限预览
