from __future__ import annotations

from dataclasses import dataclass
import shutil
import subprocess
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class PostgresHealth:
    """PostgreSQL 健康检查结果。"""

    enabled: bool
    ok: bool
    url: str
    latency_ms: float | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "ok": self.ok,
            "url": self.url,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


class PostgresClient:
    """轻量 PostgreSQL 适配层。

    当前阶段不引入 Python 驱动，先用本机 `psql`/`pg_isready` 做连接探测和健康检查，
    方便在已有 PostgreSQL 环境中提前接入网关配置与运维状态。
    """

    def __init__(
        self,
        *,
        enabled: bool,
        url: str,
        connect_timeout_seconds: float = 2.0,
    ) -> None:
        self.enabled = enabled
        self.url = url
        self.connect_timeout_seconds = max(0.2, connect_timeout_seconds)

    def health(self) -> PostgresHealth:
        """执行一次 PostgreSQL 连通性检查。"""

        if not self.enabled:
            return PostgresHealth(enabled=False, ok=True, url=self.url)
        try:
            start = time.perf_counter()
            self._run_command(["pg_isready", "-d", self.url])
            latency_ms = (time.perf_counter() - start) * 1000.0
            return PostgresHealth(
                enabled=True,
                ok=True,
                url=self.url,
                latency_ms=round(latency_ms, 3),
            )
        except Exception as exc:
            return PostgresHealth(enabled=True, ok=False, url=self.url, error=str(exc))

    def _run_command(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        """运行 PostgreSQL 命令行工具。"""

        if shutil.which(args[0]) is None:
            raise RuntimeError(f"{args[0]} is not installed")
        return subprocess.run(
            args,
            check=True,
            text=True,
            capture_output=True,
            timeout=self.connect_timeout_seconds,
        )
