from pathlib import Path

from agent_gateway.config import GatewaySettings, load_env
from agent_gateway.config_loader import (
    ensure_default_project_files,
    load_agents,
    load_auth_profiles,
    load_bindings,
    read_channels_source,
    save_agents,
    save_auth_profiles,
    save_bindings,
    save_channel_accounts,
)
from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.runtime.domain.models import AgentConfig, Binding
from agent_gateway.runtime.execution.resilience import AuthProfile


def test_load_env_overrides_empty_process_env(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ANTHROPIC_API_KEY=file-key",
                "ANTHROPIC_BASE_URL=https://example.test/anthropic",
                "MODEL_ID=file-model",
                "GATEWAY_RUNTIME_ROLES=api,delivery,dashboard",
                "GATEWAY_WEB_SEARCH_ENABLED=true",
                "GATEWAY_WEB_SEARCH_PROVIDER=tavily",
                "TAVILY_API_KEY=tvly-test-key",
                "TAVILY_BASE_URL=https://api.tavily.test",
                "GATEWAY_DASHBOARD_ENABLED=false",
                "GATEWAY_DASHBOARD_HOST=0.0.0.0",
                "GATEWAY_DASHBOARD_PORT=8870",
                "GATEWAY_DASHBOARD_REFRESH_INTERVAL_SECONDS=7",
                "GATEWAY_EVENTS_RETENTION_DAYS=21",
                "GATEWAY_INBOUND_MAX_CONCURRENT_LANES=3",
                "GATEWAY_INBOUND_MAX_QUEUE_SIZE=11",
                "GATEWAY_INBOUND_MAX_LANE_QUEUE_SIZE=5",
                "GATEWAY_INBOUND_LONG_TASK_NOTICE_SECONDS=0.5",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "")
    monkeypatch.setenv("MODEL_ID", "")

    load_env(env_file)

    assert GatewaySettings.from_env().anthropic_api_key == "file-key"
    assert GatewaySettings.from_env().anthropic_base_url == "https://example.test/anthropic"
    assert GatewaySettings.from_env().model_id == "file-model"
    assert GatewaySettings.from_env().runtime_roles == ("api", "delivery", "dashboard")
    assert GatewaySettings.from_env().web_search_enabled is True
    assert GatewaySettings.from_env().web_search_provider == "tavily"
    assert GatewaySettings.from_env().tavily_api_key == "tvly-test-key"
    assert GatewaySettings.from_env().tavily_base_url == "https://api.tavily.test"
    assert GatewaySettings.from_env().dashboard_enabled is False
    assert GatewaySettings.from_env().dashboard_host == "0.0.0.0"
    assert GatewaySettings.from_env().dashboard_port == 8870
    assert GatewaySettings.from_env().dashboard_refresh_interval_seconds == 7
    assert GatewaySettings.from_env().events_retention_days == 21
    assert GatewaySettings.from_env().inbound_max_concurrent_lanes == 3
    assert GatewaySettings.from_env().inbound_max_queue_size == 11
    assert GatewaySettings.from_env().inbound_max_lane_queue_size == 5
    assert GatewaySettings.from_env().inbound_long_task_notice_seconds == 0.5


def test_config_loader_reads_default_files(tmp_path: Path, monkeypatch) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://example.test/v1")

    ensure_default_project_files(settings)

    agents = load_agents(settings)
    bindings = load_bindings(settings)
    profiles = load_auth_profiles(settings)
    channels_source = read_channels_source(settings)

    assert len(agents) == 1
    assert agents[0].id == "main"
    assert len(bindings) == 1
    assert bindings[0].match_key == "default"
    assert len(profiles) == 1
    assert profiles[0].api_key == "test-key"
    long_connection = next(
        item
        for item in channels_source["channels"]
        if item["account_id"] == "feishu-long-local"
    )
    assert long_connection["enabled"] is False
    assert long_connection["config"]["connection_mode"] == "long_connection"
    assert long_connection["config"]["send_mode"] == "lark_cli"


def test_save_bindings_writes_atomic_payload(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()

    save_bindings(
        settings,
        [
            Binding(
                agent_id="main",
                tier=5,
                match_key="default",
                match_value="*",
                priority=0,
            )
        ],
    )

    payload = settings.bindings_config_file.read_text(encoding="utf-8")
    assert '"agent_id": "main"' in payload
    assert not (settings.bindings_config_file.parent / ".tmp.bindings.json").exists()


def test_save_agents_writes_structured_payload(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()

    save_agents(
        settings,
        [
            AgentConfig(
                id="planner",
                name="Planner",
                personality="careful",
                model="deepseek-v4-pro",
                dm_scope="per-channel-peer",
                extra_system="extra",
            )
        ],
    )

    payload = settings.agents_config_file.read_text(encoding="utf-8")
    assert '"id": "planner"' in payload
    assert '"dm_scope": "per-channel-peer"' in payload


def test_save_profiles_preserves_env_fields(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    settings.profiles_config_file.write_text(
        """{
  "profiles": [
    {
      "name": "primary",
      "provider": "anthropic",
      "api_key_env": "ANTHROPIC_API_KEY",
      "base_url_env": "ANTHROPIC_BASE_URL"
    }
  ]
}
""",
        encoding="utf-8",
    )

    save_auth_profiles(
        settings,
        [
            AuthProfile(
                name="primary",
                provider="anthropic",
                api_key="should-not-be-written",
                base_url="https://should-not-be-written",
            )
        ],
    )

    payload = settings.profiles_config_file.read_text(encoding="utf-8")
    assert '"api_key_env": "ANTHROPIC_API_KEY"' in payload
    assert '"base_url_env": "ANTHROPIC_BASE_URL"' in payload
    assert "should-not-be-written" not in payload


def test_save_channel_accounts_preserves_env_backed_fields(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    settings.channels_config_file.write_text(
        """{
  "channels": [
    {
      "channel": "feishu",
      "account_id": "feishu-main",
      "enabled": true,
      "label": "Feishu Bot",
      "config": {
        "app_id_env": "FEISHU_APP_ID",
        "app_secret_env": "FEISHU_APP_SECRET",
        "verification_token_env": "FEISHU_VERIFICATION_TOKEN",
        "encrypt_key_env": "FEISHU_ENCRYPT_KEY",
        "bot_open_id_env": "FEISHU_BOT_OPEN_ID",
        "webhook_path": "/webhooks/feishu",
        "is_lark": false
      }
    }
  ]
}
""",
        encoding="utf-8",
    )

    save_channel_accounts(
        settings,
        [
            ChannelAccount(
                channel="feishu",
                account_id="feishu-main",
                label="Feishu Bot",
                token="",
                config={
                    "app_id": "resolved-app-id",
                    "app_secret": "resolved-secret",
                    "verification_token": "resolved-verification-token",
                    "encrypt_key": "resolved-key",
                    "bot_open_id": "resolved-bot",
                    "webhook_path": "/webhooks/feishu",
                    "is_lark": False,
                },
            )
        ],
    )

    payload = settings.channels_config_file.read_text(encoding="utf-8")
    assert '"app_id_env": "FEISHU_APP_ID"' in payload
    assert '"app_secret_env": "FEISHU_APP_SECRET"' in payload
    assert '"verification_token_env": "FEISHU_VERIFICATION_TOKEN"' in payload
    assert '"webhook_path": "/webhooks/feishu"' in payload
    assert "resolved-app-id" not in payload
