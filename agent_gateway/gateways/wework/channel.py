"""企业微信通道实现。"""

from __future__ import annotations

from urllib.parse import parse_qs

from agent_gateway.gateways.messaging.base import Channel, ChannelAccount
from agent_gateway.gateways.wework.crypto import decrypt_message, verify_signature
from agent_gateway.runtime.domain.models import InboundMessage, OutboundMessage


class WeWorkChannel(Channel):
    """企业微信自建应用通道。

    当前先实现企业微信后台 URL 验证所需的签名校验和 echostr 解密，后续再扩展
    POST 消息入站、应用消息发送和文件能力。
    """

    name = "wework"

    def __init__(self, account: ChannelAccount) -> None:
        """根据账号配置初始化企业微信通道。"""

        self.account = account
        self.account_id = account.account_id
        self.corp_id = str(account.config.get("corp_id", ""))
        self.agent_id = str(account.config.get("agent_id", ""))
        self.secret = str(account.config.get("secret", ""))
        self.callback_token = str(account.token or account.config.get("callback_token", ""))
        self.encoding_aes_key = str(account.config.get("encoding_aes_key", ""))
        self.api_base = str(account.config.get("api_base", "https://qyapi.weixin.qq.com"))

    def receive(self) -> InboundMessage | None:
        """企业微信通过 webhook 推送入站事件，这里不主动拉取。"""

        return None

    def send(self, outbound: OutboundMessage) -> bool:
        """发送企业微信出站消息。

        首期只打通回调 URL 验证，真实应用消息发送在后续阶段实现。
        """

        del outbound
        print("[wework] send is not implemented yet")
        return False

    def verify_url(self, query_string: str) -> str:
        """处理企业微信后台 URL 验证请求，返回明文 echostr。"""

        query = parse_qs(query_string, keep_blank_values=True)
        signature = _first(query, "msg_signature")
        timestamp = _first(query, "timestamp")
        nonce = _first(query, "nonce")
        echostr = _first(query, "echostr")
        if not verify_signature(
            token=self.callback_token,
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=echostr,
        ):
            raise ValueError("WeWork callback signature mismatch")
        return decrypt_message(echostr, self.encoding_aes_key, self.corp_id)


def _first(query: dict[str, list[str]], key: str) -> str:
    """读取 query 参数中的第一个值。"""

    values = query.get(key, [])
    return values[0] if values else ""
