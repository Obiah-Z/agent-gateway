from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore[assignment]

try:
    from Crypto.Cipher import AES
except ImportError:  # pragma: no cover - optional dependency
    AES = None  # type: ignore[assignment]

import base64
import hashlib
import shutil
import subprocess

from agent_gateway.gateways.messaging.base import Channel, ChannelAccount
from agent_gateway.gateways.feishu.cards import FeishuCardRenderer, FeishuSendPayload
from agent_gateway.gateways.feishu.state import FeishuCardState, FeishuCardStateStore
from agent_gateway.runtime.state.queue import PermanentDeliveryError
from agent_gateway.runtime.domain.models import InboundMessage, OutboundMessage


class FeishuChannel(Channel):
    """飞书通道实现。

    负责飞书事件解析、消息发送，以及可交互卡片状态的本地维护。
    """

    name = "feishu"
    _CARD_FALLBACK_ERROR_CODES = {230025, 230054, 230099}
    _PERMANENT_SEND_ERROR_CODES = {99992351}

    def __init__(
        self,
        account: ChannelAccount,
        state_dir: Path | None = None,
        *,
        state_read_repository: Any = None,
        state_write_repository: Any = None,
    ) -> None:
        """根据账号配置初始化 API 客户端、渲染器和状态存储。"""

        if httpx is None:
            raise RuntimeError("FeishuChannel requires httpx")
        self.account = account
        self.account_id = account.account_id
        self.app_id = str(account.config.get("app_id", ""))
        self.app_secret = str(account.config.get("app_secret", ""))
        self.verification_token = str(account.config.get("verification_token", ""))
        self.encrypt_key = str(account.config.get("encrypt_key", ""))
        self.bot_open_id = str(account.config.get("bot_open_id", ""))
        self.send_mode = self._normalize_send_mode(account.config.get("send_mode", "api"))
        self.lark_cli_command = str(account.config.get("lark_cli_command", "lark-cli") or "lark-cli")
        self.lark_cli_identity = str(account.config.get("lark_cli_identity", "bot") or "bot")
        self.render_mode = self._normalize_render_mode(
            account.config.get("render_mode", "auto")
        )
        self.card_page_max_bytes = self._read_positive_int(
            account.config.get("card_page_max_bytes", 6000),
            default=6000,
            minimum=128,
        )
        self.text_page_max_bytes = self._read_positive_int(
            account.config.get("text_page_max_bytes", 12000),
            default=12000,
            minimum=256,
        )
        self.enable_stateful_cards = self._read_bool(
            account.config.get("enable_stateful_cards", False),
            default=False,
        )
        is_lark = bool(account.config.get("is_lark", False))
        self.api_base = (
            "https://open.larksuite.com/open-apis"
            if is_lark
            else "https://open.feishu.cn/open-apis"
        )
        self._http = httpx.Client(timeout=15.0, trust_env=False)
        self._tenant_token = ""
        self._token_expires_at = 0.0
        self._renderer = FeishuCardRenderer(
            card_page_max_bytes=self.card_page_max_bytes,
            text_page_max_bytes=self.text_page_max_bytes,
            enable_stateful_cards=self.enable_stateful_cards,
        )
        self._card_state_store = (
            FeishuCardStateStore(
                state_dir / "feishu" / self.account_id,
                read_backend=state_read_repository,
                write_backend=state_write_repository,
            )
            if state_dir is not None
            else None
        )

    def receive(self) -> InboundMessage | None:
        """飞书通过 webhook/长连接推送入站事件，这里不做主动拉取。"""

        return None

    def send(self, outbound: OutboundMessage) -> bool:
        """发送一条出站消息，必要时拆分成多页卡片或多段文本。"""

        if self.send_mode == "lark_cli":
            return self._send_via_lark_cli(outbound)
        token = self._refresh_token()
        if not token:
            print("[feishu] send failed: tenant token unavailable")
            return False
        payloads = self._build_send_payloads(outbound)
        for page_index, payload in enumerate(payloads, start=1):
            success = self._send_single_payload(token, outbound, payload, page_index, len(payloads))
            if not success:
                return False
        return True

    def parse_event(self, payload: dict[str, Any], token: str = "") -> InboundMessage | None:
        """把飞书 webhook 事件转换成统一入站消息。"""

        if self.verification_token and token and token != self.verification_token:
            print(f"[feishu] ignore event: verification token mismatch account={self.account_id}")
            return None
        if "challenge" in payload:
            return None
        event_type = self._extract_event_type(payload)
        if event_type == "card.action.trigger":
            return self.parse_card_action(payload, token=token)

        event = payload.get("event", {})
        message = event.get("message", {})
        sender = event.get("sender", {}).get("sender_id", {})
        user_id = sender.get("open_id", sender.get("user_id", ""))
        chat_id = message.get("chat_id", "")
        chat_type = message.get("chat_type", "")
        message_id = str(
            message.get("message_id")
            or message.get("open_message_id")
            or message.get("id")
            or ""
        )
        event_id = str(
            payload.get("event_id")
            or (payload.get("header", {}) if isinstance(payload.get("header"), dict) else {}).get("event_id", "")
            or ""
        )
        is_group = chat_type == "group"
        if is_group and self.bot_open_id and not self._bot_mentioned(event):
            print(f"[feishu] ignore group event without mention: chat_id={chat_id}")
            return None

        text, media = self._parse_content(message)
        if not text:
            print(
                "[feishu] ignore event without parsable text:"
                f" message_type={message.get('msg_type', '')}"
            )
            return None
        print(
            "[feishu] inbound event:"
            f" account={self.account_id}"
            f" user={user_id}"
            f" chat_type={chat_type}"
            f" peer={user_id if chat_type == 'p2p' else chat_id}"
            f" text={text[:80]!r}"
        )
        return InboundMessage(
            text=text,
            sender_id=user_id,
            channel=self.name,
            account_id=self.account_id,
            peer_id=user_id if chat_type == "p2p" else chat_id,
            is_group=is_group,
            media=media,
            raw=payload,
            metadata={
                "receive_id_type": "open_id" if chat_type == "p2p" else "chat_id",
                "feishu_event_id": event_id,
                "feishu_message_id": message_id,
                "feishu_event_type": event_type,
                "feishu_chat_id": chat_id,
                "feishu_chat_type": chat_type,
                "feishu_message_type": str(message.get("msg_type", "")),
            },
        )

    def parse_card_action(self, payload: dict[str, Any], token: str = "") -> InboundMessage | None:
        """把卡片交互动作转换成可继续进入 Agent 的入站消息。"""

        if self.verification_token and token and token != self.verification_token:
            print(
                f"[feishu] ignore card action: verification token mismatch account={self.account_id}"
            )
            return None
        event = payload.get("event", {})
        if not isinstance(event, dict):
            return None
        operator = event.get("operator", {})
        if not isinstance(operator, dict):
            operator = {}
        context = event.get("context", {})
        if not isinstance(context, dict):
            context = {}
        action = event.get("action", {})
        if not isinstance(action, dict):
            action = {}
        sender_id = str(operator.get("open_id") or operator.get("user_id") or "")
        peer_id = str(context.get("open_chat_id") or sender_id)
        if not sender_id or not peer_id:
            print("[feishu] ignore card action without sender or peer")
            return None
        action_value = action.get("value")
        prompt = self._build_card_action_prompt(payload, action_value)
        metadata = {
            "receive_id_type": "chat_id",
            "kind": "card_action",
            "feishu_event_type": "card.action.trigger",
            "feishu_action_tag": str(action.get("tag", "")),
            "feishu_action_name": str(action.get("name", "")),
            "feishu_action_value": action_value,
            "feishu_form_value": action.get("form_value", {}),
            "feishu_input_value": action.get("input_value", ""),
            "feishu_action_option": action.get("option", ""),
            "feishu_action_options": action.get("options", []),
            "feishu_action_checked": action.get("checked", False),
            "feishu_action_token": str(event.get("token", "")),
            "feishu_message_id": str(context.get("open_message_id", "")),
            "feishu_chat_id": peer_id,
        }
        print(
            "[feishu] inbound card action:"
            f" account={self.account_id}"
            f" sender={sender_id}"
            f" peer={peer_id}"
            f" tag={metadata['feishu_action_tag']}"
        )
        return InboundMessage(
            text=prompt,
            sender_id=sender_id,
            channel=self.name,
            account_id=self.account_id,
            peer_id=peer_id,
            is_group=True,
            raw=payload,
            metadata=metadata,
        )

    def close(self) -> None:
        """关闭底层 HTTP 客户端。"""

        self._http.close()

    def render_card_state(self, state: FeishuCardState) -> tuple[dict[str, Any], str]:
        """按当前分页/折叠状态重新渲染卡片。"""

        return self._renderer.render_stateful_card(state)

    def load_card_state(self, card_id: str) -> FeishuCardState | None:
        """读取本地缓存的卡片状态。"""

        if self._card_state_store is None:
            return None
        return self._card_state_store.load(card_id)

    def save_card_state(self, state: FeishuCardState) -> None:
        """持久化卡片状态，供后续交互继续使用。"""

        if self._card_state_store is None:
            return
        self._card_state_store.save(state)

    def is_control_card_action(self, payload: dict[str, Any]) -> bool:
        """判断该卡片动作是否属于网关自己的分页/折叠控制。"""

        action_value = self._extract_card_action_value(payload)
        return (
            isinstance(action_value, dict)
            and str(action_value.get("source", "")).strip() == "gateway_card_control"
            and bool(str(action_value.get("card_id", "")).strip())
        )

    def handle_control_card_action(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """处理本地卡片控制动作，并返回飞书要求的更新结构。"""

        action_value = self._extract_card_action_value(payload)
        if not isinstance(action_value, dict):
            return None
        card_id = str(action_value.get("card_id", "")).strip()
        if not card_id:
            return None
        state = self.load_card_state(card_id)
        if state is None:
            return {
                "toast": {
                    "type": "warning",
                    "content": "卡片状态已失效，请重新触发回复",
                    "i18n": {
                        "zh_cn": "卡片状态已失效，请重新触发回复",
                        "en_us": "Card state expired, please trigger a new reply",
                    },
                }
            }
        action = str(action_value.get("action", "")).strip()
        total_pages = max(1, (len(state.blocks) + state.page_size - 1) // state.page_size)
        if action == "expand":
            state.expanded = True
            state.page_index = 0
        elif action == "collapse":
            state.expanded = False
            state.page_index = 0
        elif action == "next_page":
            state.expanded = False
            state.page_index = min(total_pages - 1, state.page_index + 1)
        elif action == "prev_page":
            state.expanded = False
            state.page_index = max(0, state.page_index - 1)
        else:
            return None
        self.save_card_state(state)
        card, _fallback = self.render_card_state(state)
        return {
            "toast": {
                "type": "info",
                "content": "已更新卡片",
                "i18n": {
                    "zh_cn": "已更新卡片",
                    "en_us": "Card updated",
                },
            },
            "card": {
                "type": "raw",
                "data": card,
            },
        }

    def _refresh_token(self) -> str:
        """刷新并缓存 tenant access token。"""

        if self._tenant_token and time.time() < self._token_expires_at:
            return self._tenant_token
        response = self._http.post(
            f"{self.api_base}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        )
        data = response.json()
        if data.get("code") != 0:
            print(
                "[feishu] tenant token refresh failed:"
                f" account={self.account_id}"
                f" code={data.get('code')}"
                f" msg={data.get('msg', '')}"
            )
            return ""
        self._tenant_token = data.get("tenant_access_token", "")
        self._token_expires_at = time.time() + data.get("expire", 7200) - 300
        print(f"[feishu] tenant token refreshed: account={self.account_id}")
        return self._tenant_token

    def _build_send_payloads(self, outbound: OutboundMessage) -> list[FeishuSendPayload]:
        """按当前渲染模式生成一个或多个飞书发送载荷。"""

        return self._renderer.render(outbound, mode=self._resolve_render_mode(outbound))

    def _send_payload(
        self,
        token: str,
        outbound: OutboundMessage,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """调用飞书消息发送接口。"""

        receive_id_type = self._resolve_receive_id_type(outbound)
        response = self._http.post(
            f"{self.api_base}/im/v1/messages",
            params={"receive_id_type": receive_id_type},
            headers={"Authorization": f"Bearer {token}"},
            json=payload,
        )
        return response.json()

    def _resolve_receive_id_type(self, outbound: OutboundMessage) -> str:
        """推断当前消息应该按 open_id 还是 chat_id 发送。"""

        configured = str(outbound.metadata.get("receive_id_type", "")).strip()
        if configured:
            return configured
        if outbound.to.startswith("ou_"):
            return "open_id"
        return "chat_id"

    def _send_single_payload(
        self,
        token: str,
        outbound: OutboundMessage,
        rendered: FeishuSendPayload,
        page_index: int,
        total_pages: int,
    ) -> bool:
        """发送单页消息，并处理卡片降级和永久失败判断。"""

        payload = {
            "receive_id": outbound.to,
            **rendered.payload,
        }
        data = self._send_payload(token, outbound, payload)
        success = data.get("code") == 0
        if (
            not success
            and payload["msg_type"] == "interactive"
            and self._should_fallback_to_text(data)
        ):
            print(
                "[feishu] interactive send fallback to text:"
                f" account={self.account_id}"
                f" to={outbound.to}"
                f" page={page_index}/{total_pages}"
                f" code={data.get('code')}"
                f" msg={data.get('msg', '')}"
            )
            fallback_payload = {
                "receive_id": outbound.to,
                "msg_type": "text",
                "content": json.dumps({"text": rendered.fallback_text}, ensure_ascii=False),
            }
            data = self._send_payload(token, outbound, fallback_payload)
            payload = fallback_payload
            success = data.get("code") == 0
        if success:
            message_id = self._extract_sent_message_id(data)
            if rendered.card_state is not None:
                rendered.card_state.message_id = message_id
                self.save_card_state(rendered.card_state)
            print(
                "[feishu] send ok:"
                f" account={self.account_id}"
                f" to={outbound.to}"
                f" receive_id_type={self._resolve_receive_id_type(outbound)}"
                f" msg_type={payload['msg_type']}"
                f" page={page_index}/{total_pages}"
            )
        else:
            print(
                "[feishu] send failed:"
                f" account={self.account_id}"
                f" to={outbound.to}"
                f" msg_type={payload['msg_type']}"
                f" page={page_index}/{total_pages}"
                f" code={data.get('code')}"
                f" msg={data.get('msg', '')}"
            )
            if self._is_permanent_send_error(data):
                raise PermanentDeliveryError(
                    "permanent Feishu send failure:"
                    f" account={self.account_id}"
                    f" to={outbound.to}"
                    f" receive_id_type={self._resolve_receive_id_type(outbound)}"
                    f" code={data.get('code')}"
                    f" msg={data.get('msg', '')}"
                )
        return success

    def _resolve_render_mode(self, outbound: OutboundMessage) -> str:
        """结合账号配置和消息元数据决定文本/卡片发送模式。"""

        override = outbound.metadata.get("feishu_render_mode", "")
        if override:
            return self._normalize_render_mode(override)
        return self.render_mode

    def _send_via_lark_cli(self, outbound: OutboundMessage) -> bool:
        """通过 lark-cli 发送消息，作为 HTTP API 的替代实现。"""

        command = self.lark_cli_command
        if shutil.which(command) is None:
            print(f"[feishu] lark-cli send failed: command not found: {command}")
            return False
        receive_id_type = self._resolve_receive_id_type(outbound)
        argv = [
            command,
            "im",
            "+messages-send",
            "--as",
            self.lark_cli_identity,
        ]
        if receive_id_type == "open_id":
            argv.extend(["--user-id", outbound.to])
        else:
            argv.extend(["--chat-id", outbound.to])
        if self._resolve_render_mode(outbound) == "text":
            argv.extend(["--text", outbound.text])
        else:
            argv.extend(["--markdown", outbound.text])
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            print(
                "[feishu] lark-cli send timeout:"
                f" account={self.account_id}"
                f" to={outbound.to}"
            )
            return False
        if result.returncode == 0:
            print(
                "[feishu] lark-cli send ok:"
                f" account={self.account_id}"
                f" to={outbound.to}"
                f" receive_id_type={receive_id_type}"
            )
            return True
        stderr = " ".join(result.stderr.split())[:500]
        stdout = " ".join(result.stdout.split())[:500]
        print(
            "[feishu] lark-cli send failed:"
            f" account={self.account_id}"
            f" to={outbound.to}"
            f" receive_id_type={receive_id_type}"
            f" exit={result.returncode}"
            f" stderr={stderr}"
            f" stdout={stdout}"
        )
        if "invalid ids" in stderr.lower() or "not a valid" in stderr.lower():
            raise PermanentDeliveryError(
                "permanent Feishu lark-cli send failure:"
                f" account={self.account_id}"
                f" to={outbound.to}"
                f" receive_id_type={receive_id_type}"
                f" stderr={stderr}"
            )
        return False

    def _normalize_send_mode(self, value: object) -> str:
        """规范化输入值。"""
        mode = str(value or "api").strip().lower().replace("-", "_")
        if mode in {"api", "lark_cli"}:
            return mode
        return "api"

    def _normalize_render_mode(self, value: object) -> str:
        """规范化输入值。"""
        mode = str(value or "auto").strip().lower()
        if mode in {"auto", "text", "interactive"}:
            return mode
        return "auto"

    def _should_fallback_to_text(self, data: dict[str, Any]) -> bool:
        """判断是否满足条件。"""
        return data.get("code") in self._CARD_FALLBACK_ERROR_CODES

    def _is_permanent_send_error(self, data: dict[str, Any]) -> bool:
        """判断输入是否满足条件。"""
        if data.get("code") in self._PERMANENT_SEND_ERROR_CODES:
            return True
        msg = str(data.get("msg", "")).lower()
        return "not a valid" in msg and "invalid ids" in msg

    def _read_positive_int(self, value: object, *, default: int, minimum: int) -> int:
        """读取并转换配置值。"""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, parsed)

    def _read_bool(self, value: object, *, default: bool) -> bool:
        """读取并转换配置值。"""
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    def _extract_event_type(self, payload: dict[str, Any]) -> str:
        """兼容不同飞书事件结构提取 event_type。"""

        header = payload.get("header", {})
        if isinstance(header, dict) and header.get("event_type"):
            return str(header.get("event_type", ""))
        return str(payload.get("event_type", ""))

    def _extract_sent_message_id(self, payload: dict[str, Any]) -> str:
        """从发送结果中提取 message_id。"""

        data = payload.get("data", {})
        if isinstance(data, dict):
            return str(data.get("message_id", "") or data.get("message", {}).get("message_id", ""))
        return ""

    def _extract_card_action_value(self, payload: dict[str, Any]) -> Any:
        """取出卡片事件中的 action.value。"""

        event = payload.get("event", {})
        if not isinstance(event, dict):
            return None
        action = event.get("action", {})
        if not isinstance(action, dict):
            return None
        return action.get("value")

    def _build_card_action_prompt(self, payload: dict[str, Any], action_value: Any) -> str:
        """把卡片交互上下文拼成 Agent 可理解的文本提示。"""

        event = payload.get("event", {})
        header = payload.get("header", {})
        action = event.get("action", {}) if isinstance(event, dict) else {}
        context = event.get("context", {}) if isinstance(event, dict) else {}
        prompt_payload = {
            "event_type": self._extract_event_type(payload),
            "event_id": (
                str(header.get("event_id", ""))
                if isinstance(header, dict)
                else str(payload.get("event_id", ""))
            ),
            "action": {
                "tag": str(action.get("tag", "")) if isinstance(action, dict) else "",
                "name": str(action.get("name", "")) if isinstance(action, dict) else "",
                "value": action_value,
                "form_value": action.get("form_value", {}) if isinstance(action, dict) else {},
                "input_value": action.get("input_value", "") if isinstance(action, dict) else "",
                "option": action.get("option", "") if isinstance(action, dict) else "",
                "options": action.get("options", []) if isinstance(action, dict) else [],
                "checked": action.get("checked", False) if isinstance(action, dict) else False,
            },
            "context": {
                "open_message_id": str(context.get("open_message_id", ""))
                if isinstance(context, dict)
                else "",
                "open_chat_id": str(context.get("open_chat_id", ""))
                if isinstance(context, dict)
                else "",
            },
        }
        return (
            "FEISHU_CARD_ACTION\n"
            "A user clicked a Feishu card action. Interpret the callback payload and respond "
            "with the next action or user-facing result.\n"
            f"{json.dumps(prompt_payload, ensure_ascii=False, indent=2)}"
        )

    def _bot_mentioned(self, event: dict[str, Any]) -> bool:
        """群聊场景下判断消息是否明确 @ 了机器人。"""

        for mention in event.get("message", {}).get("mentions", []):
            mention_id = mention.get("id", {})
            if isinstance(mention_id, dict) and mention_id.get("open_id") == self.bot_open_id:
                return True
            if isinstance(mention_id, str) and mention_id == self.bot_open_id:
                return True
            if mention.get("key") == self.bot_open_id:
                return True
        return False

    def _parse_content(self, message: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
        """解析文本、图片、文件等飞书消息内容。"""

        message_type = message.get("msg_type", "text")
        raw = message.get("content", "{}")
        try:
            content = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            return "", []

        media: list[dict[str, str]] = []
        if message_type == "text":
            return content.get("text", ""), media
        if message_type == "post":
            texts: list[str] = []
            for locale_content in content.values():
                if not isinstance(locale_content, dict):
                    continue
                title = locale_content.get("title", "")
                if title:
                    texts.append(title)
                for paragraph in locale_content.get("content", []):
                    for node in paragraph:
                        tag = node.get("tag")
                        if tag == "text":
                            texts.append(node.get("text", ""))
                        elif tag == "a":
                            texts.append(node.get("text", "") + " " + node.get("href", ""))
            return "\n".join(texts), media
        if message_type == "image":
            image_key = content.get("image_key", "")
            if image_key:
                media.append({"type": "image", "key": image_key})
            return "[image]", media
        return "", media

    def decode_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """解密飞书加密 webhook 载荷。"""

        if "encrypt" not in payload:
            return payload
        if not self.encrypt_key:
            raise RuntimeError("Feishu encrypt payload received but encrypt_key is not configured")
        if AES is None:
            raise RuntimeError("Feishu encrypted events require pycryptodome")
        encrypted = payload.get("encrypt", "")
        if not isinstance(encrypted, str) or not encrypted:
            raise ValueError("invalid encrypt payload")
        decrypted = self._decrypt_string(encrypted)
        body = json.loads(decrypted)
        if not isinstance(body, dict):
            raise ValueError("decrypted payload must be object")
        return body

    def _decrypt_string(self, encrypted: str) -> str:
        """按飞书加密协议解出原始 JSON 字符串。"""

        assert AES is not None
        raw = base64.b64decode(encrypted)
        iv = raw[: AES.block_size]
        cipher = AES.new(
            hashlib.sha256(self.encrypt_key.encode("utf-8")).digest(),
            AES.MODE_CBC,
            iv,
        )
        decrypted = cipher.decrypt(raw[AES.block_size :])
        padding = decrypted[-1]
        return decrypted[:-padding].decode("utf-8")
