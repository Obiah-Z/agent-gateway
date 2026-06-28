import pytest

from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.diagnostics import render_doctor_text, run_doctor


def test_run_doctor_reports_missing_model_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_redis",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_postgres",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_rabbitmq",
        lambda settings: [],
    )
    settings = GatewaySettings(
        anthropic_api_key="",
        anthropic_base_url="https://api.example.test",
        model_id="model",
        workspace_root=tmp_path,
        data_dir=tmp_path,
        config_dir=tmp_path,
    )

    report = run_doctor(settings, env_file=tmp_path / ".env")

    assert report["ok"] is False
    assert report["summary"]["fail"] == 1
    assert any(check["name"] == "model.api_key" for check in report["checks"])


def test_run_doctor_passes_with_minimal_local_settings(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=test\n", encoding="utf-8")
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_redis",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_postgres",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_rabbitmq",
        lambda settings: [],
    )
    settings = GatewaySettings(
        anthropic_api_key="test",
        anthropic_base_url="https://api.example.test",
        model_id="model",
        workspace_root=tmp_path,
        data_dir=tmp_path,
        config_dir=tmp_path,
        dashboard_host="127.0.0.1",
        feishu_webhook_host="127.0.0.1",
    )

    report = run_doctor(settings, env_file=tmp_path / ".env")

    assert report["ok"] is True
    assert report["summary"]["fail"] == 0
    assert "PASS=" in render_doctor_text(report)


def test_run_doctor_fails_when_rabbitmq_enabled_without_postgres(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_redis",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_postgres",
        lambda settings: [],
    )
    settings = GatewaySettings(
        anthropic_api_key="test",
        anthropic_base_url="https://api.example.test",
        model_id="model",
        workspace_root=tmp_path,
        data_dir=tmp_path,
        config_dir=tmp_path,
        postgres_enabled=False,
        delivery_broker="rabbitmq",
    )

    report = run_doctor(settings, env_file=tmp_path / ".env")

    assert report["ok"] is False
    assert any(check["name"] == "rabbitmq.postgres_dependency" for check in report["checks"])


def test_doctor_warns_on_public_dashboard_binding(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_redis",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_postgres",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_rabbitmq",
        lambda settings: [],
    )
    settings = GatewaySettings(
        anthropic_api_key="test",
        anthropic_base_url="https://api.example.test",
        model_id="model",
        workspace_root=tmp_path,
        data_dir=tmp_path,
        config_dir=tmp_path,
        dashboard_host="0.0.0.0",
    )

    report = run_doctor(settings, env_file=tmp_path / ".env")

    assert report["ok"] is True
    assert any(check["status"] == "warn" and check["name"] == "security.dashboard_bind" for check in report["checks"])


def test_doctor_warns_on_public_feishu_without_encrypt_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FEISHU_ENCRYPT_KEY", raising=False)
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_redis",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_postgres",
        lambda settings: [],
    )
    monkeypatch.setattr(
        "agent_gateway.runtime.diagnostics._check_rabbitmq",
        lambda settings: [],
    )
    settings = GatewaySettings(
        anthropic_api_key="test",
        anthropic_base_url="https://api.example.test",
        model_id="model",
        workspace_root=tmp_path,
        data_dir=tmp_path,
        config_dir=tmp_path,
        feishu_webhook_host="0.0.0.0",
    )

    report = run_doctor(settings, env_file=tmp_path / ".env")

    assert any(check["status"] == "warn" and check["name"] == "security.feishu_encrypt" for check in report["checks"])
