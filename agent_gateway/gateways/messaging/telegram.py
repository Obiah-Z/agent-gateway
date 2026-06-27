from __future__ import annotations

import time
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore[assignment]

from agent_gateway.gateways.messaging.base import Channel, ChannelAccount
from agent_gateway.runtime.domain.models import InboundMessage, OutboundMessage


class TelegramChannel(Channel):
    """Telegram 消息通道实现。"""
    name = "telegram"
    max_message_length = 4096

    def __init__(
        self,
        account: ChannelAccount,
        state_dir: Path,
        *,
        read_backend: Any = None,
        write_backend: Any = None,
    ) -> None:
        """初始化实例。"""
        if httpx is None:
            raise RuntimeError("TelegramChannel requires httpx")
        self.account = account
        self.account_id = account.account_id
        self.base_url = f"https://api.telegram.org/bot{account.token}"
        self.allowed_chats = {
            item.strip()
            for item in str(account.config.get("allowed_chats", "")).split(",")
            if item.strip()
        }
        self._http = httpx.Client(timeout=35.0)
        self._offset_path = state_dir / "telegram" / f"offset-{self.account_id}.txt"
        self.read_backend = read_backend
        self.write_backend = write_backend
        self._offset = self._load_offset(self._offset_path)
        self._seen: set[int] = set()
        self._media_groups: dict[str, dict[str, Any]] = {}
        self._text_buffer: dict[tuple[str, str], dict[str, Any]] = {}

    def receive(self) -> InboundMessage | None:
        """接收一条入站消息。"""
        messages = self.poll()
        return messages[0] if messages else None

    def receive_batch(self) -> list[InboundMessage]:
        """批量接收入站消息。"""
        return self.poll()

    def poll(self) -> list[InboundMessage]:
        """轮询远端消息并转换为入站消息。"""
        result = self._api(
            "getUpdates",
            offset=self._offset,
            timeout=30,
            allowed_updates=["message"],
        )
        if not isinstance(result, list):
            return self._flush_all()

        for update in result:
            update_id = update.get("update_id", 0)
            if update_id >= self._offset:
                self._offset = update_id + 1
                self._save_offset(self._offset_path, self._offset)
            if update_id in self._seen:
                continue
            self._seen.add(update_id)
            if len(self._seen) > 5000:
                self._seen.clear()

            message = update.get("message")
            if not message:
                continue
            if message.get("media_group_id"):
                self._buffer_media(message, update)
                continue
            inbound = self._parse(message, update)
            if inbound is None:
                continue
            if self.allowed_chats and inbound.peer_id not in self.allowed_chats:
                continue
            self._buffer_text(inbound)

        return self._flush_all()

    def send(self, outbound: OutboundMessage) -> bool:
        """发送一条出站消息。"""
        chat_id, thread_id = outbound.to, None
        if ":topic:" in outbound.to:
            parts = outbound.to.split(":topic:")
            chat_id = parts[0]
            thread_id = int(parts[1]) if len(parts) > 1 else None
        ok = True
        for chunk in self._chunk(outbound.text):
            if not self._api(
                "sendMessage",
                chat_id=chat_id,
                text=chunk,
                message_thread_id=thread_id,
            ):
                ok = False
        return ok

    def send_typing(self, chat_id: str) -> None:
        """发送输入中状态提示。"""
        self._api("sendChatAction", chat_id=chat_id, action="typing")

    def close(self) -> None:
        """关闭通道并释放资源。"""
        self._http.close()

    def _api(self, method: str, **params: Any) -> Any:
        """调用远端 API。"""
        payload = {key: value for key, value in params.items() if value is not None}
        response = self._http.post(f"{self.base_url}/{method}", json=payload)
        data = response.json()
        if not data.get("ok"):
            return {}
        return data.get("result", {})

    def _buffer_media(self, message: dict[str, Any], update: dict[str, Any]) -> None:
        """缓存待发送内容。"""
        media_group_id = message["media_group_id"]
        if media_group_id not in self._media_groups:
            self._media_groups[media_group_id] = {"ts": time.monotonic(), "entries": []}
        self._media_groups[media_group_id]["entries"].append((message, update))

    def _flush_all(self) -> list[InboundMessage]:
        """刷新缓存内容。"""
        ready = self._flush_media()
        ready.extend(self._flush_text())
        return ready

    def _flush_media(self) -> list[InboundMessage]:
        """刷新缓存内容。"""
        now = time.monotonic()
        ready: list[InboundMessage] = []
        expired = [
            key for key, row in self._media_groups.items() if (now - row["ts"]) >= 0.5
        ]
        for media_group_id in expired:
            entries = self._media_groups.pop(media_group_id)["entries"]
            captions: list[str] = []
            media_items: list[dict[str, str]] = []
            for message, _ in entries:
                if message.get("caption"):
                    captions.append(message["caption"])
                for media_type in ("photo", "video", "document", "audio"):
                    if media_type not in message:
                        continue
                    raw_media = message[media_type]
                    if isinstance(raw_media, list) and raw_media:
                        file_id = raw_media[-1].get("file_id", "")
                    elif isinstance(raw_media, dict):
                        file_id = raw_media.get("file_id", "")
                    else:
                        file_id = ""
                    media_items.append({"type": media_type, "file_id": file_id})
            inbound = self._parse(entries[0][0], entries[0][1])
            if inbound is None:
                continue
            inbound.text = "\n".join(captions) if captions else "[media group]"
            inbound.media = media_items
            if not self.allowed_chats or inbound.peer_id in self.allowed_chats:
                ready.append(inbound)
        return ready

    def _buffer_text(self, inbound: InboundMessage) -> None:
        """缓存待发送内容。"""
        key = (inbound.peer_id, inbound.sender_id)
        now = time.monotonic()
        if key in self._text_buffer:
            self._text_buffer[key]["text"] += "\n" + inbound.text
            self._text_buffer[key]["ts"] = now
        else:
            self._text_buffer[key] = {"text": inbound.text, "msg": inbound, "ts": now}

    def _flush_text(self) -> list[InboundMessage]:
        """刷新缓存内容。"""
        now = time.monotonic()
        ready: list[InboundMessage] = []
        expired = [
            key for key, row in self._text_buffer.items() if (now - row["ts"]) >= 1.0
        ]
        for key in expired:
            buffered = self._text_buffer.pop(key)
            buffered["msg"].text = buffered["text"]
            ready.append(buffered["msg"])
        return ready

    def _parse(
        self,
        message: dict[str, Any],
        raw_update: dict[str, Any],
    ) -> InboundMessage | None:
        """解析输入内容。"""
        chat = message.get("chat", {})
        chat_type = chat.get("type", "")
        chat_id = str(chat.get("id", ""))
        user_id = str(message.get("from", {}).get("id", ""))
        text = message.get("text", "") or message.get("caption", "")
        if not text:
            return None

        thread_id = message.get("message_thread_id")
        is_forum = chat.get("is_forum", False)
        is_group = chat_type in ("group", "supergroup")

        if chat_type == "private":
            peer_id = user_id
        elif is_group and is_forum and thread_id is not None:
            peer_id = f"{chat_id}:topic:{thread_id}"
        else:
            peer_id = chat_id

        return InboundMessage(
            text=text,
            sender_id=user_id,
            channel=self.name,
            account_id=self.account_id,
            peer_id=peer_id,
            is_group=is_group,
            raw=raw_update,
        )

    def _chunk(self, text: str) -> list[str]:
        """按长度切分文本。"""
        if len(text) <= self.max_message_length:
            return [text]
        chunks: list[str] = []
        current = text
        while current:
            if len(current) <= self.max_message_length:
                chunks.append(current)
                break
            cut = current.rfind("\n", 0, self.max_message_length)
            if cut <= 0:
                cut = self.max_message_length
            chunks.append(current[:cut])
            current = current[cut:].lstrip("\n")
        return chunks

    def _save_offset(self, path: Path, offset: int) -> None:
        """保存运行状态。"""
        if self.write_backend is not None:
            try:
                write_offset = getattr(self.write_backend, "write_channel_offset", None)
                if write_offset is not None:
                    write_offset(self.name, self.account_id, offset)
                else:
                    self.write_backend.upsert(
                        "channel_offsets",
                        {
                            "key": f"{self.name}\x1f{self.account_id}",
                            "channel": self.name,
                            "account_id": self.account_id,
                            "offset_value": int(offset),
                            "updated_at": time.time(),
                            "metadata": {},
                        },
                    )
            except Exception:
                pass
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(offset), encoding="utf-8")

    def _load_offset(self, path: Path) -> int:
        """加载运行状态。"""
        if self.read_backend is not None:
            try:
                read_offset = getattr(self.read_backend, "read_channel_offset", None)
                if read_offset is not None:
                    value = read_offset(self.name, self.account_id)
                    if value is not None:
                        return int(value)
                rows = self.read_backend.list(
                    "channel_offsets",
                    limit=1,
                    filters={"channel": self.name, "account_id": self.account_id},
                )
                if rows:
                    return int(rows[0].get("offset_value", 0) or 0)
            except Exception:
                pass
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except Exception:
            return 0
