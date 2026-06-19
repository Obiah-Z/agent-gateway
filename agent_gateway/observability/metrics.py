"""Local JSONL metrics snapshot store.

Metrics complement runtime events: events explain what happened, while metrics
capture the current shape of the runtime so trends can be rendered later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any


@dataclass(slots=True)
class MetricSnapshot:
    timestamp: float = field(default_factory=time.time)
    runtime: dict[str, Any] = field(default_factory=dict)
    delivery: dict[str, Any] = field(default_factory=dict)
    lanes: dict[str, Any] = field(default_factory=dict)
    cron: dict[str, Any] = field(default_factory=dict)
    events: dict[str, Any] = field(default_factory=dict)
    profiles: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "time": datetime.fromtimestamp(self.timestamp, tz=timezone.utc).isoformat(),
            "runtime": self.runtime,
            "delivery": self.delivery,
            "lanes": self.lanes,
            "cron": self.cron,
            "events": self.events,
            "profiles": self.profiles,
            "metadata": self.metadata,
        }


class MetricsStore:
    def __init__(
        self,
        path: Path,
        *,
        max_line_bytes: int = 64_000,
        retention_days: int = 14,
    ) -> None:
        self.root_dir = path if path.suffix == "" else path.parent
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.path = self._path_for_date(date.today())
        self.max_line_bytes = max(1024, int(max_line_bytes))
        self.retention_days = max(1, int(retention_days))
        self._lock = threading.Lock()

    def record(
        self,
        *,
        runtime: dict[str, Any] | None = None,
        delivery: dict[str, Any] | None = None,
        lanes: dict[str, Any] | None = None,
        cron: dict[str, Any] | None = None,
        events: dict[str, Any] | None = None,
        profiles: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        snapshot = MetricSnapshot(
            timestamp=float(timestamp if timestamp is not None else time.time()),
            runtime=self._sanitize_mapping(runtime or {}),
            delivery=self._sanitize_mapping(delivery or {}),
            lanes=self._sanitize_mapping(lanes or {}),
            cron=self._sanitize_mapping(cron or {}),
            events=self._sanitize_mapping(events or {}),
            profiles=self._sanitize_mapping(profiles or {}),
            metadata=self._sanitize_mapping(metadata or {}),
        )
        row = snapshot.to_dict()
        self._append(row)
        return row

    def latest(self) -> dict[str, Any] | None:
        rows = self.tail(limit=1)
        return rows[-1] if rows else None

    def tail(self, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        return self._read_tail(safe_limit)

    def cleanup(self, *, now: date | None = None) -> None:
        cutoff = (now or date.today()) - timedelta(days=self.retention_days - 1)
        for path in self._metric_files():
            suffix = path.stem.removeprefix("metrics-")
            try:
                file_date = date.fromisoformat(suffix)
            except ValueError:
                continue
            if file_date < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    def _append(self, row: dict[str, Any]) -> None:
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        encoded = payload.encode("utf-8")
        if len(encoded) > self.max_line_bytes:
            row = dict(row)
            row["metadata"] = {"truncated": True}
            payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        path = self._path_for_timestamp(float(row.get("timestamp", time.time()) or time.time()))
        with self._lock, path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        self.cleanup()

    def _read_tail(self, limit: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        remaining = max(1, int(limit))
        for path in reversed(self._metric_files()):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    lines = handle.readlines()
            except OSError:
                continue
            for line in lines[-remaining:]:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
            if len(rows) >= limit:
                break
        rows.sort(key=lambda row: float(row.get("timestamp", 0.0) or 0.0))
        return rows[-limit:]

    def _metric_files(self) -> list[Path]:
        return sorted(self.root_dir.glob("metrics-*.jsonl"))

    def _path_for_timestamp(self, timestamp: float) -> Path:
        metric_date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        return self._path_for_date(metric_date)

    def _path_for_date(self, metric_date: date) -> Path:
        return self.root_dir / f"metrics-{metric_date.isoformat()}.jsonl"

    @staticmethod
    def _sanitize_mapping(payload: dict[str, Any]) -> dict[str, Any]:
        safe: dict[str, Any] = {}
        for key, value in payload.items():
            safe[str(key)] = MetricsStore._sanitize_value(value)
        return safe

    @staticmethod
    def _sanitize_value(value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [MetricsStore._sanitize_value(item) for item in value[:50]]
        if isinstance(value, dict):
            return MetricsStore._sanitize_mapping(value)
        return str(value)
