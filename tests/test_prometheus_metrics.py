from agent_gateway.monitoring.prometheus import render_prometheus_metrics


def test_render_prometheus_metrics_exports_stable_gauges() -> None:
    text = render_prometheus_metrics(
        {
            "configured": True,
            "available": True,
            "count": 3,
            "window": {"start_timestamp": 10.0, "end_timestamp": 70.5},
            "delivery": {"max_pending": 2, "max_failed": 1},
            "lanes": {"max_active": 4, "max_queued": 9},
            "events": {"max_errors_5m": 1, "max_rejected_5m": 0},
            "cron": {"max_enabled": 2},
            "profiles": {"max_available": 1, "max_cooling_down": 0},
            "tasks": {
                "broker_enabled": 1,
                "max_broker_messages": 9,
                "max_broker_dead_letter_messages": 1,
                "max_broker_partition_messages": 7,
            },
        }
    )

    assert "# TYPE gateway_metrics_available gauge" in text
    assert "gateway_metrics_configured 1" in text
    assert "gateway_metrics_available 1" in text
    assert "gateway_metrics_window_samples 3" in text
    assert "gateway_delivery_max_pending 2" in text
    assert "gateway_lanes_max_active 4" in text
    assert "gateway_events_max_errors_5m 1" in text
    assert "gateway_cron_max_enabled 2" in text
    assert "gateway_profiles_max_available 1" in text
    assert "gateway_tasks_broker_enabled 1" in text
    assert "gateway_tasks_max_broker_messages 9" in text
    assert "gateway_tasks_max_broker_dead_letter_messages 1" in text
    assert "gateway_tasks_max_broker_partition_messages 7" in text
