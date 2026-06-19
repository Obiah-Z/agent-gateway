from agent_gateway.channels.base import Channel, ChannelAccount
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.core.models import InboundMessage, OutboundMessage


class DummyChannel(Channel):
    name = "dummy"

    def receive(self) -> InboundMessage | None:
        return None

    def send(self, outbound: OutboundMessage) -> bool:
        return True


def test_channel_manager_tracks_channel_by_account() -> None:
    manager = ChannelManager()
    account_a = ChannelAccount(channel="dummy", account_id="a")
    account_b = ChannelAccount(channel="dummy", account_id="b")
    channel_a = DummyChannel()
    channel_b = DummyChannel()

    manager.register(channel_a, account_a)
    manager.register(channel_b, account_b)

    assert manager.get("dummy", "a") is channel_a
    assert manager.get("dummy", "b") is channel_b
    assert manager.get("dummy") is channel_a
    assert manager.list_channels() == ["dummy"]
