from __future__ import annotations

import asyncio
import time
from typing import Any

from agent_gateway.runtime.execution.autonomy import AutonomyRuntime
from agent_gateway.runtime.execution.lanes import CommandQueue
from agent_gateway.runtime.execution.resilience import ProfileManager
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.runtime.observability.events import RuntimeEventStore
from agent_gateway.runtime.observability.metrics import MetricsStore


class MetricsRuntime:
    """Periodically captures lightweight runtime counters into MetricsStore."""

    def __init__(
        self,
        *,
        metrics_store: MetricsStore,
        delivery_queue: DeliveryQueue,
        command_queue: CommandQueue,
        profiles: ProfileManager,
        autonomy: AutonomyRuntime | None = None,
        event_store: RuntimeEventStore | None = None,
        interval_seconds: float = 60.0,
    ) -> None:
        self.metrics_store = metrics_store
        self.delivery_queue = delivery_queue
        self.command_queue = command_queue
        self.profiles = profiles
        self.autonomy = autonomy
        self.event_store = event_store
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.started_at = time.time()
        self.last_error = ""
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="metrics-runtime")

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def snapshot_once(self) -> dict[str, Any]:
        """Collect and persist one snapshot. Exposed for tests and manual checks."""

        try:
            row = self.metrics_store.record(
                runtime=self._collect_runtime_metrics(),
                delivery=self._collect_delivery_metrics(),
                lanes=self._collect_lane_metrics(),
                cron=self._collect_cron_metrics(),
                events=self._collect_event_metrics(),
                profiles=self._collect_profile_metrics(),
                metadata={"collector": "metrics-runtime"},
            )
            self.last_error = ""
            return row
        except Exception as exc:
            self.last_error = str(exc)
            raise

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await asyncio.to_thread(self.snapshot_once)
            except Exception:
                pass
            await asyncio.sleep(self.interval_seconds)

    def _collect_runtime_metrics(self) -> dict[str, Any]:
        return {
            "uptime_seconds": round(max(0.0, time.time() - self.started_at), 3),
            "collector_last_error": self.last_error,
        }

    def _collect_delivery_metrics(self) -> dict[str, Any]:
        pending = self.delivery_queue.pending_entries()
        failed = self.delivery_queue.failed_entries()
        now = time.time()
        return {
            "pending": len(pending),
            "failed": len(failed),
            "retry_ready": sum(1 for entry in pending if not entry.next_retry_at or entry.next_retry_at <= now),
            "oldest_pending_age_seconds": self._oldest_age_seconds(
                [entry.enqueued_at for entry in pending],
                now=now,
            ),
            "oldest_failed_age_seconds": self._oldest_age_seconds(
                [entry.enqueued_at for entry in failed],
                now=now,
            ),
        }

    def _collect_lane_metrics(self) -> dict[str, Any]:
        lane_rows = self.command_queue.stats()
        active = 0
        queued = 0
        max_queue_depth = 0
        for row in lane_rows.values():
            active += int(row.get("active", 0) or 0)
            depth = int(row.get("queue_depth", 0) or 0)
            queued += depth
            max_queue_depth = max(max_queue_depth, depth)
        return {
            "count": len(lane_rows),
            "active": active,
            "queued": queued,
            "max_queue_depth": max_queue_depth,
        }

    def _collect_cron_metrics(self) -> dict[str, Any]:
        if self.autonomy is None:
            return {"configured": False}
        jobs = self.autonomy.cron.list_jobs()
        return {
            "configured": True,
            "count": len(jobs),
            "enabled": sum(1 for job in jobs if job.get("enabled")),
            "errored": sum(1 for job in jobs if int(job.get("errors", 0) or 0) > 0),
        }

    def _collect_event_metrics(self) -> dict[str, Any]:
        if self.event_store is None:
            return {"configured": False}
        rows = self.event_store.tail(limit=500)
        now = time.time()
        recent = [row for row in rows if now - float(row.get("timestamp", 0.0) or 0.0) <= 300]
        return {
            "configured": True,
            "total_sampled": len(rows),
            "errors_5m": sum(1 for row in recent if row.get("error") or row.get("status") in {"error", "failed", "critical"}),
            "rejected_5m": sum(1 for row in recent if row.get("status") == "rejected"),
            "delivery_failed_5m": sum(1 for row in recent if row.get("type") == "delivery.failed"),
            "tool_failed_5m": sum(1 for row in recent if row.get("type") == "tool.call.failed"),
            "cron_failed_5m": sum(1 for row in recent if row.get("type") == "cron.failed"),
        }

    def _collect_profile_metrics(self) -> dict[str, Any]:
        rows = self.profiles.snapshot()
        return {
            "count": len(rows),
            "available": sum(
                1
                for row in rows
                if row.get("has_key") and float(row.get("cooldown_remaining", 0.0) or 0.0) <= 0.0
            ),
            "cooling_down": sum(
                1 for row in rows if float(row.get("cooldown_remaining", 0.0) or 0.0) > 0.0
            ),
        }

    @staticmethod
    def _oldest_age_seconds(values: list[float], *, now: float) -> float | None:
        if not values:
            return None
        return round(max(0.0, now - min(values)), 3)
