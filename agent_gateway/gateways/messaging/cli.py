from __future__ import annotations

from agent_gateway.gateways.messaging.base import Channel
from agent_gateway.runtime.domain.models import InboundMessage, OutboundMessage


class CLIChannel(Channel):
    name = "cli"

    def __init__(self, account_id: str = "cli-local") -> None:
        self.account_id = account_id

    def receive(self) -> InboundMessage | None:
        try:
            text = input("You > ").strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if not text:
            return None
        return InboundMessage(
            text=text,
            sender_id="cli-user",
            channel=self.name,
            account_id=self.account_id,
            peer_id="cli-user",
        )

    def send(self, outbound: OutboundMessage) -> bool:
        print(outbound.text)
        return True
