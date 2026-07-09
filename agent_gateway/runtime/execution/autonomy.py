"""主动任务与后台调度。

Heartbeat 和 Cron 都属于网关主动执行能力：它们共享 dispatcher、channels 和投递链路，
但不依赖用户入站消息。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo
from pathlib import Path

try:
    from croniter import croniter
except ImportError:  # pragma: no cover
    croniter = None  # type: ignore[assignment]

from agent_gateway.gateways.messaging.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.domain.ids import normalize_agent_id
from agent_gateway.runtime.domain.models import ProactiveTarget, AgentReply
from agent_gateway.ai.news.collector import NewsCollector
from agent_gateway.ai.news.digest import build_digest_prompt, build_github_skill_digest_prompt
from agent_gateway.ai.news.models import NewsItem
from agent_gateway.ai.news.store import NewsDigestStore
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher
from agent_gateway.runtime.observability.events import RuntimeEventStore, new_correlation_id
from agent_gateway.runtime.tasks.models import TaskInstance
from agent_gateway.runtime.tasks.queue import LocalTaskQueue


DEFAULT_CRON_DISABLE_THRESHOLD = 5
NEWS_DIGEST_ACK_METADATA_KEY = "news_digest_items"


async def _dispatch_background_with_correlation(
    dispatcher: GatewayDispatcher,
    *,
    agent_id: str,
    session_key: str,
    prompt: str,
    channel: str,
    mode: str,
    lane_name: str,
    correlation_id: str,
    disabled_tools: list[str] | None = None,
) -> AgentReply:
    try:
        return await dispatcher.dispatch_background(
            agent_id=agent_id,
            session_key=session_key,
            prompt=prompt,
            channel=channel,
            mode=mode,
            lane_name=lane_name,
            correlation_id=correlation_id,
            disabled_tools=disabled_tools,
        )
    except TypeError as exc:
        if "correlation_id" not in str(exc) and "disabled_tools" not in str(exc):
            raise
        return await dispatcher.dispatch_background(
            agent_id=agent_id,
            session_key=session_key,
            prompt=prompt,
            channel=channel,
            mode=mode,
            lane_name=lane_name,
        )


@dataclass(slots=True)
class CronJob:
    """从 CRON.json 解析出来的一条计划任务。"""

    id: str
    config_id: str
    name: str
    enabled: bool
    schedule_kind: str
    schedule_config: dict[str, Any]
    payload: dict[str, Any]
    target: ProactiveTarget
    scope: str = "global"
    source_file: str = ""
    delete_after_run: bool = False
    consecutive_errors: int = 0
    last_run_at: float = 0.0
    next_run_at: float = 0.0


class HeartbeatService:
    """周期性执行 HEARTBEAT.md 的后台任务。"""

    def __init__(
        self,
        settings: GatewaySettings,
        dispatcher: GatewayDispatcher,
        channels: ChannelManager,
        default_target: ProactiveTarget,
        event_store: RuntimeEventStore | None = None,
        redis_client: Any = None,
        task_queue: LocalTaskQueue | None = None,
        state_read_repository: Any = None,
        state_write_repository: Any = None,
    ) -> None:
        self.settings = settings
        self.dispatcher = dispatcher
        self.channels = channels
        self.default_target = default_target
        self.event_store = event_store
        self.task_queue = task_queue
        self.heartbeat_path = settings.workspace_root / "HEARTBEAT.md"
        self.interval = settings.heartbeat_interval_seconds
        self.active_hours = (settings.heartbeat_active_start, settings.heartbeat_active_end)
        self.last_run_at = 0.0
        self.running = False
        self._last_output = ""
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    def set_channels(self, channels: ChannelManager) -> None:
        """在通道重载后替换发送出口。"""

        self.channels = channels

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="heartbeat-runtime")

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def status(self) -> dict[str, Any]:
        """返回给 control plane / dashboard 的状态摘要。"""

        ok, reason = self.should_run()
        now = time.time()
        elapsed = now - self.last_run_at if self.last_run_at else 0.0
        next_in = max(0.0, self.interval - elapsed) if self.last_run_at else self.interval
        return {
            "enabled": self.heartbeat_path.exists(),
            "running": self.running,
            "should_run": ok,
            "reason": reason,
            "last_run": (
                datetime.fromtimestamp(self.last_run_at, tz=timezone.utc).isoformat()
                if self.last_run_at
                else "never"
            ),
            "next_in_seconds": round(next_in, 1),
        }

    async def trigger(self) -> str:
        """手动触发一次 heartbeat。"""

        return await self._execute(force=True)

    def should_run(self) -> tuple[bool, str]:
        """判断当前是否允许执行 heartbeat。"""

        if not self.heartbeat_path.exists():
            return False, "HEARTBEAT.md not found"
        if not self.heartbeat_path.read_text(encoding="utf-8").strip():
            return False, "HEARTBEAT.md is empty"
        if self.running:
            return False, "heartbeat already running"
        if self.last_run_at and (time.time() - self.last_run_at) < self.interval:
            remaining = self.interval - (time.time() - self.last_run_at)
            return False, f"interval not elapsed ({remaining:.0f}s remaining)"
        hour = datetime.now().hour
        start, end = self.active_hours
        in_hours = (start <= hour < end) if start <= end else not (end <= hour < start)
        if not in_hours:
            return False, f"outside active hours ({start}:00-{end}:00)"
        if self._has_foreground_activity():
            return False, "foreground lanes active"
        return True, "all checks passed"

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                ok, _ = self.should_run()
                if ok:
                    if self.task_queue is not None:
                        self._enqueue_task(time.time())
                    else:
                        await self._execute(force=False)
            except Exception:
                pass
            await asyncio.sleep(1.0)

    def _enqueue_task(self, now: float) -> TaskInstance:
        """把到期 heartbeat 封装成后台任务。"""

        assert self.task_queue is not None
        task = self.task_queue.enqueue(
            task_type="heartbeat",
            source="scheduler",
            agent_id=self.default_target.agent_id,
            session_key=f"system:heartbeat:{self.default_target.agent_id}",
            priority=120,
            idempotency_key=f"heartbeat:{self.default_target.agent_id}:{int(now // max(1, self.interval))}",
            payload={"scheduled_at": now},
            metadata={"target_channel": self.default_target.channel},
        )
        self.last_run_at = now
        self._record_event(
            "heartbeat.queued",
            status="ok",
            message="Heartbeat queued",
            metadata={"task_id": task.id},
        )
        return task

    async def run_task_instance(self, task: TaskInstance) -> str:
        """执行由 task worker 预占的 heartbeat 任务。"""

        scheduled_at = float(task.payload.get("scheduled_at", 0.0) or time.time())
        return await self._execute(force=True, scheduled_at=scheduled_at)

    async def _execute(self, *, force: bool, scheduled_at: float | None = None) -> str:
        """读取 HEARTBEAT.md 并作为后台 Agent 任务执行。"""

        if not force:
            ok, reason = self.should_run()
            if not ok:
                return reason

        instructions = self.heartbeat_path.read_text(encoding="utf-8").strip()
        if not instructions:
            return "HEARTBEAT.md is empty"

        self.running = True
        correlation_id = new_correlation_id("heartbeat")
        try:
            reply = await _dispatch_background_with_correlation(
                self.dispatcher,
                agent_id=self.default_target.agent_id,
                session_key=f"system:heartbeat:{self.default_target.agent_id}",
                prompt=instructions,
                channel="heartbeat",
                mode="minimal",
                lane_name="heartbeat",
                correlation_id=correlation_id,
                disabled_tools=["memory_write"],
            )
            meaningful = self._parse_response(reply.text)
            self.last_run_at = scheduled_at or time.time()
            if meaningful is None:
                return "HEARTBEAT_OK (nothing to report)"
            if meaningful.strip() == self._last_output:
                return "duplicate content (skipped)"
            self._last_output = meaningful.strip()
            await self.dispatcher.deliver_text(
                self.channels,
                self.default_target,
                meaningful,
                metadata={"kind": "heartbeat", "correlation_id": correlation_id},
            )
            return f"heartbeat delivered ({len(meaningful)} chars)"
        finally:
            self.running = False

    def _record_event(
        self,
        event_type: str,
        *,
        status: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """记录 heartbeat 调度事件。"""

        if self.event_store is None:
            return
        self.event_store.record(
            event_type,
            component="heartbeat",
            status=status,
            message=message,
            agent_id=self.default_target.agent_id,
            channel="heartbeat",
            metadata=metadata or {},
        )

    def _parse_response(self, response: str) -> str | None:
        """把 HEARTBEAT_OK 协议字符串转换成实际要投递的内容。"""

        if "HEARTBEAT_OK" in response:
            stripped = response.replace("HEARTBEAT_OK", "").strip()
            return stripped if len(stripped) > 5 else None
        return response.strip() or None

    def _has_foreground_activity(self) -> bool:
        """当前台会话活跃时，避免 heartbeat 抢占资源。"""

        stats = self.dispatcher.command_queue.stats()
        for lane_name, lane_stats in stats.items():
            if lane_name.startswith("system:") or lane_name.startswith("cron:") or lane_name in {"heartbeat", "cron"}:
                continue
            if lane_stats.get("active", 0) > 0 or lane_stats.get("queue_depth", 0) > 0:
                return True
        return False


class CronService:
    """基于 CRON.json 的计划任务执行器。"""

    def __init__(
        self,
        settings: GatewaySettings,
        dispatcher: GatewayDispatcher,
        channels: ChannelManager,
        default_target: ProactiveTarget,
        event_store: RuntimeEventStore | None = None,
        redis_client: Any = None,
        task_queue: LocalTaskQueue | None = None,
        state_read_repository: Any = None,
        state_write_repository: Any = None,
        diet_store: Any = None,
    ) -> None:
        self.settings = settings
        self.dispatcher = dispatcher
        self.channels = channels
        self.default_target = default_target
        self.event_store = event_store
        self.redis_client = redis_client
        self.task_queue = task_queue
        self.state_read_repository = state_read_repository
        self.state_write_repository = state_write_repository
        self.diet_store = diet_store
        self.cron_file = settings.workspace_root / "CRON.json"
        self.run_log_dir = settings.workspace_root / "cron"
        self.run_log_dir.mkdir(parents=True, exist_ok=True)
        self.run_log = self.run_log_dir / "cron-runs.jsonl"
        self.jobs: list[CronJob] = []
        self._task: asyncio.Task[None] | None = None
        self._stopped = False
        self.load_jobs()

    def set_channels(self, channels: ChannelManager) -> None:
        self.channels = channels

    def on_delivery_success(self, entry: Any) -> None:
        """投递成功后回收新闻简报等派生状态。"""

        metadata = getattr(entry, "metadata", {})
        if not isinstance(metadata, dict):
            return
        if metadata.get("kind") != "cron" or metadata.get("cron_payload_kind") not in {
            "agent_news_digest",
            "github_skill_digest",
        }:
            return
        store_name = str(metadata.get("news_digest_store") or "news-digest").strip()
        if store_name not in {"news-digest", "github-skill-digest"}:
            store_name = "news-digest"
        rows = metadata.get(NEWS_DIGEST_ACK_METADATA_KEY, [])
        if not isinstance(rows, list):
            return
        items: list[NewsItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = NewsItem.from_dict(row)
            if item.id:
                items.append(item)
        if items:
            self._news_store(store_name).mark_seen(items)

    async def start(self) -> None:
        """启动后台轮询任务。"""

        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="cron-runtime")

    async def stop(self) -> None:
        """停止后台轮询任务。"""

        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def load_jobs(self) -> None:
        """从全局 CRON.json 和 agents/<agent_id>/CRON.json 加载任务。"""

        self.jobs.clear()
        cron_files = self._discover_cron_files()
        if not cron_files:
            return
        seen_ids: set[str] = set()
        try:
            now = time.time()
            for cron_path, owner_agent_id in cron_files:
                try:
                    payload = json.loads(cron_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                for row in payload.get("jobs", []):
                    schedule = row.get("schedule", {})
                    kind = schedule.get("kind", "")
                    if kind not in {"at", "every", "cron"}:
                        continue
                    config_id = str(row.get("id", "")).strip()
                    if not config_id:
                        continue
                    scope = owner_agent_id or "global"
                    job_id = self._build_job_id(config_id, owner_agent_id)
                    if job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)
                    job = CronJob(
                        id=job_id,
                        config_id=config_id,
                        name=row.get("name", ""),
                        enabled=row.get("enabled", True),
                        schedule_kind=kind,
                        schedule_config=schedule,
                        payload=row.get("payload", {}),
                        target=self._target_from_row(row, owner_agent_id=owner_agent_id),
                        scope=scope,
                        source_file=str(cron_path.relative_to(self.settings.workspace_root)),
                        delete_after_run=row.get("delete_after_run", False),
                    )
                    job.next_run_at = self._compute_next(job, now)
                    self.jobs.append(job)
        finally:
            self.jobs.sort(key=lambda item: (item.scope, item.config_id))

    def _discover_cron_files(self) -> list[tuple[Path, str]]:
        """返回待加载的 Cron 文件；第二项为空表示全局任务。"""

        files: list[tuple[Path, str]] = []
        if self.cron_file.exists():
            files.append((self.cron_file, ""))
        agents_root = self.settings.workspace_root / "agents"
        if agents_root.exists():
            for cron_path in sorted(agents_root.glob("*/CRON.json")):
                agent_id = normalize_agent_id(cron_path.parent.name)
                if agent_id:
                    files.append((cron_path, agent_id))
        return files

    @staticmethod
    def _build_job_id(config_id: str, owner_agent_id: str = "") -> str:
        """为全局任务和 Agent 私有任务生成稳定 job_id。"""

        if not owner_agent_id:
            return config_id
        if config_id.startswith(f"{owner_agent_id}:"):
            return config_id
        return f"{owner_agent_id}:{config_id}"

    def list_jobs(self) -> list[dict[str, Any]]:
        """导出任务状态给 control plane。"""

        now = time.time()
        result = []
        for job in self.jobs:
            next_in = max(0.0, job.next_run_at - now) if job.next_run_at > 0 else None
            result.append(
                {
                    "id": job.id,
                    "config_id": job.config_id,
                    "name": job.name,
                    "enabled": job.enabled,
                    "kind": job.schedule_kind,
                    "agent_id": job.target.agent_id,
                    "scope": job.scope,
                    "source_file": job.source_file,
                    "errors": job.consecutive_errors,
                    "last_run": (
                        datetime.fromtimestamp(job.last_run_at, tz=timezone.utc).isoformat()
                        if job.last_run_at
                        else "never"
                    ),
                    "next_run": (
                        datetime.fromtimestamp(job.next_run_at, tz=timezone.utc).isoformat()
                        if job.next_run_at
                        else "n/a"
                    ),
                    "next_in": round(next_in) if next_in is not None else None,
                }
            )
        return result

    async def trigger_job(self, job_id: str) -> str:
        """手动触发指定任务。"""

        job = self._resolve_job(job_id)
        if job is not None:
            await self._run_job(job, time.time())
            return f"'{job.name}' triggered (errors={job.consecutive_errors})"
        return f"Job '{job_id}' not found"

    def _resolve_job(self, job_id: str) -> CronJob | None:
        """按完整 ID 查找；若原始配置 ID 不冲突，也允许按 config_id 查找。"""

        normalized = str(job_id or "").strip()
        if not normalized:
            return None
        for job in self.jobs:
            if job.id == normalized:
                return job
        matches = [job for job in self.jobs if job.config_id == normalized]
        if len(matches) == 1:
            return matches[0]
        return None

    async def _loop(self) -> None:
        """后台轮询入口。"""

        while not self._stopped:
            try:
                await self.tick()
            except Exception:
                pass
            await asyncio.sleep(1.0)

    async def tick(self) -> None:
        """扫描所有任务，运行到期任务。"""

        now = time.time()
        remove_ids: list[str] = []
        for job in self.jobs:
            if not job.enabled or job.next_run_at <= 0 or now < job.next_run_at:
                continue
            if not self._claim_scheduled_run(job):
                job.next_run_at = self._compute_next(job, now)
                self._record_cron_event(
                    "cron.skipped",
                    job,
                    status="skipped",
                    message=f"Cron duplicate skipped: {job.id}",
                    metadata={
                        "reason": "duplicate scheduled run",
                        "schedule_slot": self._schedule_slot(job),
                    },
                )
                continue
            rate_limit = self._check_cron_rate_limit(now)
            if rate_limit is not None and not bool(rate_limit.get("allowed")):
                job.next_run_at = self._compute_next(job, now)
                self._record_cron_event(
                    "cron.skipped",
                    job,
                    status="skipped",
                    message=f"Cron rate limited: {job.id}",
                    metadata={
                        "reason": "redis rate limited",
                        "rate_limit": rate_limit,
                    },
                )
                continue
            if self.task_queue is not None:
                self._enqueue_job_task(job, now)
            else:
                await self._run_job(job, now)
            if job.delete_after_run and job.schedule_kind == "at":
                remove_ids.append(job.id)
        if remove_ids:
            self.jobs = [job for job in self.jobs if job.id not in remove_ids]

    async def _run_job(self, job: CronJob, now: float) -> None:
        """执行单条任务并记录运行日志。"""

        payload = job.payload
        kind = payload.get("kind", "")
        output, status, error = "", "ok", ""
        delivered_news_items: list[NewsItem] = []
        correlation_id = new_correlation_id(f"cron-{job.id}" if job.id else "cron")
        self._record_cron_event(
            "cron.triggered",
            job,
            status="ok",
            message=f"Cron job triggered: {job.id}",
            correlation_id=correlation_id,
            metadata={"payload_kind": kind},
        )
        try:
            if kind == "agent_turn":
                message = payload.get("message", "")
                if not message:
                    output, status = "[empty message]", "skipped"
                else:
                    reply = await _dispatch_background_with_correlation(
                        self.dispatcher,
                        agent_id=job.target.agent_id,
                        session_key=f"system:cron:{job.id}",
                        prompt=message,
                        channel="cron",
                        mode="minimal",
                        lane_name=f"cron:{job.target.agent_id}",
                        correlation_id=correlation_id,
                        disabled_tools=["memory_write"],
                    )
                    output = reply.text
            elif kind == "system_event":
                output = payload.get("text", "")
                if not output:
                    status = "skipped"
            elif kind == "agent_news_digest":
                output, status, delivered_news_items = await self._run_agent_news_digest(
                    job,
                    payload,
                    correlation_id=correlation_id,
                )
            elif kind == "github_skill_digest":
                output, status, delivered_news_items = await self._run_github_skill_digest(
                    job,
                    payload,
                    correlation_id=correlation_id,
                )
            elif kind in {"diet_plan_generate", "nutrition_day_summary", "meal_reminder"}:
                output, status = self._run_diet_job(job, payload, now=now)
            else:
                output = f"[unknown kind: {kind}]"
                status = "error"
                error = f"unknown kind: {kind}"
        except Exception as exc:
            output = f"[cron error: {exc}]"
            status = "error"
            error = str(exc)

        job.last_run_at = now
        if status == "error":
            job.consecutive_errors += 1
            if job.consecutive_errors >= DEFAULT_CRON_DISABLE_THRESHOLD:
                job.enabled = False
        else:
            job.consecutive_errors = 0
        job.next_run_at = self._compute_next(job, now)

        entry = {
            "job_id": job.id,
            "config_id": job.config_id,
            "agent_id": job.target.agent_id,
            "scope": job.scope,
            "run_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "status": status,
            "output_preview": output[:200],
        }
        if error:
            entry["error"] = error
        self._write_run_log(entry)

        if output and status != "skipped":
            metadata: dict[str, object] = {
                "kind": "cron",
                "job_id": job.id,
                "cron_config_id": job.config_id,
                "cron_scope": job.scope,
                "agent_id": job.target.agent_id,
            }
            metadata["correlation_id"] = correlation_id
            if delivered_news_items:
                metadata.update(
                    {
                        "cron_payload_kind": kind,
                        "news_digest_store": self._news_digest_store_name(kind),
                        NEWS_DIGEST_ACK_METADATA_KEY: [
                            item.to_dict() for item in delivered_news_items
                        ],
                    }
                )
            await self.dispatcher.deliver_text(
                self.channels,
                job.target,
                f"[{job.name}] {output}",
                metadata=metadata,
            )
        self._record_cron_event(
            "cron.completed" if status != "error" else "cron.failed",
            job,
            status="ok" if status != "error" else "error",
            message=f"Cron job {status}: {job.id}",
            correlation_id=correlation_id,
            error=error,
            metadata={
                "payload_kind": kind,
                "output_length": len(output),
                "cron_status": status,
            },
        )

    def _enqueue_job_task(self, job: CronJob, now: float) -> TaskInstance:
        """把到期 Cron 封装成后台任务。"""

        assert self.task_queue is not None
        task = self.task_queue.enqueue(
            task_type="cron",
            source="scheduler",
            agent_id=job.target.agent_id,
            session_key=f"system:cron:{job.id}",
            priority=int(job.payload.get("priority", 100) or 100),
            idempotency_key=self._cron_idempotency_key(job),
            payload={
                "job_id": job.id,
                "scheduled_at": now,
            },
            metadata={
                "config_id": job.config_id,
                "scope": job.scope,
                "source_file": job.source_file,
            },
        )
        job.last_run_at = now
        job.next_run_at = self._compute_next(job, now)
        self._record_cron_event(
            "cron.queued",
            job,
            status="ok",
            message=f"Cron job queued: {job.id}",
            metadata={"task_id": task.id},
        )
        return task

    def _write_run_log(self, entry: dict[str, Any]) -> None:
        """写入 Cron 运行记录，PostgreSQL 优先，本地 JSONL 兜底。"""

        if self.state_write_repository is not None:
            try:
                write_cron_run = getattr(self.state_write_repository, "write_cron_run", None)
                payload = self._cron_run_row(entry)
                if write_cron_run is not None:
                    write_cron_run(payload)
                else:
                    self.state_write_repository.upsert("cron_runs", payload)
            except Exception:
                pass
        try:
            with self.run_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    @staticmethod
    def _cron_run_row(entry: dict[str, Any]) -> dict[str, Any]:
        """把 JSONL 运行记录转换为 PostgreSQL 行。"""

        run_at_raw = str(entry.get("run_at", ""))
        try:
            run_at = datetime.fromisoformat(run_at_raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            run_at = time.time()
        job_id = str(entry.get("job_id", ""))
        config_id = str(entry.get("config_id", ""))
        return {
            "id": f"{job_id}:{run_at_raw or run_at}",
            "job_id": job_id,
            "config_id": config_id,
            "agent_id": str(entry.get("agent_id", "")),
            "scope": str(entry.get("scope", "")),
            "run_at": run_at,
            "status": str(entry.get("status", "")),
            "output_preview": str(entry.get("output_preview", "")),
            "error": str(entry.get("error", "")),
            "metadata": dict(entry),
        }

    async def run_task_instance(self, task: TaskInstance) -> str:
        """执行由 task worker 预占的 Cron 任务。"""

        job_id = str(task.payload.get("job_id", ""))
        job = self._resolve_job(job_id)
        if job is None:
            raise RuntimeError(f"cron job not found: {job_id}")
        scheduled_at = float(task.payload.get("scheduled_at", 0.0) or time.time())
        await self._run_job(job, scheduled_at)
        return f"cron job executed: {job.id}"

    async def _run_agent_news_digest(
        self,
        job: CronJob,
        payload: dict[str, Any],
        *,
        correlation_id: str,
    ) -> tuple[str, str, list[NewsItem]]:
        """拉取新闻源、生成摘要 prompt，并把摘要交给目标 Agent 处理。"""

        lookback_hours = int(payload.get("lookback_hours", 24))
        max_items = int(payload.get("max_items", 8))
        per_source_max_items = int(payload.get("per_source_max_items", 5))
        skip_if_empty = bool(payload.get("skip_if_empty", True))
        sources_file = self.settings.workspace_root / str(
            payload.get("sources_file", "agent-news-sources.json")
        )
        store = self._news_store("news-digest")
        collector = NewsCollector(
            sources_file,
            store,
            timeout_seconds=float(payload.get("timeout_seconds", 12.0)),
        )
        try:
            result = await asyncio.to_thread(
                collector.collect,
                lookback_hours=lookback_hours,
                max_items=max_items,
                per_source_max_items=per_source_max_items,
            )
        finally:
            collector.close()

        if not result.items and skip_if_empty:
            return "no fresh agent news items", "skipped", []

        prompt = build_digest_prompt(
            result.items,
            lookback_hours=lookback_hours,
            max_output_items=max_items,
            errors=result.errors,
        )
        reply = await _dispatch_background_with_correlation(
            self.dispatcher,
            agent_id=job.target.agent_id,
            session_key=f"system:cron:{job.id}",
            prompt=prompt,
            channel="cron",
            mode="minimal",
            lane_name=f"cron:{job.target.agent_id}",
            correlation_id=correlation_id,
            disabled_tools=["memory_write"],
        )
        return reply.text, "ok", list(result.items)

    @staticmethod
    def _news_digest_store_name(payload_kind: str) -> str:
        if payload_kind == "github_skill_digest":
            return "github-skill-digest"
        return "news-digest"

    def _news_store(self, store_name: str) -> NewsDigestStore:
        """构建带 PostgreSQL 读写后端的新闻简报状态存储。"""

        return NewsDigestStore(
            self.settings.data_dir / store_name,
            read_backend=self.state_read_repository,
            write_backend=self.state_write_repository,
        )

    def _run_diet_job(self, job: CronJob, payload: dict[str, Any], *, now: float) -> tuple[str, str]:
        """执行饮食专用 Cron，避免只靠 prompt 保证用户隔离和提醒跳过。"""

        if self.diet_store is None:
            raise RuntimeError("diet store is not configured")
        kind = str(payload.get("kind", ""))
        user_scope = str(payload.get("user_scope", "")).strip()
        if not user_scope:
            raise ValueError("diet cron payload requires user_scope")
        target_date = self._diet_payload_date(job, payload, now=now)
        idempotency_key = self._diet_idempotency_key(kind, user_scope, target_date, payload)

        if kind == "meal_reminder":
            meal_type = str(payload.get("meal_type", "") or "unknown").strip() or "unknown"
            stage = str(payload.get("stage", "") or "after").strip() or "after"
            if stage == "after" and self._diet_meal_exists(user_scope, target_date, meal_type):
                return f"{target_date} {self._diet_meal_label(meal_type)}已记录，跳过提醒。", "skipped"
            if not self._claim_diet_action(idempotency_key):
                return f"duplicate diet reminder skipped: {idempotency_key}", "skipped"
            return self._format_diet_reminder(user_scope, target_date, meal_type, stage), "ok"

        if not self._claim_diet_action(idempotency_key):
            return f"duplicate diet job skipped: {idempotency_key}", "skipped"
        if kind == "diet_plan_generate":
            plan = self.diet_store.generate_plan(user_scope, plan_date=target_date)
            return self._format_diet_plan(plan), "ok"
        if kind == "nutrition_day_summary":
            summary = self.diet_store.summarize_day(user_scope, date=target_date)
            return self._format_diet_summary(summary), "ok"
        raise RuntimeError(f"unsupported diet cron kind: {kind}")

    def _diet_payload_date(self, job: CronJob, payload: dict[str, Any], *, now: float) -> str:
        """解析饮食任务日期；未指定时按任务时区取当天。"""

        raw_date = str(payload.get("date") or payload.get("plan_date") or "").strip()
        if raw_date and raw_date != "today":
            return raw_date
        tz_name = str(payload.get("tz") or job.schedule_config.get("tz") or "Asia/Shanghai")
        try:
            zone = ZoneInfo(tz_name)
        except Exception:
            zone = timezone.utc
        return datetime.fromtimestamp(now, tz=zone).date().isoformat()

    def _diet_idempotency_key(
        self,
        kind: str,
        user_scope: str,
        target_date: str,
        payload: dict[str, Any],
    ) -> str:
        explicit = str(payload.get("idempotency_key", "")).strip()
        if explicit:
            return explicit.format(user_scope=user_scope, date=target_date)
        if kind == "diet_plan_generate":
            return f"diet-plan:{user_scope}:{target_date}"
        if kind == "nutrition_day_summary":
            return f"nutrition-summary:{user_scope}:{target_date}"
        meal_type = str(payload.get("meal_type", "") or "unknown").strip() or "unknown"
        stage = str(payload.get("stage", "") or "after").strip() or "after"
        return f"meal-reminder:{user_scope}:{target_date}:{meal_type}:{stage}"

    def _claim_diet_action(self, key: str) -> bool:
        """用 Redis 幂等键保护饮食主动任务；未启用 Redis 时退化为允许执行。"""

        if self.redis_client is None or not getattr(self.redis_client, "enabled", False):
            return True
        try:
            return bool(
                self.redis_client.mark_once(
                    f"gateway:diet:{key}",
                    ttl_seconds=3 * 86400,
                )
            )
        except Exception:
            return True

    def _diet_meal_exists(self, user_scope: str, target_date: str, meal_type: str) -> bool:
        meals = self.diet_store.list_meal_logs(user_scope, meal_date=target_date, limit=100)
        return any(str(row.get("meal_type", "")).strip() == meal_type for row in meals)

    @staticmethod
    def _diet_meal_label(meal_type: str) -> str:
        return {
            "breakfast": "早餐",
            "lunch": "午餐",
            "dinner": "晚餐",
            "snack": "加餐",
        }.get(meal_type, meal_type or "餐食")

    def _format_diet_reminder(
        self,
        user_scope: str,
        target_date: str,
        meal_type: str,
        stage: str,
    ) -> str:
        label = self._diet_meal_label(meal_type)
        if stage == "before":
            plan = self.diet_store.get_plan(user_scope, plan_date=target_date) or {}
            meals = plan.get("meals", {}) if isinstance(plan.get("meals"), dict) else {}
            options = meals.get(meal_type, []) if isinstance(meals, dict) else []
            option_text = "；".join(str(item) for item in list(options)[:2]) if options else "按今天计划选择清淡、足量蛋白质的一餐"
            return f"{label}提醒：{option_text}。吃完后可以直接回复实际吃了什么，我会帮你记录。"
        return f"{label}补录提醒：如果已经吃完，请回复“{label}吃了……”；我会记录并估算热量。"

    @staticmethod
    def _format_diet_plan(plan: dict[str, Any]) -> str:
        meals = plan.get("meals", {}) if isinstance(plan.get("meals"), dict) else {}

        def meal_line(key: str, label: str) -> str:
            options = meals.get(key, []) if isinstance(meals, dict) else []
            text = "；".join(str(item) for item in list(options)[:2]) if options else "暂无建议"
            return f"{label}：{text}"

        return "\n".join(
            [
                "今日饮食计划",
                f"日期：{plan.get('plan_date', '')}",
                f"目标热量：约 {float(plan.get('target_calories', 0) or 0):.0f} kcal",
                meal_line("breakfast", "早餐"),
                meal_line("lunch", "午餐"),
                meal_line("dinner", "晚餐"),
                meal_line("snack", "加餐"),
                f"准备建议：{plan.get('shopping_tips', '')}",
            ]
        )

    @staticmethod
    def _format_diet_summary(summary: dict[str, Any]) -> str:
        metadata = summary.get("metadata", {}) if isinstance(summary.get("metadata"), dict) else {}
        missing = metadata.get("missing_meals", [])
        missing_text = "、".join(str(item) for item in missing) if missing else "无"
        target = float(summary.get("target_calories", 0) or 0)
        actual = float(summary.get("actual_calories", 0) or 0)
        delta = actual - target
        delta_label = "高于" if delta > 0 else "低于"
        return "\n".join(
            [
                "今日饮食汇总",
                f"日期：{summary.get('date', '')}",
                f"摄入热量：约 {actual:.0f} kcal；目标约 {target:.0f} kcal，{delta_label}目标 {abs(delta):.0f} kcal",
                f"蛋白质/碳水/脂肪：{float(summary.get('protein_g', 0) or 0):.0f}g / {float(summary.get('carbs_g', 0) or 0):.0f}g / {float(summary.get('fat_g', 0) or 0):.0f}g",
                f"缺失餐次：{missing_text}",
                str(summary.get("summary_text", "") or "记录不足时，以上结果只作为粗略估算。"),
                "明天建议：优先保证蛋白质和蔬菜，主食按一拳左右控制。",
            ]
        )

    async def _run_github_skill_digest(
        self,
        job: CronJob,
        payload: dict[str, Any],
        *,
        correlation_id: str,
    ) -> tuple[str, str, list[NewsItem]]:
        """搜索 GitHub 热门 Skill 仓库，并交给目标 Agent 生成飞书摘要。"""

        lookback_hours = int(payload.get("lookback_hours", 24 * 7))
        max_items = int(payload.get("max_items", 8))
        per_source_max_items = int(payload.get("per_source_max_items", 8))
        skip_if_empty = bool(payload.get("skip_if_empty", True))
        sources_file = self.settings.workspace_root / str(
            payload.get("sources_file", "github-skill-sources.json")
        )
        store = self._news_store("github-skill-digest")
        collector = NewsCollector(
            sources_file,
            store,
            timeout_seconds=float(payload.get("timeout_seconds", 15.0)),
        )
        try:
            result = await asyncio.to_thread(
                collector.collect,
                lookback_hours=lookback_hours,
                max_items=max_items,
                per_source_max_items=per_source_max_items,
            )
        finally:
            collector.close()

        if not result.items and skip_if_empty:
            return "no fresh github skill items", "skipped", []

        prompt = build_github_skill_digest_prompt(
            result.items,
            lookback_hours=lookback_hours,
            max_output_items=max_items,
            errors=result.errors,
        )
        reply = await _dispatch_background_with_correlation(
            self.dispatcher,
            agent_id=job.target.agent_id,
            session_key=f"system:cron:{job.id}",
            prompt=prompt,
            channel="cron",
            mode="minimal",
            lane_name=f"cron:{job.target.agent_id}",
            correlation_id=correlation_id,
            disabled_tools=["memory_write"],
        )
        return reply.text, "ok", list(result.items)

    def _compute_next(self, job: CronJob, now: float) -> float:
        """根据任务类型计算下一次执行时间。"""

        schedule = job.schedule_config
        if job.schedule_kind == "at":
            try:
                timestamp = datetime.fromisoformat(schedule.get("at", "")).timestamp()
                return timestamp if timestamp > now else 0.0
            except (TypeError, ValueError, OSError):
                return 0.0

        if job.schedule_kind == "every":
            every_seconds = schedule.get("every_seconds", 3600)
            try:
                anchor = datetime.fromisoformat(schedule.get("anchor", "")).timestamp()
            except (TypeError, ValueError, OSError):
                anchor = now
            if now < anchor:
                return anchor
            steps = int((now - anchor) / every_seconds) + 1
            return anchor + steps * every_seconds

        if job.schedule_kind == "cron":
            expr = schedule.get("expr", "")
            tz_name = schedule.get("tz", "UTC")
            if not expr or croniter is None:
                return 0.0
            try:
                zone = ZoneInfo(tz_name)
            except Exception:
                zone = timezone.utc
            base = datetime.fromtimestamp(now, tz=zone)
            try:
                next_dt = croniter(expr, base).get_next(datetime)
            except Exception:
                return 0.0
            return next_dt.timestamp()

        return 0.0

    def _claim_scheduled_run(self, job: CronJob) -> bool:
        """抢占某个计划时间窗口，避免多实例重复触发同一 Cron。"""

        if self.redis_client is None or not getattr(self.redis_client, "enabled", False):
            return True
        try:
            return bool(
                self.redis_client.mark_once(
                    self._cron_idempotency_key(job),
                    ttl_seconds=self._cron_idempotency_ttl(job),
                )
            )
        except Exception:
            return True

    @staticmethod
    def _schedule_slot(job: CronJob) -> int:
        """返回当前计划触发窗口的稳定秒级 slot。"""

        return int(job.next_run_at or 0)

    def _cron_idempotency_key(self, job: CronJob) -> str:
        """生成 Redis Cron 幂等 key。"""

        safe_job_id = job.id.replace(" ", "_")
        return f"gateway:cron:{safe_job_id}:{self._schedule_slot(job)}"

    def _cron_idempotency_ttl(self, job: CronJob) -> int:
        """计算 Cron 幂等 key 的保留时间。"""

        if job.schedule_kind == "every":
            try:
                return max(60, int(job.schedule_config.get("every_seconds", 3600)) * 2)
            except (TypeError, ValueError):
                return 7200
        return 86400

    def _check_cron_rate_limit(self, now: float) -> dict[str, Any] | None:
        """检查 Cron 自动调度的跨实例限流。"""

        limit = int(getattr(self.settings, "redis_cron_rate_limit_per_minute", 0) or 0)
        if limit <= 0:
            return None
        if self.redis_client is None or not getattr(self.redis_client, "enabled", False):
            return None
        try:
            return self.redis_client.check_fixed_window_rate_limit(
                "gateway:rate:cron",
                limit=limit,
                window_seconds=60,
                now=now,
            ).to_dict()
        except Exception:
            return None

    def _target_from_row(self, row: dict[str, Any], *, owner_agent_id: str = "") -> ProactiveTarget:
        """从任务定义中恢复主动投递目标。"""

        target = row.get("target") or row.get("payload", {}).get("target") or {}
        return ProactiveTarget(
            channel=target.get("channel", self.default_target.channel),
            account_id=target.get("account_id", self.default_target.account_id),
            peer_id=target.get("peer_id", self.default_target.peer_id),
            agent_id=normalize_agent_id(target.get("agent_id", owner_agent_id or self.default_target.agent_id)),
        )

    def _record_cron_event(
        self,
        event_type: str,
        job: CronJob,
        *,
        status: str,
        message: str,
        correlation_id: str = "",
        error: str | Exception = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        if self.event_store is None:
            return
        try:
            self.event_store.record(
                event_type,
                status=status,
                component="cron",
                message=message,
                correlation_id=correlation_id,
                agent_id=job.target.agent_id,
                channel=job.target.channel,
                account_id=job.target.account_id,
                peer_id=job.target.peer_id,
                job_id=job.id,
                error=error,
                metadata={
                    "job_name": job.name,
                    "config_id": job.config_id,
                    "scope": job.scope,
                    "source_file": job.source_file,
                    "schedule_kind": job.schedule_kind,
                    **(metadata or {}),
                },
            )
        except Exception:
            pass


class AutonomyRuntime:
    """把 heartbeat 和 cron 组合成统一的主动任务运行时。"""

    def __init__(
        self,
        settings: GatewaySettings,
        dispatcher: GatewayDispatcher,
        channels: ChannelManager,
        event_store: RuntimeEventStore | None = None,
        redis_client: Any = None,
        task_queue: LocalTaskQueue | None = None,
        state_read_repository: Any = None,
        state_write_repository: Any = None,
        diet_store: Any = None,
    ) -> None:
        target = ProactiveTarget(
            channel=settings.proactive_channel,
            account_id=settings.proactive_account_id,
            peer_id=settings.proactive_peer_id,
            agent_id=settings.proactive_agent_id,
        )
        self.heartbeat = HeartbeatService(
            settings,
            dispatcher,
            channels,
            target,
            event_store=event_store,
        )
        self.cron = CronService(
            settings,
            dispatcher,
            channels,
            target,
            event_store=event_store,
            redis_client=redis_client,
            task_queue=task_queue,
            state_read_repository=state_read_repository,
            state_write_repository=state_write_repository,
            diet_store=diet_store,
        )

    def set_channels(self, channels: ChannelManager) -> None:
        """在通道重建后同步更新 heartbeat 和 cron 的发送出口。"""

        self.heartbeat.set_channels(channels)
        self.cron.set_channels(channels)

    async def start(self) -> None:
        """启动主动任务子系统。"""

        await self.heartbeat.start()
        await self.cron.start()

    async def stop(self) -> None:
        """停止主动任务子系统。"""

        await self.heartbeat.stop()
        await self.cron.stop()
