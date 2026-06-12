# Gateway 项目学习指南

这份文档不是功能说明书，而是面向“怎么把这个系统读懂”的学习入口。

目标只有两个：

1. 搞清楚这个网关是怎么从零散代码片段，逐步拼成一个可运行项目的。
2. 建立一条稳定的阅读路径，让你后续继续扩展功能时，知道该改哪一层。

---

## 1. 先建立一个整体心智模型

这个项目本质上不是“一个聊天机器人脚本”，而是一个分层的智能体运行框架。它把以下几件事拆开了：

- Agent 定义：有哪些智能体、各自用什么模型、允许用哪些工具、是否启用记忆。
- 路由：一条消息进来后，应该落到哪个 agent、哪个 session。
- 执行：如何组装 prompt、调用模型、处理 tool calling、继续多轮循环。
- 会话：如何把历史对话落盘，并在下一轮重建上下文。
- 通道：CLI、Telegram、飞书这些输入输出源如何统一抽象。
- 主动任务：Heartbeat、Cron 这种不是“用户发起”的任务如何运行。
- 可靠投递：回复为什么不是直接发，而是先入队、再后台投递。
- 控制面：如何在运行时查看和修改 agent / bindings / profiles / channels。

如果只记一句话，可以记成：

`这个项目 = 多通道入站 + 路由 + Agent Loop + 会话/记忆 + 主动调度 + 可靠投递 + 运行时控制面`

---

## 2. 建议你先跑，再读

不要一上来逐文件硬啃。先把系统跑起来，再顺着一条真实调用链去看代码，理解会快很多。

在 `gateway/` 目录下：

```bash
source .venv/bin/activate
agent-gateway serve
```

建议同时做 3 件事：

1. 用 CLI 发一条消息，观察回复是否正常。
2. 看 `workspace/` 下的 prompt、memory、cron 文件，理解它们不是样例，而是运行态资产。
3. 看 `data/` 下是否生成了 session、delivery queue、feishu webhook 审计等文件。

你要先确认一个事实：

`config/`、`workspace/`、`data/` 这三块不是辅助目录，而是这个系统真正运行时的“配置面、知识面、状态面”。`

---

## 3. 最推荐的代码阅读顺序

下面这个顺序是按“先看装配，再看主链路，再看外围系统”设计的。尽量不要跳着读。

### 第 1 站：应用装配入口

先看：

- `agent_gateway/app.py`

这里解决两个问题：

1. 这个项目启动时会创建哪些核心对象。
2. 这些对象之间是怎么连起来的。

重点函数：

- `build_application()`
- `serve()`

你需要在脑子里记住这里装配出来的核心运行对象：

- `AgentManager`
- `BindingTable`
- `SessionStore`
- `ToolRegistry`
- `PromptAssembler`
- `ProfileManager`
- `AgentLoopRunner`
- `GatewayDispatcher`
- `ChannelRuntime`
- `AutonomyRuntime`
- `DeliveryRuntime`
- `GatewayControlPlane`

如果你把 `app.py` 看明白，后面读任何模块都会知道它在整条链路中的位置。

### 第 2 站：配置和运行目录

接着看：

- `agent_gateway/config.py`
- `agent_gateway/config_loader.py`
- `config/agents.json`
- `config/bindings.json`
- `config/profiles.json`
- `config/channels.json`

这里要搞清楚 4 个问题：

1. `.env` 负责什么，`config/*.json` 又负责什么。
2. 为什么启动时会自动补默认配置文件。
3. agent / bindings / profiles / channels 是怎样从 JSON 变成内存对象的。
4. 运行时 reload 为什么可行。

简单记忆：

- `.env`：偏 secret、路径、默认模型、运行参数。
- `config/*.json`：偏结构化配置对象。
- `workspace/`：prompt、memory、skills、cron 的内容资产。
- `data/`：session、queue、webhook 状态等运行状态。

### 第 3 站：核心数据模型和路由

再看：

- `agent_gateway/models.py`
- `agent_gateway/agents.py`
- `agent_gateway/router.py`
- `agent_gateway/ids.py`

这一层很基础，但必须理解透。后面很多逻辑都只是围绕这些数据结构变形。

你要重点理解：

- `InboundMessage`：统一后的入站消息模型。
- `Binding`：路由规则。
- `AgentConfig`：agent 的 manifest。
- `RouteResolution`：一次消息最终解析出来的路由结果。
- `AgentReply`：Agent Loop 的输出。
- `ProactiveTarget`：主动消息要发给谁。

其中最值得仔细读的是：

- `resolve_route()`
- `build_session_key()`

因为这两个函数回答了最关键的问题：

`一条消息为什么会进入这个 agent，并且为什么会落到这个 session。`

### 第 4 站：Agent 主执行链

接下来进入最核心的部分：

- `agent_gateway/runtime/loop.py`
- `agent_gateway/runtime/resilience.py`
- `agent_gateway/intelligence/bootstrap.py`
- `agent_gateway/tools/registry.py`
- `agent_gateway/tools/builtin.py`
- `agent_gateway/sessions/store.py`
- `agent_gateway/sessions/context.py`

这一组文件一起看，不要拆散。

建议你按这个顺序理解：

1. `loop.py`
   看 `run_turn()` 和 `run_task_turn()`，先搞清一轮执行的总入口。
2. `bootstrap.py`
   看系统 prompt 是怎么从 `IDENTITY.md / SOUL.md / TOOLS.md / MEMORY.md / USER.md / BOOTSTRAP.md / AGENTS.md / HEARTBEAT.md` 拼起来的。
3. `resilience.py`
   看模型调用、tool_use 循环、profile failover、context overflow 压缩是怎么做的。
4. `tools/registry.py` 和 `tools/builtin.py`
   看工具 schema 如何暴露给模型、工具结果如何回注。
5. `sessions/store.py`
   看 JSONL 会话是怎么写入和重建的。

你应该重点画出这条链：

```text
用户消息
  -> SessionStore.load_messages()
  -> PromptAssembler.build()
  -> ResilienceRunner.run()
  -> 模型返回 tool_use / end_turn
  -> ToolRegistry.dispatch()
  -> 循环继续
  -> SessionStore.rewrite_messages()
  -> AgentReply
```

这里是真正的“Agent Gateway 内核”。

---

## 4. 这 3 条主链路一定要分别读懂

### 链路 A：用户消息如何变成回复

建议按下面顺序读：

- `channels/*`
- `runtime/channel_runtime.py`
- `runtime/dispatcher.py`
- `router.py`
- `runtime/loop.py`
- `delivery/queue.py`
- `runtime/delivery_runtime.py`

对应的一条完整路径是：

```text
CLI / Telegram / 飞书收到消息
  -> ChannelRuntime 收到统一 InboundMessage
  -> GatewayDispatcher.dispatch_inbound()
  -> resolve_route() 选 agent 和 session
  -> AgentLoopRunner.run_turn()
  -> SessionStore / PromptAssembler / ResilienceRunner
  -> 生成 AgentReply
  -> GatewayDispatcher.deliver_reply()
  -> DeliveryQueue.enqueue()
  -> DeliveryRuntime 后台实际发送
  -> 对应 Channel.send()
```

这里要特别注意一个设计点：

`回复不是直接 send，而是先入 DeliveryQueue。`

这是这个项目从“demo 脚本”迈向“生产化框架”的关键一步，因为它为重试、失败落盘、异步投递、削峰都留出了空间。

### 链路 B：系统如何主动做事

建议看：

- `runtime/autonomy.py`
- `workspace/HEARTBEAT.md`
- `workspace/CRON.json`

你要理解的是，这个项目不只有“收到消息就回复”这一种工作模式。

Heartbeat 和 Cron 的执行路径是：

```text
HeartbeatService / CronService
  -> 生成后台 prompt
  -> GatewayDispatcher.dispatch_background()
  -> AgentLoopRunner.run_task_turn()
  -> 结果回到 dispatcher
  -> DeliveryQueue.enqueue()
  -> DeliveryRuntime 投递到目标通道
```

也就是说，主动任务和被动对话并不是两套系统，而是共用同一套执行内核和投递链路。

### 链路 C：运行时配置如何修改并生效

建议看：

- `runtime/gateway_server.py`
- `runtime/control_plane.py`
- `runtime/agent_manifest.py`

这部分解决的问题是：

`系统跑起来以后，怎么在不停机的情况下查看和修改配置。`

当前是通过 WebSocket JSON-RPC 完成的。

你需要重点理解：

- `GatewayServer` 是对外暴露的控制接口。
- `GatewayControlPlane` 是真正执行配置读写、保存、重载的服务层。
- `agent_manifest.py` 负责 agent 配置校验和模板生成。

这条链路对于后续做 Web 控制台、管理后台、配置中心非常重要。

---

## 5. 理解 `workspace/`，不要把它当成静态文档目录

这个项目里，`workspace/` 是最容易被低估的一层。

建议按下面方式理解：

- `IDENTITY.md`
  定义系统身份。
- `SOUL.md`
  定义整体行为风格。
- `TOOLS.md`
  定义工具使用边界。
- `MEMORY.md`
  长期记忆。
- `USER.md`
  用户侧的长期上下文说明。
- `BOOTSTRAP.md`
  启动补充说明。
- `AGENTS.md`
  agent 组织约束说明。
- `HEARTBEAT.md`
  主动巡检任务 prompt。
- `CRON.json`
  定时任务定义。
- `skills/*/SKILL.md`
  技能提示词资产。
- `agents/<agent_id>/`
  agent 级 prompt 覆盖目录。

一句话概括：

`workspace/` 保存的是“让 agent 变成这个 agent”的内容，不是源码实现，但它直接参与运行。`

---

## 6. 理解 `data/`，不要只盯着源码

如果你只读 Python 文件，不看 `data/`，你对项目的理解会停留在静态层面。

建议运行几次以后，实际去看这些目录：

- `data/sessions/`
  看 session JSONL 是怎样记录 user / assistant / tool_use / tool_result 的。
- `data/delivery-queue/`
  看回复入队后的 JSON 文件结构。
- `data/delivery-queue/failed/`
  看失败消息怎样留存。
- `data/feishu-webhook/`
  看飞书 webhook 的审计日志和去重状态。
- `data/channel-state/`
  看通道本地状态，例如飞书卡片状态。

这个项目很多“工程化设计”其实都体现在这些状态文件上，而不只是在接口函数上。

---

## 7. 通道层应该怎么读

如果你想理解“为什么加一个新通道不需要重写整个系统”，建议集中看这几层：

- `channels/base.py`
- `channels/manager.py`
- `channels/bootstrap.py`
- `channels/cli.py`
- `channels/telegram.py`
- `channels/feishu.py`
- `runtime/channel_runtime.py`
- `runtime/feishu_http.py`
- `runtime/feishu_security.py`

阅读重点不是飞书 API 细节，而是抽象边界：

1. `Channel` 抽象只要求 `receive()` / `send()`。
2. `ChannelManager` 管理多个账号、多种渠道实例。
3. `ChannelRuntime` 负责把外部通道线程统一接入内部异步队列。
4. `FeishuWebhookServer` 负责飞书 webhook 入站，不直接执行业务逻辑，只桥接成 `InboundMessage`。

也就是说，真正的系统边界是：

`外部协议 -> Channel / Webhook 适配 -> InboundMessage -> 内部统一调度`

把这层看懂，后续扩 Slack、企业微信、HTTP API 都会有明确落点。

---

## 8. 工具、记忆、技能三层要分开理解

很多人第一次读这类项目时，会把 tools / memory / skills 混成一团。这里建议你强行拆开。

### Tools

看：

- `tools/registry.py`
- `tools/builtin.py`

它解决的是：

`模型可以调用什么外部能力，以及调用后怎么执行。`

### Memory

看：

- `intelligence/memory.py`

它解决的是：

`历史事实如何长期保存，以及当前问题如何召回相关记忆。`

### Skills

看：

- `intelligence/skills.py`

它解决的是：

`有哪些可复用的任务能力说明需要注入到 prompt 中。`

所以三者分别对应：

- tools = 可执行能力
- memory = 可召回事实
- skills = 可注入方法说明

---

## 9. 最适合反向理解实现的测试顺序

如果你想高效读代码，直接从测试反推实现会非常快。建议按这个顺序跑和读：

### 第一组：最基础的内核

- `tests/test_router.py`
- `tests/test_session_store.py`
- `tests/test_memory.py`
- `tests/test_skills.py`

这组能帮你先建立数据流认知。

### 第二组：执行与弹性

- `tests/test_resilience.py`
- `tests/test_dispatcher.py`
- `tests/test_channel_runtime.py`

这组能帮助你理解主执行链和并发入口。

### 第三组：配置与控制面

- `tests/test_control_plane.py`
- `tests/test_gateway_server.py`
- `tests/test_agent_manifest.py`

这组能帮助你理解运行时可变更配置的边界。

### 第四组：主动任务与可靠投递

- `tests/test_autonomy.py`
- `tests/test_delivery_runtime.py`

这组对应系统从“被动对话”走向“主动运行”的能力。

### 第五组：飞书接入

- `tests/test_feishu_channel.py`
- `tests/test_feishu_http.py`
- `tests/test_feishu_security.py`

这组能帮助你把飞书接入拆成：

- 通道发送格式
- webhook 接收
- 安全校验 / 去重 / 审计

如果你一天只想看一部分代码，最有效的方法通常不是“按目录读完”，而是：

`先看某组测试断言什么，再回头看对应实现为什么这样写。`

---

## 10. 一个推荐的学习节奏

如果你准备系统性地把这个项目吃透，我建议用下面这个节奏。

### 第一天：只读总装配和主链路

目标：

- 搞清楚 `app.py` 装了什么。
- 搞清楚一条 CLI 消息是怎么走完的。

只看这些文件：

- `app.py`
- `models.py`
- `router.py`
- `runtime/dispatcher.py`
- `runtime/loop.py`
- `runtime/channel_runtime.py`

### 第二天：把 prompt、tools、sessions 看透

目标：

- 理解 Agent Loop 为什么能持续跑。

只看这些文件：

- `intelligence/bootstrap.py`
- `tools/registry.py`
- `tools/builtin.py`
- `sessions/store.py`
- `runtime/resilience.py`

### 第三天：把运行态资产看透

目标：

- 理解 `workspace/` 和 `data/` 为什么是运行核心的一部分。

重点看：

- `workspace/*`
- `intelligence/memory.py`
- `intelligence/skills.py`
- `data/` 中运行后生成的文件

### 第四天：读主动任务和可靠投递

目标：

- 理解为什么系统已经具备“后台自主运行”的雏形。

重点看：

- `runtime/autonomy.py`
- `delivery/queue.py`
- `runtime/delivery_runtime.py`

### 第五天：读控制面和飞书接入

目标：

- 理解为什么这个项目已经不是一个单点脚本，而是一个可管理的网关。

重点看：

- `runtime/gateway_server.py`
- `runtime/control_plane.py`
- `runtime/feishu_http.py`
- `runtime/feishu_security.py`
- `channels/feishu.py`

---

## 11. 读代码时建议带着这 10 个问题

如果只是机械地看代码，很容易看完却没有结构感。建议你带着下面这些问题去读：

1. 一条消息进入系统后，第一次被“标准化”为统一结构是在什么地方？
2. 路由是在什么层决定的，通道层是否知道 agent 的存在？
3. session key 为什么要区分 `per-peer / per-channel-peer / per-account-channel-peer`？
4. prompt 的全局文件和 agent 局部文件是怎么叠加的？
5. memory、skills、tools 为什么不做成一个大模块？
6. 为什么回复不直接发送，而要经过 `DeliveryQueue`？
7. Heartbeat / Cron 为什么没有绕过主执行链，而是复用 `dispatch_background()`？
8. 控制面为什么要区分 `save` 和 `reload`？
9. 飞书 webhook 为什么先做签名校验和去重，再桥接到 runtime？
10. 现在这个架构如果要接更多通道，最稳定的扩展点在哪里？

你把这 10 个问题都答清楚，基本就不是“看过代码”，而是真的理解了这个项目。

---

## 12. 建议你做的 5 个最小练习

单纯阅读还不够，建议你做几次低风险修改。

### 练习 1：新增一个 agent

改：

- `config/agents.json`
- `config/bindings.json`
- `workspace/agents/<agent_id>/IDENTITY.md`
- `workspace/agents/<agent_id>/SOUL.md`

目标：

- 理解 agent manifest、路由绑定和局部 prompt 覆盖。

### 练习 2：新增一个技能

改：

- `workspace/skills/<skill-name>/SKILL.md`

目标：

- 理解 skill 如何被扫描并注入 prompt。

### 练习 3：观察一次会话落盘

执行一轮包含工具调用的对话，然后看：

- `data/sessions/...`

目标：

- 理解 `user / assistant / tool_use / tool_result` 的 transcript 重建逻辑。

### 练习 4：制造一次投递失败

让某个通道临时不可用，然后看：

- `data/delivery-queue/`
- `data/delivery-queue/failed/`

目标：

- 理解可靠投递为什么要和主执行链解耦。

### 练习 5：新增一条 cron 任务

改：

- `workspace/CRON.json`

目标：

- 理解系统如何从“聊天响应”扩展到“主动执行”。

---

## 13. 如果你后续要继续开发，优先掌握这几个扩展点

后续继续做功能时，最常改动的落点通常是：

- 新增能力工具：`tools/`
- 新增 prompt/agent 能力：`workspace/`、`runtime/agent_manifest.py`
- 新增通道：`channels/`、`runtime/channel_runtime.py`
- 新增后台任务：`runtime/autonomy.py`
- 新增控制接口：`runtime/gateway_server.py`、`runtime/control_plane.py`
- 新增投递运维能力：`delivery/`、`runtime/delivery_runtime.py`

如果你要扩新功能，先判断它属于哪一层，不要把逻辑直接塞进 `app.py` 或某个通道实现里。

---

## 14. 最后给你的一个阅读原则

理解这个项目时，不要问“这个文件是干什么的”，而要问：

`这个模块在整条运行链路中，负责把什么输入变成什么输出。`

一旦你用“输入 -> 转换 -> 输出”的方式看代码，这个系统就会从一堆 Python 文件，变成一个非常清晰的网关架构。

如果后续你愿意，我下一步可以继续把这份学习文档再往下落成两份配套材料：

1. 一份“按时序展开的消息流转图”。
2. 一份“逐文件讲解版”的源码导读。
