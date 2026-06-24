from __future__ import annotations

from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.messaging.cli import CLIChannel
from agent_gateway.gateways.feishu.channel import FeishuChannel
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.gateways.messaging.telegram import TelegramChannel
from agent_gateway.config import GatewaySettings


def build_channel_manager(
    settings: GatewaySettings,
    accounts: list[ChannelAccount],
) -> ChannelManager:
    """根据配置构建通道管理器。"""
    manager = ChannelManager()
    state_dir = settings.data_dir / "channel-state"
    for account in accounts:
        channel = _build_channel(account, state_dir)
        if channel is None:
            continue
        manager.register(channel, account)
    return manager


def _build_channel(account: ChannelAccount, state_dir):
    """构建输出结构。"""
    if account.channel == "cli":
        return CLIChannel(account_id=account.account_id)
    if account.channel == "telegram":
        return TelegramChannel(account, state_dir)
    if account.channel == "feishu":
        return FeishuChannel(account, state_dir)
    return None
