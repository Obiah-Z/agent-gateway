from __future__ import annotations

import json
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


BACKOFF_SECONDS = [5, 25, 120, 600]
DELIVERY_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass(slots=True)
class QueuedDelivery:
    id: str
    channel: str
    to: str
    text: str
    retry_count: int = 0
    last_error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    enqueued_at: float = field(default_factory=time.time)
    next_retry_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "channel": self.channel,
            "to": self.to,
            "text": self.text,
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "metadata": self.metadata,
            "enqueued_at": self.enqueued_at,
            "next_retry_at": self.next_retry_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueuedDelivery":
        return cls(
            id=data["id"],
            channel=data["channel"],
            to=data["to"],
            text=data["text"],
            retry_count=data.get("retry_count", 0),
            last_error=data.get("last_error"),
            metadata=data.get("metadata", {}),
            enqueued_at=data.get("enqueued_at", 0.0),
            next_retry_at=data.get("next_retry_at", 0.0),
        )


def compute_backoff_seconds(retry_count: int) -> int:
    if retry_count <= 0:
        return 0
    base = BACKOFF_SECONDS[min(retry_count - 1, len(BACKOFF_SECONDS) - 1)]
    jitter = random.randint(-base // 5, base // 5)
    return max(0, base + jitter)


class DeliveryQueue:
    def __init__(self, queue_dir: Path) -> None:
        self.queue_dir = queue_dir
        self.failed_dir = queue_dir / "failed"
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def enqueue(self, channel: str, to: str, text: str, metadata: dict[str, Any] | None = None) -> str:
        delivery_id = uuid.uuid4().hex[:12]
        entry = QueuedDelivery(
            id=delivery_id,
            channel=channel,
            to=to,
            text=text,
            metadata=metadata or {},
        )
        self._write_entry(entry)
        return delivery_id

    def pending_entries(self) -> list[QueuedDelivery]:
        return self._entries_from_dir(self.queue_dir)

    def failed_entries(self) -> list[QueuedDelivery]:
        return self._entries_from_dir(self.failed_dir)

    def get_pending(self, delivery_id: str) -> QueuedDelivery | None:
        return self._read_entry(self._entry_path(self.queue_dir, delivery_id))

    def get_failed(self, delivery_id: str) -> QueuedDelivery | None:
        return self._read_entry(self._entry_path(self.failed_dir, delivery_id))

    def ack(self, delivery_id: str) -> None:
        path = self._entry_path(self.queue_dir, delivery_id)
        if path.exists():
            path.unlink()

    def fail(self, entry: QueuedDelivery, error: str) -> None:
        entry.retry_count += 1
        entry.last_error = error
        entry.next_retry_at = time.time() + compute_backoff_seconds(entry.retry_count)
        self._write_entry(entry)

    def move_to_failed(self, entry: QueuedDelivery) -> None:
        src = self._entry_path(self.queue_dir, entry.id)
        dst = self._entry_path(self.failed_dir, entry.id)
        if src.exists():
            src.replace(dst)

    def retry_now(self, delivery_id: str) -> bool:
        pending = self.get_pending(delivery_id)
        if pending is not None:
            pending.next_retry_at = 0.0
            self._write_entry(pending)
            return True

        failed = self.get_failed(delivery_id)
        if failed is None:
            return False
        failed.retry_count = 0
        failed.next_retry_at = 0.0
        src = self._entry_path(self.failed_dir, delivery_id)
        dst = self._entry_path(self.queue_dir, delivery_id)
        self._write_entry(failed)
        failed_pending = self._entry_path(self.queue_dir, delivery_id)
        if failed_pending.exists() and src.exists():
            src.unlink()
        elif src.exists():
            src.replace(dst)
        return True

    def discard(self, delivery_id: str, *, state: str = "any") -> bool:
        if state not in {"any", "pending", "failed"}:
            raise ValueError("state must be one of: any, pending, failed")
        removed = False
        paths = []
        if state in {"any", "pending"}:
            paths.append(self._entry_path(self.queue_dir, delivery_id))
        if state in {"any", "failed"}:
            paths.append(self._entry_path(self.failed_dir, delivery_id))
        for path in paths:
            if path.exists():
                path.unlink()
                removed = True
        return removed

    def _write_entry(self, entry: QueuedDelivery) -> None:
        final_path = self._entry_path(self.queue_dir, entry.id)
        tmp_path = self.queue_dir / f".tmp.{entry.id}.json"
        payload = json.dumps(entry.to_dict(), ensure_ascii=False, indent=2)
        with self._lock, tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
        tmp_path.replace(final_path)

    @staticmethod
    def _read_entry(path: Path) -> QueuedDelivery | None:
        if not path.exists():
            return None
        try:
            return QueuedDelivery.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError):
            return None

    @staticmethod
    def _entry_path(root: Path, delivery_id: str) -> Path:
        if not DELIVERY_ID_PATTERN.fullmatch(delivery_id):
            raise ValueError("invalid delivery id")
        return root / f"{delivery_id}.json"

    @staticmethod
    def _entries_from_dir(root: Path) -> list[QueuedDelivery]:
        entries: list[QueuedDelivery] = []
        for path in sorted(root.glob("*.json")):
            loaded = DeliveryQueue._read_entry(path)
            if loaded is not None:
                entries.append(loaded)
        return entries


class DeliveryRunner:
    def __init__(
        self,
        queue: DeliveryQueue,
        deliver_fn: Callable[[QueuedDelivery], bool],
        *,
        max_retries: int = 5,
        on_success: Callable[[QueuedDelivery], None] | None = None,
    ) -> None:
        self.queue = queue
        self.deliver_fn = deliver_fn
        self.max_retries = max_retries
        self.on_success = on_success

    def run_once(self) -> None:
        now = time.time()
        for entry in self.queue.pending_entries():
            if entry.next_retry_at and entry.next_retry_at > now:
                continue
            try:
                success = self.deliver_fn(entry)
            except Exception as exc:
                success = False
                error = str(exc)
            else:
                error = ""

            if success:
                self.queue.ack(entry.id)
                if self.on_success is not None:
                    try:
                        self.on_success(entry)
                    except Exception:
                        pass
                continue
            if entry.retry_count + 1 >= self.max_retries:
                entry.last_error = error or "delivery failed"
                self.queue.move_to_failed(entry)
                continue
            self.queue.fail(entry, error or "delivery failed")
