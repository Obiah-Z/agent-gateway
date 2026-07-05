"""企业微信通道实现。"""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore[assignment]

from agent_gateway.gateways.messaging.base import Channel, ChannelAccount
from agent_gateway.gateways.wework.crypto import decrypt_message, verify_signature
from agent_gateway.runtime.domain.models import InboundMessage, OutboundMessage


class WeWorkChannel(Channel):
    """企业微信自建应用通道。

    负责企业微信回调验签解密、文本消息入站解析，以及应用文本消息发送。
    """

    name = "wework"

    def __init__(self, account: ChannelAccount) -> None:
        """根据账号配置初始化企业微信通道。"""

        if httpx is None:
            raise RuntimeError("WeWorkChannel requires httpx")
        self.account = account
        self.account_id = account.account_id
        self.corp_id = str(account.config.get("corp_id", ""))
        self.agent_id = str(account.config.get("agent_id", ""))
        self.secret = str(account.config.get("secret", ""))
        self.callback_token = str(account.token or account.config.get("callback_token", ""))
        self.encoding_aes_key = str(account.config.get("encoding_aes_key", ""))
        self.api_base = str(account.config.get("api_base", "https://qyapi.weixin.qq.com")).rstrip("/")
        self._http = httpx.Client(timeout=15.0, trust_env=False)
        self._access_token = ""
        self._token_expires_at = 0.0

    def receive(self) -> InboundMessage | None:
        """企业微信通过 webhook 推送入站事件，这里不主动拉取。"""

        return None

    def send(self, outbound: OutboundMessage) -> bool:
        """通过企业微信应用消息接口发送文本消息。"""

        token = self._refresh_access_token()
        if not token:
            print(f"[wework] send failed: access token unavailable account={self.account_id}")
            return False
        return self._send_text(token, outbound, retry_on_token_error=True)

    def close(self) -> None:
        """关闭底层 HTTP 客户端。"""

        self._http.close()

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

    def parse_callback(self, query_string: str, body_text: str) -> InboundMessage | None:
        """把企业微信加密 XML 回调转换为统一入站消息。"""

        encrypted = _xml_text(body_text, "Encrypt")
        if not encrypted:
            raise ValueError("missing WeWork Encrypt field")
        query = parse_qs(query_string, keep_blank_values=True)
        if not verify_signature(
            token=self.callback_token,
            signature=_first(query, "msg_signature"),
            timestamp=_first(query, "timestamp"),
            nonce=_first(query, "nonce"),
            encrypted=encrypted,
        ):
            raise ValueError("WeWork callback signature mismatch")
        plaintext = decrypt_message(encrypted, self.encoding_aes_key, self.corp_id)
        message = _xml_to_dict(plaintext)
        msg_type = message.get("MsgType", "")
        if msg_type != "text":
            print(
                "[wework] ignore callback without parsable text:"
                f" account={self.account_id} msg_type={msg_type}"
            )
            return None

        sender_id = message.get("FromUserName", "")
        text = message.get("Content", "").strip()
        if not sender_id or not text:
            return None
        msg_id = message.get("MsgId", "")
        create_time = message.get("CreateTime", "")
        print(
            "[wework] inbound event:"
            f" account={self.account_id}"
            f" user={sender_id}"
            f" msg_id={msg_id}"
            f" text={text[:80]!r}"
        )
        return InboundMessage(
            text=text,
            sender_id=sender_id,
            channel=self.name,
            account_id=self.account_id,
            peer_id=sender_id,
            is_group=False,
            raw={"encrypted": _xml_to_dict(body_text), "message": message},
            metadata={
                "receive_id_type": "user_id",
                "wework_msg_id": msg_id,
                "wework_msg_type": msg_type,
                "wework_create_time": create_time,
                "wework_agent_id": message.get("AgentID", ""),
                "idempotency_key": f"wework:{self.account_id}:msg:{msg_id}"
                if msg_id
                else "",
            },
        )

    def event_id_from_callback(self, query_string: str, body_text: str) -> str:
        """提取企业微信回调的稳定事件 ID，用于进入业务前去重。"""

        try:
            inbound = self.parse_callback(query_string, body_text)
        except Exception:
            return ""
        if inbound is None:
            return ""
        return str(inbound.metadata.get("wework_msg_id", ""))

    def _refresh_access_token(self) -> str:
        """刷新并缓存企业微信 access_token。"""

        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token
        response = self._http.get(
            f"{self.api_base}/cgi-bin/gettoken",
            params={"corpid": self.corp_id, "corpsecret": self.secret},
        )
        data = response.json()
        if data.get("errcode") != 0:
            print(
                "[wework] access token refresh failed:"
                f" account={self.account_id}"
                f" errcode={data.get('errcode')}"
                f" errmsg={data.get('errmsg', '')}"
            )
            return ""
        self._access_token = str(data.get("access_token", "") or "")
        self._token_expires_at = time.time() + int(data.get("expires_in", 7200) or 7200) - 300
        print(f"[wework] access token refreshed: account={self.account_id}")
        return self._access_token

    def _send_text(
        self,
        token: str,
        outbound: OutboundMessage,
        *,
        retry_on_token_error: bool,
    ) -> bool:
        """发送企业微信文本消息，token 过期时刷新后重试一次。"""

        payload = {
            "touser": outbound.to,
            "msgtype": "text",
            "agentid": int(self.agent_id) if self.agent_id.isdigit() else self.agent_id,
            "text": {"content": outbound.text},
            "safe": 0,
        }
        response = self._http.post(
            f"{self.api_base}/cgi-bin/message/send",
            params={"access_token": token},
            json=payload,
        )
        data = response.json()
        errcode = data.get("errcode")
        if errcode == 0:
            print(f"[wework] send ok: account={self.account_id} to={outbound.to}")
            return True
        if retry_on_token_error and errcode in {40001, 40014, 42001}:
            self._access_token = ""
            refreshed = self._refresh_access_token()
            return bool(refreshed) and self._send_text(
                refreshed,
                outbound,
                retry_on_token_error=False,
            )
        print(
            "[wework] send failed:"
            f" account={self.account_id}"
            f" to={outbound.to}"
            f" errcode={errcode}"
            f" errmsg={data.get('errmsg', '')}"
        )
        return False


def _first(query: dict[str, list[str]], key: str) -> str:
    """读取 query 参数中的第一个值。"""

    values = query.get(key, [])
    return values[0] if values else ""


def _xml_text(xml_text: str, tag: str) -> str:
    """读取 XML 中某个标签的文本。"""

    return _xml_to_dict(xml_text).get(tag, "")


def _xml_to_dict(xml_text: str) -> dict[str, str]:
    """把企业微信简单 XML 消息转换为字典。"""

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError("invalid WeWork XML payload") from exc
    data: dict[str, str] = {}
    for child in list(root):
        key = _strip_namespace(child.tag)
        data[key] = child.text or ""
    return data


def _strip_namespace(tag: str) -> str:
    """去掉 ElementTree 解析出的 XML namespace 前缀。"""

    return tag.rsplit("}", 1)[-1]
