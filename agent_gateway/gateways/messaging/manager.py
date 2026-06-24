from __future__ import annotations

from agent_gateway.gateways.messaging.base import Channel, ChannelAccount


class ChannelManager:
    """管理已配置的消息通道实例。"""
    def __init__(self) -> None:
        """初始化实例。"""
        self._channels_by_key: dict[tuple[str, str], Channel] = {}
        self._first_by_name: dict[str, Channel] = {}
        self.accounts: list[ChannelAccount] = []

    def register(self, channel: Channel, account: ChannelAccount) -> None:
        """注册通道实例。"""
        key = (account.channel, account.account_id)
        self._channels_by_key[key] = channel
        self._first_by_name.setdefault(channel.name, channel)
        self.accounts.append(account)

    def get(self, name: str, account_id: str = "") -> Channel | None:
        """获取指定对象。"""
        if account_id:
            return self._channels_by_key.get((name, account_id))
        return self._first_by_name.get(name)

    def list_channels(self) -> list[str]:
        """列出已注册通道。"""
        return sorted(self._first_by_name.keys())

    def iter_channels(self) -> list[tuple[ChannelAccount, Channel]]:
        """迭代已注册通道。"""
        pairs: list[tuple[ChannelAccount, Channel]] = []
        for account in self.accounts:
            channel = self._channels_by_key.get((account.channel, account.account_id))
            if channel is not None:
                pairs.append((account, channel))
        return pairs

    def replace_from(self, other: "ChannelManager") -> None:
        """用另一个管理器的通道替换当前通道集合。"""
        self._channels_by_key = dict(other._channels_by_key)
        self._first_by_name = dict(other._first_by_name)
        self.accounts = list(other.accounts)

    def close_all(self) -> None:
        """关闭所有已注册通道。"""
        for channel in self._channels_by_key.values():
            channel.close()
