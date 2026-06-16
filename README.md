# AI Agent Gateway 智能体网关系统

这是一个基于 Python 构建的 AI Agent Gateway 项目，用于把多轮对话、工具调用、多通道接入、主动任务执行和可靠投递整合到同一个可运行框架中。

当前模型调用链采用 Anthropic Messages API 兼容格式，默认可通过 DeepSeek Anthropic 兼容接口运行，也可以切换到其他兼容服务。

## 核心能力

- Agent Loop：围绕 `stop_reason` 处理模型回复、工具调用、多轮交互和最终输出。
- Tool Calling：通过工具注册表和 dispatch table 管理 bash、文件读写、记忆检索、Web Search 等工具。
- 会话持久化：使用 JSONL 保存 transcript，支持历史重放、上下文保护和长对话压缩。
- 多通道接入：支持 CLI、Telegram、飞书，并通过统一 dispatcher 进入同一条执行链。
- 消息路由：基于 `bindings.json` 将不同 channel、account、peer、session 路由到不同 Agent。
- 记忆与技能注入：从 `workspace/` 加载 SOUL、TOOLS、MEMORY、AGENTS、skills 等提示词和长期记忆。
- 主动任务：支持 Heartbeat 与 Cron，由系统主动触发后台 Agent 任务。
- 可靠投递：普通回复、heartbeat、cron 输出都会先写入本地投递队列，再由后台 runtime 发送和重试。
- 运行控制面：通过 WebSocket JSON-RPC 管理 agents、bindings、channels、profiles 等配置。
- 飞书安全接入：支持 webhook challenge、加密消息、签名校验、时间窗校验、事件去重和审计日志。

## 目录结构

```text
gateway/
  agent_gateway/
    app.py                  应用装配与命令入口
    config.py               环境变量、路径与运行参数
    config_loader.py        agents/bindings/profiles/channels 配置加载
    models.py               核心数据模型
    router.py               消息路由与 session key 生成
    agents.py               Agent 注册中心
    channels/               CLI / Telegram / Feishu 通道适配
    delivery/               本地可靠投递队列
    intelligence/           Prompt、记忆、技能发现
    runtime/                Agent Loop、dispatcher、control plane、autonomy
    sessions/               JSONL 会话存储与上下文保护
    tools/                  工具注册表与内置工具
  config/
    agents.json             Agent Manifest 配置
    bindings.json           路由绑定配置
    channels.json           通道账号配置
    profiles.json           模型服务 profile 配置
  workspace/                系统提示词、记忆、技能、heartbeat、cron 模板
  tests/                    单元测试与运行链路测试
  .env.example              环境变量样例
  PROJECT_PLAN.md           后续阶段计划
  PROJECT_STUDY_GUIDE.md    代码阅读指南
```

## 快速启动

```bash
cd ~/Desktop/gateway
python -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

编辑 `.env`，至少配置：

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
WebSocket: ws://127.0.0.1:8765
Feishu Webhook: http://127.0.0.1:8766/webhooks/feishu
Dashboard: http://127.0.0.1:8780
```

## 本地 CLI 对话

`config/channels.json` 默认启用 `cli-local` 通道。启动 `agent-gateway serve` 后，可以直接在终端输入消息，消息会进入统一 dispatcher，并走完整的路由、会话、模型调用、工具调用和可靠投递链路。

## 飞书接入

飞书通道由 `.env` 与 `config/channels.json` 共同控制。当前默认账号是 `feishu-main`，密钥从环境变量读取，不会写入 JSON 配置。

需要配置的主要变量：

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

如果本机通过公网 IP、域名或内网穿透地址暴露，飞书事件订阅中的请求地址应填写外部可访问地址：

```text
http://<你的公网IP或域名>:8766/webhooks/feishu
```

如果前面还有 HTTPS 反向代理，则填写代理后的 HTTPS 地址。服务端本地监听仍建议使用：

```env
FEISHU_WEBHOOK_HOST=0.0.0.0
FEISHU_WEBHOOK_PORT=8766
```

飞书 webhook 当前已支持：

- URL challenge 校验
- 加密事件解密
- 请求签名校验
- 回调时间窗校验
- event_id / message_id 去重
- 入站事件审计日志
- Markdown 自动转飞书卡片
- 长回复分页
- 可选状态卡片按钮回调
- 多个飞书机器人账号按 webhook path 分发

### 新增独立飞书机器人账号

项目已预留第二个独立飞书账号 `feishu-secondary`，默认处于禁用状态。启用前需要在飞书开放平台创建新的机器人应用，并准备：

- 新应用的 `App ID`
- 新应用的 `App Secret`
- 事件订阅的 `Verification Token`
- 事件订阅的 `Encrypt Key`
- 该机器人的 `bot_open_id`

然后在 `.env` 中填写：

```env
FEISHU_SECONDARY_APP_ID=cli_xxx
FEISHU_SECONDARY_APP_SECRET=xxx
FEISHU_SECONDARY_VERIFICATION_TOKEN=xxx
FEISHU_SECONDARY_ENCRYPT_KEY=xxx
FEISHU_SECONDARY_BOT_OPEN_ID=ou_xxx
FEISHU_SECONDARY_RENDER_MODE=auto
FEISHU_SECONDARY_CARD_PAGE_MAX_BYTES=6000
FEISHU_SECONDARY_TEXT_PAGE_MAX_BYTES=12000
FEISHU_SECONDARY_ENABLE_STATEFUL_CARDS=false
```

再把 `config/channels.json` 中 `feishu-secondary` 的 `enabled` 改为 `true`。该账号的 webhook path 已预留为：

```json
"webhook_path": "/webhooks/feishu/secondary"
```

第二个飞书应用事件订阅中的请求地址填写对应的外部可访问地址：

```text
http://<你的公网IP或域名>:8766/webhooks/feishu/secondary
```

`config/bindings.json` 已预留按 `account_id=feishu-secondary` 路由到 `feishu-secondary` Agent 的规则；该 Agent 的专属提示词位于 `workspace/agents/feishu-secondary/`。如果你要新增第三、第四个飞书机器人，复制 `feishu-secondary` 的配置块，换成新的 `account_id`、环境变量名、`webhook_path` 和绑定规则即可。

## 配置说明

`.env` 存放运行时密钥、端口、目录、模型参数和通道参数。该文件已被 `.gitignore` 排除，不应提交到仓库。

`config/*.json` 存放结构化配置：

- `agents.json`：定义 Agent、工具策略、记忆策略、提示词策略。
- `bindings.json`：定义不同 channel/account/peer/session 到 Agent 的路由规则。
- `channels.json`：定义 CLI、Telegram、飞书通道账号。
- `profiles.json`：定义模型服务 profile，默认从 `.env` 读取 API key 和 base URL。

`workspace/` 存放可被运行时加载的提示词和任务配置：

- `IDENTITY.md`、`SOUL.md`、`TOOLS.md`、`BOOTSTRAP.md`：系统提示词分层。
- `MEMORY.md`：长期记忆。
- `HEARTBEAT.md`：后台 heartbeat 指令。
- `CRON.json`：主动定时任务，当前包含每天 09:30 推送的 `AI Agent 每日简报`。
- `skills/*/SKILL.md`：可注入的技能说明。
- `agents/<agent_id>/`：Agent 局部提示词覆盖。

## 主动任务与每日简报

`workspace/CRON.json` 中的 `agent-news-digest` 会每天北京时间 09:30 触发 `research` Agent，整理最近 24 小时内 AI Agent 相关动态，并推送到 `.env` 中配置的主动投递目标。

该任务当前使用 `agent_news_digest` payload：程序会先读取 `workspace/agent-news-sources.json`，从 OpenAI RSS、Anthropic / Google DeepMind 官方新闻页、GitHub Releases、arXiv 等来源采集候选条目，做基础去重后再交给 `research` Agent 生成中文简报。运行状态会写入 `data/news-digest/`，其中 `seen-items.jsonl` 用于避免重复推送同一条来源。

需要确保 `.env` 中至少启用：

```env
GATEWAY_WEB_SEARCH_ENABLED=true
TAVILY_API_KEY=你的 Tavily Key
GATEWAY_PROACTIVE_CHANNEL=feishu
GATEWAY_PROACTIVE_ACCOUNT_ID=feishu-main
GATEWAY_PROACTIVE_PEER_ID=飞书 open_id 或 chat_id
GATEWAY_PROACTIVE_AGENT_ID=research
```

如果要立即验证某个 cron 任务，可以使用一次性触发命令：

```bash
agent-gateway cron-trigger agent-news-digest
```

该命令会触发指定任务，并默认 flush 本地投递队列。若只想入队、不立刻发送：

```bash
agent-gateway cron-trigger agent-news-digest --no-flush
```

如果主动推送到飞书个人，`GATEWAY_PROACTIVE_PEER_ID` 通常是 `ou_` 开头的 `open_id`；推送到群时通常是 `oc_` 开头的 `chat_id`。飞书发送层会按 ID 前缀自动推断 `receive_id_type`。

信息源配置示例：

```json
{
  "id": "langgraph-releases",
  "type": "github_releases",
  "enabled": true,
  "repo": "langchain-ai/langgraph",
  "max_results": 3,
  "tags": ["framework", "langgraph", "agent-runtime"]
}
```

当前支持的 source 类型：

- `rss`：读取 RSS `<item>` 条目。
- `html_page`：读取官方新闻或博客列表页中的 `<a>` 链接，通过 `url_patterns` / `exclude_url_patterns` 筛选候选文章；适合没有稳定 RSS 的官网。
- `github_releases`：读取指定仓库 Releases，可选配置 `GITHUB_TOKEN` 提升 GitHub API 限额。
- `arxiv`：读取 arXiv Atom API，网络不稳定时会失败隔离，不影响其他来源。

`html_page` 配置示例：

```json
{
  "id": "anthropic-news",
  "type": "html_page",
  "enabled": true,
  "url": "https://www.anthropic.com/news",
  "url_patterns": ["/news/"],
  "exclude_url_patterns": ["/company/", "/careers", "/legal"],
  "max_results": 5,
  "tags": ["official", "anthropic", "model", "agent"]
}
```

## WebSocket 控制面

网关通过 WebSocket JSON-RPC 暴露运行控制能力，当前已支持：

- `bindings.list`
- `bindings.set`
- `bindings.remove`
- `bindings.save`
- `bindings.reload`
- `agents.list`
- `agents.set`
- `agents.remove`
- `agents.capabilities`
- `agents.template`
- `agents.save`
- `agents.reload`
- `channels.list`
- `channels.set`
- `channels.remove`
- `channels.save`
- `channels.reload`
- `profiles.list`
- `profiles.set`
- `profiles.remove`
- `profiles.save`
- `profiles.reload`
- `heartbeat.status`
- `heartbeat.trigger`
- `cron.list`
- `cron.trigger`
- `delivery.stats`
- `delivery.list`
- `delivery.retry`
- `delivery.discard`
- `delivery.flush`
- `runtime.status`
- `health.check`
- `config.source`

Delivery 控制面用于本地可靠投递队列的运维：

- `delivery.stats`：查看 pending、failed、可立即重试数量和最早入队时间。
- `delivery.list`：查看 pending / failed / all 队列，默认只返回文本预览；传入 `include_text=true` 可返回完整正文。
- `delivery.retry`：传入 `delivery_id`，将 failed 消息移回 pending，或让 pending 中处于 backoff 的消息立即可重试。
- `delivery.discard`：传入 `delivery_id`，从 pending / failed 队列中人工丢弃指定消息。
- `delivery.flush`：立即执行一次或多次投递队列 flush，适合人工排障后快速验证。

运行态状态接口用于统一查看系统健康：

- `runtime.status`：返回 agents、bindings、channels、profiles、delivery、heartbeat、cron、路径与功能开关的结构化快照。
- `health.check`：返回 `ok` / `degraded` / `unhealthy` 状态，以及逐项 checks；适合后续接入监控或部署探针。

## 运维监控台

启动 `agent-gateway serve` 时会默认同时启动一个本地 Dashboard：

```text
http://127.0.0.1:8780
```

该页面不依赖 npm、前端构建或外部 CDN，会通过 WebSocket JSON-RPC 连接当前 Gateway 控制面，并展示：

- 整体健康状态与 `health.check` 明细
- agents、bindings、channels、profiles、heartbeat、cron、delivery 的运行态快照
- pending / failed 投递队列
- 投递消息重试、丢弃与 flush 操作
- Cron 任务列表与手动触发

相关环境变量：

```env
GATEWAY_DASHBOARD_ENABLED=true
GATEWAY_DASHBOARD_HOST=127.0.0.1
GATEWAY_DASHBOARD_PORT=8780
GATEWAY_DASHBOARD_REFRESH_INTERVAL_SECONDS=15
```

默认只监听 `127.0.0.1`。如果要通过公网或内网穿透访问 Dashboard，需要先评估风险；当前页面已经具备投递重试、丢弃和 Cron 触发能力，后续应增加 token 鉴权后再对外暴露。

## 测试

```bash
cd ~/Desktop/gateway
./.venv/bin/python -m compileall agent_gateway tests
./.venv/bin/python -m pytest tests -q
```

当前测试覆盖了配置加载、路由、会话存储、Agent Manifest、记忆、技能、通道适配、飞书 webhook、安全校验、dispatcher、delivery runtime、control plane、heartbeat/cron 等核心模块。

## 当前边界

- 当前是单进程本地运行时，尚未引入数据库、分布式锁或多实例协调。
- 投递队列已经具备持久化、重试、WebSocket 控制面运维能力和本地 Dashboard 管理界面。
- Agent 权限模型已支持工具策略和 capability tags，但还需要继续增强审计、校验和权限预览。
- 运行态观测已提供 WebSocket 状态、健康检查接口和本地可视化页面，但还未接入告警渠道、鉴权和长期趋势指标。

## 后续计划

完整路线见 `PROJECT_PLAN.md`。近期优先级：

1. 完成飞书入站限流与审计增强。
2. 升级 Agent 权限模型、配置审计和会话治理。
3. 演进多 Agent handoff 与任务实例状态机。
4. 为 delivery / cron / channel runtime 增加更完整的结构化审计日志。
5. 为 Dashboard 增加 token 鉴权、运行事件日志、趋势指标和告警渠道。
