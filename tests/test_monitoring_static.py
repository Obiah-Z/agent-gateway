from __future__ import annotations

from pathlib import Path

from agent_gateway.monitoring import STATIC_DIR


def test_monitoring_static_assets_exist() -> None:
    assert STATIC_DIR.is_dir()
    assert (STATIC_DIR / "index.html").is_file()
    assert (STATIC_DIR / "app.js").is_file()
    assert (STATIC_DIR / "styles.css").is_file()


def test_monitoring_dashboard_references_local_assets_only() -> None:
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "./styles.css" in index
    assert "./app.js" in index
    assert "cdn." not in index.lower()
    assert "http://" not in index
    assert "https://" not in index


def test_monitoring_json_rpc_client_covers_first_stage_methods() -> None:
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    expected_methods = {
        "health.check",
        "runtime.status",
        "metrics.summary",
        "metrics.tail",
        "alerts.active",
        "alerts.history",
        "delivery.stats",
        "delivery.list",
        "delivery.retry",
        "delivery.discard",
        "delivery.flush",
        "delivery.republish",
        "tasks.list",
        "tasks.cancel",
        "tasks.retry",
        "cron.list",
        "cron.trigger",
    }

    missing = sorted(method for method in expected_methods if method not in app_js)
    assert missing == []


def test_monitoring_dashboard_includes_triage_and_delivery_detail_ui() -> None:
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "问题摘要" in index
    assert "指标趋势" in index
    assert "当前活跃告警" in index
    assert "delivery-detail" in index
    assert "tasks-panel" in index
    assert "后台任务" in index
    assert "重建队列" in index
    assert "等待重试" in index
    assert "data-jump" in index
    assert "console-sidebar" in index
    assert "Operations Console" in index


def test_monitoring_dashboard_classifies_delivery_errors_and_confirms_actions() -> None:
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function buildIssues" in app_js
    assert "function classifyDeliveryError" in app_js
    assert "invalid_open_id" in app_js
    assert "function confirmAction" in app_js
    assert "navigator.clipboard.writeText" in app_js


def test_monitoring_runtime_snapshot_uses_compact_cards_and_details() -> None:
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "runtime-icon" in app_js
    assert "runtime-chips" in app_js
    assert "runtime-details" in app_js
    assert "document.createElement(\"details\")" in app_js
    assert ".runtime-icon" in styles
    assert ".runtime-chips" in styles


def test_monitoring_dashboard_includes_metrics_trend_view() -> None:
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "function renderMetrics" in app_js
    assert "function renderAlerts" in app_js
    assert "function buildSparkline" in app_js
    assert ".sparkline" in styles
    assert ".trend-grid" in styles
    assert ".alert-card" in styles


def test_monitoring_dashboard_uses_global_panel_collapse_limit() -> None:
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "const DEFAULT_PANEL_LIMIT = 6;" in app_js
    assert "function slicePanelItems" in app_js
    assert "function appendCollapseToggle" in app_js
    assert "展开剩余" in app_js


def test_monitoring_dashboard_uses_compact_sidebar_navigation() -> None:
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert index.count("<a href=\"#") == 7
    assert "健康运行" in index
    assert "指标告警" in index
    assert "事件错误" in index
    assert "任务投递" in index
    assert "display: flex;" in styles
    assert "margin-top: auto;" in styles


def test_monitoring_dashboard_formats_time_values_consistently() -> None:
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")

    assert "function isFormattedTimeText" in app_js
    assert "function isTimeFieldName" in app_js
    assert "function formatNestedDisplayValue" in app_js
    assert "numeric > 100000000000 ? numeric : numeric * 1000" in app_js
    assert "key.endsWith(\"_time\") || key.endsWith(\"_at\")" in app_js


def test_monitoring_dashboard_includes_task_queue_view() -> None:
    app_js = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")

    assert "function renderTasks" in app_js
    assert "function cancelTask" in app_js
    assert "function retryTask" in app_js
    assert "tasks.list" in app_js
    assert "tasks.cancel" in app_js
    assert "tasks.retry" in app_js
    assert "slicePanelItems(items, \"tasks\")" in app_js
    assert ".task-list" in styles
    assert ".task-item" in styles


def test_monitoring_static_dir_is_inside_package() -> None:
    assert Path("agent_gateway/monitoring/static") in STATIC_DIR.relative_to(Path.cwd()).parents or (
        Path.cwd() / "agent_gateway/monitoring/static"
    ) == STATIC_DIR
