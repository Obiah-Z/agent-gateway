from pathlib import Path

from agent_gateway.config import GatewaySettings
from agent_gateway.config_loader import ensure_default_project_files, load_channel_accounts


def test_channel_accounts_loader_returns_enabled_channels(tmp_path: Path) -> None:
    settings = GatewaySettings(
        config_dir=tmp_path / "config",
        data_dir=tmp_path / "data",
        workspace_root=tmp_path / "workspace",
    )
    settings.ensure_directories()
    ensure_default_project_files(settings)

    accounts = load_channel_accounts(settings)

    assert len(accounts) == 1
    assert accounts[0].channel == "cli"
