from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from agent_gateway.runtime.domain.models import InboundMessage, OutboundMessage


@dataclass(slots=True)
class ChannelAccount:
    """描述一个通道账号配置。"""
    channel: str
    account_id: str
    label: str = ""
    token: str = ""
    config: dict[str, Any] = field(default_factory=dict)


class Channel(ABC):
    """消息通道协议基类。"""
    name: str = "unknown"

    @abstractmethod
    def receive(self) -> InboundMessage | None:
        """接收一条入站消息。"""
        raise NotImplementedError

    @abstractmethod
    def send(self, outbound: OutboundMessage) -> bool:
        """发送一条出站消息。"""
        raise NotImplementedError

    def receive_batch(self) -> list[InboundMessage]:
        """批量接收入站消息。"""
        message = self.receive()
        return [message] if message is not None else []

    def close(self) -> None:
        """关闭通道并释放资源。"""
        pass
