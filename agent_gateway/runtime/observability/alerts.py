"""Local alert rules, state tracking, and append-only history."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any


@dataclass(slots=True)
class AlertRule:
    id: str
    title: str
    severity: str
    description: str
    threshold: float
    sustain_intervals: int = 1
    cooldown_seconds: float = 900.0


@dataclass(slots=True)
class AlertState:
    rule_id: str
    status: str = "inactive"
    active_since: float = 0.0
    last_triggered_at: float = 0.0
    last_recovered_at: float = 0.0
    last_evaluated_at: float = 0.0
    current_value: float = 0.0
    threshold: float = 0.0
    consecutive_hits: int = 0
    consecutive_misses: int = 0
    last_notified_at: float = 0.0
    last_notification_error: str = ""
    last_message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "status": self.status,
            "active_since": self.active_since or None,
            "active_since_time": _isoformat(self.active_since),
            "last_triggered_at": self.last_triggered_at or None,
            "last_triggered_time": _isoformat(self.last_triggered_at),
            "last_recovered_at": self.last_recovered_at or None,
            "last_recovered_time": _isoformat(self.last_recovered_at),
            "last_evaluated_at": self.last_evaluated_at or None,
            "last_evaluated_time": _isoformat(self.last_evaluated_at),
            "current_value": self.current_value,
            "threshold": self.threshold,
            "consecutive_hits": self.consecutive_hits,
            "consecutive_misses": self.consecutive_misses,
            "last_notified_at": self.last_notified_at or None,
            "last_notified_time": _isoformat(self.last_notified_at),
            "last_notification_error": self.last_notification_error,
            "last_message": self.last_message,
            "metadata": self.metadata,
        }


class AlertStore:
    def __init__(self, path: Path, *, retention_days: int = 14) -> None:
        self.root_dir = path if path.suffix == "" else path.parent
        self.root_dir.mkdir(parents=True, exist_ok=True)
        self.retention_days = max(1, int(retention_days))
        self._lock = threading.Lock()

    def append(
        self,
        *,
        rule: AlertRule,
        state: AlertState,
        event: str,
        message: str,
        value: float,
        metadata: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> dict[str, Any]:
        ts = float(timestamp if timestamp is not None else time.time())
        row = {
            "timestamp": ts,
            "time": _isoformat(ts),
            "event": event,
            "rule": {
                "id": rule.id,
                "title": rule.title,
                "severity": rule.severity,
                "description": rule.description,
                "threshold": rule.threshold,
                "sustain_intervals": rule.sustain_intervals,
                "cooldown_seconds": rule.cooldown_seconds,
            },
            "state": state.to_dict(),
            "message": message,
            "value": value,
            "metadata": _sanitize_value(metadata or {}),
        }
        path = self._path_for_timestamp(ts)
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True)
        with self._lock, path.open("a", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        self.cleanup()
        return row

    def tail(self, *, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        rows: list[dict[str, Any]] = []
        remaining = safe_limit
        for path in reversed(self._alert_files()):
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
            if len(rows) >= safe_limit:
                break
            remaining = safe_limit - len(rows)
        rows.sort(key=lambda row: float(row.get("timestamp", 0.0) or 0.0))
        return rows[-safe_limit:]

    def cleanup(self, *, now: date | None = None) -> None:
        cutoff = (now or date.today()) - timedelta(days=self.retention_days - 1)
        for path in self._alert_files():
            suffix = path.stem.removeprefix("alerts-")
            try:
                file_date = date.fromisoformat(suffix)
            except ValueError:
                continue
            if file_date < cutoff:
                try:
                    path.unlink()
                except OSError:
                    pass

    def _alert_files(self) -> list[Path]:
        return sorted(self.root_dir.glob("alerts-*.jsonl"))

    def _path_for_timestamp(self, timestamp: float) -> Path:
        alert_date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        return self.root_dir / f"alerts-{alert_date.isoformat()}.jsonl"


def _isoformat(timestamp: float | None) -> str | None:
    if not timestamp:
        return None
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value[:50]]
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    return str(value)
