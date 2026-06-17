import asyncio

from agent_gateway.channels.base import ChannelAccount
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.runtime.feishu_long_connection import FeishuLongConnectionRuntime


class FakeChannelRuntime:
    def __init__(self) -> None:
        self.messages = []

    async def ingest_external(self, inbound) -> None:
        self.messages.append(inbound)


def test_feishu_long_connection_event_to_inbound_for_p2p() -> None:
    inbound = FeishuLongConnectionRuntime.event_to_inbound(
        {
            "type": "im.message.receive_v1",
            "event_id": "evt-1",
            "message_id": "om_1",
            "chat_id": "oc_1",
            "chat_type": "p2p",
            "sender_id": "ou_user",
            "message_type": "text",
            "content": "你好",
        },
        "feishu-long-local",
    )

    assert inbound is not None
    assert inbound.text == "你好"
    assert inbound.channel == "feishu"
    assert inbound.account_id == "feishu-long-local"
    assert inbound.sender_id == "ou_user"
    assert inbound.peer_id == "ou_user"
    assert inbound.is_group is False
    assert inbound.metadata["receive_id_type"] == "open_id"
    assert inbound.metadata["connection_mode"] == "long_connection"
    assert inbound.metadata["feishu_event_id"] == "evt-1"


def test_feishu_long_connection_event_to_inbound_for_group() -> None:
    inbound = FeishuLongConnectionRuntime.event_to_inbound(
        {
            "type": "im.message.receive_v1",
            "event_id": "evt-2",
            "message_id": "om_2",
            "chat_id": "oc_group",
            "chat_type": "group",
            "sender_id": "ou_user",
            "message_type": "text",
            "content": "群消息",
        },
        "feishu-long-local",
    )

    assert inbound is not None
    assert inbound.peer_id == "oc_group"
    assert inbound.is_group is True
    assert inbound.metadata["receive_id_type"] == "chat_id"


def test_feishu_long_connection_ignores_unusable_event() -> None:
    assert FeishuLongConnectionRuntime.event_to_inbound(
        {"sender_id": "ou_user", "chat_type": "p2p", "content": ""},
        "feishu-long-local",
    ) is None
    assert FeishuLongConnectionRuntime.event_to_inbound(
        {"sender_id": "", "chat_type": "p2p", "content": "hello"},
        "feishu-long-local",
    ) is None


def test_feishu_long_connection_builds_consumers_only_for_enabled_mode() -> None:
    manager = ChannelManager()
    long_account = ChannelAccount(
        channel="feishu",
        account_id="long",
        config={
            "connection_mode": "long_connection",
            "event_keys": [
                "im.message.receive_v1",
                "im.chat.member.bot.added_v1",
            ],
            "event_identity": "bot",
        },
    )
    webhook_account = ChannelAccount(
        channel="feishu",
        account_id="webhook",
        config={"connection_mode": "webhook"},
    )
    manager.register(type("Channel", (), {"name": "feishu", "close": lambda self: None})(), long_account)
    manager.register(type("Channel", (), {"name": "feishu", "close": lambda self: None})(), webhook_account)

    runtime = FeishuLongConnectionRuntime(
        channels=manager,
        channel_runtime=FakeChannelRuntime(),
    )

    consumers = runtime._build_consumers()

    assert len(consumers) == 2
    assert consumers[0].account_id == "long"
    assert consumers[0].event_key == "im.message.receive_v1"
    assert consumers[1].event_key == "im.chat.member.bot.added_v1"


def test_feishu_long_connection_normalizes_comma_separated_event_keys() -> None:
    keys = FeishuLongConnectionRuntime._normalize_event_keys(
        {
            "event_keys": "im.message.receive_v1, im.chat.member.bot.added_v1",
            "event_key": "im.message.receive_v1",
        }
    )

    assert keys == ("im.message.receive_v1", "im.chat.member.bot.added_v1")


def test_feishu_long_connection_submit_inbound_uses_channel_runtime() -> None:
    channel_runtime = FakeChannelRuntime()
    runtime = FeishuLongConnectionRuntime(
        channels=ChannelManager(),
        channel_runtime=channel_runtime,
    )
    inbound = FeishuLongConnectionRuntime.event_to_inbound(
        {
            "chat_id": "oc_1",
            "chat_type": "group",
            "sender_id": "ou_user",
            "content": "hello",
        },
        "feishu-long-local",
    )
    assert inbound is not None

    async def _run() -> None:
        runtime._loop = asyncio.get_running_loop()
        runtime._submit_inbound(inbound)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert channel_runtime.messages == [inbound]
