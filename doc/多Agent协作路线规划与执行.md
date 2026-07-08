# 多 Agent 协作路线规划与执行

本文档记录 Gateway 当前真实可运行的多 Agent 协作链路。当前系统不再维护“静态协作蓝图后台执行”模式，也不再把 `blueprint_json` 作为 `agent_collaboration` 后台任务输入。复杂任务的执行入口统一收敛到主控 Agent 持续规划模式。

## 当前已实现链路

复杂任务有两种进入主控协作的方式。

第一种是 dispatcher 自动识别。用户提出“分析仓库是否适合引入 Gateway，并给风险审查、采纳计划和正式报告”这类高置信复杂目标后，`GatewayDispatcher` 会创建 `agent_collaboration` 后台任务，并把 `user_goal`、`controller_agent_id=main`、`response_target`、`run_id`、`max_iterations` 等信息写入任务 payload。

第二种是入口 Agent 主动触发。`main`、`feishu-entry` 或 `wework-entry` 在模型执行时可以调用 `start_agent_orchestration`，该工具会把用户目标提交到任务队列。工具会根据当前 `runtime_context.session_key` 尽量恢复原平台、账号和会话目标，便于后台完成后把最终结果投递回原会话。

实际执行由 `TaskWorkerRuntime` 消费 `agent_collaboration` 任务，交给 `AgentCollaborationTaskHandler`。handler 只接受 `user_goal` 和 `controller_agent_id` 这类主控协作 payload；如果旧 payload 只包含 `blueprint_json`，会被拒绝。随后 handler 调用 `CollaborationRuntime.execute_orchestrated()`，由 `main` 作为主控 Agent 持续输出下一步 action。

## 主控循环

`CollaborationRuntime.execute_orchestrated()` 的核心机制是“规划一步、执行一步、回灌观察结果”。主控 Agent 每轮只能返回一个 JSON action：

```json
{"action": "delegate", "target_agent_id": "repo-analyzer", "task_prompt": "分析仓库价值和接入风险"}
```

```json
{"action": "final", "final_output": "最终结论、风险审查和采纳计划"}
```

```json
{"action": "abort", "reason": "缺少必要输入，无法继续"}
```

当 action 为 `delegate` 时，runtime 使用目标专家 Agent 执行 `task_prompt`，把专家输出包装成 observation，再回灌给主控 Agent。主控 Agent 基于新的 observation 决定下一步。这个过程会持续到主控 Agent 返回 `final`、返回 `abort`、输出非法 action，或达到 `max_iterations`。

## 后台任务 payload

当前 `agent_collaboration` 的最小 payload 如下：

```json
{
  "user_goal": "分析这个仓库是否适合引入 Gateway，并给我风险审查、采纳计划和正式报告：https://github.com/example/project",
  "controller_agent_id": "main",
  "run_id": "repo-review-demo",
  "channel": "wework",
  "mode": "minimal",
  "max_iterations": 8,
  "disabled_tools": ["memory_write"],
  "response_target": {
    "channel": "wework",
    "account_id": "wework-main",
    "peer_id": "zhanghaibo"
  }
}
```

`user_goal` 是主控协作的原始目标，`controller_agent_id` 通常是 `main`。`response_target` 用于把最终结果投递回原平台会话。`disabled_tools` 默认禁用 `memory_write`，避免后台协作阶段把临时中间结果写入长期记忆。

## 执行结果

runtime 返回 `agent_orchestration_run_result`，主要字段如下：

```json
{
  "type": "agent_orchestration_run_result",
  "run_id": "repo-review-demo",
  "user_goal": "分析仓库并生成采纳计划",
  "controller_agent_id": "main",
  "status": "completed",
  "stop_reason": "controller_final",
  "observation_count": 3,
  "observations": [],
  "final_output": "最终结论、风险审查和采纳计划",
  "duration_ms": 1234.5
}
```

每个 observation 记录一次专家 Agent 委托结果，包括 `target_agent_id`、`task_prompt`、`session_key`、`output_text`、`stop_reason` 和 `tool_calls`。最终 `final_output` 会由 `AgentCollaborationTaskHandler` 通过可靠投递链路回投到原会话。

## 运行事件

主控协作会写入运行事件，便于 Dashboard 和控制面追踪：

```text
agent.orchestration.enqueued
collaboration.orchestration.started
collaboration.orchestration.action
collaboration.orchestration.completed
collaboration.orchestration.finished
collaboration.orchestration.failed
```

这些事件进入现有 `RuntimeEventStore`。如果任务失败、达到最大轮次或主控输出非法 action，事件流和最近错误视图应能定位到模型、工具、调度或投递问题。

## 已移除边界

旧版“静态执行蓝图”模式已经从系统执行路径中移除：

- 不再注册 `compose_collaboration_execution_blueprint`。
- 不再注册 `render_collaboration_execution_blueprint_markdown`。
- 不再支持 `agent_collaboration_execution_blueprint` 作为后台执行输入。
- 不再由 `CollaborationRuntime` 按固定阶段顺序消费 `blueprint_json`。

保留的 `plan_agent_collaboration` 只用于路线规划和人工可读说明，不会自动调用专家 Agent。需要真实执行复杂协作时，应使用 `start_agent_orchestration` 或 dispatcher 自动创建的主控协作任务。

## 后续增强方向

后续增强应围绕主控协作链路继续演进，包括主控 action schema 校验、阶段级恢复、人工确认点、协作任务状态查询、并行/串行混合调度、专家输出压缩和协作观测面板。不要恢复静态蓝图执行模式，否则会重新引入两套协作路径并存的问题。
