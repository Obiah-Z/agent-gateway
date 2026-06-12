from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent_gateway.models import InboundMessage, OutboundMessage


@dataclass(slots=True)
class ChannelAccount:
    channel: str
    account_id: str
    label: str = ""
    token: str = ""
    config: dict[str, Any] = field(default_factory=dict)


class Channel(ABC):
    name: str = "unknown"

    @abstractmethod
    def receive(self) -> InboundMessage | None:
        raise NotImplementedError

    @abstractmethod
    def send(self, outbound: OutboundMessage) -> bool:
        raise NotImplementedError

    def receive_batch(self) -> list[InboundMessage]:
        message = self.receive()
        return [message] if message is not None else []

    def close(self) -> None:
        pass
