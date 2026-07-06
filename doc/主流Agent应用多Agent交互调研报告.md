# 主流 Agent 应用多 Agent 交互调研报告

调研日期：2026-07-06

## 1. 调研结论

市场上的多 Agent 交互大致分成两条路线。

第一条是产品型编码 Agent 路线，代表是 Claude Code 和 Codex。这类产品不一定把“多 Agent”暴露成复杂 API，而是把它做成主会话的委派能力：主 Agent 负责理解目标、拆任务、发起子 Agent、等待结果、汇总结论。子 Agent 通常拥有独立上下文、独立工具执行过程和较窄任务边界，最终只把摘要或结果返回主会话。这样做的核心目标是减少主上下文污染，同时提高并行探索、代码审查、日志排查和大任务拆解效率。

第二条是框架型多 Agent 路线，代表是 OpenAI Agents SDK、LangGraph、AutoGen、CrewAI。这类框架更强调可编程编排：开发者显式定义 Agent、工具、状态、handoff、supervisor、group chat、crew process、trace 和 guardrail。它们更适合构建业务系统，但也要求开发者自己处理状态一致性、权限边界、循环终止、成本控制和可观测性。

对本项目 Gateway 来说，最值得借鉴的不是简单“多开几个 Agent”，而是建立清晰的 Agent 协作协议：任务进入后先由路由/调度层判断是否需要拆分；主 Agent 或 orchestrator 持有最终控制权；子 Agent 只拿必要上下文和必要工具；所有 Agent 间交互必须落入可观测事件流；最终结果通过可靠投递链路发送。

## 2. 主流多 Agent 交互模式

### 2.1 主 Agent + Subagent

这是 Codex、Claude Code 和 LangChain Deep Agents 都在强调的模式。主 Agent 保持用户目标、约束和最终决策上下文，子 Agent 负责一个窄任务，例如代码搜索、测试失败分析、安全审查、文档总结、某个模块改造方案。

这种模式的价值在于隔离上下文。子 Agent 可以产生大量中间日志、搜索结果和试错过程，但主会话只接收压缩后的结论。缺点是每个子 Agent 都会单独消耗模型调用和工具调用，成本更高；如果子 Agent 任务边界不清，会产生重复工作或冲突修改。

适合场景：

- 大代码库探索、审查、排障和多文件理解。
- 可以并行处理的读多写少任务。
- 需要多个专家视角但最终由一个主 Agent 汇总的任务。

不适合场景：

- 强依赖顺序执行的业务流程。
- 多个 Agent 同时写同一批文件且缺少合并协议。
- 任务很简单，单 Agent 加动态工具选择即可完成。

### 2.2 Supervisor + Worker

Supervisor 模式把一个 Agent 或确定性调度器放在中心位置，由它判断下一步该调用哪个 Worker。OpenAI Agents SDK 中可以用 `agent.as_tool(...)` 保持 orchestrator 控制权，也可以用 handoff 把控制权交给专家 Agent。LangGraph 的 supervisor 生态也强调工具式 handoff 和明确路由。

这种模式适合生产系统，因为控制权清楚：supervisor 决定谁工作、何时终止、结果是否合格。缺点是 supervisor 可能成为瓶颈；如果所有判断都靠 LLM，会引入不确定性和额外成本。

适合场景：

- 客服、运维、个人秘书、研发协作等需要“先判断意图，再分派专家”的系统。
- 需要统一权限、审计、重试和结果验收的系统。
- 需要人类审批或关键工具调用保护的系统。

### 2.3 Handoff

Handoff 是把当前任务控制权从一个 Agent 转交给另一个 Agent。它更像真实组织里的“转人工”或“转专家”。OpenAI Agents SDK 明确把 handoff 作为多 Agent 所有权选择之一：要么把专家 Agent 当工具调用，保留编排者控制；要么通过 handoff 让专家接管。

Handoff 的优点是自然，适合多轮会话：用户和当前 Agent 互动到某个阶段后，切换给更懂这个领域的 Agent。缺点是通常更偏顺序执行，难以天然并行；如果历史上下文不断累积，token 成本和错误传播会变大。

适合场景：

- 从通用助理转到专业助理，例如从 main Agent 转到 diet Agent、repo analyzer Agent。
- 多轮咨询中需要由专家持续接管上下文。
- 用户体验上希望“角色切换”而不是“后台并行任务”。

### 2.4 Group Chat

AutoGen 的 Group Chat 是典型代表：多个 Agent 共享一个消息线程，所有参与者订阅/发布同一个 topic，由 group chat manager 选择下一个发言者。它适合模拟多人协作、辩论、评审和创作过程。

这种模式的优点是表达力强，可以让 writer、reviewer、planner、critic 等角色连续协作。缺点是顺序发言成本高，容易出现循环、跑题、空消息、角色边界模糊等问题。它更像“协作讨论室”，不一定适合高吞吐后端任务调度。

适合场景：

- 方案评审、内容创作、研究讨论、模拟专家会议。
- 需要多个 Agent 互相看见彼此输出并迭代的任务。

### 2.5 Crew / Process

CrewAI 把 Agent、Task、Crew、Process 作为核心抽象，支持 sequential、hierarchical 或 hybrid process。顺序模式强调任务按固定顺序执行；层级模式由 manager agent 协调 crew，进行任务委派和结果校验。

这种模式对业务自动化友好，因为它把“团队、角色、任务、流程”概念产品化了。缺点是如果流程本身可以确定性建模，过早引入 manager Agent 反而会增加不确定性和成本。

适合场景：

- 营销报告、销售线索处理、数据分析报告等业务流程自动化。
- 角色和任务边界明确的重复性工作。
- 需要流程模板、企业控制台、权限和观测的场景。

## 3. 典型产品与框架对比

| 产品 / 框架 | 多 Agent 形态 | 控制权 | 上下文策略 | 并行能力 | 更适合 |
|---|---|---|---|---|---|
| Claude Code | 主会话委派 sub-agent，社区和官方课程强调隔离上下文、任务委派、限制工具访问 | 主会话 / 用户驱动 | 子 Agent 独立上下文，返回摘要 | 适合并行探索 | 编码、审查、长会话降噪 |
| Codex | 显式 subagents、自定义 agent、`/agent` 管理；也可作为 MCP server 被 Agents SDK 编排 | Codex 主线程或外部 SDK orchestrator | 子 Agent 独立线程，主线程汇总 | 官方支持并行 spawn | 编码 Agent、代码审查、复杂研发流程 |
| OpenAI Agents SDK | Agent、Runner、tool、handoff、guardrail、trace；Codex 可作为 MCP 工具接入 | 编排代码或 handoff 目标 Agent | 开发者控制上下文、状态和 trace | 取决于编排实现 | 生产级 Agent 应用 |
| LangGraph / LangChain | subagents、handoffs、skills、router、supervisor | 图节点 / supervisor / router | 强调 context engineering | router/subagents 可并行 | 可控状态机、多步骤业务流 |
| AutoGen | AgentChat、Teams、Group Chat、Group Chat Manager | manager 选择下一发言者 | 共享 group thread | group chat 通常顺序，Core 可事件驱动扩展 | 多角色讨论、评审、协作生成 |
| CrewAI | Agents、Tasks、Crews、Flows，支持 sequential/hierarchical/hybrid | process 或 manager agent | task/crew 维度组织上下文 | 取决于 process | 企业业务自动化和流程模板 |

## 4. Claude Code 的多 Agent 思路

Claude Code 的公开产品页强调它是项目级 agentic coding system，可以读取代码库、跨文件修改、运行测试并交付代码。公开材料还提到工程师的角色正在转向“管理多个并行 Agent、给方向、做决策”。

Anthropic Academy 的 subagents 课程说明，sub-agent 用于管理上下文、委派任务和构建专门工作流。课程介绍中明确提到：sub-agent 会在独立上下文窗口里工作，输入流入子 Agent，子 Agent 将摘要返回；同时建议通过结构化输出、障碍报告和限制工具访问来提高可靠性。

可归纳为以下机制：

- 主会话负责保留用户目标和最终决策。
- 子 Agent 负责独立任务，避免主上下文被日志、搜索过程和中间推理污染。
- 子 Agent 可以被定制为 code reviewer、documentation generator 等窄角色。
- 可靠性关键不在“Agent 数量”，而在结构化输出、失败报告、工具权限收敛和明确停止条件。

对 Gateway 的启发：当前系统已有多 Agent 配置、Skill、Cron 和通道绑定，下一步如果做多 Agent 协作，应优先做“主 Agent 委派子任务 + 子任务独立上下文 + 结果汇总”，而不是让多个 Agent 在同一个 session 里自由聊天。

## 5. Codex / OpenAI 的多 Agent 思路

Codex 官方文档把 subagents 定义为“并行启动专门 Agent，再把结果收集到一个响应中”。文档还说明 Codex 只会在用户明确要求时启动 subagent，因为每个 subagent 都会进行自己的模型调用和工具调用，成本高于单 Agent。Codex 会负责启动子 Agent、路由跟进指令、等待结果、关闭线程，并最终返回合并响应。

Codex 的一个重要设计是自定义 Agent 文件。项目级或个人级 Agent 可以有不同的 `name`、`description`、`developer_instructions`，并可覆盖模型、推理强度、沙箱、MCP server 和 skill 配置。Codex 也提供默认的 `default`、`worker`、`explorer` 三类 Agent。

OpenAI 还提供“Codex as MCP server + Agents SDK”的组合方式。Codex CLI 可以作为 MCP server 暴露 `codex` 和 `codex-reply` 工具，外部 Agents SDK 则负责构建多 Agent 工作流、handoff、guardrail 和 trace。这一点对 Gateway 很重要：Agent 不一定只能是模型 prompt，也可以是一个可被调用的外部 Agent runtime。

可归纳为以下机制：

- 产品内：主线程显式 spawn subagents，适合并行审查、探索和拆分实现计划。
- 平台内：Codex 作为 MCP 工具，被更上层的 Agents SDK orchestrator 调用。
- 工程化重点：线程 ID、沙箱继承、审批继承、子 Agent 可见性、最大并发线程数、trace 和结果合并。

对 Gateway 的启发：可以把本项目的 AgentRuntime 抽象成可被上层 orchestrator 调用的“Agent 工具”，同时保留 Gateway 自己的路由、队列、幂等和投递能力。

## 6. LangGraph / LangChain 的多 Agent 思路

LangChain 官方文档明确提醒：不是每个复杂任务都需要多 Agent，单 Agent 配合合适工具和动态 prompt 有时更好。多 Agent 主要解决三类问题：上下文管理、分布式开发、并行化。文档把 multi-agent 的核心放在 context engineering，即决定每个 Agent 看到什么信息。

LangChain 文档对 subagents、handoffs、skills、router 做了性能和适用场景比较。它指出 subagents 和 router 更适合并行执行和大上下文领域；handoffs 更偏顺序执行，不适合同时咨询多个领域；skills 调用次数少，但可能因为上下文累积导致 token 成本高。

可归纳为以下机制：

- Router：先判断任务属于哪些领域，再并行调用对应 Agent。
- Subagents：每个子 Agent 只拿相关上下文，降低上下文污染。
- Handoffs：把控制权顺序转交给另一个 Agent。
- Skills：把能力作为可加载模块，但要小心 prompt 累积。

对 Gateway 的启发：当前 Skill 全量进入 prompt 的问题，应向“按需检索 Skill + 子 Agent 独立上下文”演进。路由层可以先决定是否走单 Agent、Skill、handoff 或后台子任务。

## 7. AutoGen 的多 Agent 思路

AutoGen 提供 AgentChat 作为构建多 Agent 应用的高级 API，也提供 autogen-core 的事件驱动模型。官方 Group Chat 文档描述了一个典型模式：多个 Agent 共享一个消息线程，所有 Agent 订阅并发布到同一 topic，每个 Agent 有特定角色，Group Chat Manager 负责选择下一个发言者。

这种模式把多 Agent 交互建模成“对话协议”。它适合模拟团队协作，但它的执行通常是顺序发言：同一时刻只有一个 Agent 工作。下一发言者可以用 round-robin，也可以由 LLM selector 决定。Group Chat 还可以嵌套为层级结构。

可归纳为以下机制：

- 多 Agent 共享 thread/topic。
- manager 负责发言顺序和终止控制。
- 每个参与者有明确角色。
- 适合动态拆解复杂任务，但需要防止循环和无效发言。

对 Gateway 的启发：如果未来做“方案评审 Agent 团队”，可以借鉴 group chat；但当前消息网关的核心诉求是高可靠任务执行，不应默认采用共享线程群聊模式，否则会增加顺序阻塞和不可控循环。

## 8. CrewAI 的多 Agent 思路

CrewAI 官方文档定位为“collaborative AI agents, crews, and flows”，强调 guardrails、memory、knowledge、observability。它把基础构件分为 Agents、Flows、Tasks & Processes，其中 Tasks & Processes 支持 sequential、hierarchical 或 hybrid process。

CrewAI 的优势是流程表达贴近业务：先定义角色，再定义任务，再定义 crew 的执行过程。sequential process 适合流程固定的任务；hierarchical process 适合需要 manager agent 动态委派和校验的任务；flows 则更接近事件驱动状态机，可以持久化执行并恢复长流程。

可归纳为以下机制：

- Agent 负责角色和能力。
- Task 负责任务描述和期望输出。
- Crew 负责组织 Agent 和 Task。
- Process 决定执行顺序、层级管理或混合流程。
- Flow 负责更可控的事件驱动编排和恢复。

对 Gateway 的启发：本项目的 Cron、Heartbeat、入站消息、Skill 执行都可以向“Task Instance + Process Runtime”靠拢，但不建议把所有动态决策都交给 manager LLM；稳定流程应优先用确定性状态机。

## 9. 对 Gateway 的落地建议

### 9.1 建立 Agent 协作的四层模型

建议把 Gateway 的多 Agent 协作拆成四层。

接入层负责接收飞书、企业微信、CLI、Webhook 等通道消息，只生成标准 InboundEvent，不直接决定复杂协作。

路由层负责判断本轮任务应该进入哪个 Agent，是否需要 handoff，是否需要后台子任务，是否需要技能检索。

执行层负责运行主 Agent、子 Agent、Skill、工具调用和模型调用。这里需要明确每个执行单元的上下文边界、工具权限和最大迭代次数。

投递层负责把最终结果或阶段性结果可靠发送回对应通道，不关心内部由几个 Agent 参与。

### 9.2 优先实现“可控委派”，不要先做“自由群聊”

短期最适合 Gateway 的方案是主 Agent 委派子任务：

- 主 Agent 判断是否需要并行子任务。
- 子任务进入任务队列，拥有独立 trace、独立上下文和超时。
- 子 Agent 只返回结构化摘要、证据、文件路径、失败原因。
- 主 Agent 或 deterministic merger 汇总结果。
- 所有中间事件写入 runtime_events。

不建议优先做多个 Agent 在同一 session 中自由 group chat。原因是当前系统已经有消息队列、会话串行、可靠投递和多通道接入，核心风险是重复消费、顺序和状态一致性；自由群聊会放大这些风险。

### 9.3 多 Agent 任务需要统一状态模型

建议新增或扩展以下概念：

- `agent_task`：一次 Agent 执行任务，可以是主任务或子任务。
- `parent_task_id`：标识子任务归属。
- `agent_role`：main、worker、reviewer、researcher、summarizer 等。
- `context_scope`：本次任务可见的上下文范围。
- `tool_scope`：本次任务允许调用的工具集合。
- `handoff_policy`：允许接管、只允许作为工具调用、禁止转交。
- `merge_policy`：结果合并方式，例如全部等待、最快返回、投票、人工确认。

这样可以避免“Agent 调 Agent”变成不可观测的黑盒。

### 9.4 多 Agent 场景必须有硬性终止条件

从 Codex、AutoGen、CrewAI 的经验看，多 Agent 的常见风险包括成本膨胀、循环交互、上下文污染、工具权限过宽、重复执行和结果不可验收。因此 Gateway 应该强制定义：

- 最大子任务数量。
- 最大嵌套深度。
- 单个子任务超时。
- 单个任务最大工具调用次数。
- 最大模型迭代次数。
- 子任务输出 schema。
- 失败时是否允许降级为单 Agent。

这些约束应进入配置，而不是只靠 prompt。

### 9.5 与现有 Gateway 架构的结合点

当前 Gateway 已经具备一些适合多 Agent 的基础设施：

- RabbitMQ / Redis / PostgreSQL：可支撑任务分发、幂等、状态恢复和分布式执行。
- Runtime events：可记录 agent started、agent completed、tool call、delivery、error。
- Skills：可作为 Agent 能力扩展，但需要从“全量提示词”演进为“按需加载”。
- Workspace agent 配置：可以继续承载 Agent prompt、Cron、用户画像和专属配置。
- Control plane / Dashboard：可以展示主任务、子任务、handoff、失败链路。

建议下一步不是替换现有执行链路，而是在现有 TaskStore 和 RuntimeEventStore 上增加 `parent_task_id`、`agent_role`、`delegation_type`、`merge_status` 等字段，先把多 Agent 协作可观测化。

## 10. 建议的演进路线

第一阶段：只做只读并行子 Agent。适用代码库分析、日志分析、文档总结、方案评审。禁止子 Agent 写文件和发送消息，只允许返回结构化报告。

第二阶段：引入 supervisor / router。由路由层或主 Agent 决定调用哪个专属 Agent，例如 diet、repo-analyzer、space-advisor、secretary。普通消息仍走单 Agent，复杂任务再拆分。

第三阶段：支持 handoff。允许 main Agent 把一段会话转交给专属 Agent，但必须记录 handoff event，并可切回 main Agent。

第四阶段：支持多 Agent 结果合并。增加 deterministic merger 或 reviewer Agent，对多个子任务结果做合并、去重、冲突检测。

第五阶段：支持受控写操作。子 Agent 可以产生 patch 或计划，但默认由主 Agent 或用户审批后落地，避免并行写冲突。

## 11. 资料来源

- OpenAI Codex Subagents：<https://developers.openai.com/codex/subagents>
- OpenAI Codex with Agents SDK：<https://developers.openai.com/codex/guides/agents-sdk>
- Anthropic Claude Code 产品页：<https://www.anthropic.com/product/claude-code>
- Anthropic Academy Introduction to subagents：<https://anthropic.skilljar.com/introduction-to-subagents>
- LangChain Multi-agent 文档：<https://docs.langchain.com/oss/python/langchain/multi-agent>
- AutoGen AgentChat 文档：<https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/index.html>
- AutoGen Group Chat 文档：<https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/design-patterns/group-chat.html>
- CrewAI 文档首页：<https://docs.crewai.com/>
- CrewAI Processes 文档：<https://docs.crewai.com/v1.15.1/en/concepts/processes>

