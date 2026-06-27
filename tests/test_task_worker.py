import asyncio
from pathlib import Path

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.domain.models import AgentReply, InboundMessage, RouteResolution
from agent_gateway.runtime.tasks.handlers import AgentInboundTaskHandler
from agent_gateway.runtime.tasks import LocalTaskQueue, LocalTaskStore, TaskWorkerRuntime


def test_task_worker_run_once_acknowledges_success(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="echo", source="test", payload={"text": "hello"})
    worker = TaskWorkerRuntime(queue, worker_id="worker-1")
    worker.register_handler("echo", lambda item: f"echo:{item.payload['text']}")

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "done"
    assert stored.result_preview == "echo:hello"
    assert stored.metadata["worker_id"] == "worker-1"


def test_task_worker_run_once_returns_false_without_available_task(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    worker = TaskWorkerRuntime(queue)
    worker.register_handler("echo", lambda item: "ok")

    assert asyncio.run(worker.run_once()) is False


def test_task_worker_ignores_unregistered_task_type(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="unknown", source="test")
    worker = TaskWorkerRuntime(queue)

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is False
    assert stored.status == "pending"


def test_task_worker_retries_handler_exception_when_configured(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(task_type="unstable", source="test")
    worker = TaskWorkerRuntime(queue, retry_exceptions=True)

    def fail(_task):
        raise RuntimeError("temporary")

    worker.register_handler("unstable", fail)

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "retrying"
    assert stored.retry_count == 1
    assert stored.error == "temporary"


def test_task_worker_stats_include_registered_handlers_and_queue(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    queue.enqueue(task_type="echo", source="test")
    worker = TaskWorkerRuntime(queue, worker_id="worker-1", concurrency=3)
    worker.register_handler("echo", lambda item: "ok")

    stats = worker.stats()

    assert stats["running"] is False
    assert stats["worker_id"] == "worker-1"
    assert stats["concurrency"] == 3
    assert stats["registered_task_types"] == ["echo"]
    assert stats["queue"]["pending"] == 1


class FakeInboundDispatcher:
    def __init__(self) -> None:
        self.dispatched: list[InboundMessage] = []
        self.delivered = 0

    async def dispatch_inbound(self, inbound: InboundMessage, *, forced_agent_id: str = ""):
        del forced_agent_id
        self.dispatched.append(inbound)
        return type(
            "DispatchResult",
            (),
            {
                "inbound": inbound,
                "route": RouteResolution(agent_id="main", session_key="main:user-1"),
                "reply": AgentReply(
                    agent_id="main",
                    session_key="main:user-1",
                    text="done",
                    stop_reason="end_turn",
                ),
            },
        )()

    async def deliver_reply(self, channels: ChannelManager, result) -> str:
        del channels, result
        self.delivered += 1
        return "delivery-1"


def test_task_worker_executes_agent_inbound_task(tmp_path: Path) -> None:
    queue = LocalTaskQueue(LocalTaskStore(tmp_path / "tasks"))
    task = queue.enqueue(
        task_type="agent_inbound",
        source="feishu",
        payload={
            "text": "/github-repo-analyzer https://github.com/openai/openai-python",
            "sender_id": "user-1",
            "channel": "feishu",
            "account_id": "bot-a",
            "peer_id": "user-1",
            "metadata": {"receive_id_type": "open_id"},
        },
    )
    dispatcher = FakeInboundDispatcher()
    worker = TaskWorkerRuntime(queue)
    worker.register_handler(
        "agent_inbound",
        AgentInboundTaskHandler(dispatcher, ChannelManager()),
    )

    handled = asyncio.run(worker.run_once())
    stored = queue.store.get(task.id)

    assert handled is True
    assert stored.status == "done"
    assert stored.result_preview == "agent inbound delivered: delivery-1"
    assert dispatcher.delivered == 1
    assert dispatcher.dispatched[0].text.startswith("/github-repo-analyzer ")
    assert dispatcher.dispatched[0].metadata["receive_id_type"] == "open_id"
    assert dispatcher.dispatched[0].metadata["background_task_id"] == task.id
