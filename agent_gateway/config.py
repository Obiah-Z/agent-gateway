from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from agent_gateway.runtime.execution.roles import parse_runtime_roles

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional during bootstrap
    def load_dotenv(*args: object, **kwargs: object) -> bool:
        return False


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKSPACE_ROOT = PACKAGE_ROOT / "workspace"
DEFAULT_DATA_DIR = PACKAGE_ROOT / "data"


def env_bool(name: str, default: bool = False) -> bool:
    """把环境变量解析为布尔值。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_csv_tuple(raw_value: str) -> tuple[str, ...]:
    """解析逗号分隔的配置项列表。"""

    return tuple(item.strip() for item in raw_value.split(",") if item.strip())


def load_env(env_file: Path | None = None) -> None:
    """从 `.env` 文件加载环境变量。"""

    target = (env_file.expanduser().resolve() if env_file else PACKAGE_ROOT / ".env")
    if target.exists():
        load_dotenv(target, override=True)


def resolve_env_path(raw_value: str, default: Path) -> Path:
    """把环境变量中的路径解析为绝对路径。"""

    candidate = Path(raw_value).expanduser() if raw_value else default
    if candidate.is_absolute():
        return candidate.resolve()
    return (PACKAGE_ROOT / candidate).resolve()


@dataclass(slots=True)
class GatewaySettings:
    """网关运行时总配置。"""

    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    model_id: str = "deepseek-v4-pro"
    runtime_roles: tuple[str, ...] = ("all",)
    redis_enabled: bool = False
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_socket_timeout_seconds: float = 1.0
    redis_cron_rate_limit_per_minute: int = 0
    host: str = "127.0.0.1"
    port: int = 8765
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT
    data_dir: Path = DEFAULT_DATA_DIR
    config_dir: Path = PACKAGE_ROOT / "config"
    default_agent_id: str = "main"
    default_agent_name: str = "GatewayMain"
    max_iterations: int = 12
    max_tokens: int = 4096
    tool_timeout_seconds: int = 30
    max_tool_output_chars: int = 50_000
    context_safe_limit: int = 180_000
    max_overflow_compaction: int = 3
    fallback_models: tuple[str, ...] = ()
    inbound_max_concurrent_lanes: int = 4
    inbound_max_queue_size: int = 200
    inbound_max_lane_queue_size: int = 20
    inbound_long_task_notice_seconds: float = 15.0
    background_inbound_commands: tuple[str, ...] = ("/github-repo-analyzer", "/space-advisor")
    heartbeat_interval_seconds: float = 1800.0
    heartbeat_active_start: int = 9
    heartbeat_active_end: int = 22
    proactive_channel: str = "cli"
    proactive_account_id: str = "cli-local"
    proactive_peer_id: str = "cli-user"
    proactive_agent_id: str = "main"
    alert_channel: str = ""
    alert_account_id: str = ""
    alert_peer_id: str = ""
    alert_agent_id: str = "main"
    feishu_webhook_host: str = "127.0.0.1"
    feishu_webhook_port: int = 8766
    feishu_webhook_path: str = "/webhooks/feishu"
    feishu_signature_window_seconds: int = 300
    feishu_event_dedup_ttl_seconds: int = 86400
    feishu_onboarding_bot_link: str = ""
    feishu_onboarding_auto_bind_first_message: bool = True
    feishu_onboarding_auto_bind_bot_added: bool = False
    dashboard_enabled: bool = True
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8780
    dashboard_refresh_interval_seconds: int = 15
    web_search_enabled: bool = False
    web_search_provider: str = "tavily"
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    web_search_timeout_seconds: float = 15.0
    web_search_max_results: int = 5
    web_search_max_output_chars: int = 12_000
    events_retention_days: int = 14
    metrics_retention_days: int = 14
    metrics_interval_seconds: float = 60.0
    alerts_retention_days: int = 14
    alerts_interval_seconds: float = 60.0

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def delivery_queue_dir(self) -> Path:
        return self.data_dir / "delivery-queue"

    @property
    def feishu_webhook_dir(self) -> Path:
        return self.data_dir / "feishu-webhook"

    @property
    def events_dir(self) -> Path:
        return self.data_dir / "events"

    @property
    def metrics_dir(self) -> Path:
        return self.data_dir / "metrics"

    @property
    def alerts_dir(self) -> Path:
        return self.data_dir / "alerts"

    @property
    def tasks_dir(self) -> Path:
        return self.data_dir / "tasks"

    @property
    def agents_config_file(self) -> Path:
        return self.config_dir / "agents.json"

    @property
    def bindings_config_file(self) -> Path:
        return self.config_dir / "bindings.json"

    @property
    def profiles_config_file(self) -> Path:
        return self.config_dir / "profiles.json"

    @property
    def channels_config_file(self) -> Path:
        return self.config_dir / "channels.json"

    @classmethod
    def from_env(cls) -> "GatewaySettings":
        """从当前环境变量构造配置对象。"""

        fallback_models = parse_csv_tuple(os.getenv("GATEWAY_FALLBACK_MODELS", ""))
        return cls(
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            anthropic_base_url=os.getenv("ANTHROPIC_BASE_URL", ""),
            model_id=os.getenv("MODEL_ID", "claude-opus-4-6"),
            runtime_roles=parse_runtime_roles(os.getenv("GATEWAY_RUNTIME_ROLES", "all")),
            redis_enabled=env_bool("GATEWAY_REDIS_ENABLED", False),
            redis_url=os.getenv("GATEWAY_REDIS_URL", "redis://127.0.0.1:6379/0"),
            redis_socket_timeout_seconds=max(
                0.05,
                float(os.getenv("GATEWAY_REDIS_SOCKET_TIMEOUT_SECONDS", "1.0")),
            ),
            redis_cron_rate_limit_per_minute=max(
                0,
                int(os.getenv("GATEWAY_REDIS_CRON_RATE_LIMIT_PER_MINUTE", "0")),
            ),
            host=os.getenv("GATEWAY_HOST", "127.0.0.1"),
            port=int(os.getenv("GATEWAY_PORT", "8765")),
            workspace_root=resolve_env_path(
                os.getenv("GATEWAY_WORKSPACE_ROOT", str(DEFAULT_WORKSPACE_ROOT)),
                DEFAULT_WORKSPACE_ROOT,
            ),
            data_dir=resolve_env_path(
                os.getenv("GATEWAY_DATA_DIR", str(DEFAULT_DATA_DIR)),
                DEFAULT_DATA_DIR,
            ),
            config_dir=resolve_env_path(
                os.getenv("GATEWAY_CONFIG_DIR", str(PACKAGE_ROOT / "config")),
                PACKAGE_ROOT / "config",
            ),
            default_agent_id=os.getenv("GATEWAY_DEFAULT_AGENT_ID", "main"),
            default_agent_name=os.getenv("GATEWAY_DEFAULT_AGENT_NAME", "GatewayMain"),
            max_iterations=int(os.getenv("GATEWAY_MAX_ITERATIONS", "12")),
            max_tokens=int(os.getenv("GATEWAY_MAX_TOKENS", "4096")),
            tool_timeout_seconds=int(os.getenv("GATEWAY_TOOL_TIMEOUT_SECONDS", "30")),
            max_tool_output_chars=int(os.getenv("GATEWAY_MAX_TOOL_OUTPUT_CHARS", "50000")),
            context_safe_limit=int(os.getenv("GATEWAY_CONTEXT_SAFE_LIMIT", "180000")),
            max_overflow_compaction=int(os.getenv("GATEWAY_MAX_OVERFLOW_COMPACTION", "3")),
            fallback_models=fallback_models,
            inbound_max_concurrent_lanes=max(
                1,
                int(os.getenv("GATEWAY_INBOUND_MAX_CONCURRENT_LANES", "4")),
            ),
            inbound_max_queue_size=max(1, int(os.getenv("GATEWAY_INBOUND_MAX_QUEUE_SIZE", "200"))),
            inbound_max_lane_queue_size=max(
                1,
                int(os.getenv("GATEWAY_INBOUND_MAX_LANE_QUEUE_SIZE", "20")),
            ),
            inbound_long_task_notice_seconds=max(
                0.0,
                float(os.getenv("GATEWAY_INBOUND_LONG_TASK_NOTICE_SECONDS", "15")),
            ),
            background_inbound_commands=parse_csv_tuple(
                os.getenv(
                    "GATEWAY_BACKGROUND_INBOUND_COMMANDS",
                    "/github-repo-analyzer,/space-advisor",
                )
            ),
            heartbeat_interval_seconds=float(os.getenv("HEARTBEAT_INTERVAL_SECONDS", "1800")),
            heartbeat_active_start=int(os.getenv("HEARTBEAT_ACTIVE_START", "9")),
            heartbeat_active_end=int(os.getenv("HEARTBEAT_ACTIVE_END", "22")),
            proactive_channel=os.getenv("GATEWAY_PROACTIVE_CHANNEL", "cli"),
            proactive_account_id=os.getenv("GATEWAY_PROACTIVE_ACCOUNT_ID", "cli-local"),
            proactive_peer_id=os.getenv("GATEWAY_PROACTIVE_PEER_ID", "cli-user"),
            proactive_agent_id=os.getenv("GATEWAY_PROACTIVE_AGENT_ID", "main"),
            alert_channel=os.getenv("GATEWAY_ALERT_CHANNEL", "").strip(),
            alert_account_id=os.getenv("GATEWAY_ALERT_ACCOUNT_ID", "").strip(),
            alert_peer_id=os.getenv("GATEWAY_ALERT_PEER_ID", "").strip(),
            alert_agent_id=os.getenv("GATEWAY_ALERT_AGENT_ID", "main").strip() or "main",
            feishu_webhook_host=os.getenv("FEISHU_WEBHOOK_HOST", "127.0.0.1"),
            feishu_webhook_port=int(os.getenv("FEISHU_WEBHOOK_PORT", "8766")),
            feishu_webhook_path=os.getenv("FEISHU_WEBHOOK_PATH", "/webhooks/feishu"),
            feishu_signature_window_seconds=int(
                os.getenv("FEISHU_SIGNATURE_WINDOW_SECONDS", "300")
            ),
            feishu_event_dedup_ttl_seconds=int(
                os.getenv("FEISHU_EVENT_DEDUP_TTL_SECONDS", "86400")
            ),
            feishu_onboarding_bot_link=os.getenv("FEISHU_ONBOARDING_BOT_LINK", "").strip(),
            feishu_onboarding_auto_bind_first_message=env_bool(
                "FEISHU_ONBOARDING_AUTO_BIND_FIRST_MESSAGE",
                True,
            ),
            feishu_onboarding_auto_bind_bot_added=env_bool(
                "FEISHU_ONBOARDING_AUTO_BIND_BOT_ADDED",
                False,
            ),
            dashboard_enabled=env_bool("GATEWAY_DASHBOARD_ENABLED", True),
            dashboard_host=os.getenv("GATEWAY_DASHBOARD_HOST", "127.0.0.1"),
            dashboard_port=int(os.getenv("GATEWAY_DASHBOARD_PORT", "8780")),
            dashboard_refresh_interval_seconds=int(
                os.getenv("GATEWAY_DASHBOARD_REFRESH_INTERVAL_SECONDS", "15")
            ),
            web_search_enabled=env_bool("GATEWAY_WEB_SEARCH_ENABLED", False),
            web_search_provider=os.getenv("GATEWAY_WEB_SEARCH_PROVIDER", "tavily"),
            tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
            tavily_base_url=os.getenv("TAVILY_BASE_URL", "https://api.tavily.com").rstrip("/"),
            web_search_timeout_seconds=float(
                os.getenv("GATEWAY_WEB_SEARCH_TIMEOUT_SECONDS", "15")
            ),
            web_search_max_results=int(os.getenv("GATEWAY_WEB_SEARCH_MAX_RESULTS", "5")),
            web_search_max_output_chars=int(
                os.getenv("GATEWAY_WEB_SEARCH_MAX_OUTPUT_CHARS", "12000")
            ),
            events_retention_days=int(os.getenv("GATEWAY_EVENTS_RETENTION_DAYS", "14")),
            metrics_retention_days=int(os.getenv("GATEWAY_METRICS_RETENTION_DAYS", "14")),
            metrics_interval_seconds=float(os.getenv("GATEWAY_METRICS_INTERVAL_SECONDS", "60")),
            alerts_retention_days=int(os.getenv("GATEWAY_ALERTS_RETENTION_DAYS", "14")),
            alerts_interval_seconds=float(os.getenv("GATEWAY_ALERTS_INTERVAL_SECONDS", "60")),
        )

    def ensure_directories(self) -> None:
        """确保运行所需目录存在。"""

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.delivery_queue_dir.mkdir(parents=True, exist_ok=True)
        self.feishu_webhook_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self.alerts_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
