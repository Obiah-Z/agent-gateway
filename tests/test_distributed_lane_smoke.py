from scripts.smoke_distributed_lane import (
    assert_smoke_result,
    assert_ttl_takeover_result,
    parse_args,
)


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

    assert args.scenario == "inbound"
    assert args.rabbitmq_exchange == "agent_gateway.inbound.smoke"
    assert args.rabbitmq_queue_prefix == "agent_gateway.inbound.smoke.partition"
    assert args.lane_namespace == "gateway:smoke:lane"
    assert args.rabbitmq_prefetch == 1


def test_ttl_takeover_smoke_asserts_successful_result() -> None:
    result = {
        "status": "ok",
        "first_owner_acquired": True,
        "second_owner_blocked_before_ttl": True,
        "second_owner_acquired_after_ttl": True,
        "first_worker_id": "smoke-worker-old",
        "second_worker_id": "smoke-worker-new",
        "second_task_id": "smoke-task-new",
        "before_takeover": {
            "owned": True,
            "worker_id": "smoke-worker-old",
            "task_id": "smoke-task-old",
        },
        "after_takeover": {
            "owned": True,
            "worker_id": "smoke-worker-new",
            "task_id": "smoke-task-new",
        },
    }

    assert assert_ttl_takeover_result(result) == []


def test_ttl_takeover_smoke_reports_failed_invariants() -> None:
    result = {
        "status": "failed",
        "first_owner_acquired": False,
        "second_owner_blocked_before_ttl": False,
        "second_owner_acquired_after_ttl": False,
        "first_worker_id": "smoke-worker-old",
        "second_worker_id": "smoke-worker-new",
        "second_task_id": "smoke-task-new",
        "before_takeover": {"owned": True, "worker_id": "smoke-worker-new"},
        "after_takeover": {"owned": False, "worker_id": "smoke-worker-old"},
    }

    failures = assert_ttl_takeover_result(result)

    assert any("unexpected status" in item for item in failures)
    assert any("first owner did not acquire lane" in item for item in failures)
    assert any("second owner was not blocked" in item for item in failures)
    assert any("second owner did not acquire" in item for item in failures)
    assert any("lane owner did not switch" in item for item in failures)
    assert any("initial lane owner was not first worker" in item for item in failures)
    assert any("lane owner task mismatch" in item for item in failures)
    assert any("lane is not owned" in item for item in failures)


def test_ttl_takeover_smoke_parses_scenario() -> None:
    args = parse_args(["--scenario", "ttl-takeover", "--lane-ttl-seconds", "1"])

    assert args.scenario == "ttl-takeover"
    assert args.lane_ttl_seconds == 1
