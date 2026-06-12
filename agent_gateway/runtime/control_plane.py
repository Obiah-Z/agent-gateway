from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_gateway.agents import AgentManager
from agent_gateway.channels.bootstrap import build_channel_manager
from agent_gateway.channels.manager import ChannelManager
from agent_gateway.config import GatewaySettings
from agent_gateway.config_loader import (
    load_agents,
    load_auth_profiles,
    load_bindings,
    load_channel_accounts,
    read_agents_source,
    read_bindings_source,
    read_channels_source,
    read_profiles_source,
    save_agents,
    save_auth_profiles,
    save_bindings,
    save_channel_accounts,
    write_json_atomic,
)
from agent_gateway.models import AgentConfig, Binding
from agent_gateway.router import BindingTable, normalize_agent_id
from agent_gateway.runtime.agent_manifest import (
    ALLOWED_TOOL_CAPABILITIES,
    build_agent_template,
    materialize_agent_template,
    validate_agent_config,
)
from agent_gateway.runtime.autonomy import AutonomyRuntime
from agent_gateway.runtime.channel_runtime import ChannelRuntime
from agent_gateway.runtime.resilience import AuthProfile
from agent_gateway.runtime.resilience import ProfileManager
from agent_gateway.tools.registry import ToolRegistry


SUPPORTED_CHANNELS = {"cli", "telegram", "feishu"}


@dataclass(slots=True)
class GatewayControlPlane:
    settings: GatewaySettings
    agents: AgentManager
    bindings: BindingTable
    profiles: ProfileManager
    channels: ChannelManager
    tools: ToolRegistry | None = None
    autonomy: AutonomyRuntime | None = None
    channel_runtime: ChannelRuntime | None = None

    def list_bindings(self) -> list[Binding]:
        return self.bindings.list_all()

    def list_agents(self) -> list[AgentConfig]:
        return self.agents.list()

    def list_tool_capabilities(self) -> list[dict[str, Any]]:
        if self.tools is None:
            return []
        return [
            {
                "tag": tag,
                "tools": self.tools.names_for_tags([tag]),
            }
            for tag in sorted(ALLOWED_TOOL_CAPABILITIES)
        ]

    def validate_agent(self, agent: AgentConfig) -> list[str]:
        if self.tools is None:
            return []
        return validate_agent_config(agent, self.tools)

    def list_profiles(self) -> list[dict[str, Any]]:
        return self.profiles.snapshot()

    def list_channels(self) -> list[dict[str, Any]]:
        active_accounts = {
            (account.channel, account.account_id): account
            for account in self.channels.accounts
        }
        rows = [
            {
                "channel": row.get("channel", ""),
                "account_id": row.get("account_id", ""),
                "label": row.get("label", ""),
                "enabled": bool(row.get("enabled", True)),
                "active": (row.get("channel", ""), row.get("account_id", "")) in active_accounts,
                "has_token": bool(
                    row.get("token")
                    or row.get("token_env")
                    or active_accounts.get((row.get("channel", ""), row.get("account_id", "")), None)
                ),
                "config_keys": sorted(
                    row.get("config", {}).keys() if isinstance(row.get("config"), dict) else []
                ),
            }
            for row in self.get_source("channels").get("channels", [])
            if isinstance(row, dict)
        ]
        seen = {(row["channel"], row["account_id"]) for row in rows}
        for key, account in active_accounts.items():
            if key in seen:
                continue
            rows.append(
                {
                    "channel": account.channel,
                    "account_id": account.account_id,
                    "label": account.label,
                    "enabled": True,
                    "active": True,
                    "has_token": bool(account.token),
                    "config_keys": sorted(account.config.keys()),
                }
            )
        return rows

    def get_source(self, kind: str) -> dict[str, Any]:
        readers = {
            "agents": read_agents_source,
            "bindings": read_bindings_source,
            "profiles": read_profiles_source,
            "channels": read_channels_source,
        }
        reader = readers.get(kind)
        if reader is None:
            raise ValueError(f"unknown source kind: {kind}")
        return reader(self.settings)

    def add_binding(self, binding: Binding) -> Binding:
        binding.agent_id = normalize_agent_id(binding.agent_id)
        self.bindings.add(binding)
        return binding

    def set_agent(
        self,
        *,
        agent_id: str,
        name: str | None = None,
        personality: str | None = None,
        model: str | None = None,
        dm_scope: str | None = None,
        extra_system: str | None = None,
        tool_policy_mode: str | None = None,
        tool_names: list[str] | None = None,
        memory_enabled: bool | None = None,
        memory_auto_recall: bool | None = None,
        memory_top_k: int | None = None,
        prompt_dir: str | None = None,
        use_global_prompt_files: bool | None = None,
        skills_enabled: bool | None = None,
    ) -> AgentConfig:
        normalized = normalize_agent_id(agent_id)
        payload = self.get_source("agents")
        rows = [row for row in payload.get("agents", []) if isinstance(row, dict)]
        existing_index, existing = self._find_agent_row(rows, normalized)
        row = dict(existing or {})
        tool_policy = dict(row.get("tool_policy", {}) if isinstance(row.get("tool_policy"), dict) else {})
        memory_policy = dict(row.get("memory_policy", {}) if isinstance(row.get("memory_policy"), dict) else {})
        prompt_policy = dict(row.get("prompt_policy", {}) if isinstance(row.get("prompt_policy"), dict) else {})
        row["id"] = normalized
        row["name"] = name if name is not None else str(row.get("name", normalized)) or normalized
        if personality is not None:
            row["personality"] = personality
        row.setdefault("personality", "")
        if model is not None:
            row["model"] = model
        row.setdefault("model", "")
        if dm_scope is not None:
            row["dm_scope"] = dm_scope
        row.setdefault("dm_scope", "per-peer")
        if extra_system is not None:
            row["extra_system"] = extra_system
        row.setdefault("extra_system", "")
        if tool_policy_mode is not None:
            tool_policy["mode"] = tool_policy_mode
        if tool_names is not None:
            tool_policy["tool_names"] = [str(name) for name in tool_names if str(name).strip()]
        if memory_enabled is not None:
            memory_policy["enabled"] = memory_enabled
        if memory_auto_recall is not None:
            memory_policy["auto_recall"] = memory_auto_recall
        if memory_top_k is not None:
            memory_policy["top_k"] = max(1, int(memory_top_k))
        if prompt_dir is not None:
            prompt_policy["prompt_dir"] = prompt_dir
        if use_global_prompt_files is not None:
            prompt_policy["use_global_files"] = use_global_prompt_files
        if skills_enabled is not None:
            prompt_policy["skills_enabled"] = skills_enabled
        row["tool_policy"] = {
            "mode": str(tool_policy.get("mode", "all") or "all"),
            "tool_names": [str(name) for name in tool_policy.get("tool_names", []) if str(name).strip()],
        }
        row["memory_policy"] = {
            "enabled": bool(memory_policy.get("enabled", True)),
            "auto_recall": bool(memory_policy.get("auto_recall", True)),
            "top_k": max(1, int(memory_policy.get("top_k", 3) or 3)),
        }
        row["prompt_policy"] = {
            "prompt_dir": str(prompt_policy.get("prompt_dir", "")),
            "use_global_files": bool(prompt_policy.get("use_global_files", True)),
            "skills_enabled": bool(prompt_policy.get("skills_enabled", True)),
        }
        candidate = AgentConfig(
            id=normalized,
            name=str(row["name"]),
            personality=str(row["personality"]),
            model=str(row["model"]),
            dm_scope=str(row["dm_scope"]),
            extra_system=str(row["extra_system"]),
            tool_policy_mode=str(row["tool_policy"]["mode"]),
            tool_names=tuple(str(name) for name in row["tool_policy"]["tool_names"]),
            memory_enabled=bool(row["memory_policy"]["enabled"]),
            memory_auto_recall=bool(row["memory_policy"]["auto_recall"]),
            memory_top_k=int(row["memory_policy"]["top_k"]),
            prompt_dir=str(row["prompt_policy"]["prompt_dir"]),
            use_global_prompt_files=bool(row["prompt_policy"]["use_global_files"]),
            skills_enabled=bool(row["prompt_policy"]["skills_enabled"]),
        )
        issues = self.validate_agent(candidate)
        if issues:
            raise ValueError("; ".join(issues))
        self._write_rows(self.settings.agents_config_file, "agents", rows, existing_index, row)
        self.reload_agents()
        agent = self.agents.get(normalized)
        if agent is None:
            raise RuntimeError(f"agent '{normalized}' was not reloaded")
        return agent

    def generate_agent_template(
        self,
        *,
        agent_id: str,
        name: str = "",
        capability_tags: list[str] | None = None,
        use_global_prompt_files: bool = True,
        memory_enabled: bool = True,
        skills_enabled: bool = True,
        write_files: bool = True,
    ) -> dict[str, Any]:
        template = build_agent_template(
            agent_id,
            name=name,
            capability_tags=capability_tags or [],
            use_global_prompt_files=use_global_prompt_files,
            memory_enabled=memory_enabled,
            skills_enabled=skills_enabled,
            tools=self.tools,
        )
        written_files = (
            materialize_agent_template(self.settings.workspace_root, template)
            if write_files
            else []
        )
        return {
            "agent": template.agent,
            "prompt_files": template.prompt_files,
            "written_files": written_files,
        }

    def remove_agent(self, agent_id: str) -> bool:
        normalized = normalize_agent_id(agent_id)
        rows = [row for row in self.get_source("agents").get("agents", []) if isinstance(row, dict)]
        existing_index, _existing = self._find_agent_row(rows, normalized)
        if existing_index < 0:
            return False
        if len(rows) <= 1:
            raise RuntimeError("cannot remove the last agent")
        if any(binding.agent_id == normalized for binding in self.bindings.list_all()):
            raise RuntimeError(f"agent '{normalized}' is still referenced by bindings")
        if normalize_agent_id(self.settings.proactive_agent_id) == normalized:
            raise RuntimeError(f"agent '{normalized}' is configured as proactive agent")
        del rows[existing_index]
        write_json_atomic(self.settings.agents_config_file, {"agents": rows})
        self.reload_agents()
        return True

    def remove_binding(self, agent_id: str, match_key: str, match_value: str) -> bool:
        return self.bindings.remove(normalize_agent_id(agent_id), match_key, match_value)

    def save_bindings(self) -> int:
        bindings = self.bindings.list_all()
        save_bindings(self.settings, bindings)
        return len(bindings)

    def save_agents(self) -> int:
        agents = self.agents.list()
        save_agents(self.settings, agents)
        return len(agents)

    def save_profiles(self) -> int:
        profiles = list(self.profiles.profiles)
        save_auth_profiles(self.settings, profiles)
        return len(profiles)

    def save_channels(self) -> int:
        accounts = list(self.channels.accounts)
        save_channel_accounts(self.settings, accounts)
        return len(accounts)

    def set_profile(
        self,
        *,
        name: str,
        provider: str | None = None,
        api_key: str | None = None,
        api_key_env: str | None = None,
        base_url: str | None = None,
        base_url_env: str | None = None,
    ) -> dict[str, Any]:
        if api_key and api_key_env:
            raise ValueError("api_key and api_key_env are mutually exclusive")
        if base_url and base_url_env:
            raise ValueError("base_url and base_url_env are mutually exclusive")
        rows = [row for row in self.get_source("profiles").get("profiles", []) if isinstance(row, dict)]
        existing_index, existing = self._find_profile_row(rows, name)
        row = dict(existing or {})
        row["name"] = name
        if provider is not None:
            row["provider"] = provider
        row.setdefault("provider", "anthropic")
        self._apply_secret_field(row, "api_key", api_key, api_key_env)
        self._apply_secret_field(row, "base_url", base_url, base_url_env)
        self._write_rows(self.settings.profiles_config_file, "profiles", rows, existing_index, row)
        snapshot = self.reload_profiles()
        return self._find_profile_snapshot(snapshot, name)

    def remove_profile(self, name: str) -> bool:
        rows = [row for row in self.get_source("profiles").get("profiles", []) if isinstance(row, dict)]
        existing_index, _existing = self._find_profile_row(rows, name)
        if existing_index < 0:
            return False
        if len(rows) <= 1:
            raise RuntimeError("cannot remove the last profile")
        del rows[existing_index]
        write_json_atomic(self.settings.profiles_config_file, {"profiles": rows})
        self.reload_profiles()
        return True

    def reload_bindings(self) -> int:
        bindings = load_bindings(self.settings)
        for binding in bindings:
            binding.agent_id = normalize_agent_id(binding.agent_id)
        self.bindings.replace_all(bindings)
        return len(bindings)

    def reload_agents(self) -> list[AgentConfig]:
        agents = load_agents(self.settings)
        if not agents:
            raise RuntimeError("No agents loaded from config")
        self.agents.replace_all(agents)
        return self.agents.list()

    def reload_profiles(self) -> list[dict[str, Any]]:
        profiles = load_auth_profiles(self.settings)
        self.profiles.replace_profiles(profiles)
        return self.profiles.snapshot()

    async def set_channel(
        self,
        *,
        channel: str,
        account_id: str,
        enabled: bool | None = None,
        label: str | None = None,
        token: str | None = None,
        token_env: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if token and token_env:
            raise ValueError("token and token_env are mutually exclusive")
        normalized_channel = channel.strip().lower()
        if normalized_channel not in SUPPORTED_CHANNELS:
            raise ValueError(f"unsupported channel: {normalized_channel}")
        if not account_id.strip():
            raise ValueError("account_id is required")
        rows = [row for row in self.get_source("channels").get("channels", []) if isinstance(row, dict)]
        existing_index, existing = self._find_channel_row(rows, normalized_channel, account_id)
        row = dict(existing or {})
        row["channel"] = normalized_channel
        row["account_id"] = account_id
        if enabled is not None:
            row["enabled"] = enabled
        row.setdefault("enabled", True)
        if label is not None:
            row["label"] = label
        row.setdefault("label", "")
        self._apply_secret_field(row, "token", token, token_env)
        merged_config = self._merge_channel_config(
            row.get("config", {}) if isinstance(row.get("config"), dict) else {},
            config or {},
        )
        row["config"] = merged_config
        self._write_rows(self.settings.channels_config_file, "channels", rows, existing_index, row)
        await self.reload_channels()
        return self._find_channel_descriptor(normalized_channel, account_id)

    async def remove_channel(self, channel: str, account_id: str) -> bool:
        normalized_channel = channel.strip().lower()
        rows = [row for row in self.get_source("channels").get("channels", []) if isinstance(row, dict)]
        existing_index, _existing = self._find_channel_row(rows, normalized_channel, account_id)
        if existing_index < 0:
            return False
        if (
            normalized_channel == self.settings.proactive_channel.strip().lower()
            and account_id == self.settings.proactive_account_id
        ):
            raise RuntimeError("cannot remove the configured proactive channel account")
        del rows[existing_index]
        write_json_atomic(self.settings.channels_config_file, {"channels": rows})
        await self.reload_channels()
        return True

    async def reload_channels(self) -> list[str]:
        next_manager = build_channel_manager(self.settings, load_channel_accounts(self.settings))
        if self.channel_runtime is not None:
            await self.channel_runtime.restart(next_manager)
            self.channels.replace_from(next_manager)
            if hasattr(self.channel_runtime, "channels"):
                self.channel_runtime.channels = self.channels
            delivery_runtime = getattr(self.channel_runtime, "delivery_runtime", None)
            if delivery_runtime is not None:
                delivery_runtime.channels = self.channels
        else:
            self.channels.close_all()
            self.channels.replace_from(next_manager)
        if self.autonomy is not None:
            self.autonomy.set_channels(self.channels)
        return self.channels.list_channels()

    @staticmethod
    def _find_agent_row(rows: list[dict[str, Any]], agent_id: str) -> tuple[int, dict[str, Any] | None]:
        for index, row in enumerate(rows):
            if normalize_agent_id(str(row.get("id", ""))) == agent_id:
                return index, row
        return -1, None

    @staticmethod
    def _find_profile_row(rows: list[dict[str, Any]], name: str) -> tuple[int, dict[str, Any] | None]:
        for index, row in enumerate(rows):
            if str(row.get("name", "")) == name:
                return index, row
        return -1, None

    @staticmethod
    def _find_channel_row(
        rows: list[dict[str, Any]],
        channel: str,
        account_id: str,
    ) -> tuple[int, dict[str, Any] | None]:
        for index, row in enumerate(rows):
            if str(row.get("channel", "")).strip().lower() == channel and str(
                row.get("account_id", "")
            ) == account_id:
                return index, row
        return -1, None

    @staticmethod
    def _write_rows(
        path,
        root_key: str,
        rows: list[dict[str, Any]],
        existing_index: int,
        row: dict[str, Any],
    ) -> None:
        if existing_index >= 0:
            rows[existing_index] = row
        else:
            rows.append(row)
        write_json_atomic(path, {root_key: rows})

    @staticmethod
    def _apply_secret_field(
        row: dict[str, Any],
        field: str,
        literal_value: str | None,
        env_value: str | None,
    ) -> None:
        env_key = f"{field}_env"
        if env_value is not None:
            row.pop(field, None)
            if env_value:
                row[env_key] = env_value
            else:
                row.pop(env_key, None)
        if literal_value is not None:
            row.pop(env_key, None)
            if literal_value:
                row[field] = literal_value
            else:
                row.pop(field, None)

    @staticmethod
    def _merge_channel_config(
        current: dict[str, Any],
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(current)
        for key, value in patch.items():
            if key.endswith("_env"):
                base_key = key[:-4]
                merged.pop(base_key, None)
                if value in ("", None):
                    merged.pop(key, None)
                else:
                    merged[key] = value
                continue
            env_key = f"{key}_env"
            merged.pop(env_key, None)
            if value is None:
                merged.pop(key, None)
            else:
                merged[key] = value
        return merged

    @staticmethod
    def _find_profile_snapshot(snapshot: list[dict[str, Any]], name: str) -> dict[str, Any]:
        for row in snapshot:
            if row.get("name") == name:
                return row
        raise RuntimeError(f"profile '{name}' was not reloaded")

    def _find_channel_descriptor(self, channel: str, account_id: str) -> dict[str, Any]:
        for row in self.list_channels():
            if row.get("channel") == channel and row.get("account_id") == account_id:
                return row
        raise RuntimeError(f"channel '{channel}/{account_id}' was not reloaded")
