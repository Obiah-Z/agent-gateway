from datetime import date
from pathlib import Path

from agent_gateway.observability.alerts import AlertRule, AlertState, AlertStore


def test_alert_state_to_dict_exposes_human_times() -> None:
    state = AlertState(
        rule_id="delivery_pending_backlog",
        status="active",
        active_since=1_704_067_200.0,
        last_triggered_at=1_704_067_260.0,
        current_value=12,
        threshold=10,
        consecutive_hits=3,
    )

    row = state.to_dict()

    assert row["rule_id"] == "delivery_pending_backlog"
    assert row["status"] == "active"
    assert row["active_since_time"].endswith("+00:00")
    assert row["current_value"] == 12


def test_alert_store_appends_and_tails(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "alerts", retention_days=2000)
    rule = AlertRule(
        id="profiles_unavailable",
        title="没有可用模型 Profile",
        severity="critical",
        description="all profiles unavailable",
        threshold=0,
    )
    state = AlertState(rule_id=rule.id, status="active", current_value=0, threshold=0)

    store.append(
        rule=rule,
        state=state,
        event="triggered",
        message="profiles unavailable",
        value=0,
        timestamp=1_704_067_200.0,
    )
    store.append(
        rule=rule,
        state=state,
        event="recovered",
        message="profiles recovered",
        value=1,
        timestamp=1_704_067_260.0,
    )

    rows = store.tail(limit=10)

    assert [row["event"] for row in rows] == ["triggered", "recovered"]
    assert rows[-1]["rule"]["id"] == "profiles_unavailable"


def test_alert_store_cleanup_respects_retention(tmp_path: Path) -> None:
    store = AlertStore(tmp_path / "alerts", retention_days=2)
    old_file = tmp_path / "alerts" / "alerts-2024-01-01.jsonl"
    keep_file = tmp_path / "alerts" / "alerts-2024-01-03.jsonl"
    old_file.write_text("{}", encoding="utf-8")
    keep_file.write_text("{}", encoding="utf-8")

    store.cleanup(now=date(2024, 1, 3))

    assert not old_file.exists()
    assert keep_file.exists()
