import json

from scripts.build_capacity_baseline import (
    load_results,
    pick_best_per_scenario,
    render_capacity_baseline,
    write_capacity_baseline,
)


def _result(scenario: str, *, throughput: float, success: int = 10, failed: int = 0) -> dict:
    return {
        "meta": {"generated_at": "2026-06-28T10:00:00+08:00"},
        "scenario": {
            "name": scenario,
            "requests": success + failed,
            "concurrency": 2,
        },
        "summary": {
            "success": success,
            "failed": failed,
            "error_rate": round(failed / max(1, success + failed), 6),
            "throughput_rps": throughput,
            "e2e_ms": {"p95": 12.3},
            "agent_turn_ms": {"p95": 4.5},
            "delivery_ms": {"p95": 6.7},
            "max_delivery_backlog": 9,
        },
        "errors": [],
    }


def test_load_results_reads_valid_json_only(tmp_path) -> None:
    (tmp_path / "a.json").write_text(json.dumps(_result("mock-local", throughput=10)), encoding="utf-8")
    (tmp_path / "bad.json").write_text("{bad", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("{}", encoding="utf-8")

    rows = load_results(tmp_path)

    assert len(rows) == 1
    assert rows[0]["scenario"]["name"] == "mock-local"
    assert rows[0]["_source_path"].endswith("a.json")


def test_pick_best_per_scenario_prefers_success_and_throughput() -> None:
    rows = [
        _result("mock-local", throughput=10, success=10),
        _result("mock-local", throughput=20, success=10),
        _result("delivery-local", throughput=5, success=8),
    ]

    selected = pick_best_per_scenario(rows)

    by_name = {row["scenario"]["name"]: row for row in selected}
    assert by_name["mock-local"]["summary"]["throughput_rps"] == 20
    assert by_name["delivery-local"]["summary"]["success"] == 8


def test_render_capacity_baseline_outputs_summary_table() -> None:
    markdown = render_capacity_baseline(
        [
            _result("mock-local", throughput=100),
            _result("delivery-rabbitmq", throughput=200),
            _result("model-real", throughput=1.5),
        ]
    )

    assert "# AI Agent Gateway 容量基线报告" in markdown
    assert "| mock-local |" in markdown
    assert "| delivery-rabbitmq |" in markdown
    assert "真实模型链路" in markdown
    assert "## 使用边界" in markdown


def test_write_capacity_baseline_creates_output_file(tmp_path) -> None:
    output = write_capacity_baseline([_result("mock-local", throughput=100)], tmp_path / "baseline.md")

    assert output.exists()
    assert "mock-local" in output.read_text(encoding="utf-8")
