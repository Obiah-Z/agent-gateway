from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

try:
    from croniter import croniter
except ImportError:  # pragma: no cover
    croniter = None  # type: ignore[assignment]

from agent_gateway.channels.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.models import ProactiveTarget
from agent_gateway.runtime.dispatcher import GatewayDispatcher


DEFAULT_CRON_DISABLE_THRESHOLD = 5


@dataclass(slots=True)
class CronJob:
    id: str
    name: str
    enabled: bool
    schedule_kind: str
    schedule_config: dict[str, Any]
    payload: dict[str, Any]
    target: ProactiveTarget
    delete_after_run: bool = False
    consecutive_errors: int = 0
    last_run_at: float = 0.0
    next_run_at: float = 0.0


class HeartbeatService:
    def __init__(
        self,
        settings: GatewaySettings,
        dispatcher: GatewayDispatcher,
        channels: ChannelManager,
        default_target: ProactiveTarget,
    ) -> None:
        self.settings = settings
        self.dispatcher = dispatcher
        self.channels = channels
        self.default_target = default_target
        self.heartbeat_path = settings.workspace_root / "HEARTBEAT.md"
        self.interval = settings.heartbeat_interval_seconds
        self.active_hours = (settings.heartbeat_active_start, settings.heartbeat_active_end)
        self.last_run_at = 0.0
        self.running = False
        self._last_output = ""
        self._task: asyncio.Task[None] | None = None
        self._stopped = False

    def set_channels(self, channels: ChannelManager) -> None:
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
        return await self._execute(force=True)

    def should_run(self) -> tuple[bool, str]:
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
                    await self._execute(force=False)
            except Exception:
                pass
            await asyncio.sleep(1.0)

    async def _execute(self, *, force: bool) -> str:
        if not force:
            ok, reason = self.should_run()
            if not ok:
                return reason

        instructions = self.heartbeat_path.read_text(encoding="utf-8").strip()
        if not instructions:
            return "HEARTBEAT.md is empty"

        self.running = True
        try:
            reply = await self.dispatcher.dispatch_background(
                agent_id=self.default_target.agent_id,
                session_key=f"system:heartbeat:{self.default_target.agent_id}",
                prompt=instructions,
                channel="heartbeat",
                mode="minimal",
                lane_name="heartbeat",
            )
            meaningful = self._parse_response(reply.text)
            self.last_run_at = time.time()
            if meaningful is None:
                return "HEARTBEAT_OK (nothing to report)"
            if meaningful.strip() == self._last_output:
                return "duplicate content (skipped)"
            self._last_output = meaningful.strip()
            await self.dispatcher.deliver_text(
                self.channels,
                self.default_target,
                meaningful,
                metadata={"kind": "heartbeat"},
            )
            return f"heartbeat delivered ({len(meaningful)} chars)"
        finally:
            self.running = False

    def _parse_response(self, response: str) -> str | None:
        if "HEARTBEAT_OK" in response:
            stripped = response.replace("HEARTBEAT_OK", "").strip()
            return stripped if len(stripped) > 5 else None
        return response.strip() or None

    def _has_foreground_activity(self) -> bool:
        stats = self.dispatcher.command_queue.stats()
        for lane_name, lane_stats in stats.items():
            if lane_name.startswith("system:") or lane_name in {"heartbeat", "cron"}:
                continue
            if lane_stats.get("active", 0) > 0 or lane_stats.get("queue_depth", 0) > 0:
                return True
        return False


class CronService:
    def __init__(
        self,
        settings: GatewaySettings,
        dispatcher: GatewayDispatcher,
        channels: ChannelManager,
        default_target: ProactiveTarget,
    ) -> None:
        self.settings = settings
        self.dispatcher = dispatcher
        self.channels = channels
        self.default_target = default_target
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

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="cron-runtime")

    async def stop(self) -> None:
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def load_jobs(self) -> None:
        self.jobs.clear()
        if not self.cron_file.exists():
            return
        try:
            payload = json.loads(self.cron_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        now = time.time()
        for row in payload.get("jobs", []):
            schedule = row.get("schedule", {})
            kind = schedule.get("kind", "")
            if kind not in {"at", "every", "cron"}:
                continue
            job = CronJob(
                id=row.get("id", ""),
                name=row.get("name", ""),
                enabled=row.get("enabled", True),
                schedule_kind=kind,
                schedule_config=schedule,
                payload=row.get("payload", {}),
                target=self._target_from_row(row),
                delete_after_run=row.get("delete_after_run", False),
            )
            job.next_run_at = self._compute_next(job, now)
            self.jobs.append(job)

    def list_jobs(self) -> list[dict[str, Any]]:
        now = time.time()
        result = []
        for job in self.jobs:
            next_in = max(0.0, job.next_run_at - now) if job.next_run_at > 0 else None
            result.append(
                {
                    "id": job.id,
                    "name": job.name,
                    "enabled": job.enabled,
                    "kind": job.schedule_kind,
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
        for job in self.jobs:
            if job.id == job_id:
                await self._run_job(job, time.time())
                return f"'{job.name}' triggered (errors={job.consecutive_errors})"
        return f"Job '{job_id}' not found"

    async def _loop(self) -> None:
        while not self._stopped:
            try:
                await self.tick()
            except Exception:
                pass
            await asyncio.sleep(1.0)

    async def tick(self) -> None:
        now = time.time()
        remove_ids: list[str] = []
        for job in self.jobs:
            if not job.enabled or job.next_run_at <= 0 or now < job.next_run_at:
                continue
            await self._run_job(job, now)
            if job.delete_after_run and job.schedule_kind == "at":
                remove_ids.append(job.id)
        if remove_ids:
            self.jobs = [job for job in self.jobs if job.id not in remove_ids]

    async def _run_job(self, job: CronJob, now: float) -> None:
        payload = job.payload
        kind = payload.get("kind", "")
        output, status, error = "", "ok", ""
        try:
            if kind == "agent_turn":
                message = payload.get("message", "")
                if not message:
                    output, status = "[empty message]", "skipped"
                else:
                    reply = await self.dispatcher.dispatch_background(
                        agent_id=job.target.agent_id,
                        session_key=f"system:cron:{job.id}",
                        prompt=message,
                        channel="cron",
                        mode="minimal",
                        lane_name="cron",
                    )
                    output = reply.text
            elif kind == "system_event":
                output = payload.get("text", "")
                if not output:
                    status = "skipped"
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
            "run_at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "status": status,
            "output_preview": output[:200],
        }
        if error:
            entry["error"] = error
        try:
            with self.run_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

        if output and status != "skipped":
            await self.dispatcher.deliver_text(
                self.channels,
                job.target,
                f"[{job.name}] {output}",
                metadata={"kind": "cron", "job_id": job.id},
            )

    def _compute_next(self, job: CronJob, now: float) -> float:
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

    def _target_from_row(self, row: dict[str, Any]) -> ProactiveTarget:
        target = row.get("target") or row.get("payload", {}).get("target") or {}
        return ProactiveTarget(
            channel=target.get("channel", self.default_target.channel),
            account_id=target.get("account_id", self.default_target.account_id),
            peer_id=target.get("peer_id", self.default_target.peer_id),
            agent_id=target.get("agent_id", self.default_target.agent_id),
        )


class AutonomyRuntime:
    def __init__(
        self,
        settings: GatewaySettings,
        dispatcher: GatewayDispatcher,
        channels: ChannelManager,
    ) -> None:
        target = ProactiveTarget(
            channel=settings.proactive_channel,
            account_id=settings.proactive_account_id,
            peer_id=settings.proactive_peer_id,
            agent_id=settings.proactive_agent_id,
        )
        self.heartbeat = HeartbeatService(settings, dispatcher, channels, target)
        self.cron = CronService(settings, dispatcher, channels, target)

    def set_channels(self, channels: ChannelManager) -> None:
        self.heartbeat.set_channels(channels)
        self.cron.set_channels(channels)

    async def start(self) -> None:
        await self.heartbeat.start()
        await self.cron.start()

    async def stop(self) -> None:
        await self.heartbeat.stop()
        await self.cron.stop()
