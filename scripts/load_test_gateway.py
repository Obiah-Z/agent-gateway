#!/usr/bin/env python3
"""AI Agent Gateway load test helper.

Phase 20.8 starts with deterministic local scenarios and then adds explicitly
enabled real-external scenarios. Real model or platform calls must be opt-in so
load tests do not accidentally consume API quota or trigger platform limits.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
import json
import platform
from pathlib import Path
import statistics
import subprocess
import sys
import time
import threading
import uuid
from typing import Any

from agent_gateway.gateways.messaging.base import Channel, ChannelAccount
from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.runtime.domain.models import InboundMessage, OutboundMessage
from agent_gateway.runtime.execution.delivery_runtime import DeliveryRuntime
from agent_gateway.runtime.infra.rabbitmq import RabbitMQDeliveryBroker, RabbitMQInboundTaskBroker
from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.state.queue import DeliveryQueue
from agent_gateway.runtime.tasks import (
    LaneOwnerToken,
    LocalTaskQueue,
    LocalTaskStore,
    RedisLaneCoordinator,
    TaskWorkerRuntime,
)
from agent_gateway.runtime.tasks.worker import RetryableTaskError


DEFAULT_REPORT_DIR = Path("workspace/reports/load-tests")
SUPPORTED_SCENARIOS = {
    "delivery-local",
    "delivery-rabbitmq",
    "feishu-send-real",
    "inbound-rabbitmq",
    "mock-local",
    "model-real",
}


@dataclass(slots=True)
class RequestSample:
    """Single synthetic request timing sample."""

    request_id: str
    ok: bool
    error: str
    e2e_ms: float
    agent_turn_ms: float
    delivery_ms: float


class LoadTestChannel(Channel):
    """Mock channel used by delivery-local load tests."""

    name = "load"

    def __init__(self, *, delay_ms: float = 0.0) -> None:
        self.delay_ms = max(0.0, delay_ms)
        self.sent: list[OutboundMessage] = []
        self._lock = threading.Lock()

    def receive(self) -> InboundMessage | None:
        return None

    def send(self, outbound: OutboundMessage) -> bool:
        if self.delay_ms:
            time.sleep(self.delay_ms / 1000)
        with self._lock:
            self.sent.append(outbound)
        return True


class InMemoryDeliveryBackend:
    """Minimal delivery_entries backend for broker load tests."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    def list(self, table: str, *, limit: int = 50, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if table != "delivery_entries":
            return []
        state = str((filters or {}).get("state", ""))
        rows = [row for row in self.rows.values() if not state or row.get("state") == state]
        rows.sort(key=lambda row: float(row.get("enqueued_at", 0.0) or 0.0))
        return [dict(row) for row in rows[:limit]]

    def get(self, table: str, key: str) -> dict[str, Any] | None:
        if table != "delivery_entries":
            return None
        row = self.rows.get(key)
        return dict(row) if row is not None else None

    def upsert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        if table != "delivery_entries":
            return row
        payload = dict(row)
        self.rows[str(payload["id"])] = payload
        return payload

    def delete(self, table: str, key: str) -> bool:
        if table != "delivery_entries":
            return False
        return self.rows.pop(key, None) is not None

    def write_delivery_entry(self, entry: Any, *, state: str = "pending") -> dict[str, Any]:
        payload = entry.to_dict() if hasattr(entry, "to_dict") else dict(entry)
        payload["state"] = state
        payload["updated_at"] = time.time()
        self.rows[str(payload["id"])] = payload
        return payload

    def delete_delivery_entry(self, delivery_id: str) -> bool:
        return self.rows.pop(delivery_id, None) is not None

    def reserve_delivery(
        self,
        *,
        worker_id: str,
        now: float | None = None,
        delivery_id: str = "",
    ) -> dict[str, Any] | None:
        current = time.time() if now is None else float(now)
        candidates = [
            row
            for row in self.rows.values()
            if row.get("state") in {"pending", "retrying"}
            and (not delivery_id or row.get("id") == delivery_id)
            and float(row.get("next_retry_at", 0.0) or 0.0) <= current
        ]
        candidates.sort(key=lambda row: float(row.get("enqueued_at", 0.0) or 0.0))
        if not candidates:
            return None
        row = candidates[0]
        row["state"] = "running"
        row["locked_by"] = worker_id
        row["locked_at"] = current
        row["updated_at"] = current
        return dict(row)


class InMemoryTaskBackend:
    """Minimal tasks backend for inbound broker load tests."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def list(self, table: str, *, limit: int = 50, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if table != "tasks":
            return []
        statuses = {str(item) for item in (filters or {}).get("statuses", []) if str(item)}
        with self._lock:
            rows = [
                dict(row)
                for row in self.rows.values()
                if not statuses or str(row.get("status", "")) in statuses
            ]
        rows.sort(key=lambda row: float(row.get("updated_at", 0.0) or 0.0), reverse=True)
        return rows[:limit]

    def get(self, table: str, key: str) -> dict[str, Any] | None:
        if table != "tasks":
            return None
        with self._lock:
            row = self.rows.get(key)
            return dict(row) if row is not None else None

    def write_task(self, task: Any) -> dict[str, Any]:
        payload = task.to_dict() if hasattr(task, "to_dict") else dict(task)
        with self._lock:
            self.rows[str(payload["id"])] = dict(payload)
        return payload

    def reserve_task_id(
        self,
        *,
        task_id: str,
        worker_id: str,
        task_types: list[str] | tuple[str, ...] | None = None,
        blocked_session_keys: list[str] | tuple[str, ...] | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        current = time.time() if now is None else float(now)
        type_set = {str(item) for item in (task_types or []) if str(item)}
        blocked_sessions = {str(item) for item in (blocked_session_keys or []) if str(item)}
        with self._lock:
            row = self.rows.get(task_id)
            if row is None:
                return None
            if row.get("status") not in {"pending", "retrying"}:
                return None
            if type_set and row.get("task_type") not in type_set:
                return None
            if row.get("session_key") and row.get("session_key") in blocked_sessions:
                return None
            row["status"] = "running"
            row["started_at"] = current
            row["updated_at"] = current
            metadata = dict(row.get("metadata", {}) or {})
            metadata["worker_id"] = worker_id
            row["metadata"] = metadata
            return dict(row)


class LocalLoadTestLaneCoordinator:
    """In-process lane ownership probe for inbound load tests.

    This does not replace RedisLaneCoordinator in production. It lets the load
    test measure session-level serialization without requiring a real Redis
    server, while preserving the same invariant: one active owner per session.
    """

    def __init__(self) -> None:
        self._active_by_session: dict[str, str] = {}
        self._active_counts: dict[str, int] = {}
        self._lock = asyncio.Lock()
        self.max_active_lanes = 0
        self.max_same_session_concurrency = 0
        self.acquire_conflicts = 0
        self.acquire_attempts = 0

    async def acquire(self, session_key: str, task_id: str, *, worker_id: str = "load-worker") -> bool:
        del worker_id
        async with self._lock:
            self.acquire_attempts += 1
            if session_key in self._active_by_session:
                self.acquire_conflicts += 1
                return False
            self._active_by_session[session_key] = task_id
            self._active_counts[session_key] = self._active_counts.get(session_key, 0) + 1
            self.max_active_lanes = max(self.max_active_lanes, len(self._active_by_session))
            self.max_same_session_concurrency = max(
                self.max_same_session_concurrency,
                self._active_counts[session_key],
            )
            return True

    async def release(self, session_key: str, task_id: str) -> None:
        async with self._lock:
            if self._active_by_session.get(session_key) == task_id:
                self._active_by_session.pop(session_key, None)
            current = max(0, self._active_counts.get(session_key, 0) - 1)
            if current:
                self._active_counts[session_key] = current
            else:
                self._active_counts.pop(session_key, None)

    def is_owned(self, session_key: str) -> bool:
        return session_key in self._active_by_session

    def inspect(self, session_key: str) -> dict[str, Any]:
        task_id = self._active_by_session.get(session_key, "")
        return {
            "session_key": session_key,
            "owned": bool(task_id),
            "task_id": task_id,
            "backend": "local",
        }

    def summary(self) -> dict[str, Any]:
        return {
            "mode": "local",
            "max_active_lanes": self.max_active_lanes,
            "max_same_session_concurrency": self.max_same_session_concurrency,
            "lane_acquire_attempts": self.acquire_attempts,
            "lane_acquire_conflicts": self.acquire_conflicts,
            "redis_health": {},
        }


class RedisLoadTestLaneCoordinator:
    """Redis-backed lane ownership probe for inbound load tests."""

    def __init__(
        self,
        *,
        redis_url: str,
        socket_timeout_seconds: float,
        ttl_seconds: int,
        namespace: str,
    ) -> None:
        self.redis = RedisClient(
            enabled=True,
            url=redis_url,
            socket_timeout_seconds=socket_timeout_seconds,
        )
        self.coordinator = RedisLaneCoordinator(self.redis, namespace=namespace)
        self.ttl_seconds = max(1, int(ttl_seconds))
        self._active_counts: dict[str, int] = {}
        self._ownership_by_task: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self.max_active_lanes = 0
        self.max_same_session_concurrency = 0
        self.acquire_conflicts = 0
        self.acquire_attempts = 0
        self.redis_health = self.redis.health().to_dict()

    async def acquire(self, session_key: str, task_id: str, *, worker_id: str = "load-worker") -> bool:
        self.acquire_attempts += 1
        ownership = await asyncio.to_thread(
            self.coordinator.acquire,
            session_key,
            owner=LaneOwnerToken(worker_id=worker_id, task_id=task_id),
            ttl_seconds=self.ttl_seconds,
        )
        if ownership is None:
            self.acquire_conflicts += 1
            return False
        async with self._lock:
            self._ownership_by_task[task_id] = ownership
            self._active_counts[session_key] = self._active_counts.get(session_key, 0) + 1
            self.max_active_lanes = max(self.max_active_lanes, len(self._ownership_by_task))
            self.max_same_session_concurrency = max(
                self.max_same_session_concurrency,
                self._active_counts[session_key],
            )
        return True

    async def release(self, session_key: str, task_id: str) -> None:
        ownership = None
        async with self._lock:
            ownership = self._ownership_by_task.pop(task_id, None)
            current = max(0, self._active_counts.get(session_key, 0) - 1)
            if current:
                self._active_counts[session_key] = current
            else:
                self._active_counts.pop(session_key, None)
        if ownership is not None:
            await asyncio.to_thread(self.coordinator.release, ownership)

    def is_owned(self, session_key: str) -> bool:
        return self.coordinator.is_owned(session_key)

    def inspect(self, session_key: str) -> dict[str, Any]:
        return self.coordinator.inspect(session_key).to_dict()

    def summary(self) -> dict[str, Any]:
        return {
            "mode": "redis",
            "max_active_lanes": self.max_active_lanes,
            "max_same_session_concurrency": self.max_same_session_concurrency,
            "lane_acquire_attempts": self.acquire_attempts,
            "lane_acquire_conflicts": self.acquire_conflicts,
            "redis_health": self.redis_health,
        }


def build_gateway_application() -> Any:
    """Build the real gateway application lazily for external load tests."""

    from agent_gateway.app import build_application

    return build_application()


def load_gateway_env(env_file: Path | None = None) -> None:
    """Load gateway `.env` before building settings for standalone scripts."""

    from agent_gateway.config import load_env

    load_env(env_file)


async def build_gateway_application_async() -> Any:
    """Build the real gateway app outside the active asyncio loop.

    `build_application()` still performs some synchronous startup work that calls
    `asyncio.run()` internally. Running it in a worker thread avoids nested event
    loops when load-test scenarios are already inside `asyncio.run()`.
    """

    return await asyncio.to_thread(build_gateway_application)


def percentile(values: list[float], percent: float) -> float:
    """Return a nearest-rank percentile value."""

    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((percent / 100) * (len(ordered) - 1)))))
    return ordered[index]


def summarize_latencies(values: list[float]) -> dict[str, float]:
    """Build latency summary used by JSON and Markdown reports."""

    if not values:
        return {"min": 0.0, "avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0}
    return {
        "min": round(min(values), 3),
        "avg": round(statistics.fmean(values), 3),
        "p50": round(percentile(values, 50), 3),
        "p95": round(percentile(values, 95), 3),
        "p99": round(percentile(values, 99), 3),
        "max": round(max(values), 3),
    }


async def _run_mock_request(
    *,
    request_index: int,
    semaphore: asyncio.Semaphore,
    agent_delay_ms: float,
    delivery_delay_ms: float,
) -> RequestSample:
    """Run one synthetic local request through mock agent and delivery phases."""

    request_id = f"load-{request_index:06d}-{uuid.uuid4().hex[:8]}"
    async with semaphore:
        started = time.perf_counter()
        try:
            agent_started = time.perf_counter()
            await asyncio.sleep(agent_delay_ms / 1000)
            agent_turn_ms = (time.perf_counter() - agent_started) * 1000

            delivery_started = time.perf_counter()
            await asyncio.sleep(delivery_delay_ms / 1000)
            delivery_ms = (time.perf_counter() - delivery_started) * 1000

            return RequestSample(
                request_id=request_id,
                ok=True,
                error="",
                e2e_ms=(time.perf_counter() - started) * 1000,
                agent_turn_ms=agent_turn_ms,
                delivery_ms=delivery_ms,
            )
        except Exception as exc:  # pragma: no cover - defensive guard for report stability.
            return RequestSample(
                request_id=request_id,
                ok=False,
                error=str(exc),
                e2e_ms=(time.perf_counter() - started) * 1000,
                agent_turn_ms=0.0,
                delivery_ms=0.0,
            )


async def run_mock_local(
    *,
    requests: int,
    concurrency: int,
    agent_delay_ms: float,
    delivery_delay_ms: float,
) -> tuple[list[RequestSample], float]:
    """Execute the mock-local scenario and return samples plus wall time."""

    semaphore = asyncio.Semaphore(max(1, concurrency))
    started = time.perf_counter()
    samples = await asyncio.gather(
        *[
            _run_mock_request(
                request_index=index,
                semaphore=semaphore,
                agent_delay_ms=agent_delay_ms,
                delivery_delay_ms=delivery_delay_ms,
            )
            for index in range(1, requests + 1)
        ]
    )
    return list(samples), time.perf_counter() - started


async def run_delivery_local(
    *,
    requests: int,
    concurrency: int,
    delivery_delay_ms: float,
    work_dir: Path,
) -> tuple[list[RequestSample], float, dict[str, Any]]:
    """Run real DeliveryQueue + DeliveryRuntime with a mock local channel."""

    queue_dir = work_dir / f"delivery-local-{uuid.uuid4().hex[:8]}"
    queue = DeliveryQueue(queue_dir)
    manager = ChannelManager()
    channel = LoadTestChannel(delay_ms=delivery_delay_ms)
    manager.register(channel, ChannelAccount(channel="load", account_id="load-local"))
    runtime = DeliveryRuntime(queue, manager)
    samples: list[RequestSample] = []

    enqueue_started = time.perf_counter()
    for index in range(1, requests + 1):
        request_id = f"delivery-{index:06d}-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        delivery_id = queue.enqueue(
            "load",
            "load-peer",
            f"load test message {index}",
            {
                "account_id": "load-local",
                "kind": "load-test",
                "request_id": request_id,
            },
        )
        samples.append(
            RequestSample(
                request_id=delivery_id,
                ok=True,
                error="",
                e2e_ms=0.0,
                agent_turn_ms=0.0,
                delivery_ms=(time.perf_counter() - started) * 1000,
            )
        )
    max_delivery_backlog = len(queue.pending_entries())
    enqueue_seconds = time.perf_counter() - enqueue_started

    started = time.perf_counter()
    # The local file queue is a fallback/audit path and is intentionally measured
    # as a single delivery worker. Multi-worker correctness is covered by the
    # PostgreSQL/RabbitMQ path, not by concurrent local file scans.
    while len(queue.pending_entries()) > 0:
        await runtime.flush_once()
    wall_seconds = time.perf_counter() - started

    delivery_finished_ms = wall_seconds * 1000
    if samples:
        per_message_ms = delivery_finished_ms / len(samples)
        for sample in samples:
            sample.e2e_ms = per_message_ms
            sample.delivery_ms = per_message_ms

    context = {
        "enqueue_seconds": round(enqueue_seconds, 3),
        "sent": len(channel.sent),
        "max_delivery_backlog": max_delivery_backlog,
        "queue_dir": str(queue_dir),
        "uses_real_delivery_queue": True,
        "effective_delivery_workers": 1,
        "requested_concurrency": concurrency,
    }
    return samples, wall_seconds, context


async def run_delivery_rabbitmq(
    *,
    requests: int,
    concurrency: int,
    delivery_delay_ms: float,
    work_dir: Path,
    rabbitmq_url: str,
    rabbitmq_exchange: str,
    rabbitmq_queue: str,
    rabbitmq_dead_letter_exchange: str,
    rabbitmq_dead_letter_queue: str,
    connect_timeout_seconds: float,
) -> tuple[list[RequestSample], float, dict[str, Any]]:
    """Run RabbitMQ-backed delivery publishing and consumption."""

    queue_dir = work_dir / f"delivery-rabbitmq-{uuid.uuid4().hex[:8]}"
    queue = DeliveryQueue(queue_dir)
    backend = InMemoryDeliveryBackend()
    queue.read_backend = backend
    queue.write_backend = backend
    broker = RabbitMQDeliveryBroker(
        url=rabbitmq_url,
        exchange=rabbitmq_exchange,
        queue=rabbitmq_queue,
        dead_letter_exchange=rabbitmq_dead_letter_exchange,
        dead_letter_queue=rabbitmq_dead_letter_queue,
        connect_timeout_seconds=connect_timeout_seconds,
        enabled=True,
    )
    queue.broker = broker
    purged_before = broker.purge()
    manager = ChannelManager()
    channel = LoadTestChannel(delay_ms=delivery_delay_ms)
    manager.register(channel, ChannelAccount(channel="load", account_id="load-local"))
    samples: list[RequestSample] = []

    enqueue_started = time.perf_counter()
    for index in range(1, requests + 1):
        request_id = f"rabbitmq-{index:06d}-{uuid.uuid4().hex[:8]}"
        started = time.perf_counter()
        delivery_id = queue.enqueue(
            "load",
            "load-peer",
            f"rabbitmq load test message {index}",
            {
                "account_id": "load-local",
                "kind": "load-test",
                "request_id": request_id,
                "correlation_id": request_id,
            },
        )
        samples.append(
            RequestSample(
                request_id=delivery_id,
                ok=True,
                error="",
                e2e_ms=0.0,
                agent_turn_ms=0.0,
                delivery_ms=(time.perf_counter() - started) * 1000,
            )
        )
    broker_stats_after_publish = queue.broker_stats()
    max_delivery_backlog = len(queue.pending_entries())
    enqueue_seconds = time.perf_counter() - enqueue_started

    def build_consumer_runtime() -> tuple[DeliveryRuntime, RabbitMQDeliveryBroker]:
        consumer_queue = DeliveryQueue(queue_dir)
        consumer_queue.read_backend = backend
        consumer_queue.write_backend = backend
        consumer_broker = RabbitMQDeliveryBroker(
            url=rabbitmq_url,
            exchange=rabbitmq_exchange,
            queue=rabbitmq_queue,
            dead_letter_exchange=rabbitmq_dead_letter_exchange,
            dead_letter_queue=rabbitmq_dead_letter_queue,
            connect_timeout_seconds=connect_timeout_seconds,
            enabled=True,
        )
        consumer_queue.broker = consumer_broker
        return DeliveryRuntime(consumer_queue, manager), consumer_broker

    worker_count = max(1, concurrency)
    consumer_runtimes: list[DeliveryRuntime] = []
    consumer_brokers: list[RabbitMQDeliveryBroker] = []
    for _ in range(worker_count):
        consumer_runtime, consumer_broker = build_consumer_runtime()
        consumer_runtimes.append(consumer_runtime)
        consumer_brokers.append(consumer_broker)

    started = time.perf_counter()
    broker_stats_after_consume: dict[str, Any] = {}
    while True:
        broker_stats_after_consume = queue.broker_stats()
        if len(channel.sent) >= requests and int(broker_stats_after_consume.get("messages", 0) or 0) == 0:
            break
        tasks = [runtime.flush_once() for runtime in consumer_runtimes]
        await asyncio.gather(*tasks)
    wall_seconds = time.perf_counter() - started
    broker_stats_after_consume = queue.broker_stats()
    purged_after = {"messages": 0, "dead_letter_messages": 0}
    if int(broker_stats_after_consume.get("messages", 0) or 0) or int(
        broker_stats_after_consume.get("dead_letter_messages", 0) or 0
    ):
        purged_after = broker.purge()
        broker_stats_after_consume = queue.broker_stats()
    for consumer_broker in consumer_brokers:
        consumer_broker.close()
    broker.close()

    delivery_finished_ms = wall_seconds * 1000
    if samples:
        per_message_ms = delivery_finished_ms / len(samples)
        for sample in samples:
            sample.e2e_ms = per_message_ms
            sample.delivery_ms = per_message_ms

    context = {
        "enqueue_seconds": round(enqueue_seconds, 3),
        "sent": len(channel.sent),
        "max_delivery_backlog": max_delivery_backlog,
        "queue_dir": str(queue_dir),
        "uses_real_delivery_queue": True,
        "uses_rabbitmq": True,
        "effective_delivery_workers": worker_count,
        "broker_purged_before": purged_before,
        "broker_purged_after": purged_after,
        "broker_after_publish": broker_stats_after_publish,
        "broker_after_consume": broker_stats_after_consume,
    }
    return samples, wall_seconds, context


async def run_inbound_rabbitmq(
    *,
    requests: int,
    concurrency: int,
    agent_delay_ms: float,
    work_dir: Path,
    rabbitmq_url: str,
    rabbitmq_exchange: str,
    rabbitmq_queue_prefix: str,
    rabbitmq_dead_letter_exchange: str,
    rabbitmq_dead_letter_queue: str,
    rabbitmq_partitions: int,
    rabbitmq_prefetch: int,
    session_count: int,
    lane_mode: str,
    redis_url: str,
    redis_socket_timeout_seconds: float,
    lane_ttl_seconds: int,
    lane_namespace: str,
    connect_timeout_seconds: float,
) -> tuple[list[RequestSample], float, dict[str, Any]]:
    """Run RabbitMQ-backed inbound task publishing and worker consumption."""

    task_dir = work_dir / f"inbound-rabbitmq-{uuid.uuid4().hex[:8]}"
    backend = InMemoryTaskBackend()
    store = LocalTaskStore(task_dir)
    store.read_backend = backend
    store.write_backend = backend
    broker = RabbitMQInboundTaskBroker(
        url=rabbitmq_url,
        exchange=rabbitmq_exchange,
        queue_prefix=rabbitmq_queue_prefix,
        dead_letter_exchange=rabbitmq_dead_letter_exchange,
        dead_letter_queue=rabbitmq_dead_letter_queue,
        partitions=rabbitmq_partitions,
        prefetch=rabbitmq_prefetch,
        connect_timeout_seconds=connect_timeout_seconds,
        enabled=True,
    )
    queue = LocalTaskQueue(store, broker=broker)
    purged_before = broker.purge()
    samples: list[RequestSample] = []
    sample_by_task_id: dict[str, RequestSample] = {}
    sample_started_at: dict[str, float] = {}
    worker_count = max(1, concurrency)
    effective_session_count = max(1, session_count)
    normalized_lane_mode = lane_mode.strip().lower() or "local"
    lane_coordinator = None
    if normalized_lane_mode in {"local", "inmemory", "memory"}:
        lane_coordinator = LocalLoadTestLaneCoordinator()
    elif normalized_lane_mode == "redis":
        lane_coordinator = RedisLoadTestLaneCoordinator(
            redis_url=redis_url,
            socket_timeout_seconds=max(0.05, redis_socket_timeout_seconds),
            ttl_seconds=max(1, lane_ttl_seconds),
            namespace=lane_namespace,
        )
    elif normalized_lane_mode != "off":
        raise ValueError(f"unsupported inbound lane mode: {lane_mode}")

    async def inbound_handler(task, *, worker_id: str = "load-worker") -> str:
        acquired = False
        if lane_coordinator is not None:
            acquired = await lane_coordinator.acquire(
                task.session_key or task.id,
                task.id,
                worker_id=worker_id,
            )
            if not acquired:
                raise RetryableTaskError("session lane is currently owned by another task")
        try:
            if agent_delay_ms:
                await asyncio.sleep(agent_delay_ms / 1000)
            sample = sample_by_task_id.get(task.id)
            if sample is not None:
                elapsed_ms = (time.perf_counter() - sample_started_at[task.id]) * 1000
                sample.ok = True
                sample.e2e_ms = elapsed_ms
                sample.agent_turn_ms = elapsed_ms
            return "inbound processed"
        finally:
            if acquired and lane_coordinator is not None:
                await lane_coordinator.release(task.session_key or task.id, task.id)

    if lane_coordinator is not None:
        inbound_handler.is_session_locked = lambda task: lane_coordinator.is_owned(task.session_key or task.id)  # type: ignore[attr-defined]
        inbound_handler.inspect_session_lane = lambda task: lane_coordinator.inspect(task.session_key or task.id)  # type: ignore[attr-defined]

    enqueue_started = time.perf_counter()
    for index in range(1, requests + 1):
        request_id = f"inbound-{index:06d}-{uuid.uuid4().hex[:8]}"
        session_index = (index - 1) % effective_session_count
        session_key = f"load-session-{session_index:04d}"
        started = time.perf_counter()
        task = queue.enqueue(
            task_type="agent_inbound",
            source="load-test",
            agent_id="load-agent",
            session_key=session_key,
            priority=100,
            idempotency_key=request_id,
            payload={
                "text": f"inbound rabbitmq load test message {index}",
                "sender_id": f"user-{session_index:04d}",
                "channel": "load",
                "account_id": "load-local",
                "peer_id": f"user-{session_index:04d}",
                "metadata": {"request_id": request_id},
            },
            metadata={"request_id": request_id, "session_index": session_index},
        )
        sample = RequestSample(
            request_id=task.id,
            ok=False,
            error="not processed",
            e2e_ms=0.0,
            agent_turn_ms=0.0,
            delivery_ms=0.0,
        )
        samples.append(sample)
        sample_by_task_id[task.id] = sample
        sample_started_at[task.id] = started
    broker_stats_after_publish = broker.stats()
    max_inbound_backlog = requests
    enqueue_seconds = time.perf_counter() - enqueue_started

    workers: list[TaskWorkerRuntime] = []
    worker_brokers: list[RabbitMQInboundTaskBroker] = []
    for index in range(worker_count):
        worker_broker = RabbitMQInboundTaskBroker(
            url=rabbitmq_url,
            exchange=rabbitmq_exchange,
            queue_prefix=rabbitmq_queue_prefix,
            dead_letter_exchange=rabbitmq_dead_letter_exchange,
            dead_letter_queue=rabbitmq_dead_letter_queue,
            partitions=rabbitmq_partitions,
            prefetch=rabbitmq_prefetch,
            connect_timeout_seconds=connect_timeout_seconds,
            enabled=True,
        )
        worker_store = LocalTaskStore(task_dir)
        worker_store.read_backend = backend
        worker_store.write_backend = backend
        worker_queue = LocalTaskQueue(worker_store, broker=worker_broker)
        worker = TaskWorkerRuntime(worker_queue, worker_id=f"load-worker-{index + 1}")

        async def worker_handler(task, *, _worker_id: str = worker.worker_id) -> str:
            return await inbound_handler(task, worker_id=_worker_id)

        if lane_coordinator is not None:
            worker_handler.is_session_locked = inbound_handler.is_session_locked  # type: ignore[attr-defined]
            worker_handler.inspect_session_lane = inbound_handler.inspect_session_lane  # type: ignore[attr-defined]
        worker.register_handler("agent_inbound", worker_handler)
        workers.append(worker)
        worker_brokers.append(worker_broker)

    started = time.perf_counter()
    broker_stats_after_consume: dict[str, Any] = {}
    deadline = started + max(5.0, requests * max(0.001, agent_delay_ms / 1000) * 4 + 10.0)
    while True:
        done_count = sum(1 for sample in samples if sample.ok)
        broker_stats_after_consume = broker.stats()
        broker_messages = int(broker_stats_after_consume.get("messages", 0) or 0)
        if done_count >= requests and broker_messages == 0:
            break
        if time.perf_counter() > deadline:
            raise TimeoutError(
                f"inbound-rabbitmq load test timed out: processed={done_count}/{requests}, "
                f"broker_messages={broker_messages}, lane_mode={normalized_lane_mode}"
            )
        handled = await asyncio.gather(*[worker.run_once() for worker in workers])
        if not any(handled):
            await asyncio.sleep(0.001)
    wall_seconds = time.perf_counter() - started
    broker_stats_after_consume = broker.stats()
    purged_after = {"messages": 0, "dead_letter_messages": 0}
    if int(broker_stats_after_consume.get("messages", 0) or 0) or int(
        broker_stats_after_consume.get("dead_letter_messages", 0) or 0
    ):
        purged_after = broker.purge()
        broker_stats_after_consume = broker.stats()
    for worker_broker in worker_brokers:
        worker_broker.close()
    broker.close()

    for sample in samples:
        if sample.ok:
            sample.error = ""
        else:
            sample.e2e_ms = wall_seconds * 1000
            sample.agent_turn_ms = 0.0

    partitions_seen = {
        broker.partition_for(f"load-session-{index:04d}")
        for index in range(effective_session_count)
    }
    lane_summary = lane_coordinator.summary() if lane_coordinator is not None else {
        "mode": "off",
        "max_active_lanes": 0,
        "max_same_session_concurrency": 0,
        "lane_acquire_attempts": 0,
        "lane_acquire_conflicts": 0,
        "redis_health": {},
    }
    context = {
        "enqueue_seconds": round(enqueue_seconds, 3),
        "processed": sum(1 for sample in samples if sample.ok),
        "max_inbound_backlog": max_inbound_backlog,
        "queue_dir": str(task_dir),
        "uses_real_delivery_queue": False,
        "uses_rabbitmq": True,
        "effective_task_workers": worker_count,
        "inbound_session_count": effective_session_count,
        "inbound_partitions": max(1, rabbitmq_partitions),
        "inbound_prefetch": max(1, rabbitmq_prefetch),
        "lane_mode": lane_summary["mode"],
        "lane_namespace": lane_namespace if lane_summary["mode"] == "redis" else "",
        "lane_ttl_seconds": max(1, lane_ttl_seconds) if lane_summary["mode"] == "redis" else 0,
        "max_active_lanes": lane_summary["max_active_lanes"],
        "max_same_session_concurrency": lane_summary["max_same_session_concurrency"],
        "lane_acquire_attempts": lane_summary["lane_acquire_attempts"],
        "lane_acquire_conflicts": lane_summary["lane_acquire_conflicts"],
        "redis_health": lane_summary["redis_health"],
        "partitions_seen": len(partitions_seen),
        "broker_purged_before": purged_before,
        "broker_purged_after": purged_after,
        "broker_after_publish": broker_stats_after_publish,
        "broker_after_consume": broker_stats_after_consume,
    }
    return samples, wall_seconds, context


async def _run_model_real_request(
    *,
    app: Any,
    request_index: int,
    semaphore: asyncio.Semaphore,
    agent_id: str,
    session_prefix: str,
    prompt: str,
) -> RequestSample:
    """Run one request through the real AgentLoopRunner and model provider."""

    request_id = f"model-real-{request_index:06d}-{uuid.uuid4().hex[:8]}"
    session_key = f"{session_prefix}:{request_id}"
    async with semaphore:
        started = time.perf_counter()
        try:
            await app.runner.run_turn(
                agent_id,
                session_key,
                prompt,
                channel="load-test",
                correlation_id=request_id,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
            return RequestSample(
                request_id=request_id,
                ok=True,
                error="",
                e2e_ms=elapsed_ms,
                agent_turn_ms=elapsed_ms,
                delivery_ms=0.0,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            return RequestSample(
                request_id=request_id,
                ok=False,
                error=str(exc),
                e2e_ms=elapsed_ms,
                agent_turn_ms=elapsed_ms,
                delivery_ms=0.0,
            )


async def run_model_real(
    *,
    requests: int,
    concurrency: int,
    agent_id: str,
    session_prefix: str,
    prompt: str,
) -> tuple[list[RequestSample], float, dict[str, Any]]:
    """Run a low-concurrency real model scenario without sending platform messages."""

    app = await build_gateway_application_async()
    semaphore = asyncio.Semaphore(max(1, concurrency))
    started = time.perf_counter()
    samples = await asyncio.gather(
        *[
            _run_model_real_request(
                app=app,
                request_index=index,
                semaphore=semaphore,
                agent_id=agent_id,
                session_prefix=session_prefix,
                prompt=prompt,
            )
            for index in range(1, requests + 1)
        ]
    )
    wall_seconds = time.perf_counter() - started
    context = {
        "uses_real_model": True,
        "agent_id": agent_id,
        "session_prefix": session_prefix,
        "prompt_length": len(prompt),
        "model_id": getattr(app.settings, "model_id", ""),
        "base_url_configured": bool(getattr(app.settings, "anthropic_base_url", "")),
        "api_key_configured": bool(getattr(app.settings, "anthropic_api_key", "")),
    }
    return list(samples), wall_seconds, context


async def _run_feishu_send_real_request(
    *,
    channel: Channel,
    request_index: int,
    semaphore: asyncio.Semaphore,
    account_id: str,
    peer_id: str,
    text: str,
) -> RequestSample:
    """Send one real Feishu outbound message through the configured channel."""

    request_id = f"feishu-send-{request_index:06d}-{uuid.uuid4().hex[:8]}"
    async with semaphore:
        started = time.perf_counter()
        outbound = OutboundMessage(
            channel="feishu",
            to=peer_id,
            text=f"{text}\n\n[load-test:{request_id}]",
            metadata={
                "account_id": account_id,
                "kind": "load-test",
                "request_id": request_id,
                "correlation_id": request_id,
            },
        )
        try:
            ok = await asyncio.to_thread(channel.send, outbound)
            elapsed_ms = (time.perf_counter() - started) * 1000
            return RequestSample(
                request_id=request_id,
                ok=bool(ok),
                error="" if ok else "channel.send returned false",
                e2e_ms=elapsed_ms,
                agent_turn_ms=0.0,
                delivery_ms=elapsed_ms,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            return RequestSample(
                request_id=request_id,
                ok=False,
                error=str(exc),
                e2e_ms=elapsed_ms,
                agent_turn_ms=0.0,
                delivery_ms=elapsed_ms,
            )


async def run_feishu_send_real(
    *,
    requests: int,
    concurrency: int,
    account_id: str,
    peer_id: str,
    text: str,
) -> tuple[list[RequestSample], float, dict[str, Any]]:
    """Run a real Feishu outbound send scenario without model calls."""

    app = await build_gateway_application_async()
    channel = app.channel_manager.get("feishu", account_id)
    if channel is None:
        raise ValueError(f"feishu channel account not found: {account_id}")
    semaphore = asyncio.Semaphore(max(1, concurrency))
    started = time.perf_counter()
    samples = await asyncio.gather(
        *[
            _run_feishu_send_real_request(
                channel=channel,
                request_index=index,
                semaphore=semaphore,
                account_id=account_id,
                peer_id=peer_id,
                text=text,
            )
            for index in range(1, requests + 1)
        ]
    )
    wall_seconds = time.perf_counter() - started
    context = {
        "uses_real_feishu": True,
        "feishu_account_id": account_id,
        "peer_id": peer_id,
        "text_length": len(text),
    }
    return list(samples), wall_seconds, context


def git_commit() -> str:
    """Return current short git commit for report reproducibility."""

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
            timeout=2,
        )
    except Exception:
        return "unknown"
    return completed.stdout.strip() or "unknown"


def build_result(
    *,
    scenario: str,
    requests: int,
    concurrency: int,
    wall_seconds: float,
    samples: list[RequestSample],
    agent_delay_ms: float,
    delivery_delay_ms: float,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable JSON-serializable load test result."""

    successful = [sample for sample in samples if sample.ok]
    failed = [sample for sample in samples if not sample.ok]
    e2e_values = [sample.e2e_ms for sample in successful]
    agent_values = [sample.agent_turn_ms for sample in successful]
    delivery_values = [sample.delivery_ms for sample in successful]
    throughput = len(successful) / wall_seconds if wall_seconds > 0 else 0.0
    context = context or {}
    return {
        "meta": {
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "git_commit": git_commit(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
        },
        "scenario": {
            "name": scenario,
            "requests": requests,
            "concurrency": concurrency,
            "agent_delay_ms": agent_delay_ms,
            "delivery_delay_ms": delivery_delay_ms,
            "uses_real_model": bool(context.get("uses_real_model", False)),
            "uses_real_feishu": bool(context.get("uses_real_feishu", False)),
            "uses_real_delivery_queue": bool(context.get("uses_real_delivery_queue", False)),
            "uses_rabbitmq": bool(context.get("uses_rabbitmq", False)),
        },
        "summary": {
            "success": len(successful),
            "failed": len(failed),
            "error_rate": round(len(failed) / requests, 6) if requests else 0.0,
            "wall_seconds": round(wall_seconds, 3),
            "throughput_rps": round(throughput, 3),
            "e2e_ms": summarize_latencies(e2e_values),
            "agent_turn_ms": summarize_latencies(agent_values),
            "delivery_ms": summarize_latencies(delivery_values),
            "max_inbound_backlog": int(context.get("max_inbound_backlog", 0) or 0),
            "max_delivery_backlog": int(context.get("max_delivery_backlog", 0) or 0),
        },
        "context": context,
        "errors": [
            {"request_id": sample.request_id, "error": sample.error}
            for sample in failed[:20]
        ],
    }


def render_markdown(result: dict[str, Any]) -> str:
    """Render the fixed Phase 20.8 report structure."""

    meta = result["meta"]
    scenario = result["scenario"]
    summary = result["summary"]
    bottleneck = "mock-local 基线未发现外部服务瓶颈"
    evidence = "该场景只模拟本地 agent/delivery 延迟，不调用真实模型、飞书、PostgreSQL 或 RabbitMQ。"
    suggestion = "下一步运行 delivery-local / delivery-rabbitmq 场景，分离队列瓶颈。"
    middleware = "本场景不访问外部中间件"
    runtime_role = "mock-local script"
    if scenario["name"] == "delivery-local":
        bottleneck = "delivery-local 基线主要反映本地文件队列和 DeliveryRuntime flush 吞吐"
        evidence = "该场景使用真实 DeliveryQueue、DeliveryRuntime 和 mock channel，不调用真实模型、飞书、PostgreSQL 或 RabbitMQ。"
        suggestion = "下一步接入 delivery-rabbitmq 场景，对比 RabbitMQ 分发和本地轮询差异。"
        middleware = "使用本地文件 DeliveryQueue，不访问 Redis/PostgreSQL/RabbitMQ"
        runtime_role = "delivery-local script"
    if scenario["name"] == "delivery-rabbitmq":
        bottleneck = "delivery-rabbitmq 基线主要反映 RabbitMQ 分发和 DeliveryRuntime broker consume 吞吐"
        evidence = "该场景使用真实 RabbitMQ broker、DeliveryQueue、DeliveryRuntime 和内存事实状态 backend，不调用真实模型或飞书。"
        suggestion = "对比 delivery-local 报告，观察 RabbitMQ 引用分发、ack 和多 worker flush 的成本。"
        middleware = "使用 RabbitMQ；事实状态使用脚本内内存 backend，不访问真实 PostgreSQL"
        runtime_role = "delivery-rabbitmq script"
    if scenario["name"] == "inbound-rabbitmq":
        bottleneck = "inbound-rabbitmq 基线主要反映 RabbitMQ 入站分区、task_id 预占、session lane ownership 和 TaskWorkerRuntime 消费吞吐"
        evidence = "该场景使用真实 RabbitMQ inbound broker、真实 LocalTaskQueue/TaskWorkerRuntime、本地 lane ownership 探针和内存任务状态 backend，不调用真实模型或飞书。"
        suggestion = "调整 --inbound-session-count、--inbound-rabbitmq-partitions 和 --concurrency，观察热点 session、分区数和 worker 池对吞吐的影响。"
        middleware = "使用 RabbitMQ；任务事实状态使用脚本内内存 backend，不访问真实 PostgreSQL"
        runtime_role = "inbound-rabbitmq script"
    if scenario["name"] == "model-real":
        bottleneck = "model-real 基线主要反映真实模型 API、上下文装配和 AgentLoopRunner 执行延迟"
        evidence = "该场景调用真实模型，但不发送飞书消息，也不压测出站投递。"
        suggestion = "低并发逐步提升，优先观察 P95、错误率、限流和 profile fallback。"
        middleware = "使用真实模型配置；不访问真实飞书发送链路"
        runtime_role = "model-real script"
    if scenario["name"] == "feishu-send-real":
        bottleneck = "feishu-send-real 基线主要反映飞书出站 API、lark-cli 或 token 刷新延迟"
        evidence = "该场景不调用模型，只通过已配置飞书通道向指定 peer 发送真实消息。"
        suggestion = "从 requests=1 concurrency=1 开始，逐步观察 P95、失败率和平台限流。"
        middleware = "使用真实飞书发送链路；不调用真实模型"
        runtime_role = "feishu-send-real script"
    if summary["error_rate"] > 0:
        bottleneck = "存在请求失败，优先检查脚本错误和本地资源"
    return "\n".join(
        [
            "# AI Agent Gateway 压测报告",
            "",
            "## 基本信息",
            f"- 时间：{meta['generated_at']}",
            f"- 机器：{meta['platform']}",
            f"- Git commit：{meta['git_commit']}",
            f"- Python 版本：{meta['python']}",
            f"- 运行角色：{runtime_role}",
            f"- Redis / PostgreSQL / RabbitMQ 配置：{middleware}",
            "",
            "## 场景配置",
            f"- 场景：{scenario['name']}",
            f"- 并发：{scenario['concurrency']}",
            f"- 请求数：{scenario['requests']}",
            f"- 是否真实模型：{scenario['uses_real_model']}",
            f"- 是否真实飞书：{scenario['uses_real_feishu']}",
            f"- 是否真实 RabbitMQ：{scenario['uses_rabbitmq']}",
            "",
            "## 结果摘要",
            f"- 成功数 / 失败数 / 错误率：{summary['success']} / {summary['failed']} / {summary['error_rate']}",
            f"- 吞吐：{summary['throughput_rps']} req/s",
            (
                "- E2E P50 / P95 / P99："
                f"{summary['e2e_ms']['p50']} / {summary['e2e_ms']['p95']} / {summary['e2e_ms']['p99']} ms"
            ),
            (
                "- Agent P50 / P95 / P99："
                f"{summary['agent_turn_ms']['p50']} / {summary['agent_turn_ms']['p95']} / {summary['agent_turn_ms']['p99']} ms"
            ),
            (
                "- Delivery P50 / P95 / P99："
                f"{summary['delivery_ms']['p50']} / {summary['delivery_ms']['p95']} / {summary['delivery_ms']['p99']} ms"
            ),
            f"- 最大入站积压：{summary['max_inbound_backlog']}",
            f"- 最大投递积压：{summary['max_delivery_backlog']}",
            "",
            "## 瓶颈判断",
            f"- 主要瓶颈：{bottleneck}",
            f"- 证据：{evidence}",
            f"- 建议：{suggestion}",
            "",
            "## 原始指标摘要",
            "- runtime.status：mock-local 未采集运行中网关状态",
            f"- delivery.stats：最大投递积压 {summary['max_delivery_backlog']}",
            "- metrics.summary：mock-local 未访问控制面 metrics",
            f"- errors.recent：{len(result['errors'])} 条脚本内错误",
            "",
        ]
    )


def write_reports(result: dict[str, Any], report_dir: Path, basename: str) -> tuple[Path, Path]:
    """Write JSON and Markdown reports."""

    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / f"{basename}.json"
    md_path = report_dir / f"{basename}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(result), encoding="utf-8")
    return json_path, md_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AI Agent Gateway load tests.")
    parser.add_argument("--scenario", default="mock-local", choices=sorted(SUPPORTED_SCENARIOS))
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--agent-delay-ms", type=float, default=10.0)
    parser.add_argument("--delivery-delay-ms", type=float, default=2.0)
    parser.add_argument("--work-dir", type=Path, default=Path(".load-test-tmp"))
    parser.add_argument("--rabbitmq-url", default="amqp://admin:admin123@127.0.0.1:5672/")
    parser.add_argument("--rabbitmq-exchange", default="agent_gateway.delivery.load_test")
    parser.add_argument("--rabbitmq-queue", default="agent_gateway.delivery.load_test.outbound")
    parser.add_argument("--rabbitmq-dead-letter-exchange", default="agent_gateway.delivery.load_test.dlx")
    parser.add_argument("--rabbitmq-dead-letter-queue", default="agent_gateway.delivery.load_test.dead")
    parser.add_argument("--rabbitmq-connect-timeout-seconds", type=float, default=2.0)
    parser.add_argument("--inbound-rabbitmq-exchange", default="agent_gateway.inbound.load_test")
    parser.add_argument("--inbound-rabbitmq-queue-prefix", default="agent_gateway.inbound.load_test.partition")
    parser.add_argument("--inbound-rabbitmq-dead-letter-exchange", default="agent_gateway.inbound.load_test.dlx")
    parser.add_argument("--inbound-rabbitmq-dead-letter-queue", default="agent_gateway.inbound.load_test.dead")
    parser.add_argument("--inbound-rabbitmq-partitions", type=int, default=8)
    parser.add_argument("--inbound-rabbitmq-prefetch", type=int, default=1)
    parser.add_argument("--inbound-session-count", type=int, default=32)
    parser.add_argument(
        "--inbound-lane-mode",
        choices=("local", "redis", "off"),
        default="local",
        help="inbound-rabbitmq lane ownership verification mode",
    )
    parser.add_argument("--redis-url", default="redis://127.0.0.1:6379/0")
    parser.add_argument("--redis-socket-timeout-seconds", type=float, default=1.0)
    parser.add_argument("--inbound-lane-ttl-seconds", type=int, default=30)
    parser.add_argument("--inbound-lane-namespace", default="gateway:load-test:lane")
    parser.add_argument("--allow-real-external", action="store_true")
    parser.add_argument("--agent-id", default="main")
    parser.add_argument("--session-prefix", default="load-test")
    parser.add_argument("--prompt", default="请用一句中文回复 pong，不要调用工具。")
    parser.add_argument("--feishu-account-id", default="")
    parser.add_argument("--feishu-peer-id", default="")
    parser.add_argument("--message-text", default="AI Agent Gateway 飞书发送压测。")
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--basename", default="")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_gateway_env(args.env_file)
    requests = max(1, args.requests)
    concurrency = max(1, args.concurrency)
    context: dict[str, Any] = {}
    if args.scenario == "model-real":
        if not args.allow_real_external:
            raise SystemExit("model-real 会调用真实模型；请显式添加 --allow-real-external 后再运行。")
        samples, wall_seconds, context = asyncio.run(
            run_model_real(
                requests=requests,
                concurrency=concurrency,
                agent_id=args.agent_id,
                session_prefix=args.session_prefix,
                prompt=args.prompt,
            )
        )
    elif args.scenario == "feishu-send-real":
        if not args.allow_real_external:
            raise SystemExit("feishu-send-real 会发送真实飞书消息；请显式添加 --allow-real-external 后再运行。")
        if not args.feishu_account_id or not args.feishu_peer_id:
            raise SystemExit("feishu-send-real 需要同时提供 --feishu-account-id 和 --feishu-peer-id。")
        samples, wall_seconds, context = asyncio.run(
            run_feishu_send_real(
                requests=requests,
                concurrency=concurrency,
                account_id=args.feishu_account_id,
                peer_id=args.feishu_peer_id,
                text=args.message_text,
            )
        )
    elif args.scenario == "delivery-rabbitmq":
        samples, wall_seconds, context = asyncio.run(
            run_delivery_rabbitmq(
                requests=requests,
                concurrency=concurrency,
                delivery_delay_ms=max(0.0, args.delivery_delay_ms),
                work_dir=args.work_dir,
                rabbitmq_url=args.rabbitmq_url,
                rabbitmq_exchange=args.rabbitmq_exchange,
                rabbitmq_queue=args.rabbitmq_queue,
                rabbitmq_dead_letter_exchange=args.rabbitmq_dead_letter_exchange,
                rabbitmq_dead_letter_queue=args.rabbitmq_dead_letter_queue,
                connect_timeout_seconds=max(0.2, args.rabbitmq_connect_timeout_seconds),
            )
        )
    elif args.scenario == "inbound-rabbitmq":
        samples, wall_seconds, context = asyncio.run(
            run_inbound_rabbitmq(
                requests=requests,
                concurrency=concurrency,
                agent_delay_ms=max(0.0, args.agent_delay_ms),
                work_dir=args.work_dir,
                rabbitmq_url=args.rabbitmq_url,
                rabbitmq_exchange=args.inbound_rabbitmq_exchange,
                rabbitmq_queue_prefix=args.inbound_rabbitmq_queue_prefix,
                rabbitmq_dead_letter_exchange=args.inbound_rabbitmq_dead_letter_exchange,
                rabbitmq_dead_letter_queue=args.inbound_rabbitmq_dead_letter_queue,
                rabbitmq_partitions=max(1, args.inbound_rabbitmq_partitions),
                rabbitmq_prefetch=max(1, args.inbound_rabbitmq_prefetch),
                session_count=max(1, args.inbound_session_count),
                lane_mode=args.inbound_lane_mode,
                redis_url=args.redis_url,
                redis_socket_timeout_seconds=max(0.05, args.redis_socket_timeout_seconds),
                lane_ttl_seconds=max(1, args.inbound_lane_ttl_seconds),
                lane_namespace=args.inbound_lane_namespace,
                connect_timeout_seconds=max(0.2, args.rabbitmq_connect_timeout_seconds),
            )
        )
    elif args.scenario == "delivery-local":
        samples, wall_seconds, context = asyncio.run(
            run_delivery_local(
                requests=requests,
                concurrency=concurrency,
                delivery_delay_ms=max(0.0, args.delivery_delay_ms),
                work_dir=args.work_dir,
            )
        )
    else:
        samples, wall_seconds = asyncio.run(
            run_mock_local(
                requests=requests,
                concurrency=concurrency,
                agent_delay_ms=max(0.0, args.agent_delay_ms),
                delivery_delay_ms=max(0.0, args.delivery_delay_ms),
            )
        )
    result = build_result(
        scenario=args.scenario,
        requests=requests,
        concurrency=concurrency,
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=max(0.0, args.agent_delay_ms),
        delivery_delay_ms=max(0.0, args.delivery_delay_ms),
        context=context,
    )
    basename = args.basename or f"{args.scenario}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    json_path, md_path = write_reports(result, args.report_dir, basename)
    print(
        json.dumps(
            {
                "scenario": args.scenario,
                "success": result["summary"]["success"],
                "failed": result["summary"]["failed"],
                "throughput_rps": result["summary"]["throughput_rps"],
                "e2e_p95_ms": result["summary"]["e2e_ms"]["p95"],
                "json": str(json_path),
                "markdown": str(md_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if result["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
