from __future__ import annotations

import math
from typing import Any


def render_prometheus_metrics(summary: dict[str, Any]) -> str:
    """Render a small Prometheus text exposition from metrics_summary()."""

    lines: list[str] = [
        "# HELP gateway_metrics_configured Whether metrics storage is configured.",
        "# TYPE gateway_metrics_configured gauge",
        f"gateway_metrics_configured {_bool_value(summary.get('configured', False))}",
        "# HELP gateway_metrics_available Whether at least one metrics snapshot is available.",
        "# TYPE gateway_metrics_available gauge",
        f"gateway_metrics_available {_bool_value(summary.get('available', False))}",
        "# HELP gateway_metrics_window_samples Number of snapshots in the summary window.",
        "# TYPE gateway_metrics_window_samples gauge",
        f"gateway_metrics_window_samples {_number(summary.get('count', 0))}",
    ]

    window = summary.get("window") if isinstance(summary.get("window"), dict) else {}
    if window:
        lines.extend(
            [
                "# HELP gateway_metrics_window_start_timestamp_seconds Summary window start timestamp.",
                "# TYPE gateway_metrics_window_start_timestamp_seconds gauge",
                (
                    "gateway_metrics_window_start_timestamp_seconds "
                    f"{_number(window.get('start_timestamp', 0))}"
                ),
                "# HELP gateway_metrics_window_end_timestamp_seconds Summary window end timestamp.",
                "# TYPE gateway_metrics_window_end_timestamp_seconds gauge",
                (
                    "gateway_metrics_window_end_timestamp_seconds "
                    f"{_number(window.get('end_timestamp', 0))}"
                ),
            ]
        )

    _append_section(
        lines,
        "delivery",
        {
            "max_pending": "Maximum pending delivery count in the summary window.",
            "max_failed": "Maximum failed delivery count in the summary window.",
            "max_retry_ready": "Maximum retry-ready delivery count in the summary window.",
            "max_oldest_pending_age_seconds": "Maximum age of oldest pending delivery.",
            "max_oldest_failed_age_seconds": "Maximum age of oldest failed delivery.",
        },
        summary.get("delivery", {}),
    )
    _append_section(
        lines,
        "lanes",
        {
            "max_count": "Maximum inbound lane count in the summary window.",
            "max_active": "Maximum active inbound lane count in the summary window.",
            "max_queued": "Maximum queued inbound message count in the summary window.",
            "max_queue_depth": "Maximum per-lane queue depth in the summary window.",
        },
        summary.get("lanes", {}),
    )
    _append_section(
        lines,
        "events",
        {
            "max_errors_5m": "Maximum recent runtime error count.",
            "max_rejected_5m": "Maximum recent rejected event count.",
            "max_delivery_failed_5m": "Maximum recent delivery failure count.",
            "max_tool_failed_5m": "Maximum recent tool failure count.",
            "max_cron_failed_5m": "Maximum recent cron failure count.",
        },
        summary.get("events", {}),
    )
    _append_section(
        lines,
        "cron",
        {
            "max_configured": "Maximum configured cron job count.",
            "max_count": "Maximum cron job count.",
            "max_enabled": "Maximum enabled cron job count.",
            "max_errored": "Maximum errored cron job count.",
        },
        summary.get("cron", {}),
    )
    _append_section(
        lines,
        "profiles",
        {
            "max_count": "Maximum configured model profile count.",
            "max_available": "Maximum available model profile count.",
            "max_cooling_down": "Maximum cooling-down model profile count.",
        },
        summary.get("profiles", {}),
    )
    _append_section(
        lines,
        "tasks",
        {
            "max_pending": "Maximum pending background task count.",
            "max_running": "Maximum running background task count.",
            "max_retrying": "Maximum retrying background task count.",
            "max_failed": "Maximum failed background task count.",
            "broker_enabled": "Whether inbound task broker was enabled in the summary window.",
            "max_broker_messages": "Maximum inbound broker queued message count.",
            "max_broker_dead_letter_messages": "Maximum inbound broker dead-letter message count.",
            "max_broker_partitions": "Maximum configured inbound broker partition count.",
            "max_broker_prefetch": "Maximum configured inbound broker prefetch count.",
            "max_broker_partition_messages": "Maximum queued messages in a single inbound broker partition.",
        },
        summary.get("tasks", {}),
    )
    return "\n".join(lines) + "\n"


def _append_section(
    lines: list[str],
    section: str,
    descriptions: dict[str, str],
    values: Any,
) -> None:
    payload = values if isinstance(values, dict) else {}
    for key, description in descriptions.items():
        metric = f"gateway_{section}_{key}"
        lines.append(f"# HELP {metric} {description}")
        lines.append(f"# TYPE {metric} gauge")
        lines.append(f"{metric} {_number(payload.get(key, 0))}")


def _bool_value(value: Any) -> int:
    return 1 if bool(value) else 0


def _number(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if not isinstance(value, (int, float)):
        return "0"
    if not math.isfinite(float(value)):
        return "0"
    return str(int(value)) if float(value).is_integer() else str(float(value))
