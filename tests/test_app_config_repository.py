from __future__ import annotations

from pathlib import Path

from agent_gateway import app as gateway_app
from agent_gateway.app import build_application
from agent_gateway.config import GatewaySettings
from agent_gateway.runtime.state.factory import StateRepositoryBundle
from agent_gateway.runtime.state.repository import StateReadRepository


class FakeConfigReadRepository(StateReadRepository):
    """只返回配置表数据的测试仓储。"""

    def __init__(self) -> None:
        self.rows = {
            "agents": [
                {
                    "id": "db-agent",
                    "name": "Database Agent",
                    "personality": "from-db",
                    "model": "db-model",
                    "dm_scope": "per-peer",
                    "extra_system": "",
                    "tool_policy": {"mode": "all", "tool_names": []},
                    "memory_policy": {"enabled": True, "auto_recall": True, "top_k": 3},
                    "prompt_policy": {
                        "prompt_dir": "",
                        "use_global_files": True,
                        "skills_enabled": True,
                    },
                }
            ],
            "bindings": [
                {
                    "key": "db-agent\x1fdefault\x1f*",
                    "agent_id": "db-agent",
                    "tier": 5,
                    "match_key": "default",
                    "match_value": "*",
                    "priority": 9,
                }
            ],
            "profiles": [
                {
                    "name": "db-profile",
                    "provider": "anthropic",
                    "api_key_env": "DB_PROFILE_API_KEY",
                    "base_url_env": "DB_PROFILE_BASE_URL",
                }
            ],
            "channels": [
                {
                    "key": "cli\x1fcli-db",
                    "channel": "cli",
                    "account_id": "cli-db",
                    "enabled": True,
                    "label": "DB CLI",
                    "token_env": "DB_CHANNEL_TOKEN",
                    "config": {"trace_env": "DB_CHANNEL_TRACE"},
                },
                {
                    "key": "telegram\x1fdisabled",
                    "channel": "telegram",
                    "account_id": "disabled",
                    "enabled": False,
                    "label": "Disabled",
                    "token": "disabled-token",
                    "config": {},
                }
            ],
        }

    def list(self, table: str, *, limit: int = 50, cursor: str = "", filters=None):
        del limit, cursor, filters
        return list(self.rows.get(table, []))

    def get(self, table: str, key: str):
        del table, key
        return None


def test_build_application_prefers_state_repository_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
        postgres_enabled=True,
    )

    fake_read = FakeConfigReadRepository()
    monkeypatch.setenv("DB_PROFILE_API_KEY", "db-key")
    monkeypatch.setenv("DB_PROFILE_BASE_URL", "https://db.example/anthropic")
    monkeypatch.setenv("DB_CHANNEL_TOKEN", "db-channel-token")
    monkeypatch.setenv("DB_CHANNEL_TRACE", "trace-from-env")

    def fake_build_state_repository(settings, **kwargs):
        del settings, kwargs
        return StateRepositoryBundle(read=fake_read, write=None, config_write=None, backup=None)

    monkeypatch.setattr(gateway_app, "build_state_repository", fake_build_state_repository)

    application = build_application(settings)

    assert application.agents.get("db-agent") is not None
    assert application.agents.get("main") is None
    assert application.bindings.list_all()[0].agent_id == "db-agent"
    assert application.profile_manager.profiles[0].name == "db-profile"
    assert application.profile_manager.profiles[0].api_key == "db-key"
    assert application.profile_manager.profiles[0].base_url == "https://db.example/anthropic"
    assert application.channel_manager.accounts[0].account_id == "cli-db"
    assert application.channel_manager.accounts[0].token == "db-channel-token"
    assert application.channel_manager.accounts[0].config["trace"] == "trace-from-env"
    assert len(application.channel_manager.accounts) == 1


def test_build_application_injects_primary_write_backend_only_when_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class FakeWriteRepository:
        def __init__(self, *, enabled: bool) -> None:
            self.enabled = enabled

    fake_read = FakeConfigReadRepository()

    def build_with_write_enabled(enabled: bool):
        settings = GatewaySettings(
            config_dir=tmp_path / ("config-on" if enabled else "config-off"),
            data_dir=tmp_path / ("data-on" if enabled else "data-off"),
            workspace_root=tmp_path / ("workspace-on" if enabled else "workspace-off"),
            postgres_enabled=enabled,
        )

        def fake_build_state_repository(settings, **kwargs):
            del settings, kwargs
            writer = FakeWriteRepository(enabled=enabled)
            return StateRepositoryBundle(
                read=fake_read,
                write=writer,
                config_write=writer,
                backup=None,
            )

        monkeypatch.setattr(gateway_app, "build_state_repository", fake_build_state_repository)
        return build_application(settings)

    disabled_app = build_with_write_enabled(False)
    enabled_app = build_with_write_enabled(True)

    assert disabled_app.sessions.write_backend is None
    assert disabled_app.task_store.write_backend is None
    assert disabled_app.event_store.write_backend is None
    assert disabled_app.memory_store.write_backend is None
    assert disabled_app.metrics_store.write_backend is None
    assert disabled_app.alert_store.write_backend is None
    assert disabled_app.delivery_queue.write_backend is None
    assert enabled_app.sessions.write_backend is not None
    assert enabled_app.task_store.write_backend is not None
    assert enabled_app.event_store.write_backend is not None
    assert enabled_app.memory_store.write_backend is not None
    assert enabled_app.metrics_store.write_backend is not None
    assert enabled_app.alert_store.write_backend is not None
    assert enabled_app.delivery_queue.write_backend is not None
