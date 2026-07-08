from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any

from agent_gateway.config import GatewaySettings, PACKAGE_ROOT
from agent_gateway.runtime.infra.postgres_client import PostgresClient
from agent_gateway.runtime.infra.rabbitmq import RabbitMQDeliveryBroker
from agent_gateway.runtime.infra.redis_client import RedisClient
from agent_gateway.runtime.state.postgres import check_postgres_schema


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One startup diagnostic check."""

    status: str
    name: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "name": self.name,
            "message": self.message,
            "detail": self.detail,
        }


def run_doctor(settings: GatewaySettings, *, env_file: Path | None = None) -> dict[str, Any]:
    """Run lightweight startup diagnostics without building the full app."""

    checks: list[DoctorCheck] = []
    checks.append(_check_env_file(env_file))
    checks.extend(_check_required_model_settings(settings))
    checks.extend(_check_paths(settings))
    checks.extend(_check_redis(settings))
    checks.extend(_check_postgres(settings))
    checks.extend(_check_rabbitmq(settings))
    checks.extend(_check_agent_contracts(settings))
    checks.extend(_check_security_bindings(settings))
    summary = _summary(checks)
    return {
        "ok": summary["fail"] == 0,
        "summary": summary,
        "checks": [check.to_dict() for check in checks],
    }


def render_doctor_text(report: dict[str, Any]) -> str:
    """Render diagnostics as a compact human-readable report."""

    lines = ["AI Agent Gateway doctor"]
    summary = report.get("summary", {})
    lines.append(
        "Summary: "
        f"PASS={summary.get('pass', 0)} "
        f"WARN={summary.get('warn', 0)} "
        f"FAIL={summary.get('fail', 0)}"
    )
    for check in report.get("checks", []):
        lines.append(f"{str(check.get('status', '')).upper():<5} {check.get('name')}: {check.get('message')}")
    return "\n".join(lines)


def _check_env_file(env_file: Path | None) -> DoctorCheck:
    path = env_file.expanduser().resolve() if env_file else PACKAGE_ROOT / ".env"
    if path.exists():
        return DoctorCheck("pass", "env.file", f".env loaded: {path}")
    return DoctorCheck("warn", "env.file", f".env not found: {path}")


def _check_required_model_settings(settings: GatewaySettings) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    if settings.anthropic_api_key:
        checks.append(DoctorCheck("pass", "model.api_key", "ANTHROPIC_API_KEY configured"))
    else:
        checks.append(DoctorCheck("fail", "model.api_key", "ANTHROPIC_API_KEY is missing"))
    if settings.anthropic_base_url:
        checks.append(
            DoctorCheck(
                "pass",
                "model.base_url",
                "ANTHROPIC_BASE_URL configured",
                {"base_url": settings.anthropic_base_url},
            )
        )
    else:
        checks.append(DoctorCheck("warn", "model.base_url", "ANTHROPIC_BASE_URL is empty; SDK default will be used"))
    if settings.model_id:
        checks.append(DoctorCheck("pass", "model.id", f"MODEL_ID configured: {settings.model_id}"))
    else:
        checks.append(DoctorCheck("fail", "model.id", "MODEL_ID is missing"))
    return checks


def _check_paths(settings: GatewaySettings) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    for name, path in {
        "path.workspace": settings.workspace_root,
        "path.data": settings.data_dir,
        "path.config": settings.config_dir,
    }.items():
        if not path.exists():
            checks.append(DoctorCheck("fail", name, f"path does not exist: {path}"))
            continue
        if not path.is_dir():
            checks.append(DoctorCheck("fail", name, f"path is not a directory: {path}"))
            continue
        if os.access(path, os.W_OK):
            checks.append(DoctorCheck("pass", name, f"path is writable: {path}"))
        else:
            checks.append(DoctorCheck("fail", name, f"path is not writable: {path}"))
    return checks


def _check_redis(settings: GatewaySettings) -> list[DoctorCheck]:
    health = RedisClient(
        enabled=settings.redis_enabled,
        url=settings.redis_url,
        socket_timeout_seconds=settings.redis_socket_timeout_seconds,
    ).health()
    if not health.enabled:
        return [DoctorCheck("warn", "redis.ping", "Redis disabled", health.to_dict())]
    if health.ok:
        return [DoctorCheck("pass", "redis.ping", "Redis reachable", health.to_dict())]
    return [DoctorCheck("fail", "redis.ping", f"Redis unreachable: {health.error}", health.to_dict())]


def _check_postgres(settings: GatewaySettings) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    health = PostgresClient(
        enabled=settings.postgres_enabled,
        url=settings.postgres_url,
        connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
    ).health()
    if not health.enabled:
        checks.append(DoctorCheck("warn", "postgres.ping", "PostgreSQL disabled", health.to_dict()))
        return checks
    if health.ok:
        checks.append(DoctorCheck("pass", "postgres.ping", "PostgreSQL reachable", health.to_dict()))
    else:
        checks.append(DoctorCheck("fail", "postgres.ping", f"PostgreSQL unreachable: {health.error}", health.to_dict()))
        return checks

    try:
        schema = check_postgres_schema(
            url=settings.postgres_url,
            connect_timeout_seconds=settings.postgres_connect_timeout_seconds,
        ).to_dict()
    except Exception as exc:
        checks.append(DoctorCheck("fail", "postgres.schema", f"PostgreSQL schema check failed: {exc}"))
        return checks
    if schema.get("ok"):
        checks.append(DoctorCheck("pass", "postgres.schema", "PostgreSQL schema matches", schema))
    else:
        checks.append(DoctorCheck("warn", "postgres.schema", "PostgreSQL schema drift detected", schema))
    return checks


def _check_rabbitmq(settings: GatewaySettings) -> list[DoctorCheck]:
    if settings.delivery_broker != "rabbitmq":
        return [DoctorCheck("warn", "rabbitmq.stats", "RabbitMQ broker disabled", {"broker": settings.delivery_broker})]
    if not settings.postgres_enabled:
        return [
            DoctorCheck(
                "fail",
                "rabbitmq.postgres_dependency",
                "RabbitMQ delivery broker requires PostgreSQL as delivery source of truth",
            )
        ]
    broker = RabbitMQDeliveryBroker(
        url=settings.rabbitmq_url,
        exchange=settings.rabbitmq_exchange,
        queue=settings.rabbitmq_queue,
        dead_letter_exchange=settings.rabbitmq_dead_letter_exchange,
        dead_letter_queue=settings.rabbitmq_dead_letter_queue,
        connect_timeout_seconds=settings.rabbitmq_connect_timeout_seconds,
        enabled=True,
    )
    try:
        stats = broker.stats()
    finally:
        broker.close()
    if stats.get("error"):
        return [DoctorCheck("fail", "rabbitmq.stats", f"RabbitMQ unreachable: {stats.get('error')}", stats)]
    return [DoctorCheck("pass", "rabbitmq.stats", "RabbitMQ reachable", stats)]


def _check_agent_contracts(settings: GatewaySettings) -> list[DoctorCheck]:
    """Check that configured Agents still expose the baseline routed capabilities."""

    config_path = settings.config_dir / "agents.json"
    if not config_path.exists():
        return [
            DoctorCheck(
                "warn",
                "agent.contracts",
                f"Agent config not found: {config_path}",
                {"path": str(config_path)},
            )
        ]
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [
            DoctorCheck(
                "fail",
                "agent.contracts",
                f"Agent config cannot be parsed: {exc}",
                {"path": str(config_path)},
            )
        ]

    agents = data.get("agents", [])
    if not isinstance(agents, list):
        return [
            DoctorCheck(
                "fail",
                "agent.contracts",
                "Agent config field 'agents' must be a list",
                {"path": str(config_path)},
            )
        ]

    tool_allowlists = {
        str(agent.get("id") or ""): set(agent.get("tool_policy", {}).get("tool_names", []))
        for agent in agents
        if isinstance(agent, dict) and agent.get("id")
    }
    required = _baseline_agent_required_tools()
    missing_agents = [agent_id for agent_id in required if agent_id not in tool_allowlists]
    missing_tools: dict[str, list[str]] = {}
    for agent_id, tool_names in required.items():
        if agent_id not in tool_allowlists:
            continue
        missing = [tool_name for tool_name in tool_names if tool_name not in tool_allowlists[agent_id]]
        if missing:
            missing_tools[agent_id] = missing

    detail = {
        "path": str(config_path),
        "checked_agents": sorted(required),
        "missing_agents": missing_agents,
        "missing_tools": missing_tools,
    }
    if missing_agents or missing_tools:
        return [
            DoctorCheck(
                "fail",
                "agent.contracts",
                "Agent routing capability contracts are incomplete",
                detail,
            )
        ]
    return [
        DoctorCheck(
            "pass",
            "agent.contracts",
            f"Agent routing capability contracts ok: {len(required)} agents checked",
            detail,
        )
    ]


def _baseline_agent_required_tools() -> dict[str, tuple[str, ...]]:
    """Baseline tool allowlist required by entry routing and capability checks."""

    return {
        "main": (
            "classify_task_intent",
            "format_entry_response",
            "list_agent_capabilities",
            "format_agent_capability_catalog",
        ),
        "repo-analyzer": (
            "compose_github_repo_analysis",
            "format_github_repo_analysis",
            "github_repo_reading_guide",
            "format_github_repo_reading_guide",
            "plan_github_repo_adoption",
            "format_github_repo_adoption_plan",
        ),
        "research": ("compose_research_option_comparison",),
        "planner": ("plan_execution_stage", "format_execution_stage_plan"),
        "ops": ("ops_readonly_health", "ops_runtime_diagnostics"),
        "diet-assistant-zhanghaibo": ("meal_log_add", "format_meal_log_entry"),
        "personal-secretary-zhanghaibo": (
            "personal_todo_add",
            "personal_review_add",
            "personal_due_todo_digest_generate",
            "format_personal_due_todo_digest",
        ),
        "doc-writer": ("outline_structured_document", "save_structured_document"),
        "reviewer": ("assess_risk_decision", "format_risk_decision_assessment"),
    }


def _check_security_bindings(settings: GatewaySettings) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    if settings.dashboard_enabled and settings.dashboard_host in {"0.0.0.0", "::"}:
        checks.append(DoctorCheck("warn", "security.dashboard_bind", "Dashboard binds all interfaces; do not expose it without auth/proxy"))
    else:
        checks.append(DoctorCheck("pass", "security.dashboard_bind", f"Dashboard bind is {settings.dashboard_host}"))
    if settings.feishu_webhook_host in {"0.0.0.0", "::"} and not os.getenv("FEISHU_ENCRYPT_KEY", ""):
        checks.append(DoctorCheck("warn", "security.feishu_encrypt", "Feishu webhook is externally bindable but FEISHU_ENCRYPT_KEY is empty"))
    else:
        checks.append(DoctorCheck("pass", "security.feishu_encrypt", "Feishu encrypt configuration acceptable"))
    return checks


def _summary(checks: list[DoctorCheck]) -> dict[str, int]:
    return {
        "pass": sum(1 for check in checks if check.status == "pass"),
        "warn": sum(1 for check in checks if check.status == "warn"),
        "fail": sum(1 for check in checks if check.status == "fail"),
    }
