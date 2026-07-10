from __future__ import annotations

import asyncio
import json
from pathlib import Path
import pytest

from agent_gateway.runtime.domain.models import AgentReply
from agent_gateway.runtime.execution.collaboration import CollaborationRuntime
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.tasks.handlers import AgentCollaborationTaskHandler
from agent_gateway.runtime.tasks.models import TaskInstance


class SequencedCollaborationRunner:
    def __init__(self, replies: dict[str, list[str]]) -> None:
        self.calls: list[dict[str, object]] = []
        self.replies = {agent_id: list(items) for agent_id, items in replies.items()}
        self.sessions = FakeSessionStore()

    async def run_task_turn(
        self,
        *,
        agent_id: str,
        session_key: str,
        user_text: str,
        channel: str,
        mode: str,
        correlation_id: str = "",
        disabled_tools: list[str] | None = None,
        persist_history: bool = True,
    ) -> AgentReply:
        self.calls.append(
            {
                "agent_id": agent_id,
                "session_key": session_key,
                "user_text": user_text,
                "channel": channel,
                "mode": mode,
                "correlation_id": correlation_id,
                "disabled_tools": disabled_tools or [],
                "persist_history": persist_history,
            }
        )
        queue = self.replies.setdefault(agent_id, [])
        text = queue.pop(0) if queue else f"{agent_id} fallback"
        return AgentReply(
            agent_id=agent_id,
            session_key=session_key,
            text=text,
            stop_reason="end_turn",
            tool_calls=[f"{agent_id}_tool"],
        )


class FakeResultDispatcher:
    def __init__(self) -> None:
        self.deliveries: list[dict[str, object]] = []

    async def deliver_text(self, channels, target, text: str, *, metadata=None) -> str:
        self.deliveries.append(
            {
                "channels": channels,
                "target": target,
                "text": text,
                "metadata": dict(metadata or {}),
            }
        )
        return "delivery-1"


class FakeSessionStore:
    def __init__(self) -> None:
        self.appended: list[dict[str, object]] = []

    def append_message(self, agent_id: str, session_key: str, role: str, content: object) -> None:
        self.appended.append(
            {
                "agent_id": agent_id,
                "session_key": session_key,
                "role": role,
                "content": content,
            }
        )


class FakeOrchestrationWriter:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []
        self.steps: list[dict[str, object]] = []

    def write_agent_orchestration_run(self, row: dict[str, object]) -> dict[str, object]:
        self.runs.append(dict(row))
        return row

    def write_agent_orchestration_step(self, row: dict[str, object]) -> dict[str, object]:
        self.steps.append(dict(row))
        return row


def test_agent_collaboration_task_handler_rejects_blueprint_payload(tmp_path: Path) -> None:
    runner = SequencedCollaborationRunner({})
    runtime = CollaborationRuntime(runner, artifact_root=tmp_path)  # type: ignore[arg-type]
    handler = AgentCollaborationTaskHandler(runtime)
    task = TaskInstance.create(
        task_type="agent_collaboration",
        source="test",
        payload={
            "blueprint_json": json.dumps(
                {
                    "type": "agent_collaboration_execution_blueprint",
                    "blueprint_id": "legacy-blueprint",
                    "stages": [],
                },
                ensure_ascii=False,
            ),
            "channel": "feishu",
        },
    )

    with pytest.raises(ValueError, match="user_goal and controller_agent_id"):
        asyncio.run(handler(task))

    assert runner.calls == []


def test_collaboration_runtime_orchestrates_next_actions_until_final(tmp_path: Path) -> None:
    runner = SequencedCollaborationRunner(
        {
            "main": [
                json.dumps(
                    {
                        "action": "delegate",
                        "target_agent_id": "repo-analyzer",
                        "purpose": "先审查仓库适配风险",
                        "task_prompt": "分析 https://github.com/Obiah-Z/smart-trip",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "action": "final",
                        "final_output": "smart-trip 可作为 Gateway 旅行规划能力候选，但需先做安全隔离。",
                    },
                    ensure_ascii=False,
                ),
            ],
            "repo-analyzer": ["仓库分析：存在外部 API、配置和数据边界风险。"],
        }
    )
    events = RuntimeEventStore(tmp_path / "events")
    runtime = CollaborationRuntime(
        runner,  # type: ignore[arg-type]
        event_store=events,
        artifact_root=tmp_path,
    )

    result = asyncio.run(
        runtime.execute_orchestrated(
            user_goal="分析 smart-trip 是否适合引入 Gateway",
            controller_agent_id="main",
            channel="wework",
            mode="minimal",
            correlation_id="corr-orch",
            run_id="orch-test",
            max_iterations=4,
            disabled_tools=["memory_write"],
        )
    )

    assert result["status"] == "completed"
    assert result["observation_count"] == 1
    assert result["final_output"] == "smart-trip 可作为 Gateway 旅行规划能力候选，但需先做安全隔离。"
    assert [call["agent_id"] for call in runner.calls] == ["main", "repo-analyzer", "main"]
    assert runner.calls[0]["session_key"] == "orchestration:orch-test:controller:main"
    assert runner.calls[1]["session_key"] == "orchestration:orch-test:step-01:repo-analyzer"
    assert all(call["persist_history"] is False for call in runner.calls)
    assert runner.calls[1]["disabled_tools"] == ["memory_write"]
    assert "仓库分析：存在外部 API" in str(runner.calls[2]["user_text"])
    assert json.loads(
        (tmp_path / "workspace/reports/orchestration/orch-test/run.json").read_text(
            encoding="utf-8"
        )
    )["status"] == "completed"
    event_types = {row["type"] for row in events.tail(limit=20)}
    assert "collaboration.orchestration.started" in event_types
    assert "collaboration.orchestration.completed" in event_types


def test_collaboration_runtime_persists_orchestration_to_database_without_local_artifact(
    tmp_path: Path,
) -> None:
    runner = SequencedCollaborationRunner(
        {
            "main": [
                '{"action":"delegate","target_agent_id":"repo-analyzer","task_prompt":"分析仓库"}',
                '{"action":"final","final_output":"最终报告"}',
            ],
            "repo-analyzer": ["分析完成"],
        }
    )
    writer = FakeOrchestrationWriter()
    runtime = CollaborationRuntime(
        runner,  # type: ignore[arg-type]
        artifact_root=tmp_path,
        state_write_repository=writer,
    )

    result = asyncio.run(
        runtime.execute_orchestrated(
            user_goal="分析仓库",
            controller_agent_id="main",
            run_id="orch-db",
            max_iterations=3,
        )
    )

    assert result["status"] == "completed"
    assert [row["run_id"] for row in writer.runs] == ["orch-db"]
    assert [row["action"] for row in writer.steps] == ["delegate", "final"]
    assert writer.runs[0]["final_output"] == "最终报告"
    assert not (tmp_path / "workspace/reports/orchestration/orch-db/run.json").exists()


def test_agent_collaboration_task_handler_executes_orchestration_payload(tmp_path: Path) -> None:
    runner = SequencedCollaborationRunner(
        {
            "main": [
                '{"action":"delegate","target_agent_id":"repo-analyzer","task_prompt":"分析仓库"}',
                '{"action":"final","final_output":"最终报告"}',
            ],
            "repo-analyzer": ["分析完成"],
        }
    )
    runtime = CollaborationRuntime(runner, artifact_root=tmp_path)  # type: ignore[arg-type]
    handler = AgentCollaborationTaskHandler(runtime)
    task = TaskInstance.create(
        task_type="agent_collaboration",
        source="test",
        payload={
            "user_goal": "分析仓库",
            "controller_agent_id": "main",
            "run_id": "orch-task",
            "channel": "wework",
            "mode": "minimal",
            "correlation_id": "corr-task-orch",
            "max_iterations": 3,
        },
    )

    preview = asyncio.run(handler(task))

    assert preview == "agent orchestration completed: orch-task observations=1"
    assert [call["agent_id"] for call in runner.calls] == ["main", "repo-analyzer", "main"]


def test_collaboration_runtime_rewrites_entry_agent_delegate_to_specific_expert(
    tmp_path: Path,
) -> None:
    runner = SequencedCollaborationRunner(
        {
            "personal-secretary-zhanghaibo": [
                json.dumps(
                    {
                        "action": "delegate",
                        "target_agent_id": "main",
                        "purpose": "写入本地 Markdown 文档",
                        "task_prompt": "请把调研内容写入 workspace/reports/晚餐.md",
                    },
                    ensure_ascii=False,
                ),
                '{"action":"final","final_output":"文档已写入"}',
            ],
            "doc-writer": ["写入完成"],
        }
    )
    runtime = CollaborationRuntime(runner, artifact_root=tmp_path)  # type: ignore[arg-type]

    result = asyncio.run(
        runtime.execute_orchestrated(
            user_goal="调研晚餐搭配并写入本地文档",
            controller_agent_id="personal-secretary-zhanghaibo",
            channel="wework",
            mode="minimal",
            run_id="rewrite-main",
            max_iterations=3,
        )
    )

    assert result["status"] == "completed"
    assert [call["agent_id"] for call in runner.calls] == [
        "personal-secretary-zhanghaibo",
        "doc-writer",
        "personal-secretary-zhanghaibo",
    ]
    assert result["observations"][0]["requested_target_agent_id"] == "main"
    assert result["observations"][0]["target_agent_id"] == "doc-writer"


def test_collaboration_runtime_routes_blocked_diet_delegate_to_diet_agent(
    tmp_path: Path,
) -> None:
    runner = SequencedCollaborationRunner(
        {
            "personal-secretary-zhanghaibo": [
                json.dumps(
                    {
                        "action": "delegate",
                        "target_agent_id": "personal-secretary-zhanghaibo",
                        "purpose": "查询用户当前饮食计划",
                        "task_prompt": "请查询饮食计划、餐食记录和减脂目标，并汇总当前安排。",
                    },
                    ensure_ascii=False,
                ),
                '{"action":"final","final_output":"已汇总你的当前饮食计划"}',
            ],
            "diet-assistant-zhanghaibo": ["当前饮食计划：午餐控油，晚餐高蛋白。"],
        }
    )
    runtime = CollaborationRuntime(runner, artifact_root=tmp_path)  # type: ignore[arg-type]

    result = asyncio.run(
        runtime.execute_orchestrated(
            user_goal="我现在的饮食计划",
            controller_agent_id="personal-secretary-zhanghaibo",
            channel="wework",
            mode="minimal",
            run_id="diet-route",
            max_iterations=3,
            response_target={
                "channel": "wework",
                "account_id": "wework-main",
                "peer_id": "zhanghaibo",
                "source_session_key": (
                    "agent:personal-secretary-zhanghaibo:wework:"
                    "wework-main:direct:zhanghaibo"
                ),
                "source_agent_id": "personal-secretary-zhanghaibo",
            },
        )
    )

    assert result["status"] == "completed"
    assert [call["agent_id"] for call in runner.calls] == [
        "personal-secretary-zhanghaibo",
        "diet-assistant-zhanghaibo",
        "personal-secretary-zhanghaibo",
    ]
    diet_call = runner.calls[1]
    assert diet_call["session_key"] == (
        "agent:diet-assistant-zhanghaibo:wework:wework-main:direct:zhanghaibo"
    )
    assert diet_call["persist_history"] is True
    assert result["observations"][0]["requested_target_agent_id"] == (
        "personal-secretary-zhanghaibo"
    )
    assert result["observations"][0]["target_agent_id"] == "diet-assistant-zhanghaibo"
    assert result["observations"][0]["persist_history"] is True


def test_collaboration_runtime_rewrites_diet_agent_alias_to_real_agent(
    tmp_path: Path,
) -> None:
    runner = SequencedCollaborationRunner(
        {
            "personal-secretary-zhanghaibo": [
                json.dumps(
                    {
                        "action": "delegate",
                        "target_agent_id": "diet-agent",
                        "purpose": "查询当前饮食计划",
                        "task_prompt": "看一下我现在的饮食计划",
                    },
                    ensure_ascii=False,
                ),
                '{"action":"final","final_output":"饮食计划查询完成"}',
            ],
            "diet-assistant-zhanghaibo": ["当前没有已记录的饮食计划。"],
        }
    )
    runtime = CollaborationRuntime(runner, artifact_root=tmp_path)  # type: ignore[arg-type]

    result = asyncio.run(
        runtime.execute_orchestrated(
            user_goal="看一下我现在的饮食计划",
            controller_agent_id="personal-secretary-zhanghaibo",
            channel="wework",
            mode="minimal",
            run_id="diet-alias",
            max_iterations=3,
            response_target={
                "source_session_key": (
                    "agent:personal-secretary-zhanghaibo:wework:"
                    "wework-main:direct:zhanghaibo"
                ),
            },
        )
    )

    assert result["status"] == "completed"
    assert runner.calls[1]["agent_id"] == "diet-assistant-zhanghaibo"
    assert runner.calls[1]["session_key"] == (
        "agent:diet-assistant-zhanghaibo:wework:wework-main:direct:zhanghaibo"
    )
    assert result["observations"][0]["requested_target_agent_id"] == "diet-agent"
    assert result["observations"][0]["target_agent_id"] == "diet-assistant-zhanghaibo"


def test_collaboration_runtime_rewrites_researcher_alias_to_research(
    tmp_path: Path,
) -> None:
    runner = SequencedCollaborationRunner(
        {
            "personal-secretary-zhanghaibo": [
                '{"action":"delegate","target_agent_id":"web-researcher","task_prompt":"调研 RabbitMQ"}',
                '{"action":"final","final_output":"调研完成"}',
            ],
            "research": ["RabbitMQ 调研结果。"],
        }
    )
    runtime = CollaborationRuntime(runner, artifact_root=tmp_path)  # type: ignore[arg-type]

    result = asyncio.run(
        runtime.execute_orchestrated(
            user_goal="调研 RabbitMQ",
            controller_agent_id="personal-secretary-zhanghaibo",
            channel="wework",
            mode="minimal",
            run_id="research-alias",
            max_iterations=3,
        )
    )

    assert result["status"] == "completed"
    assert runner.calls[1]["agent_id"] == "research"
    assert result["observations"][0]["requested_target_agent_id"] == "web-researcher"
    assert result["observations"][0]["target_agent_id"] == "research"


def test_collaboration_runtime_repairs_malformed_delegate_json(
    tmp_path: Path,
) -> None:
    runner = SequencedCollaborationRunner(
        {
            "personal-secretary-zhanghaibo": [
                (
                    '{"action":"delegate","target_agent_id":"personal-secretary-zhanghaibo",'
                    '"purpose":"搜索长期记忆和待办记录中与饮食计划相关的内容",'
                    '"task_prompt":"请同时执行以下操作：1) 用 memory_search 查询"饮食计划"'
                    '相关内容；2) 用 personal_todo_search 查询"饮食"相关的待办事项。"}'
                ),
                '{"action":"final","final_output":"饮食计划查询完成"}',
            ],
            "diet-assistant-zhanghaibo": ["已从饮食 Agent 汇总。"],
        }
    )
    runtime = CollaborationRuntime(runner, artifact_root=tmp_path)  # type: ignore[arg-type]

    result = asyncio.run(
        runtime.execute_orchestrated(
            user_goal="我现在的饮食计划",
            controller_agent_id="personal-secretary-zhanghaibo",
            channel="wework",
            mode="minimal",
            run_id="diet-repair",
            max_iterations=3,
            response_target={
                "source_session_key": (
                    "agent:personal-secretary-zhanghaibo:wework:"
                    "wework-main:direct:zhanghaibo"
                ),
            },
        )
    )

    assert result["status"] == "completed"
    assert runner.calls[1]["agent_id"] == "diet-assistant-zhanghaibo"
    assert runner.calls[1]["persist_history"] is True
    assert result["observations"][0]["target_agent_id"] == "diet-assistant-zhanghaibo"


def test_agent_collaboration_task_handler_delivers_orchestration_result(
    tmp_path: Path,
) -> None:
    runner = SequencedCollaborationRunner(
        {
            "main": [
                '{"action":"delegate","target_agent_id":"repo-analyzer","task_prompt":"分析仓库"}',
                '{"action":"final","final_output":"最终报告已完成"}',
            ],
            "repo-analyzer": ["分析完成"],
        }
    )
    runtime = CollaborationRuntime(runner, artifact_root=tmp_path)  # type: ignore[arg-type]
    dispatcher = FakeResultDispatcher()
    channels = object()
    handler = AgentCollaborationTaskHandler(
        runtime,
        dispatcher=dispatcher,  # type: ignore[arg-type]
        channels=channels,  # type: ignore[arg-type]
    )
    task = TaskInstance.create(
        task_type="agent_collaboration",
        source="test",
        agent_id="main",
        payload={
            "user_goal": "分析仓库",
            "controller_agent_id": "main",
            "run_id": "orch-deliver",
            "channel": "wework",
            "mode": "minimal",
            "correlation_id": "corr-deliver",
            "response_target": {
                "channel": "wework",
                "account_id": "wework-main",
                "peer_id": "zhanghaibo",
                "source_session_key": "agent:wework-entry:wework:wework-main:direct:zhanghaibo",
                "source_agent_id": "wework-entry",
            },
        },
    )

    preview = asyncio.run(handler(task))

    assert preview == "agent orchestration completed: orch-deliver observations=1 delivered=delivery-1"
    assert dispatcher.deliveries[0]["channels"] is channels
    assert dispatcher.deliveries[0]["text"] == "最终报告已完成"
    target = dispatcher.deliveries[0]["target"]
    assert target.channel == "wework"
    assert target.account_id == "wework-main"
    assert target.peer_id == "zhanghaibo"
    assert target.agent_id == "main"
    assert dispatcher.deliveries[0]["metadata"]["kind"] == "agent_orchestration_result"
    assert dispatcher.deliveries[0]["metadata"]["run_id"] == "orch-deliver"
    assert runner.sessions.appended == [
        {
            "agent_id": "wework-entry",
            "session_key": "agent:wework-entry:wework:wework-main:direct:zhanghaibo",
            "role": "assistant",
            "content": "最终报告已完成",
        }
    ]
