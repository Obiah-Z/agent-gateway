# Codex 与 Claude Code 多 Agent 协作机制调研

本文只关注两个问题：

1. 不同 Agent 之间的数据如何流转。
2. 一个 Agent 的操作如何衔接到另一个 Agent。

## 1. Codex 的多 Agent 协作机制

Codex 的多 Agent 机制核心是 **主线程 + 子 Agent 线程**。

主 Agent 保留需求、约束、决策和最终输出。子 Agent 被显式启动后，在独立线程中完成探索、测试、审查或局部实现。子 Agent 不把完整中间过程塞回主上下文，而是把结论、证据、文件引用、风险点或执行结果摘要返回给主 Agent。这样做的目的不是让多个 Agent 共享同一个大上下文，而是减少主上下文污染。

### 1.1 数据如何流转

Codex 的数据流可以抽象成：

```text
用户请求
  -> 主 Agent 解析任务
  -> 主 Agent 拆分子任务
  -> 子 Agent 获得独立任务说明、当前仓库环境、可用工具、继承的权限策略
  -> 子 Agent 独立读取代码、运行命令、分析结果
  -> 子 Agent 返回结构化摘要
  -> 主 Agent 汇总多个子 Agent 结果
  -> 主 Agent 给出最终结论或继续派发后续操作
```

关键点是：**子 Agent 之间默认不直接共享完整上下文**。它们共享的是同一个工作区、同一套可访问工具、同一套权限边界，以及主 Agent 下发的任务说明。子 Agent 的输出再由主 Agent 汇总。

因此 Codex 的数据流不是：

```text
Agent A 全量上下文 -> Agent B 全量上下文 -> Agent C 全量上下文
```

而更接近：

```text
主 Agent
  -> 子 Agent A: 任务 A + 环境访问权
  -> 子 Agent B: 任务 B + 环境访问权
  -> 子 Agent C: 任务 C + 环境访问权

子 Agent A -> 摘要 A -> 主 Agent
子 Agent B -> 摘要 B -> 主 Agent
子 Agent C -> 摘要 C -> 主 Agent

主 Agent -> 汇总判断
```

### 1.2 操作如何衔接

Codex 的操作衔接由主 Agent 编排。

主 Agent 负责决定：

- 是否启动子 Agent。
- 启动几个子 Agent。
- 每个子 Agent 的任务边界。
- 是否等待所有子 Agent 完成。
- 是否根据子 Agent 结果继续追问、停止、合并或执行修改。

子 Agent 可以自己使用工具执行读取、命令、测试等动作。涉及权限时，子 Agent 继承当前会话的 sandbox 和 approval 策略。也就是说，子 Agent 并不是绕过主 Agent 权限独立行动，而是在同一安全边界下工作。

操作衔接可以表示为：

```text
主 Agent: 发现任务可并行
  -> spawn 子 Agent A: 检查安全风险
  -> spawn 子 Agent B: 检查测试缺口
  -> spawn 子 Agent C: 检查代码质量

子 Agent A: 读取代码、形成安全结论
子 Agent B: 读取测试、运行测试、形成缺口结论
子 Agent C: 读取实现、形成维护性结论

主 Agent: 等待 A/B/C
  -> 合并结论
  -> 去重冲突观点
  -> 排序问题严重级别
  -> 输出最终审查结果
```

### 1.3 Codex 机制的本质

Codex 的多 Agent 更像 **并行工作线程模型**：

- 主 Agent 是调度者和最终解释者。
- 子 Agent 是独立执行单元。
- 子 Agent 之间不强依赖，不适合复杂链式状态传递。
- 最重要的数据接口是“任务说明”和“结果摘要”。
- 共享状态主要来自文件系统、仓库、命令输出、MCP 工具和主 Agent 汇总结果。

这类机制适合：

- 并行代码审查。
- 多方向调研。
- 多模块代码探索。
- 测试、日志、风险点并行分析。

不适合直接当作：

- 长期自治 Agent 网络。
- 多 Agent 连续业务流程引擎。
- Agent A 自动把完整内部状态交给 Agent B 的流水线。

## 2. Claude Code 的多 Agent 协作机制

Claude Code 的多 Agent 协作主要通过 **Subagents** 实现。它的设计更偏向“专用 Agent 被主 Agent 委派任务”，每个 Subagent 有自己的上下文窗口、系统提示和工具权限。

Claude Code 中的 Subagent 通常以配置文件形式定义，包含：

- Agent 名称。
- 适用场景描述。
- 系统提示词。
- 可使用工具列表。

主 Agent 根据用户请求和 Subagent 描述判断是否委派任务，也可以由用户显式要求某个 Subagent 处理。

### 2.1 数据如何流转

Claude Code 的数据流可以抽象成：

```text
用户请求
  -> 主 Agent 判断任务类型
  -> 选择合适 Subagent
  -> 把任务说明和必要上下文交给 Subagent
  -> Subagent 在独立上下文中执行
  -> Subagent 返回结果
  -> 主 Agent 使用该结果继续回答或执行下一步
```

它的关键点是：**Subagent 拥有独立上下文窗口**。

这意味着主 Agent 不会把完整历史无条件塞给 Subagent。主 Agent 需要把“这个子任务需要什么上下文”整理后交给 Subagent。Subagent 执行完成后，也不是把完整执行轨迹全部注入回主 Agent，而是返回处理结果。

因此 Claude Code 的多 Agent 数据边界更明确：

```text
主 Agent 上下文
  -> 提炼后的任务上下文
  -> Subagent 独立上下文
  -> Subagent 结果摘要
  -> 主 Agent 上下文
```

Subagent 的工具权限可以单独配置，因此数据访问能力也可以按 Agent 收缩。例如：

```text
reviewer Subagent: 只读代码和测试
security Subagent: 允许读取配置和依赖
docs Subagent: 允许读取文档并编辑 Markdown
```

这让 Claude Code 的协作更像“带权限边界的专家委派”。

### 2.2 操作如何衔接

Claude Code 的操作衔接方式是：

```text
主 Agent 识别任务
  -> 匹配 Subagent 描述
  -> 调用 Subagent
  -> Subagent 独立完成任务
  -> 主 Agent 接收结果
  -> 主 Agent 决定是否继续调用其他 Subagent 或结束
```

如果要做链式协作，通常不是 Subagent A 直接调用 Subagent B，而是主 Agent 接收 A 的结果后，再决定是否把结果整理成新任务交给 B。

例如：

```text
用户: 分析这个仓库并给出改造计划

主 Agent
  -> repo-analyzer Subagent: 分析仓库结构和核心能力
  <- 返回仓库分析摘要

主 Agent
  -> reviewer Subagent: 基于仓库摘要审查风险
  <- 返回风险清单

主 Agent
  -> planner Subagent: 基于分析和风险生成计划
  <- 返回改造计划

主 Agent
  -> 输出最终结果
```

这里的衔接点不是“Subagent 之间共享内存”，而是：

```text
Subagent A 结果
  -> 主 Agent 提炼
  -> Subagent B 输入
```

### 2.3 Claude Code 机制的本质

Claude Code 的多 Agent 更像 **专家委派模型**：

- 主 Agent 是入口、路由器和结果整合者。
- Subagent 是特定领域专家。
- 每个 Subagent 有独立上下文和工具权限。
- 数据通过“任务输入”和“结果输出”流转。
- 操作通过主 Agent 串联，而不是 Subagent 之间随意互调。

这类机制适合：

- 专家型能力拆分。
- 代码审查、测试、文档、安全等角色隔离。
- 限制不同 Agent 的工具权限。
- 防止一个 Agent 的上下文污染另一个 Agent。

## 3. Codex 与 Claude Code 的关键差异

| 维度 | Codex | Claude Code |
|---|---|---|
| 协作形态 | 主 Agent 启动并行子 Agent 线程 | 主 Agent 委派给专用 Subagent |
| 触发方式 | 通常需要显式要求并行/子 Agent | 可显式调用，也可按 Subagent 描述匹配 |
| 数据传递 | 主 Agent 下发任务，子 Agent 返回摘要 | 主 Agent 提炼上下文，Subagent 返回结果 |
| 上下文关系 | 子 Agent 独立线程，避免污染主上下文 | Subagent 独立上下文窗口 |
| 工具权限 | 子 Agent 继承当前权限，可由自定义 Agent 配置覆盖部分设置 | Subagent 可配置独立工具白名单 |
| 操作衔接 | 主 Agent 等待、汇总、继续调度 | 主 Agent 接收结果后决定下一次委派 |
| 更适合 | 并行探索、并行审查、并行测试 | 专家角色隔离、权限隔离、链式委派 |

## 4. 对 Gateway 多 Agent 设计的直接启发

Gateway 不应该把多 Agent 协作设计成“所有 Agent 共享同一个巨大上下文”。

更合理的模型是：

```text
入口 Agent
  -> 识别任务
  -> 生成 handoff package
  -> 调用目标 Agent
  -> 目标 Agent 独立执行
  -> 返回结构化结果
  -> 入口 Agent 或编排层汇总
```

数据接口应该显式化，而不是隐式共享。

建议每次 Agent 交接只传这些内容：

```text
handoff_id
source_agent_id
target_agent_id
user_id / session_key
task_goal
required_context
constraints
available_artifacts
expected_output_schema
deadline / priority
trace_id
```

目标 Agent 输出也应该结构化：

```text
handoff_id
target_agent_id
status
summary
evidence
artifacts
next_action
errors
```

这样才能避免三类问题：

1. Agent 间上下文污染。
2. 工具权限失控。
3. 多 Agent 链路无法追踪和恢复。

## 5. 推荐落地形态

Gateway 后续多 Agent 协作可以采用“主控编排 + 专家 Agent”的模式。

```text
用户消息
  -> 平台入口 Agent
  -> 路由/编排层判断任务类型
  -> 生成 handoff package
  -> 专家 Agent 执行
  -> 写入事件流和任务状态
  -> 返回结构化结果
  -> 入口 Agent 汇总给用户
```

其中：

- 入口 Agent 不直接拥有所有工具。
- 专家 Agent 不直接修改全局路由。
- Agent 之间不共享完整对话历史。
- 每次交接必须有可持久化的 handoff package。
- 每个 Agent 的输入输出都要能被事件流追踪。

这与 Codex 和 Claude Code 的共同点一致：**多 Agent 协作的核心不是共享记忆，而是清晰的任务边界、独立上下文、结构化结果和主控汇总。**

## 6. 资料来源

- OpenAI Codex Manual：Subagents、Agent Skills、Model Context Protocol、Hooks。
  - https://developers.openai.com/codex/codex-manual.md
- Anthropic Claude Code Docs：Subagents、Hooks、MCP、Slash Commands、Skills。
  - https://code.claude.com/docs/en/agent-sdk/subagents
  - https://code.claude.com/docs/en/hooks
  - https://code.claude.com/docs/en/hooks-guide
  - https://code.claude.com/docs/en/mcp-quickstart
  - https://code.claude.com/docs/en/agent-sdk/slash-commands
  - https://code.claude.com/docs/en/skills
