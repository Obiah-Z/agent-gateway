import asyncio

from agent_gateway.runtime.domain.agents import AgentManager
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.runtime.domain.models import (
    AgentConfig,
    Binding,
    InboundMessage,
)
from agent_gateway.runtime.domain.router import BindingTable
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher
from agent_gateway.runtime.execution.lanes import CommandQueue
from agent_gateway.runtime.observability.events import RuntimeEventStore


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []
        self.sessions = FakeSessionStore()

    async def run_turn(
        self,
        agent_id: str,
        session_key: str,
        user_text: str,
        *,
        channel: str,
        correlation_id: str = "",
    ):
        self.calls.append((agent_id, session_key, user_text))
        self.correlation_id = correlation_id
        from agent_gateway.runtime.domain.models import AgentReply

        return AgentReply(
            agent_id=agent_id,
            session_key=session_key,
            text=f"echo:{user_text}",
            stop_reason="end_turn",
            tool_calls=[],
        )


class FakeSessionStore:
    def __init__(self) -> None:
        self.appended = []

    def append_message(self, agent_id: str, session_key: str, role: str, content) -> None:
        self.appended.append(
            {
                "agent_id": agent_id,
                "session_key": session_key,
                "role": role,
                "content": content,
            }
        )


class LegacyHandoffRunner(FakeRunner):
    async def run_turn(
        self,
        agent_id: str,
        session_key: str,
        user_text: str,
        *,
        channel: str,
        correlation_id: str = "",
    ):
        self.calls.append((agent_id, session_key, user_text))
        from agent_gateway.runtime.domain.models import AgentReply

        if agent_id == "personal-secretary-zhanghaibo":
            return AgentReply(
                agent_id=agent_id,
                session_key=session_key,
                text="旧版模型声称正在转交饮食助手处理。",
                stop_reason="end_turn",
                tool_calls=["request_agent_handoff"],
            )
        return AgentReply(
            agent_id=agent_id,
            session_key=session_key,
            text=f"diet-result:{user_text}",
            stop_reason="end_turn",
            tool_calls=[],
        )


class FakeTaskQueue:
    def __init__(self) -> None:
        self.calls = []

    def enqueue(self, **kwargs):
        self.calls.append(kwargs)
        return type("Task", (), {"id": "task-auto-1"})()


def test_dispatcher_routes_executes_and_enqueues_reply(tmp_path) -> None:
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    queue = DeliveryQueue(tmp_path / "delivery")
    dispatcher = GatewayDispatcher(agents, bindings, FakeRunner(), CommandQueue(), queue)

    inbound = InboundMessage(
        text="hello",
        sender_id="u1",
        channel="cli",
        account_id="cli-local",
        peer_id="u1",
    )

    result = asyncio.run(dispatcher.dispatch_inbound(inbound))
    delivery_id = asyncio.run(dispatcher.deliver_reply(ChannelManager(), result))
    queued = queue.pending_entries()

    assert result.reply.text == "echo:hello"
    assert result.route.agent_id == "main"
    assert len(queued) == 1
    assert queued[0].id == delivery_id
    assert queued[0].channel == "cli"
    assert queued[0].to == "u1"
    assert queued[0].text == "echo:hello"
    assert queued[0].metadata["account_id"] == "cli-local"
    assert queued[0].metadata["session_key"] == result.reply.session_key


def test_dispatcher_auto_orchestrates_complex_repo_adoption_request(tmp_path) -> None:
    agents = AgentManager()
    agents.register(AgentConfig(id="wework-entry", name="WeWork", dm_scope="per-account-channel-peer"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="wework-entry", tier=5, match_key="default", match_value="*"))
    queue = DeliveryQueue(tmp_path / "delivery")
    runner = FakeRunner()
    task_queue = FakeTaskQueue()
    dispatcher = GatewayDispatcher(
        agents,
        bindings,
        runner,
        CommandQueue(),
        queue,
        task_queue=task_queue,
    )
    inbound = InboundMessage(
        text=(
            "分析这个仓库是否适合引入 Gateway，并给我风险审查、采纳计划和正式报告："
            "https://github.com/Obiah-Z/smart-trip"
        ),
        sender_id="zhanghaibo",
        channel="wework",
        account_id="wework-main",
        peer_id="zhanghaibo",
    )

    result = asyncio.run(dispatcher.dispatch_inbound(inbound))

    assert runner.calls == []
    assert result.reply.stop_reason == "orchestration_enqueued"
    assert result.reply.tool_calls == ["start_agent_orchestration"]
    assert "已启动主控协作任务" in result.reply.text
    assert len(task_queue.calls) == 1
    call = task_queue.calls[0]
    assert call["task_type"] == "agent_collaboration"
    assert call["source"] == "auto_orchestration"
    assert call["agent_id"] == "wework-entry"
    assert call["session_key"] == (
        f"orchestration:{call['payload']['run_id']}:controller:wework-entry"
    )
    assert call["payload"]["controller_agent_id"] == "wework-entry"
    assert call["payload"]["channel"] == "wework"
    assert call["payload"]["response_target"] == {
        "channel": "wework",
        "account_id": "wework-main",
        "peer_id": "zhanghaibo",
        "source_session_key": (
            "agent:wework-entry:wework:wework-main:direct:zhanghaibo"
        ),
        "source_agent_id": "wework-entry",
    }


def test_dispatcher_does_not_auto_orchestrate_simple_repo_question(tmp_path) -> None:
    agents = AgentManager()
    agents.register(AgentConfig(id="wework-entry", name="WeWork", dm_scope="per-account-channel-peer"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="wework-entry", tier=5, match_key="default", match_value="*"))
    queue = DeliveryQueue(tmp_path / "delivery")
    runner = FakeRunner()
    task_queue = FakeTaskQueue()
    dispatcher = GatewayDispatcher(
        agents,
        bindings,
        runner,
        CommandQueue(),
        queue,
        task_queue=task_queue,
    )
    inbound = InboundMessage(
        text="帮我看看这个 GitHub 仓库先读哪些文件：https://github.com/example/repo",
        sender_id="zhanghaibo",
        channel="wework",
        account_id="wework-main",
        peer_id="zhanghaibo",
    )

    result = asyncio.run(dispatcher.dispatch_inbound(inbound))

    assert len(task_queue.calls) == 0
    assert len(runner.calls) == 1
    assert result.reply.text.startswith("echo:")


def test_dispatcher_auto_orchestrates_personal_secretary_research_report_request(
    tmp_path,
) -> None:
    agents = AgentManager()
    agents.register(
        AgentConfig(
            id="personal-secretary-zhanghaibo",
            name="Secretary",
            dm_scope="per-account-channel-peer",
        )
    )
    bindings = BindingTable()
    bindings.add(
        Binding(
            agent_id="personal-secretary-zhanghaibo",
            tier=1,
            match_key="peer_id",
            match_value="ZhangHaiBo",
            priority=100,
        )
    )
    queue = DeliveryQueue(tmp_path / "delivery")
    runner = FakeRunner()
    task_queue = FakeTaskQueue()
    dispatcher = GatewayDispatcher(
        agents,
        bindings,
        runner,
        CommandQueue(),
        queue,
        task_queue=task_queue,
    )
    inbound = InboundMessage(
        text="调研一下常见的午餐搭配，并写入本地文档",
        sender_id="ZhangHaiBo",
        channel="wework",
        account_id="wework-main",
        peer_id="ZhangHaiBo",
        metadata={"correlation_id": "wework_msg_1"},
    )

    result = asyncio.run(dispatcher.dispatch_inbound(inbound))

    assert runner.calls == []
    assert result.route.agent_id == "personal-secretary-zhanghaibo"
    assert result.reply.stop_reason == "orchestration_enqueued"
    assert result.reply.tool_calls == ["start_agent_orchestration"]
    assert len(task_queue.calls) == 1
    call = task_queue.calls[0]
    assert call["task_type"] == "agent_collaboration"
    assert call["source"] == "auto_orchestration"
    assert call["agent_id"] == "personal-secretary-zhanghaibo"
    assert call["session_key"] == (
        f"orchestration:{call['payload']['run_id']}:controller:personal-secretary-zhanghaibo"
    )
    assert "wework_msg_1" in call["idempotency_key"]
    assert call["payload"]["user_goal"] == inbound.text
    assert call["payload"]["controller_agent_id"] == "personal-secretary-zhanghaibo"
    assert call["payload"]["response_target"] == {
        "channel": "wework",
        "account_id": "wework-main",
        "peer_id": "ZhangHaiBo",
        "source_session_key": (
            "agent:personal-secretary-zhanghaibo:wework:wework-main:direct:zhanghaibo"
        ),
        "source_agent_id": "personal-secretary-zhanghaibo",
    }
    assert runner.sessions.appended == [
        {
            "agent_id": "personal-secretary-zhanghaibo",
            "session_key": (
                "agent:personal-secretary-zhanghaibo:wework:wework-main:direct:zhanghaibo"
            ),
            "role": "user",
            "content": inbound.text,
        },
        {
            "agent_id": "personal-secretary-zhanghaibo",
            "session_key": (
                "agent:personal-secretary-zhanghaibo:wework:wework-main:direct:zhanghaibo"
            ),
            "role": "assistant",
            "content": result.reply.text,
        },
    ]


def test_dispatcher_auto_orchestration_same_text_distinct_messages_create_distinct_tasks(
    tmp_path,
) -> None:
    agents = AgentManager()
    agents.register(
        AgentConfig(
            id="personal-secretary-zhanghaibo",
            name="Secretary",
            dm_scope="per-account-channel-peer",
        )
    )
    bindings = BindingTable()
    bindings.add(
        Binding(
            agent_id="personal-secretary-zhanghaibo",
            tier=1,
            match_key="peer_id",
            match_value="ZhangHaiBo",
            priority=100,
        )
    )
    queue = DeliveryQueue(tmp_path / "delivery")
    task_queue = FakeTaskQueue()
    dispatcher = GatewayDispatcher(
        agents,
        bindings,
        FakeRunner(),
        CommandQueue(),
        queue,
        task_queue=task_queue,
    )
    text = "调研一下常见的午餐搭配，并写入本地文档"

    for correlation_id in ("wework_msg_1", "wework_msg_2"):
        inbound = InboundMessage(
            text=text,
            sender_id="ZhangHaiBo",
            channel="wework",
            account_id="wework-main",
            peer_id="ZhangHaiBo",
            metadata={"correlation_id": correlation_id},
        )
        result = asyncio.run(dispatcher.dispatch_inbound(inbound))
        assert result.reply.stop_reason == "orchestration_enqueued"

    first, second = task_queue.calls
    assert first["payload"]["user_goal"] == second["payload"]["user_goal"] == text
    assert first["payload"]["run_id"] != second["payload"]["run_id"]
    assert first["idempotency_key"] != second["idempotency_key"]
    assert "wework_msg_1" in first["idempotency_key"]
    assert "wework_msg_2" in second["idempotency_key"]


def test_dispatcher_does_not_execute_legacy_one_shot_agent_handoff(tmp_path) -> None:
    agents = AgentManager()
    agents.register(
        AgentConfig(
            id="personal-secretary-zhanghaibo",
            name="Secretary",
            dm_scope="per-account-channel-peer",
        )
    )
    agents.register(
        AgentConfig(
            id="diet-assistant-zhanghaibo",
            name="Diet",
            dm_scope="per-account-channel-peer",
        )
    )
    bindings = BindingTable()
    bindings.add(
        Binding(
            agent_id="personal-secretary-zhanghaibo",
            tier=1,
            match_key="peer_id",
            match_value="zhanghaibo",
            priority=100,
        )
    )
    queue = DeliveryQueue(tmp_path / "delivery")
    runner = LegacyHandoffRunner()
    dispatcher = GatewayDispatcher(agents, bindings, runner, CommandQueue(), queue)
    inbound = InboundMessage(
        text="帮我切换到饮食 Agent，记录早餐",
        sender_id="zhanghaibo",
        channel="wework",
        account_id="wework-main",
        peer_id="zhanghaibo",
    )

    result = asyncio.run(dispatcher.dispatch_inbound(inbound))

    assert result.route.agent_id == "personal-secretary-zhanghaibo"
    assert result.reply.agent_id == "personal-secretary-zhanghaibo"
    assert result.reply.text == "旧版模型声称正在转交饮食助手处理。"
    assert [call[0] for call in runner.calls] == ["personal-secretary-zhanghaibo"]


def test_dispatcher_propagates_correlation_id_to_events_and_delivery(tmp_path) -> None:
    agents = AgentManager()
    agents.register(AgentConfig(id="main", name="Main"))
    bindings = BindingTable()
    bindings.add(Binding(agent_id="main", tier=5, match_key="default", match_value="*"))
    queue = DeliveryQueue(tmp_path / "delivery")
    store = RuntimeEventStore(tmp_path / "events" / "runtime-events.jsonl")
    runner = FakeRunner()
    dispatcher = GatewayDispatcher(
        agents,
        bindings,
        runner,
        CommandQueue(),
        queue,
        event_store=store,
    )
    inbound = InboundMessage(
        text="hello",
        sender_id="u1",
        channel="cli",
        account_id="cli-local",
        peer_id="u1",
        metadata={"correlation_id": "corr-test-1"},
    )

    result = asyncio.run(dispatcher.dispatch_inbound(inbound))
    asyncio.run(dispatcher.deliver_reply(ChannelManager(), result))

    events = store.tail(limit=10)
    queued = queue.pending_entries()

    assert runner.correlation_id == "corr-test-1"
    assert {event["correlation_id"] for event in events} == {"corr-test-1"}
    assert queued[0].metadata["correlation_id"] == "corr-test-1"
