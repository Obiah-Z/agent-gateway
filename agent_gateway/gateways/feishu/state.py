from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class FeishuCardState:
    """记录飞书有状态卡片的分页和内容状态。"""
    card_id: str
    owner_channel: str
    owner_account_id: str
    peer_id: str
    message_id: str
    title: str
    summary: str
    template: str
    card_link: str
    blocks: list[str]
    structured_blocks: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    page_size: int
    page_index: int = 0
    expanded: bool = False
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典。"""
        return {
            "card_id": self.card_id,
            "owner_channel": self.owner_channel,
            "owner_account_id": self.owner_account_id,
            "peer_id": self.peer_id,
            "message_id": self.message_id,
            "title": self.title,
            "summary": self.summary,
            "template": self.template,
            "card_link": self.card_link,
            "blocks": self.blocks,
            "structured_blocks": self.structured_blocks,
            "actions": self.actions,
            "page_size": self.page_size,
            "page_index": self.page_index,
            "expanded": self.expanded,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeishuCardState":
        """从字典恢复对象。"""
        return cls(
            card_id=str(data.get("card_id", "")),
            owner_channel=str(data.get("owner_channel", "feishu")),
            owner_account_id=str(data.get("owner_account_id", "")),
            peer_id=str(data.get("peer_id", "")),
            message_id=str(data.get("message_id", "")),
            title=str(data.get("title", "")),
            summary=str(data.get("summary", "")),
            template=str(data.get("template", "blue")),
            card_link=str(data.get("card_link", "")),
            blocks=list(data.get("blocks", [])),
            structured_blocks=list(data.get("structured_blocks", [])),
            actions=list(data.get("actions", [])),
            page_size=max(1, int(data.get("page_size", 4) or 4)),
            page_index=max(0, int(data.get("page_index", 0) or 0)),
            expanded=bool(data.get("expanded", False)),
            updated_at=float(data.get("updated_at", 0.0) or 0.0),
        )


class FeishuCardStateStore:
    """持久化飞书有状态卡片状态。

    PostgreSQL 后端存在时优先读写数据库；本地 JSON 文件始终保留为兜底，
    便于数据库不可用时继续响应卡片分页、展开和收起动作。
    """

    def __init__(
        self,
        state_dir: Path,
        *,
        read_backend: Any = None,
        write_backend: Any = None,
    ) -> None:
        """初始化实例。"""
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cards_dir = self.state_dir / "cards"
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        self.read_backend = read_backend
        self.write_backend = write_backend
        self._lock = threading.Lock()

    def load(self, card_id: str) -> FeishuCardState | None:
        """读取卡片状态，优先从 PostgreSQL 读取。"""

        state = self._load_from_backend(card_id)
        if state is not None:
            return state
        return self._load_from_disk(card_id)

    def _load_from_backend(self, card_id: str) -> FeishuCardState | None:
        """从外部状态仓储读取卡片状态。"""

        if self.read_backend is None:
            return None
        try:
            get_row = getattr(self.read_backend, "get", None)
            if get_row is not None:
                row = get_row("feishu_card_states", card_id)
            else:
                rows = self.read_backend.list(
                    "feishu_card_states",
                    limit=1,
                    filters={"card_id": card_id},
                )
                row = rows[0] if rows else None
        except Exception:
            return None
        if not isinstance(row, dict):
            return None
        return FeishuCardState.from_dict(self._row_to_state_dict(row))

    def _load_from_disk(self, card_id: str) -> FeishuCardState | None:
        """从本地 JSON 文件读取卡片状态。"""

        path = self.cards_dir / f"{card_id}.json"
        if not path.exists():
            return None
        try:
            return FeishuCardState.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return None

    def save(self, state: FeishuCardState) -> None:
        """保存卡片状态，PostgreSQL 优先，本地 JSON 兜底。"""

        state.updated_at = time.time()
        self._save_to_backend(state)
        self._save_to_disk(state)

    def _save_to_backend(self, state: FeishuCardState) -> None:
        """写入卡片状态到外部仓储。"""

        if self.write_backend is None:
            return
        row = self._state_to_row(state)
        try:
            write_state = getattr(self.write_backend, "write_feishu_card_state", None)
            if write_state is not None:
                write_state(row)
            else:
                self.write_backend.upsert("feishu_card_states", row)
        except Exception:
            return

    def _save_to_disk(self, state: FeishuCardState) -> None:
        """写入卡片状态到本地 JSON 文件。"""

        path = self.cards_dir / f"{state.card_id}.json"
        tmp = self.cards_dir / f".tmp.{state.card_id}.json"
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        with self._lock:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)

    @staticmethod
    def _state_to_row(state: FeishuCardState) -> dict[str, Any]:
        """把领域对象转换为 PostgreSQL 行。"""

        return {
            "card_id": state.card_id,
            "owner_channel": state.owner_channel,
            "owner_account_id": state.owner_account_id,
            "peer_id": state.peer_id,
            "message_id": state.message_id,
            "title": state.title,
            "summary": state.summary,
            "template": state.template,
            "card_link": state.card_link,
            "blocks": list(state.blocks),
            "structured_blocks": list(state.structured_blocks),
            "actions": list(state.actions),
            "page_size": state.page_size,
            "page_index": state.page_index,
            "expanded": state.expanded,
            "updated_at": state.updated_at,
            "metadata": state.to_dict(),
        }

    @staticmethod
    def _row_to_state_dict(row: dict[str, Any]) -> dict[str, Any]:
        """把 PostgreSQL 行转换为 FeishuCardState 可识别的字典。"""

        metadata = row.get("metadata", {})
        if isinstance(metadata, dict):
            payload = dict(metadata)
        else:
            payload = {}
        for key in (
            "card_id",
            "owner_channel",
            "owner_account_id",
            "peer_id",
            "message_id",
            "title",
            "summary",
            "template",
            "card_link",
            "blocks",
            "structured_blocks",
            "actions",
            "page_size",
            "page_index",
            "expanded",
            "updated_at",
        ):
            if key in row:
                payload[key] = row[key]
        return payload
