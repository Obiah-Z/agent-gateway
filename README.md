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
cd ~/Desktop/claw0/gateway
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

如果本机通过公网 IP 或内网穿透暴露，例如公网地址是 `8.153.15.37`，飞书事件订阅中的请求地址应填写：

```text
http://8.153.15.37:8766/webhooks/feishu
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

如果公网地址仍是 `8.153.15.37`，第二个飞书应用事件订阅中的请求地址填写：

```text
http://8.153.15.37:8766/webhooks/feishu/secondary
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
- `CRON.json`：主动定时任务。
- `skills/*/SKILL.md`：可注入的技能说明。
- `agents/<agent_id>/`：Agent 局部提示词覆盖。

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
- `config.source`

## 测试

```bash
cd ~/Desktop/claw0
./gateway/.venv/bin/python -m compileall gateway/agent_gateway gateway/tests
./gateway/.venv/bin/python -m pytest gateway/tests -q
```

当前测试覆盖了配置加载、路由、会话存储、Agent Manifest、记忆、技能、通道适配、飞书 webhook、安全校验、dispatcher、delivery runtime、control plane、heartbeat/cron 等核心模块。

## 当前边界

- 当前是单进程本地运行时，尚未引入数据库、分布式锁或多实例协调。
- 投递队列已经具备持久化和重试能力，但人工运维接口还在后续阶段补齐。
- Agent 权限模型已支持工具策略和 capability tags，但还需要继续增强审计、校验和权限预览。
- 运行态观测仍以测试和本地日志为主，后续会增加统一状态面和结构化日志。

## 后续计划

完整路线见 `PROJECT_PLAN.md`。近期优先级：

1. 完成飞书入站限流与审计增强。
2. 增加 delivery 控制面，支持查看 pending / failed 队列和人工重试。
3. 增加统一运行态状态接口和健康检查。
4. 升级 Agent 权限模型、配置审计和会话治理。
5. 演进多 Agent handoff 与任务实例状态机。
