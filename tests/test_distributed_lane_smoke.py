from scripts.smoke_distributed_lane import assert_smoke_result, parse_args


def test_distributed_lane_smoke_asserts_successful_result() -> None:
    result = {
        "scenario": {"requests": 4},
        "summary": {"success": 4, "failed": 0},
        "context": {
            "lane_mode": "redis",
            "max_same_session_concurrency": 1,
            "broker_after_consume": {"messages": 0, "dead_letter_messages": 0},
            "redis_health": {"enabled": True, "ok": True},
        },
    }

    assert assert_smoke_result(result) == []


def test_distributed_lane_smoke_reports_failed_invariants() -> None:
    result = {
        "scenario": {"requests": 4},
        "summary": {"success": 3, "failed": 1},
        "context": {
            "lane_mode": "local",
            "max_same_session_concurrency": 2,
            "broker_after_consume": {"messages": 1, "dead_letter_messages": 1},
            "redis_health": {"enabled": True, "ok": False},
        },
    }

    failures = assert_smoke_result(result)

    assert any("success mismatch" in item for item in failures)
    assert any("lane_mode is not redis" in item for item in failures)
    assert any("same-session serialization failed" in item for item in failures)
    assert any("broker backlog not drained" in item for item in failures)
    assert any("broker DLQ not empty" in item for item in failures)
    assert any("redis health failed" in item for item in failures)


def test_distributed_lane_smoke_defaults_to_isolated_topology() -> None:
    args = parse_args([])

    assert args.rabbitmq_exchange == "agent_gateway.inbound.smoke"
    assert args.rabbitmq_queue_prefix == "agent_gateway.inbound.smoke.partition"
    assert args.lane_namespace == "gateway:smoke:lane"
    assert args.rabbitmq_prefetch == 1
