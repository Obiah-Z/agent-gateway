from datetime import date
from pathlib import Path

from agent_gateway.observability.metrics import MetricsStore


def test_metrics_store_records_and_reads_latest_snapshot(tmp_path: Path) -> None:
    store = MetricsStore(tmp_path / "metrics")

    first = store.record(delivery={"pending": 1, "failed": 0})
    second = store.record(
        delivery={"pending": 0, "failed": 1},
        lanes={"active": 2, "queued": 3},
        metadata={"source": "test"},
    )

    rows = store.tail(limit=10)
    latest = store.latest()

    assert [row["delivery"]["pending"] for row in rows] == [1, 0]
    assert latest is not None
    assert latest["timestamp"] == second["timestamp"]
    assert latest["lanes"] == {"active": 2, "queued": 3}
    assert first["time"].endswith("+00:00")


def test_metrics_store_rotates_by_day_and_tails_across_files(tmp_path: Path) -> None:
    store = MetricsStore(tmp_path / "metrics", retention_days=2000)
    first_ts = 1_704_067_200.0  # 2024-01-01T00:00:00Z
    second_ts = 1_704_153_600.0  # 2024-01-02T00:00:00Z

    store.record(delivery={"pending": 1}, timestamp=first_ts)
    store.record(delivery={"pending": 2}, timestamp=second_ts)

    files = sorted(path.name for path in (tmp_path / "metrics").glob("metrics-*.jsonl"))
    rows = store.tail(limit=10)

    assert files == ["metrics-2024-01-01.jsonl", "metrics-2024-01-02.jsonl"]
    assert [row["delivery"]["pending"] for row in rows] == [1, 2]


def test_metrics_store_cleans_up_expired_files(tmp_path: Path) -> None:
    store = MetricsStore(tmp_path / "metrics", retention_days=2)
    old_file = tmp_path / "metrics" / "metrics-2024-01-01.jsonl"
    keep_file = tmp_path / "metrics" / "metrics-2024-01-03.jsonl"
    old_file.write_text("{}", encoding="utf-8")
    keep_file.write_text("{}", encoding="utf-8")

    store.cleanup(now=date(2024, 1, 3))

    assert not old_file.exists()
    assert keep_file.exists()


def test_metrics_store_sanitizes_nested_values(tmp_path: Path) -> None:
    store = MetricsStore(tmp_path / "metrics")

    row = store.record(metadata={"custom": object(), "nested": {"value": object()}})

    assert isinstance(row["metadata"]["custom"], str)
    assert isinstance(row["metadata"]["nested"]["value"], str)
