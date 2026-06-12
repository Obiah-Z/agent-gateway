from __future__ import annotations

from agent_gateway.channels.base import Channel, ChannelAccount


class ChannelManager:
    def __init__(self) -> None:
        self._channels_by_key: dict[tuple[str, str], Channel] = {}
        self._first_by_name: dict[str, Channel] = {}
        self.accounts: list[ChannelAccount] = []

    def register(self, channel: Channel, account: ChannelAccount) -> None:
        key = (account.channel, account.account_id)
        self._channels_by_key[key] = channel
        self._first_by_name.setdefault(channel.name, channel)
        self.accounts.append(account)

    def get(self, name: str, account_id: str = "") -> Channel | None:
        if account_id:
            return self._channels_by_key.get((name, account_id))
        return self._first_by_name.get(name)

    def list_channels(self) -> list[str]:
        return sorted(self._first_by_name.keys())

    def iter_channels(self) -> list[tuple[ChannelAccount, Channel]]:
        pairs: list[tuple[ChannelAccount, Channel]] = []
        for account in self.accounts:
            channel = self._channels_by_key.get((account.channel, account.account_id))
            if channel is not None:
                pairs.append((account, channel))
        return pairs

    def replace_from(self, other: "ChannelManager") -> None:
        self._channels_by_key = dict(other._channels_by_key)
        self._first_by_name = dict(other._first_by_name)
        self.accounts = list(other.accounts)

    def close_all(self) -> None:
        for channel in self._channels_by_key.values():
            channel.close()
