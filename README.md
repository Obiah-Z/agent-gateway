# AI Agent Gateway 智能体网关系统

AI Agent Gateway 是一个基于 Python 构建的智能体运行网关，用于承载多轮对话、工具调用、多通道接入、主动任务调度、可靠消息投递和运行观测等场景。项目以“稳定运行闭环”为核心目标，将用户输入、Agent 执行、会话持久化、出站投递和运维排障拆分为清晰的运行边界，逐步形成可本地部署、可扩展、可观测的 AI Agent 运行框架。

当前项目采用 Anthropic Messages API 兼容调用方式，默认可接入 DeepSeek Anthropic 兼容接口，也可以切换到其他兼容服务。

## 项目定位

本项目不是单纯的聊天机器人，而是面向个人自动化与智能体运行时的网关系统。它重点解决以下问题：

- 多个消息入口如何统一进入同一套 Agent 执行链路。
- 多轮会话、长期记忆和技能上下文如何稳定注入模型。
- 模型工具调用、结果解析和后续推理如何形成闭环。
- 普通回复、Cron、Heartbeat 等后台任务如何可靠投递到外部平台。
- 出错时如何快速定位是路由、模型、工具、投递还是通道问题。

## 核心能力

- `Agent 执行闭环`：围绕 `stop_reason` 构建模型调用、工具调用、结果回填和多轮交互处理流程。
- `多通道接入`：支持 CLI、Telegram、飞书 Webhook 和飞书长连接，并将不同来源统一抽象为入站消息。
- `消息路由`：基于 `config/bindings.json` 将 channel、account、peer、session 分流到不同 Agent。
- `会话持久化`：使用 JSONL 保存会话历史，支持历史重放、上下文压缩和长期对话保护。
- `工具调用`：通过工具注册表和 dispatch table 封装 bash、文件读写、记忆写入、联网搜索、GitHub 分析等能力。
- `Workspace 扩展`：通过 `SOUL.md`、`TOOLS.md`、`MEMORY.md`、Agent 局部提示词和 `skills/` 注入运行上下文。
- `主动任务`：支持 Heartbeat、全局 Cron、Agent 局部 Cron、新闻采集和技能调度。
- `可靠投递`：普通回复和后台任务统一先写入磁盘队列，再由后台运行时发送、重试和恢复。
- `运维观测`：提供 Dashboard、WebSocket 控制面、运行事件流、最近错误、指标快照和告警视图。

## 消息闭环

系统的核心链路如下：

```text
多通道输入
  -> ChannelRuntime
  -> GatewayDispatcher
  -> CommandQueue / 命名 lane
  -> AgentLoopRunner
  -> SessionStore
  -> DeliveryQueue
  -> DeliveryRuntime
  -> CLI / 飞书 / Telegram
```

关键设计点：

- 入站消息先进入统一队列，避免各通道直接耦合 Agent 执行逻辑。
- 同一会话使用命名 lane 串行处理，降低会话历史并发写入风险。
- Agent 执行结果先写会话，再写出站投递队列，避免发送失败导致结果丢失。
- 出站投递由后台运行时统一负责，支持失败重试、重放和状态查看。
- 每个关键节点写入 runtime event，Dashboard 可以按事件查看运行链路。

更完整的链路说明见 [消息闭环实现说明](doc/消息闭环实现说明.md)。

## 目录结构

```text
gateway/
  agent_gateway/
    runtime/
      domain/            # 领域模型、消息、路由、事件等核心结构
      execution/         # ChannelRuntime、Dispatcher、Agent Loop、DeliveryRuntime
      state/             # 会话、投递队列、事件、指标、告警等本地状态
      observability/     # 运行观测、指标和告警聚合
    gateways/
      messaging/         # CLI、Telegram 等消息通道
      feishu/            # 飞书 Webhook、长连接和 onboarding
      control/           # WebSocket JSON-RPC 控制面
    ai/
      context/           # Prompt、记忆、技能和上下文装配
      tools/             # 工具注册与工具实现
      news/              # 新闻源采集与摘要
    monitoring/          # 本地 Dashboard
    app.py               # 应用装配入口
    config.py            # 运行配置
    config_loader.py     # 静态配置加载
  config/                # agents、bindings、channels、profiles
  workspace/             # Prompt、记忆、skills、Cron、Heartbeat、新闻源
  data/                  # sessions、delivery queue、events、metrics、alerts
  doc/                   # 项目说明文档
  tests/                 # 自动化测试
```

## 快速开始

### 1. 安装

```bash
cd ~/Desktop/claw0/gateway
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

### 2. 配置模型

至少需要在 `.env` 中配置以下变量：

```env
ANTHROPIC_API_KEY=你的模型接口密钥
ANTHROPIC_BASE_URL=https://api.deepseek.com/anthropic
MODEL_ID=deepseek-v4-pro
```

`.env` 包含密钥和本机运行参数，不应提交到 Git。

### 3. 启动服务

```bash
agent-gateway serve
```

默认监听地址：

```text
WebSocket 控制面: ws://127.0.0.1:8765
飞书 Webhook:   http://127.0.0.1:8766/webhooks/feishu
运维 Dashboard: http://127.0.0.1:8780
```

### 4. 手动触发 Cron

```bash
agent-gateway cron-trigger <job_id>
agent-gateway cron-trigger <job_id> --no-flush
```

`--no-flush` 表示只写入可靠投递队列，不立即刷送。

## 配置说明

| 文件 | 说明 |
| --- | --- |
| `.env` | 运行时密钥、端口、目录、模型参数和通道参数 |
| `config/agents.json` | Agent 定义、工具策略、记忆策略和提示词策略 |
| `config/bindings.json` | channel/account/peer/session 到 Agent 的路由规则 |
| `config/channels.json` | CLI、Telegram、飞书等通道账号配置 |
| `config/profiles.json` | 模型服务 profile |
| `workspace/` | 系统提示词、长期记忆、skills、Heartbeat、Cron、新闻源和 Agent 局部提示词 |

## 飞书接入

项目支持两种飞书接入方式：

- `长连接模式`：适合本地开发或不方便暴露公网 Webhook 的场景，依赖本机已配置好的 `lark-cli`。
- `Webhook 模式`：适合公网部署或通过反向代理接入的场景，需要配置飞书事件回调地址。

Webhook 模式常用环境变量包括：

```env
FEISHU_APP_ID=
FEISHU_APP_SECRET=
FEISHU_VERIFICATION_TOKEN=
FEISHU_ENCRYPT_KEY=
FEISHU_WEBHOOK_HOST=0.0.0.0
FEISHU_WEBHOOK_PORT=8766
FEISHU_WEBHOOK_PATH=/webhooks/feishu
```

飞书 onboarding 页面用于绑定个人 Agent：

```text
http://127.0.0.1:8780/onboarding/feishu
```

## 主动任务与 Skill

Cron 支持两层配置：

- `workspace/CRON.json`：全局任务。
- `workspace/agents/<agent_id>/CRON.json`：Agent 局部任务。

后台任务默认禁止调用 `memory_write`，避免巡检、新闻简报或定时分析误写长期记忆。项目内置和自定义 Skill 放在：

```text
workspace/skills/
```

明确的长任务命令会先进入后台任务队列，再由 worker 执行。默认后台命令可通过 `.env` 扩展：

```env
GATEWAY_BACKGROUND_INBOUND_COMMANDS=/github-repo-analyzer,/space-advisor
```

当前已经支持的典型自用能力包括：

- 服务器空间巡检，只分析不自动删除。
- GitHub 热门仓库发现和技能灵感推荐。
- GitHub 仓库分析，并将分析结果落地为 Markdown 报告。

新闻源配置文件位于：

```text
workspace/agent-news-sources.json
```

当前支持 RSS、官网 HTML 页面、GitHub Releases 和 arXiv。

## 运维与可观测性

启动 `agent-gateway serve` 后，本地 Dashboard 默认运行在：

```text
http://127.0.0.1:8780
```

Dashboard 主要用于：

- 查看运行健康状态、agents、bindings、channels、profiles、heartbeat、cron 和 delivery 状态。
- 查看 pending / failed 投递队列，并执行 retry、discard、flush。
- 查看最近运行事件、最近错误和最近记忆写入。
- 查看指标快照、趋势变化、当前告警和近期告警历史。

常用 WebSocket JSON-RPC 方法：

| 方法 | 说明 |
| --- | --- |
| `runtime.status` | 查看运行态快照 |
| `health.check` | 执行健康检查 |
| `events.tail` | 查看最近运行事件 |
| `errors.recent` | 查看最近错误、失败或拒绝事件 |
| `memory.recent` | 查看最近写入的 daily memory 记录 |
| `tasks.list/get/cancel/retry` | 后台任务查看、详情、取消和重试 |
| `delivery.stats/list/retry/discard/flush` | 可靠投递队列运维 |
| `cron.list/trigger` | 主动任务查看与触发 |
| `feishu.onboarding.start/status/list` | 飞书绑定会话管理 |
| `feishu.long_connection.status` | 飞书长连接消费状态 |
| `agents.*`、`bindings.*`、`channels.*`、`profiles.*` | 运行配置查看、修改、保存和重载 |

## 测试

```bash
cd ~/Desktop/claw0/gateway
./.venv/bin/python -m compileall agent_gateway tests
./.venv/bin/python -m pytest tests -q
```

## 文档索引

- [项目架构说明](doc/项目架构说明.md)
- [消息闭环实现说明](doc/消息闭环实现说明.md)
- [项目计划](PROJECT_PLAN.md)

## 当前边界

- 当前主要面向单机本地运行，尚未引入数据库、分布式锁或多实例协调。
- Dashboard 默认无鉴权，仅建议绑定本机或可信网络访问。
- 当前以本地 JSONL 和文件状态为主，尚未接入集中式数据库、消息系统或多实例共享状态。
- Agent 权限模型已支持工具策略和 capability tags，但仍需继续增强审计、校验和权限预览。
- ChannelRuntime 当前仍以统一入站队列为主，lane 化、背压和热重启不丢消息已进入后续高优先级计划。
