from scripts.smoke_distributed_lane import (
    assert_broker_unavailable_result,
    assert_postgres_lane_result,
    assert_primary_unavailable_result,
    assert_smoke_result,
    assert_ttl_takeover_result,
    assert_worker_crash_result,
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


def test_broker_unavailable_smoke_asserts_successful_result() -> None:
    result = {
        "status": "ok",
        "publish_failed": True,
        "worker_handled": True,
        "task_status_after_enqueue": "pending",
        "task_status_after_worker": "done",
        "handler_calls": 1,
        "broker_consume_attempts": 1,
    }

    assert assert_broker_unavailable_result(result) == []


def test_broker_unavailable_smoke_reports_failed_invariants() -> None:
    result = {
        "status": "failed",
        "publish_failed": False,
        "worker_handled": False,
        "task_status_after_enqueue": "failed",
        "task_status_after_worker": "pending",
        "handler_calls": 2,
        "broker_consume_attempts": 0,
    }

    failures = assert_broker_unavailable_result(result)

    assert any("unexpected status" in item for item in failures)
    assert any("broker publish failure was not exercised" in item for item in failures)
    assert any("worker did not handle fallback task" in item for item in failures)
    assert any("task was not kept pending" in item for item in failures)
    assert any("task was not completed" in item for item in failures)
    assert any("handler call count mismatch" in item for item in failures)
    assert any("worker did not attempt broker consume" in item for item in failures)


def test_broker_unavailable_smoke_parses_scenario() -> None:
    args = parse_args(["--scenario", "broker-unavailable"])

    assert args.scenario == "broker-unavailable"


def test_primary_unavailable_smoke_asserts_successful_result() -> None:
    result = {
        "status": "ok",
        "primary_reserve_failed": True,
        "worker_handled": True,
        "task_status_after_worker": "done",
        "handler_calls": 1,
        "broker_stats": {"acked": 1},
    }

    assert assert_primary_unavailable_result(result) == []


def test_primary_unavailable_smoke_reports_failed_invariants() -> None:
    result = {
        "status": "failed",
        "primary_reserve_failed": False,
        "worker_handled": False,
        "task_status_after_worker": "pending",
        "handler_calls": 2,
        "broker_stats": {"acked": 0},
    }

    failures = assert_primary_unavailable_result(result)

    assert any("unexpected status" in item for item in failures)
    assert any("primary reserve_task_id failure was not exercised" in item for item in failures)
    assert any("worker did not handle task" in item for item in failures)
    assert any("task was not completed" in item for item in failures)
    assert any("handler call count mismatch" in item for item in failures)
    assert any("broker payload was not acked" in item for item in failures)


def test_primary_unavailable_smoke_parses_scenario() -> None:
    args = parse_args(["--scenario", "primary-unavailable"])

    assert args.scenario == "primary-unavailable"


def test_postgres_lane_smoke_asserts_successful_result() -> None:
    result = {
        "status": "ok",
        "write_ok": True,
        "listed_owned": True,
        "stale_before_release": True,
        "mismatch_release": False,
        "matched_release": True,
        "release_reason": "smoke postgres lane release",
        "released_row": {
            "state": "released",
            "metadata": {
                "release_reason": "smoke postgres lane release",
                "released_at": 1782660000.0,
            },
        },
    }

    assert assert_postgres_lane_result(result) == []


def test_postgres_lane_smoke_reports_failed_invariants() -> None:
    result = {
        "status": "failed",
        "write_ok": False,
        "listed_owned": False,
        "stale_before_release": False,
        "mismatch_release": True,
        "matched_release": False,
        "release_reason": "smoke postgres lane release",
        "released_row": {"state": "owned", "metadata": {}},
    }

    failures = assert_postgres_lane_result(result)

    assert any("unexpected status" in item for item in failures)
    assert any("write did not return" in item for item in failures)
    assert any("not readable" in item for item in failures)
    assert any("not detected as stale" in item for item in failures)
    assert any("mismatch unexpectedly released" in item for item in failures)
    assert any("matched release did not update" in item for item in failures)
    assert any("released row state mismatch" in item for item in failures)
    assert any("release metadata missing reason" in item for item in failures)
    assert any("release metadata missing released_at" in item for item in failures)


def test_postgres_lane_smoke_parses_scenario() -> None:
    args = parse_args(["--scenario", "postgres-lane", "--postgres-connect-timeout-seconds", "3"])

    assert args.scenario == "postgres-lane"
    assert args.postgres_url == "postgresql://postgres:postgres@127.0.0.1:5432/postgres"
    assert args.postgres_connect_timeout_seconds == 3


def test_worker_crash_smoke_asserts_successful_result() -> None:
    result = {
        "status": "ok",
        "old_owner_acquired": True,
        "blocked_before_ttl": True,
        "handled_after_ttl": True,
        "task_status_after_blocked": "pending",
        "task_status_after_takeover": "done",
        "handler_calls": 1,
        "old_worker_id": "smoke-worker-crashed",
        "new_worker_id": "smoke-worker-takeover",
        "before_ttl_owner": {
            "owned": True,
            "worker_id": "smoke-worker-crashed",
        },
        "during_handler_owner": {
            "owned": True,
            "worker_id": "smoke-worker-takeover",
        },
        "after_completion_owner": {"owned": False},
    }

    assert assert_worker_crash_result(result) == []


def test_worker_crash_smoke_reports_failed_invariants() -> None:
    result = {
        "status": "failed",
        "old_owner_acquired": False,
        "blocked_before_ttl": False,
        "handled_after_ttl": False,
        "task_status_after_blocked": "running",
        "task_status_after_takeover": "pending",
        "handler_calls": 2,
        "old_worker_id": "smoke-worker-crashed",
        "new_worker_id": "smoke-worker-takeover",
        "before_ttl_owner": {"owned": True, "worker_id": "other"},
        "during_handler_owner": {"owned": True, "worker_id": "other"},
        "after_completion_owner": {"owned": True},
    }

    failures = assert_worker_crash_result(result)

    assert any("unexpected status" in item for item in failures)
    assert any("old worker did not acquire lane" in item for item in failures)
    assert any("new worker was not blocked" in item for item in failures)
    assert any("new worker did not handle task" in item for item in failures)
    assert any("task status changed before TTL" in item for item in failures)
    assert any("task was not completed" in item for item in failures)
    assert any("handler call count mismatch" in item for item in failures)
    assert any("old lane owner metadata mismatch" in item for item in failures)
    assert any("new lane owner metadata mismatch" in item for item in failures)
    assert any("lane was not released" in item for item in failures)


def test_worker_crash_smoke_parses_scenario() -> None:
    args = parse_args(["--scenario", "worker-crash", "--lane-ttl-seconds", "1"])

    assert args.scenario == "worker-crash"
    assert args.lane_ttl_seconds == 1
