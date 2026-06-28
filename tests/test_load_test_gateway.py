import asyncio
import json

from scripts.load_test_gateway import (
    build_result,
    percentile,
    run_delivery_local,
    render_markdown,
    run_mock_local,
    write_reports,
)


def test_percentile_uses_nearest_rank() -> None:
    values = [1, 2, 3, 4, 5]

    assert percentile(values, 50) == 3
    assert percentile(values, 95) == 5
    assert percentile([], 95) == 0.0


def test_mock_local_load_test_generates_summary_and_reports(tmp_path) -> None:
    samples, wall_seconds = asyncio.run(
        run_mock_local(
            requests=5,
            concurrency=2,
            agent_delay_ms=0,
            delivery_delay_ms=0,
        )
    )
    result = build_result(
        scenario="mock-local",
        requests=5,
        concurrency=2,
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=0,
        delivery_delay_ms=0,
    )

    assert result["summary"]["success"] == 5
    assert result["summary"]["failed"] == 0
    assert result["summary"]["throughput_rps"] > 0
    assert result["scenario"]["uses_real_model"] is False
    assert result["scenario"]["uses_real_feishu"] is False

    json_path, md_path = write_reports(result, tmp_path, "mock")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = md_path.read_text(encoding="utf-8")

    assert payload["scenario"]["name"] == "mock-local"
    assert "# AI Agent Gateway 压测报告" in markdown
    assert "E2E P50 / P95 / P99" in markdown
    assert render_markdown(result).startswith("# AI Agent Gateway 压测报告")


def test_delivery_local_load_test_uses_real_delivery_queue(tmp_path) -> None:
    samples, wall_seconds, context = asyncio.run(
        run_delivery_local(
            requests=4,
            concurrency=2,
            delivery_delay_ms=0,
            work_dir=tmp_path / "work",
        )
    )
    result = build_result(
        scenario="delivery-local",
        requests=4,
        concurrency=2,
        wall_seconds=wall_seconds,
        samples=samples,
        agent_delay_ms=0,
        delivery_delay_ms=0,
        context=context,
    )

    assert result["summary"]["success"] == 4
    assert result["summary"]["failed"] == 0
    assert result["summary"]["max_delivery_backlog"] == 4
    assert result["scenario"]["uses_real_delivery_queue"] is True
    assert context["sent"] == 4
    assert "delivery-local 基线" in render_markdown(result)
