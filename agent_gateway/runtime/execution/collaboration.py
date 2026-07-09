"""多 Agent 主控协作执行 runtime。

该模块由主控 Agent 持续规划下一步，runtime 执行主控输出的 delegate/final/abort
action，并把专家 Agent 的 observation 回灌给主控 Agent。静态协作蓝图后台执行模式
已移除，避免两套协作路径并存导致入口行为漂移。
"""

from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from agent_gateway.runtime.execution.loop import AgentLoopRunner
from agent_gateway.runtime.observability.events import RuntimeEventStore, new_correlation_id


class CollaborationRuntime:
    """由主控 Agent 持续规划并编排多个专家 Agent。"""

    def __init__(
        self,
        runner: AgentLoopRunner,
        *,
        event_store: RuntimeEventStore | None = None,
        artifact_root: Path | None = None,
    ) -> None:
        self.runner = runner
        self.event_store = event_store
        self.artifact_root = artifact_root

    async def execute_orchestrated(
        self,
        *,
        user_goal: str,
        controller_agent_id: str,
        channel: str = "collaboration",
        mode: str = "minimal",
        correlation_id: str = "",
        session_prefix: str = "orchestration",
        run_id: str = "",
        max_iterations: int = 8,
        disabled_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        """由主控 Agent 持续规划下一步并驱动专家 Agent 执行。

        这条路径更接近 Claude Code 的控制流：主控 Agent 每轮基于目标和观察结果输出
        一个 JSON action，runtime 执行动作后把 observation 回灌，直到主控 Agent 返回 final。
        """

        goal = user_goal.strip()
        if not goal:
            raise ValueError("orchestrated collaboration requires user_goal")
        if not controller_agent_id.strip():
            raise ValueError("orchestrated collaboration requires controller_agent_id")
        max_iterations = max(1, int(max_iterations))
        run_id = run_id.strip() or f"orch_{int(time.time())}"
        correlation_id = correlation_id or new_correlation_id("orchestration")
        started_at = time.time()
        self._record(
            "collaboration.orchestration.started",
            status="ok",
            message="Agent orchestration started",
            correlation_id=correlation_id,
            agent_id=controller_agent_id,
            metadata={"run_id": run_id, "max_iterations": max_iterations},
        )

        observations: list[dict[str, Any]] = []
        status = "running"
        final_output = ""
        stop_reason = ""
        try:
            for iteration in range(1, max_iterations + 1):
                controller_reply = await self.runner.run_task_turn(
                    agent_id=controller_agent_id,
                    session_key=f"{session_prefix}:{run_id}:controller:{controller_agent_id}",
                    user_text=self._build_controller_prompt(
                        user_goal=goal,
                        run_id=run_id,
                        iteration=iteration,
                        max_iterations=max_iterations,
                        observations=observations,
                    ),
                    channel=channel,
                    mode=mode,
                    correlation_id=correlation_id,
                    disabled_tools=disabled_tools,
                    persist_history=False,
                )
                action = self._parse_orchestration_action(controller_reply.text)
                action_type = str(action.get("action") or "").strip().lower()
                self._record(
                    "collaboration.orchestration.action",
                    status="ok",
                    message=f"Orchestration action: {action_type or 'invalid'}",
                    correlation_id=correlation_id,
                    agent_id=controller_agent_id,
                    metadata={
                        "run_id": run_id,
                        "iteration": iteration,
                        "action": action_type,
                        "target_agent_id": action.get("target_agent_id", ""),
                    },
                )

                if action_type == "delegate":
                    observation = await self._execute_orchestration_delegate(
                        action=action,
                        user_goal=goal,
                        controller_agent_id=controller_agent_id,
                        run_id=run_id,
                        iteration=iteration,
                        channel=channel,
                        mode=mode,
                        correlation_id=correlation_id,
                        session_prefix=session_prefix,
                        disabled_tools=disabled_tools,
                    )
                    observations.append(observation)
                    continue

                if action_type == "final":
                    status = "completed"
                    final_output = str(
                        action.get("final_output")
                        or action.get("output")
                        or controller_reply.text
                    ).strip()
                    stop_reason = "controller_final"
                    break

                if action_type == "abort":
                    status = "aborted"
                    final_output = str(action.get("reason") or "主控 Agent 中止协作。").strip()
                    stop_reason = "controller_abort"
                    break

                status = "failed"
                final_output = f"主控 Agent 返回了无法识别的动作：{controller_reply.text}"
                stop_reason = "invalid_controller_action"
                break
            else:
                status = "max_iterations_reached"
                final_output = "协作已达到最大规划轮次，未获得主控 Agent 的 final 动作。"
                stop_reason = "max_iterations_reached"
        except Exception as exc:
            status = "failed"
            self._record(
                "collaboration.orchestration.failed",
                status="error",
                message="Agent orchestration failed",
                correlation_id=correlation_id,
                error=exc,
                metadata={"run_id": run_id},
            )
            raise

        finished_at = time.time()
        result = {
            "type": "agent_orchestration_run_result",
            "run_id": run_id,
            "user_goal": goal,
            "controller_agent_id": controller_agent_id,
            "status": status,
            "stop_reason": stop_reason,
            "correlation_id": correlation_id,
            "observation_count": len(observations),
            "observations": observations,
            "final_output": final_output,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": round((finished_at - started_at) * 1000, 1),
            "boundary": "该结果表示主控 Agent 已完成规划执行闭环；不包含消息投递状态。",
        }
        self._write_orchestration_artifact(result)
        self._record(
            "collaboration.orchestration.completed"
            if status == "completed"
            else "collaboration.orchestration.finished",
            status="ok" if status == "completed" else "warning",
            message=f"Agent orchestration {status}",
            correlation_id=correlation_id,
            agent_id=controller_agent_id,
            metadata={
                "run_id": run_id,
                "status": status,
                "observation_count": len(observations),
                "duration_ms": result["duration_ms"],
            },
        )
        return result

    async def _execute_orchestration_delegate(
        self,
        *,
        action: dict[str, Any],
        user_goal: str,
        controller_agent_id: str,
        run_id: str,
        iteration: int,
        channel: str,
        mode: str,
        correlation_id: str,
        session_prefix: str,
        disabled_tools: list[str] | None,
    ) -> dict[str, Any]:
        """执行主控 Agent 规划出的单个专家委托动作。"""

        requested_target_agent_id = str(
            action.get("target_agent_id") or action.get("agent_id") or ""
        ).strip()
        task_prompt = str(
            action.get("task_prompt")
            or action.get("handoff_prompt")
            or action.get("prompt")
            or ""
        ).strip()
        if not requested_target_agent_id:
            raise ValueError("delegate action requires target_agent_id")
        if not task_prompt:
            raise ValueError("delegate action requires task_prompt")

        target_agent_id = self._normalize_delegate_target(
            requested_target_agent_id,
            task_prompt=task_prompt,
            purpose=str(action.get("purpose") or ""),
            controller_agent_id=controller_agent_id,
        )
        session_key = f"{session_prefix}:{run_id}:step-{iteration:02d}:{target_agent_id}"
        reply = await self.runner.run_task_turn(
            agent_id=target_agent_id,
            session_key=session_key,
            user_text=self._build_delegate_prompt(user_goal, action, task_prompt),
            channel=channel,
            mode=mode,
            correlation_id=correlation_id,
            disabled_tools=disabled_tools,
            persist_history=False,
        )
        return {
            "iteration": iteration,
            "action": "delegate",
            "target_agent_id": target_agent_id,
            "requested_target_agent_id": requested_target_agent_id,
            "purpose": str(action.get("purpose") or ""),
            "task_prompt": task_prompt,
            "session_key": session_key,
            "status": "completed",
            "output_text": reply.text,
            "stop_reason": reply.stop_reason,
            "tool_calls": list(reply.tool_calls),
        }

    def _normalize_delegate_target(
        self,
        requested_target_agent_id: str,
        *,
        task_prompt: str,
        purpose: str,
        controller_agent_id: str,
    ) -> str:
        """把错误委托到入口/主控 Agent 的动作改写到具体专家 Agent。

        主控 Agent 负责规划，不应该再作为专家执行自己的子任务；入口 Agent（main、
        feishu-entry、wework-entry、个人秘书）也不应该承接写文件、调研、审查等专家任务。
        """

        blocked = {
            "",
            "main",
            "feishu-entry",
            "wework-entry",
            "personal-secretary-zhanghaibo",
            controller_agent_id,
        }
        requested = requested_target_agent_id.strip()
        if requested and requested not in blocked:
            return requested

        text = f"{purpose}\n{task_prompt}".lower()
        if any(token in text for token in ("写入", "本地文档", "markdown", "文件", "报告", "成文")):
            return "doc-writer"
        if any(token in text for token in ("调研", "研究", "资料", "搜索", "来源", "证据")):
            return "research"
        if any(token in text for token in ("审查", "风险", "评审", "review", "gate")):
            return "reviewer"
        if any(token in text for token in ("计划", "规划", "拆解", "路线")):
            return "planner"
        return "planner"

    def _build_controller_prompt(
        self,
        *,
        user_goal: str,
        run_id: str,
        iteration: int,
        max_iterations: int,
        observations: list[dict[str, Any]],
    ) -> str:
        """构造主控 Agent 的下一步规划 prompt。"""

        return "\n\n".join(
            [
                "你是多 Agent 协作的主控 Agent。请像 Claude Code 一样基于目标和观察结果规划下一步。",
                "你每轮只能输出一个 JSON 对象，不能输出 Markdown、解释文字或代码块。",
                "可用 action：",
                "- delegate：委托一个专家 Agent 执行子任务。",
                "- final：给出面向用户的最终结果。",
                "- abort：任务无法继续时中止，并说明原因。",
                "delegate 格式："
                '{"action":"delegate","target_agent_id":"repo-analyzer",'
                '"purpose":"为什么需要这一步","task_prompt":"交给专家的完整任务"}',
                'final 格式：{"action":"final","final_output":"最终答复"}',
                'abort 格式：{"action":"abort","reason":"中止原因"}',
                f"run_id：{run_id}",
                f"当前轮次：{iteration}/{max_iterations}",
                f"用户目标：{user_goal}",
                "已完成观察结果：\n```json\n"
                + json.dumps(observations, ensure_ascii=False, indent=2)
                + "\n```",
                "约束：优先拆成可验证的小步；已有足够信息时必须 final；不要重复委托同一子任务。",
            ]
        )

    def _build_delegate_prompt(
        self,
        user_goal: str,
        action: dict[str, Any],
        task_prompt: str,
    ) -> str:
        """构造专家 Agent 的单步任务 prompt。"""

        return "\n\n".join(
            [
                "你正在执行主控 Agent 委托的单个子任务，只完成本次委托职责。",
                f"用户最终目标：{user_goal}",
                f"本步目的：{action.get('purpose') or '未说明'}",
                f"本步任务：{task_prompt}",
                "输出要求：给出可被主控 Agent 继续使用的结果，明确证据、风险和下一步依赖。",
            ]
        )

    def _parse_orchestration_action(self, text: str) -> dict[str, Any]:
        """解析主控 Agent 返回的 JSON action。"""

        raw = text.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                return {}
            try:
                payload = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return {}
        return payload if isinstance(payload, dict) else {}

    def _write_orchestration_artifact(self, result: dict[str, Any]) -> None:
        """保存主控协作 run 结果，便于本地回放和人工排查。"""

        if self.artifact_root is None:
            return
        run_id = str(result.get("run_id") or "unknown")
        path = self._safe_artifact_path(f"workspace/reports/orchestration/{run_id}/run.json")
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    def _safe_artifact_path(self, expected_path: str) -> Path:
        """把蓝图路径约束在 artifact_root 下，避免写出工作区。"""

        assert self.artifact_root is not None
        relative = Path(expected_path)
        if relative.is_absolute():
            relative = Path(*relative.parts[1:])
        if self.artifact_root.name == "workspace" and relative.parts[:1] == ("workspace",):
            relative = Path(*relative.parts[1:])
        path = self.artifact_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _record(
        self,
        event_type: str,
        *,
        status: str,
        message: str,
        correlation_id: str,
        agent_id: str = "",
        session_key: str = "",
        error: str | Exception = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """记录协作运行事件。"""

        if self.event_store is None:
            return
        self.event_store.record(
            event_type,
            status=status,
            component="collaboration_runtime",
            message=message,
            correlation_id=correlation_id,
            agent_id=agent_id,
            session_key=session_key,
            error=error,
            metadata=metadata or {},
        )
