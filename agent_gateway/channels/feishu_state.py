from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class FeishuCardState:
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
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cards_dir = self.state_dir / "cards"
        self.cards_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def load(self, card_id: str) -> FeishuCardState | None:
        path = self.cards_dir / f"{card_id}.json"
        if not path.exists():
            return None
        try:
            return FeishuCardState.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return None

    def save(self, state: FeishuCardState) -> None:
        state.updated_at = time.time()
        path = self.cards_dir / f"{state.card_id}.json"
        tmp = self.cards_dir / f".tmp.{state.card_id}.json"
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        with self._lock:
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)
