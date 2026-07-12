"""企业微信通道实现。"""

from __future__ import annotations

from pathlib import Path
import re
import time
from typing import Any
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs

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
    _TOKEN_ERROR_CODES = {40001, 40014, 42001}
    _REPORT_PATH_PATTERN = re.compile(
        r"(?P<path>(?:workspace/)?reports/(?:github-repos/[^\s`，。；,;]+\.md|diagrams/[^\s`，。；,;]+\.(?:drawio|png|jpg|jpeg|svg|pdf)))",
        re.IGNORECASE,
    )

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
        self.render_mode = str(account.config.get("render_mode", "markdown") or "markdown").strip().lower()
        self._http = httpx.Client(timeout=15.0, trust_env=False)
        self._access_token = ""
        self._token_expires_at = 0.0

    def receive(self) -> InboundMessage | None:
        """企业微信通过 webhook 推送入站事件，这里不主动拉取。"""

        return None

    def send(self, outbound: OutboundMessage) -> bool:
        """通过企业微信应用消息接口发送 Markdown、普通文本和文件附件。"""

        token = self._refresh_access_token()
        if not token:
            print(f"[wework] send failed: access token unavailable account={self.account_id}")
            return False
        ok = self._send_message(token, outbound, retry_on_token_error=True)
        for attachment in self._collect_file_attachments(outbound):
            if not self._send_file_attachment(token, outbound, attachment):
                ok = False
        return ok

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

    def _send_message(
        self,
        token: str,
        outbound: OutboundMessage,
        *,
        retry_on_token_error: bool,
    ) -> bool:
        """发送企业微信应用消息，token 过期时刷新后重试一次。"""

        msgtype = self._message_type(outbound)
        payload = {
            "touser": outbound.to,
            "msgtype": msgtype,
            "agentid": int(self.agent_id) if self.agent_id.isdigit() else self.agent_id,
            msgtype: {"content": outbound.text},
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
            print(
                "[wework] send ok:"
                f" account={self.account_id}"
                f" to={outbound.to}"
                f" msgtype={msgtype}"
            )
            return True
        if retry_on_token_error and errcode in self._TOKEN_ERROR_CODES:
            self._access_token = ""
            refreshed = self._refresh_access_token()
            return bool(refreshed) and self._send_message(
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

    def _upload_file_attachment(
        self,
        token: str,
        attachment: dict[str, str],
        *,
        retry_on_token_error: bool = True,
    ) -> str:
        """上传本地文件为企业微信临时素材，返回 media_id。"""

        path = Path(attachment["path"])
        file_name = attachment.get("name") or path.name
        with path.open("rb") as file_obj:
            response = self._http.post(
                f"{self.api_base}/cgi-bin/media/upload",
                params={"access_token": token, "type": "file"},
                files={"media": (file_name, file_obj)},
            )
        data = response.json()
        errcode = data.get("errcode")
        if errcode == 0:
            return str(data.get("media_id") or "")
        if retry_on_token_error and errcode in self._TOKEN_ERROR_CODES:
            self._access_token = ""
            refreshed = self._refresh_access_token()
            return (
                self._upload_file_attachment(
                    refreshed,
                    attachment,
                    retry_on_token_error=False,
                )
                if refreshed
                else ""
            )
        if errcode != 0:
            print(
                "[wework] file upload failed:"
                f" account={self.account_id}"
                f" file={file_name}"
                f" errcode={errcode}"
                f" errmsg={data.get('errmsg', '')}"
            )
            return ""
        return ""

    def _send_file_attachment(
        self,
        token: str,
        outbound: OutboundMessage,
        attachment: dict[str, str],
        *,
        retry_on_token_error: bool = True,
    ) -> bool:
        """发送企业微信文件消息。"""

        media_id = self._upload_file_attachment(token, attachment)
        file_name = attachment.get("name") or Path(attachment["path"]).name
        if not media_id:
            return False
        payload = {
            "touser": outbound.to,
            "msgtype": "file",
            "agentid": int(self.agent_id) if self.agent_id.isdigit() else self.agent_id,
            "file": {"media_id": media_id},
            "safe": 0,
        }
        response = self._http.post(
            f"{self.api_base}/cgi-bin/message/send",
            params={"access_token": token},
            json=payload,
        )
        data = response.json()
        if data.get("errcode") == 0:
            print(
                "[wework] file send ok:"
                f" account={self.account_id}"
                f" to={outbound.to}"
                f" file={file_name}"
            )
            return True
        errcode = data.get("errcode")
        if retry_on_token_error and errcode in self._TOKEN_ERROR_CODES:
            self._access_token = ""
            refreshed = self._refresh_access_token()
            return bool(refreshed) and self._send_file_attachment(
                refreshed,
                outbound,
                attachment,
                retry_on_token_error=False,
            )
        print(
            "[wework] file send failed:"
            f" account={self.account_id}"
            f" to={outbound.to}"
            f" file={file_name}"
            f" errcode={errcode}"
            f" errmsg={data.get('errmsg', '')}"
        )
        return False

    def _collect_file_attachments(self, outbound: OutboundMessage) -> list[dict[str, str]]:
        """从 metadata 和回复文本中提取可发送的本地文件附件。"""

        attachments: list[dict[str, str]] = []
        seen: set[str] = set()
        for raw in self._iter_metadata_attachments(outbound.metadata):
            attachment = self._normalize_file_attachment(raw)
            if attachment and attachment["path"] not in seen:
                attachments.append(attachment)
                seen.add(attachment["path"])
        for match in self._REPORT_PATH_PATTERN.finditer(outbound.text):
            attachment = self._normalize_file_attachment({"path": match.group("path")})
            if attachment and attachment["path"] not in seen:
                attachments.append(attachment)
                seen.add(attachment["path"])
        return attachments

    def _iter_metadata_attachments(self, metadata: dict[str, Any]) -> list[Any]:
        """兼容多个 metadata 字段名，便于不同调用方接入文件附件。"""

        raw = (
            metadata.get("attachments")
            or metadata.get("wework_attachments")
            or metadata.get("files")
            or metadata.get("wework_files")
            or []
        )
        if isinstance(raw, (str, Path)):
            return [raw]
        if isinstance(raw, dict):
            return [raw]
        if isinstance(raw, list):
            return raw
        return []

    def _normalize_file_attachment(self, raw: Any) -> dict[str, str] | None:
        """把路径或字典形式的附件描述转换成可发送的安全本地文件路径。"""

        if isinstance(raw, (str, Path)):
            raw_path = str(raw)
            name = ""
        elif isinstance(raw, dict):
            raw_path = str(raw.get("path") or raw.get("file_path") or "").strip()
            name = str(raw.get("name") or raw.get("file_name") or "").strip()
        else:
            return None
        if not raw_path:
            return None
        path = self._resolve_attachment_path(raw_path)
        if path is None:
            print(
                "[wework] ignore unsafe attachment path:"
                f" account={self.account_id}"
                f" path={raw_path}"
            )
            return None
        if not path.is_file():
            print(
                "[wework] ignore missing attachment:"
                f" account={self.account_id}"
                f" path={path}"
            )
            return None
        return {"path": str(path), "name": name or path.name}

    def _resolve_attachment_path(self, raw_path: str) -> Path | None:
        """只允许发送当前项目 workspace/reports 下的文件，避免任意文件泄漏。"""

        normalized = raw_path.strip()
        if not normalized:
            return None
        candidate = Path(normalized)
        base = Path.cwd().resolve()
        workspace = (base / "workspace").resolve()
        reports = (workspace / "reports").resolve()
        if not candidate.is_absolute():
            if candidate.parts and candidate.parts[0] == "workspace":
                candidate = base / candidate
            else:
                candidate = workspace / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(reports)
        except ValueError:
            return None
        return resolved

    def _message_type(self, outbound: OutboundMessage) -> str:
        """根据配置和内容选择企业微信消息类型。"""

        configured = str(outbound.metadata.get("render_mode") or self.render_mode or "markdown").lower()
        if configured in {"text", "plain"}:
            return "text"
        return "markdown"


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
