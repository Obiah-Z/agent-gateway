from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from agent_gateway.channels.base import ChannelAccount
from agent_gateway.config import GatewaySettings
from agent_gateway.models import AgentConfig, Binding
from agent_gateway.runtime.resilience import AuthProfile


def ensure_default_project_files(settings: GatewaySettings) -> None:
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    _write_json_if_missing(
        settings.agents_config_file,
        {
            "agents": [
                {
                    "id": settings.default_agent_id,
                    "name": settings.default_agent_name,
                    "personality": "direct, pragmatic, and tool-capable",
                    "model": "",
                    "dm_scope": "per-peer",
                    "extra_system": "",
                    "tool_policy": {
                        "mode": "all",
                        "tool_names": [],
                    },
                    "memory_policy": {
                        "enabled": True,
                        "auto_recall": True,
                        "top_k": 3,
                    },
                    "prompt_policy": {
                        "prompt_dir": "",
                        "use_global_files": True,
                        "skills_enabled": True,
                    },
                }
            ]
        },
    )
    _write_json_if_missing(
        settings.bindings_config_file,
        {
            "bindings": [
                {
                    "agent_id": settings.default_agent_id,
                    "tier": 5,
                    "match_key": "default",
                    "match_value": "*",
                    "priority": 0,
                }
            ]
        },
    )
    _write_json_if_missing(
        settings.profiles_config_file,
        {
            "profiles": [
                {
                    "name": "primary",
                    "provider": "anthropic",
                    "api_key_env": "ANTHROPIC_API_KEY",
                    "base_url_env": "ANTHROPIC_BASE_URL",
                }
            ]
        },
    )
    _write_json_if_missing(
        settings.channels_config_file,
        {
            "channels": [
                {
                    "channel": "cli",
                    "account_id": "cli-local",
                    "enabled": True,
                    "label": "Local CLI",
                    "token": "",
                    "config": {},
                },
                {
                    "channel": "telegram",
                    "account_id": "telegram-main",
                    "enabled": False,
                    "label": "Telegram Bot",
                    "token_env": "TELEGRAM_BOT_TOKEN",
                    "config": {
                        "allowed_chats": ""
                    },
                },
                {
                    "channel": "feishu",
                    "account_id": "feishu-main",
                    "enabled": False,
                    "label": "Feishu Bot",
                    "config": {
                        "app_id_env": "FEISHU_APP_ID",
                        "app_secret_env": "FEISHU_APP_SECRET",
                        "verification_token_env": "FEISHU_VERIFICATION_TOKEN",
                        "encrypt_key_env": "FEISHU_ENCRYPT_KEY",
                        "bot_open_id_env": "FEISHU_BOT_OPEN_ID",
                        "render_mode_env": "FEISHU_RENDER_MODE",
                        "card_page_max_bytes_env": "FEISHU_CARD_PAGE_MAX_BYTES",
                        "text_page_max_bytes_env": "FEISHU_TEXT_PAGE_MAX_BYTES",
                        "enable_stateful_cards_env": "FEISHU_ENABLE_STATEFUL_CARDS",
                        "webhook_path": "/webhooks/feishu",
                        "is_lark": False
                    },
                },
                {
                    "channel": "feishu",
                    "account_id": "feishu-long-local",
                    "enabled": False,
                    "label": "Feishu Long Connection",
                    "config": {
                        "connection_mode": "long_connection",
                        "send_mode": "lark_cli",
                        "event_key": "im.message.receive_v1",
                        "event_keys": [
                            "im.message.receive_v1",
                            "im.chat.member.bot.added_v1"
                        ],
                        "event_identity": "bot",
                        "event_command": "lark-cli",
                        "lark_cli_command": "lark-cli",
                        "lark_cli_identity": "bot",
                        "render_mode": "text",
                        "is_lark": False
                    },
                }
            ]
        },
    )


def load_agents(settings: GatewaySettings) -> list[AgentConfig]:
    payload = _read_json(settings.agents_config_file, {"agents": []})
    agents = []
    for item in payload.get("agents", []):
        tool_policy = item.get("tool_policy", {}) if isinstance(item, dict) else {}
        memory_policy = item.get("memory_policy", {}) if isinstance(item, dict) else {}
        prompt_policy = item.get("prompt_policy", {}) if isinstance(item, dict) else {}
        agents.append(
            AgentConfig(
                id=item["id"],
                name=item["name"],
                personality=item.get("personality", ""),
                model=item.get("model", ""),
                dm_scope=item.get("dm_scope", "per-peer"),
                extra_system=item.get("extra_system", ""),
                tool_policy_mode=str(tool_policy.get("mode", "all") or "all"),
                tool_names=tuple(
                    str(name)
                    for name in tool_policy.get("tool_names", [])
                    if str(name).strip()
                ),
                memory_enabled=bool(memory_policy.get("enabled", True)),
                memory_auto_recall=bool(memory_policy.get("auto_recall", True)),
                memory_top_k=max(1, int(memory_policy.get("top_k", 3) or 3)),
                prompt_dir=str(prompt_policy.get("prompt_dir", "")),
                use_global_prompt_files=bool(prompt_policy.get("use_global_files", True)),
                skills_enabled=bool(prompt_policy.get("skills_enabled", True)),
            )
        )
    return agents


def save_agents(settings: GatewaySettings, agents: list[AgentConfig]) -> None:
    write_json_atomic(
        settings.agents_config_file,
        {
            "agents": [item.manifest_row() for item in agents]
        },
    )


def load_bindings(settings: GatewaySettings) -> list[Binding]:
    payload = _read_json(settings.bindings_config_file, {"bindings": []})
    bindings = []
    for item in payload.get("bindings", []):
        bindings.append(
            Binding(
                agent_id=item["agent_id"],
                tier=int(item["tier"]),
                match_key=item["match_key"],
                match_value=item["match_value"],
                priority=int(item.get("priority", 0)),
            )
        )
    return bindings


def save_bindings(settings: GatewaySettings, bindings: list[Binding]) -> None:
    write_json_atomic(
        settings.bindings_config_file,
        {
            "bindings": [
                {
                    "agent_id": item.agent_id,
                    "tier": item.tier,
                    "match_key": item.match_key,
                    "match_value": item.match_value,
                    "priority": item.priority,
                }
                for item in bindings
            ]
        },
    )


def load_auth_profiles(settings: GatewaySettings) -> list[AuthProfile]:
    payload = _read_json(settings.profiles_config_file, {"profiles": []})
    profiles: list[AuthProfile] = []
    for item in payload.get("profiles", []):
        api_key = item.get("api_key", "")
        if not api_key and item.get("api_key_env"):
            api_key = os.getenv(item["api_key_env"], "")

        base_url = item.get("base_url", "")
        if not base_url and item.get("base_url_env"):
            base_url = os.getenv(item["base_url_env"], "")

        profiles.append(
            AuthProfile(
                name=item.get("name", "primary"),
                provider=item.get("provider", "anthropic"),
                api_key=api_key,
                base_url=base_url,
            )
        )

    if not profiles:
        profiles.append(
            AuthProfile(
                name="primary",
                provider="anthropic",
                api_key=settings.anthropic_api_key,
                base_url=settings.anthropic_base_url,
            )
        )
    return profiles


def save_auth_profiles(settings: GatewaySettings, profiles: list[AuthProfile]) -> None:
    existing = read_profiles_source(settings).get("profiles", [])
    existing_by_name = {
        str(item.get("name", "")): item
        for item in existing
        if isinstance(item, dict) and item.get("name")
    }
    rows: list[dict[str, Any]] = []
    for profile in profiles:
        raw = existing_by_name.get(profile.name, {})
        row: dict[str, Any] = {
            "name": profile.name,
            "provider": profile.provider,
        }
        if raw.get("api_key_env"):
            row["api_key_env"] = raw["api_key_env"]
        else:
            row["api_key"] = profile.api_key
        if raw.get("base_url_env"):
            row["base_url_env"] = raw["base_url_env"]
        elif profile.base_url:
            row["base_url"] = profile.base_url
        rows.append(row)
    write_json_atomic(settings.profiles_config_file, {"profiles": rows})


def load_channel_accounts(settings: GatewaySettings) -> list[ChannelAccount]:
    payload = _read_json(settings.channels_config_file, {"channels": []})
    accounts: list[ChannelAccount] = []
    for item in payload.get("channels", []):
        if not item.get("enabled", True):
            continue
        token = item.get("token", "")
        if not token and item.get("token_env"):
            token = os.getenv(item["token_env"], "")

        config = dict(item.get("config", {}))
        for key, value in list(config.items()):
            if key.endswith("_env") and isinstance(value, str):
                config[key[:-4]] = os.getenv(value, "")

        accounts.append(
            ChannelAccount(
                channel=item["channel"],
                account_id=item["account_id"],
                label=item.get("label", ""),
                token=token,
                config=config,
            )
        )
    return accounts


def save_channel_accounts(settings: GatewaySettings, accounts: list[ChannelAccount]) -> None:
    existing = read_channels_source(settings).get("channels", [])
    existing_by_key = {
        (str(item.get("channel", "")), str(item.get("account_id", ""))): item
        for item in existing
        if isinstance(item, dict)
    }
    active_keys = {(account.channel, account.account_id) for account in accounts}
    rows: list[dict[str, Any]] = []
    for account in accounts:
        raw = existing_by_key.get((account.channel, account.account_id), {})
        row: dict[str, Any] = {
            "channel": account.channel,
            "account_id": account.account_id,
            "enabled": bool(raw.get("enabled", True)),
            "label": account.label,
            "config": _serialize_channel_config(account.config, raw.get("config", {})),
        }
        if raw.get("token_env"):
            row["token_env"] = raw["token_env"]
        else:
            row["token"] = account.token
        rows.append(row)

    for item in existing:
        if not isinstance(item, dict):
            continue
        key = (str(item.get("channel", "")), str(item.get("account_id", "")))
        if key not in active_keys:
            rows.append(item)

    write_json_atomic(settings.channels_config_file, {"channels": rows})


def read_agents_source(settings: GatewaySettings) -> dict[str, Any]:
    return _read_json(settings.agents_config_file, {"agents": []})


def read_bindings_source(settings: GatewaySettings) -> dict[str, Any]:
    return _read_json(settings.bindings_config_file, {"bindings": []})


def read_profiles_source(settings: GatewaySettings) -> dict[str, Any]:
    return _read_json(settings.profiles_config_file, {"profiles": []})


def read_channels_source(settings: GatewaySettings) -> dict[str, Any]:
    return _read_json(settings.channels_config_file, {"channels": []})


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _write_json_if_missing(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        return
    write_json_atomic(path, payload)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".tmp.{path.name}")
    tmp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)


def _serialize_channel_config(
    current: dict[str, Any],
    raw: dict[str, Any] | object,
) -> dict[str, Any]:
    raw_config = raw if isinstance(raw, dict) else {}
    env_keys = {
        key[:-4]
        for key in list(raw_config.keys()) + list(current.keys())
        if key.endswith("_env")
    }
    result: dict[str, Any] = {}

    for key, value in raw_config.items():
        if key.endswith("_env"):
            result[key] = value

    for key, value in current.items():
        if key.endswith("_env"):
            result[key] = value
            continue
        if key in env_keys:
            continue
        result[key] = value

    for key, value in raw_config.items():
        if key.endswith("_env") or key in env_keys or key in result:
            continue
        result[key] = value

    return result
