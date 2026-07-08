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
