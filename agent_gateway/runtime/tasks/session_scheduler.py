from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Iterable

from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.tasks.models import TaskInstance


@dataclass(frozen=True, slots=True)
class SessionTaskClaim:
    """一次通过 session 调度器拿到的队首任务声明。"""

    task_id: str
    session_key: str
    owner_value: str
    busy_key: str
    pending_key: str
    ttl_seconds: int


@dataclass(frozen=True, slots=True)
class SessionSchedulerSnapshot:
    """Session 调度器的轻量可观测快照。"""

    ready_count: int
    namespace: str
    enabled: bool
    ready_sessions: tuple[str, ...] = ()
    pending_buckets: tuple[dict[str, Any], ...] = ()
    busy_owners: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """返回可被控制面或 Dashboard 展示的字典。"""

        return {
            "enabled": self.enabled,
            "namespace": self.namespace,
            "ready_count": self.ready_count,
            "ready_sessions": list(self.ready_sessions),
            "pending_buckets": list(self.pending_buckets),
            "busy_owners": list(self.busy_owners),
        }


class RedisSessionReadyScheduler:
    """基于 Redis 的 per-session FIFO 调度索引。

    PostgreSQL / TaskStore 仍是任务事实状态；Redis 只保存热路径调度索引：
    每个 session 一个 pending bucket，全局 ready index 只保存“可运行 session”。
    """

    CLAIM_SCRIPT = """
    local ready_key = KEYS[1]
    local namespace = ARGV[1]
    local worker_id = ARGV[2]
    local ttl_seconds = tonumber(ARGV[3])
    local now = ARGV[4]
    local max_scan = tonumber(ARGV[5])
    local allowed_raw = ARGV[6]

    local allowed = {}
    if allowed_raw ~= "" then
      for item in string.gmatch(allowed_raw, "([^,]+)") do
        allowed[item] = true
      end
    end

    for _ = 1, max_scan do
      local session_key = redis.call("LPOP", ready_key)
      if not session_key then
        return nil
      end
      local pending_key = namespace .. ":session:" .. session_key .. ":pending"
      local busy_key = namespace .. ":session:" .. session_key .. ":busy"
      if redis.call("EXISTS", busy_key) == 1 then
        if redis.call("LLEN", pending_key) > 0 then
          redis.call("RPUSH", ready_key, session_key)
        end
      else
        local raw = redis.call("LINDEX", pending_key, 0)
        if raw then
          local task_id = raw
          local task_type = ""
          local sep = string.find(raw, "|", 1, true)
          if sep then
            task_id = string.sub(raw, 1, sep - 1)
            task_type = string.sub(raw, sep + 1)
          end
          if allowed_raw == "" or allowed[task_type] then
            redis.call("LPOP", pending_key)
            local owner_value = cjson.encode({
              version = 1,
              worker_id = worker_id,
              task_id = task_id,
              session_key = session_key,
              acquired_at = tonumber(now),
              renewed_at = tonumber(now)
            })
            redis.call("SET", busy_key, owner_value, "EX", ttl_seconds)
            return {task_id, session_key, owner_value, busy_key, pending_key}
          else
            redis.call("RPUSH", ready_key, session_key)
          end
        end
      end
    end
    return nil
    """

    RELEASE_SCRIPT = """
    local ready_key = KEYS[1]
    local busy_key = KEYS[2]
    local pending_key = KEYS[3]
    local session_key = ARGV[1]
    local owner_value = ARGV[2]
    if redis.call("GET", busy_key) ~= owner_value then
      return 0
    end
    redis.call("DEL", busy_key)
    if redis.call("LLEN", pending_key) > 0 then
      redis.call("RPUSH", ready_key, session_key)
    end
    return 1
    """

    RENEW_SCRIPT = """
    local busy_key = KEYS[1]
    local owner_value = ARGV[1]
    local ttl_seconds = tonumber(ARGV[2])
    if redis.call("GET", busy_key) ~= owner_value then
      return 0
    end
    return redis.call("EXPIRE", busy_key, ttl_seconds)
    """

    def __init__(
        self,
        redis_client: RedisClient | None,
        *,
        namespace: str = "gateway:tasks",
        default_ttl_seconds: int = 120,
    ) -> None:
        self.redis_client = redis_client
        self.namespace = namespace.strip(":") or "gateway:tasks"
        self.default_ttl_seconds = max(1, int(default_ttl_seconds))

    @property
    def enabled(self) -> bool:
        """Redis 调度索引是否启用。"""

        return bool(self.redis_client is not None and self.redis_client.enabled)

    @property
    def ready_key(self) -> str:
        """全局 ready session index key。"""

        return f"{self.namespace}:sessions:ready"

    def pending_key(self, session_key: str) -> str:
        """生成指定 session 的 pending bucket key。"""

        return f"{self.namespace}:session:{session_key}:pending"

    def busy_key(self, session_key: str) -> str:
        """生成指定 session 的 busy owner key。"""

        return f"{self.namespace}:session:{session_key}:busy"

    def enqueue(self, task: TaskInstance) -> bool:
        """把任务写入 session pending bucket，并把 session 放入 ready index。"""

        if not self.enabled or not task.session_key:
            return False
        item = self._encode_task_ref(task)
        client = self.redis_client._get_client()
        pending_key = self.pending_key(task.session_key)
        existing = client.lrange(pending_key, 0, -1)
        if item not in set(str(value) for value in existing):
            client.rpush(pending_key, item)
        ready_items = set(str(value) for value in client.lrange(self.ready_key, 0, -1))
        if task.session_key not in ready_items and not client.exists(self.busy_key(task.session_key)):
            client.rpush(self.ready_key, task.session_key)
        return True

    def claim_next(
        self,
        *,
        worker_id: str,
        task_types: Iterable[str] | None = None,
        ttl_seconds: int | None = None,
        max_scan: int = 128,
        now: float | None = None,
    ) -> SessionTaskClaim | None:
        """原子声明下一个可执行 session 的队首任务。"""

        if not self.enabled:
            return None
        allowed = ",".join(sorted(str(item) for item in (task_types or []) if str(item)))
        ttl = max(1, int(ttl_seconds or self.default_ttl_seconds))
        current = time.time() if now is None else float(now)
        result = self.redis_client._get_client().eval(
            self.CLAIM_SCRIPT,
            1,
            self.ready_key,
            self.namespace,
            worker_id,
            str(ttl),
            str(current),
            str(max(1, int(max_scan))),
            allowed,
        )
        if not result:
            return None
        task_id, session_key, owner_value, busy_key, pending_key = [str(item) for item in result]
        return SessionTaskClaim(
            task_id=task_id,
            session_key=session_key,
            owner_value=owner_value,
            busy_key=busy_key,
            pending_key=pending_key,
            ttl_seconds=ttl,
        )

    def release(self, claim: SessionTaskClaim) -> bool:
        """释放 session busy owner；若 bucket 仍有任务则把 session 放回 ready index。"""

        if not self.enabled:
            return True
        return bool(
            self.redis_client._get_client().eval(
                self.RELEASE_SCRIPT,
                3,
                self.ready_key,
                claim.busy_key,
                claim.pending_key,
                claim.session_key,
                claim.owner_value,
            )
        )

    def renew(self, claim: SessionTaskClaim, *, ttl_seconds: int | None = None) -> bool:
        """续租 busy owner，避免长模型调用期间锁自然过期。"""

        if not self.enabled:
            return True
        ttl = max(1, int(ttl_seconds or claim.ttl_seconds or self.default_ttl_seconds))
        return bool(
            self.redis_client._get_client().eval(
                self.RENEW_SCRIPT,
                1,
                claim.busy_key,
                claim.owner_value,
                str(ttl),
            )
        )

    def rebuild(self, tasks: Iterable[TaskInstance]) -> int:
        """从事实任务状态重建 Redis pending bucket 与 ready index。"""

        if not self.enabled:
            return 0
        client = self.redis_client._get_client()
        sessions: dict[str, list[TaskInstance]] = {}
        for task in tasks:
            if task.status not in {"pending", "retrying"} or not task.session_key:
                continue
            sessions.setdefault(task.session_key, []).append(task)
        client.delete(self.ready_key)
        rebuilt = 0
        for session_key, items in sessions.items():
            pending_key = self.pending_key(session_key)
            client.delete(pending_key)
            for task in sorted(items, key=lambda item: (item.priority, item.created_at, item.id)):
                client.rpush(pending_key, self._encode_task_ref(task))
                rebuilt += 1
            if not client.exists(self.busy_key(session_key)):
                client.rpush(self.ready_key, session_key)
        return rebuilt

    def snapshot(self, *, detail: bool = False, limit: int = 20) -> SessionSchedulerSnapshot:
        """返回调度器状态；detail=True 时包含 ready/pending/busy 样例。"""

        if not self.enabled:
            return SessionSchedulerSnapshot(
                ready_count=0,
                namespace=self.namespace,
                enabled=False,
            )
        client = self.redis_client._get_client()
        ready_sessions = tuple(str(item) for item in client.lrange(self.ready_key, 0, -1))
        if not detail:
            return SessionSchedulerSnapshot(
                ready_count=int(client.llen(self.ready_key)),
                namespace=self.namespace,
                enabled=True,
            )
        safe_limit = max(1, int(limit))
        pending_buckets: list[dict[str, Any]] = []
        busy_owners: list[dict[str, Any]] = []
        seen_sessions = list(dict.fromkeys(ready_sessions))
        for key in self._scan_keys(f"{self.namespace}:session:*:pending", limit=safe_limit):
            session_key = self._session_from_key(key, suffix=":pending")
            if session_key and session_key not in seen_sessions:
                seen_sessions.append(session_key)
        for session_key in seen_sessions[:safe_limit]:
            pending_key = self.pending_key(session_key)
            pending_items = [str(item) for item in client.lrange(pending_key, 0, -1)]
            if pending_items:
                pending_buckets.append(
                    {
                        "session_key": session_key,
                        "key": pending_key,
                        "count": len(pending_items),
                        "items": pending_items[:safe_limit],
                    }
                )
            busy_key = self.busy_key(session_key)
            raw_owner = str(client.get(busy_key) or "")
            if raw_owner:
                owner = decode_session_owner(raw_owner)
                busy_owners.append(
                    {
                        "session_key": session_key,
                        "key": busy_key,
                        "owner": owner,
                        "raw": raw_owner,
                    }
                )
        return SessionSchedulerSnapshot(
            ready_count=int(client.llen(self.ready_key)),
            namespace=self.namespace,
            enabled=True,
            ready_sessions=ready_sessions[:safe_limit],
            pending_buckets=tuple(pending_buckets),
            busy_owners=tuple(busy_owners),
        )

    def _scan_keys(self, pattern: str, *, limit: int) -> list[str]:
        """扫描少量 Redis key 用于观测，失败时返回空列表。"""

        try:
            client = self.redis_client._get_client()
            keys: list[str] = []
            for key in client.scan_iter(match=pattern, count=max(1, int(limit))):
                keys.append(str(key))
                if len(keys) >= limit:
                    break
            return keys
        except Exception:
            return []

    def _session_from_key(self, key: str, *, suffix: str) -> str:
        """从 scheduler key 反推出 session_key。"""

        prefix = f"{self.namespace}:session:"
        if not key.startswith(prefix) or not key.endswith(suffix):
            return ""
        return key[len(prefix) : -len(suffix)]

    def _encode_task_ref(self, task: TaskInstance) -> str:
        """编码 pending bucket 中的轻量任务引用。"""

        task_type = task.task_type.replace("|", "")
        return f"{task.id}|{task_type}"


def decode_session_owner(value: str) -> dict[str, Any]:
    """解码 scheduler busy owner value，便于测试和观测复用。"""

    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}
