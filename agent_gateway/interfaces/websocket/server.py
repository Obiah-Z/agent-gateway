from __future__ import annotations

import json
import time
from typing import Any

from websockets.exceptions import ConnectionClosed

from agent_gateway.core.models import Binding, InboundMessage
from agent_gateway.application.autonomy import AutonomyRuntime
from agent_gateway.application.control_plane import GatewayControlPlane
from agent_gateway.application.dispatcher import GatewayDispatcher
from agent_gateway.sessions.store import SessionStore


class GatewayServer:
    def __init__(
        self,
        host: str,
        port: int,
        dispatcher: GatewayDispatcher,
        sessions: SessionStore,
        autonomy: AutonomyRuntime | None = None,
        control_plane: GatewayControlPlane | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.dispatcher = dispatcher
        self.sessions = sessions
        self.autonomy = autonomy
        self.control_plane = control_plane
        self._server: Any = None
        self._running = False
        self._start_time = time.monotonic()

    async def start(self) -> None:
        try:
            import websockets
        except ImportError as exc:  # pragma: no cover - dependency check
            raise RuntimeError("websockets is not installed") from exc
        self._start_time = time.monotonic()
        self._server = await websockets.serve(self._handle, self.host, self.port)
        self._running = True

    async def wait_closed(self) -> None:
        if self._server is not None:
            await self._server.wait_closed()

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._running = False

    async def _handle(self, websocket: Any) -> None:
        try:
            async for raw in websocket:
                response = await self._dispatch(raw)
                try:
                    await websocket.send(json.dumps(response, ensure_ascii=False))
                except ConnectionClosed:
                    break
        except ConnectionClosed:
            return

    async def _dispatch(self, raw: str) -> dict[str, Any]:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None,
            }

        request_id = request.get("id")
        method = request.get("method", "")
        params = request.get("params", {})
        handlers = {
            "send": self._m_send,
            "bindings.set": self._m_bind_set,
            "bindings.remove": self._m_bind_remove,
            "bindings.save": self._m_bind_save,
            "bindings.reload": self._m_bind_reload,
            "bindings.list": self._m_bind_list,
            "agents.list": self._m_agents,
            "agents.set": self._m_agents_set,
            "agents.remove": self._m_agents_remove,
            "agents.template": self._m_agents_template,
            "agents.capabilities": self._m_agents_capabilities,
            "agents.save": self._m_agents_save,
            "agents.reload": self._m_agents_reload,
            "profiles.list": self._m_profiles_list,
            "profiles.set": self._m_profiles_set,
            "profiles.remove": self._m_profiles_remove,
            "profiles.save": self._m_profiles_save,
            "profiles.reload": self._m_profiles_reload,
            "channels.list": self._m_channels_list,
            "channels.set": self._m_channels_set,
            "channels.remove": self._m_channels_remove,
            "channels.save": self._m_channels_save,
            "channels.reload": self._m_channels_reload,
            "feishu.long_connection.status": self._m_feishu_long_connection_status,
            "feishu.onboarding.start": self._m_feishu_onboarding_start,
            "feishu.onboarding.status": self._m_feishu_onboarding_status,
            "feishu.onboarding.list": self._m_feishu_onboarding_list,
            "config.source": self._m_config_source,
            "sessions.list": self._m_sessions,
            "status": self._m_status,
            "runtime.status": self._m_runtime_status,
            "health.check": self._m_health_check,
            "events.tail": self._m_events_tail,
            "errors.recent": self._m_errors_recent,
            "memory.recent": self._m_memory_recent,
            "ingest": self._m_ingest,
            "heartbeat.status": self._m_heartbeat_status,
            "heartbeat.trigger": self._m_heartbeat_trigger,
            "cron.list": self._m_cron_list,
            "cron.trigger": self._m_cron_trigger,
            "delivery.stats": self._m_delivery_stats,
            "delivery.list": self._m_delivery_list,
            "delivery.retry": self._m_delivery_retry,
            "delivery.discard": self._m_delivery_discard,
            "delivery.flush": self._m_delivery_flush,
        }
        handler = handlers.get(method)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Unknown method: {method}"},
                "id": request_id,
            }

        try:
            result = await handler(params)
            return {"jsonrpc": "2.0", "result": result, "id": request_id}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": str(exc)},
                "id": request_id,
            }

    async def _m_send(self, params: dict[str, Any]) -> dict[str, Any]:
        text = params.get("text", "")
        if not text:
            raise ValueError("text is required")

        inbound = InboundMessage(
            text=text,
            sender_id=params.get("sender_id", params.get("peer_id", "ws-client")),
            channel=params.get("channel", "websocket"),
            account_id=params.get("account_id", "default"),
            peer_id=params.get("peer_id", "ws-client"),
            guild_id=params.get("guild_id", ""),
        )
        result = await self.dispatcher.dispatch_inbound(
            inbound,
            forced_agent_id=params.get("agent_id", ""),
        )
        return {
            "agent_id": result.reply.agent_id,
            "session_key": result.reply.session_key,
            "reply": result.reply.text,
            "stop_reason": result.reply.stop_reason,
            "tool_calls": result.reply.tool_calls,
            "binding": result.route.matched_binding.display() if result.route.matched_binding else None,
        }

    async def _m_bind_set(self, params: dict[str, Any]) -> dict[str, Any]:
        binding = Binding(
            agent_id=params.get("agent_id", "main"),
            tier=int(params.get("tier", 5)),
            match_key=params.get("match_key", "default"),
            match_value=params.get("match_value", "*"),
            priority=int(params.get("priority", 0)),
        )
        if self.control_plane is not None:
            binding = self.control_plane.add_binding(binding)
        else:
            self.dispatcher.bindings.add(binding)
        return {"ok": True, "binding": binding.display()}

    async def _m_bind_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        agent_id = params.get("agent_id", "main")
        match_key = params.get("match_key", "")
        match_value = params.get("match_value", "")
        if not match_key or not match_value:
            raise ValueError("match_key and match_value are required")
        if self.control_plane is None:
            removed = self.dispatcher.bindings.remove(agent_id, match_key, match_value)
        else:
            removed = self.control_plane.remove_binding(agent_id, match_key, match_value)
        return {"ok": removed}

    async def _m_bind_save(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        count = self.control_plane.save_bindings()
        return {"ok": True, "count": count}

    async def _m_bind_reload(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        count = self.control_plane.reload_bindings()
        return {"ok": True, "count": count}

    async def _m_bind_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": binding.agent_id,
                "tier": binding.tier,
                "match_key": binding.match_key,
                "match_value": binding.match_value,
                "priority": binding.priority,
            }
            for binding in self.dispatcher.bindings.list_all()
        ]

    async def _m_agents(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.control_plane is not None:
            agents = self.control_plane.list_agents()
        else:
            agents = self.dispatcher.agents.list()
        return [
            {
                "id": agent.id,
                "name": agent.name,
                "model": agent.model,
                "dm_scope": agent.dm_scope,
                "personality": agent.personality,
                "tool_policy_mode": agent.tool_policy_mode,
                "tool_names": list(agent.tool_names),
                "memory_enabled": agent.memory_enabled,
                "memory_auto_recall": agent.memory_auto_recall,
                "memory_top_k": agent.memory_top_k,
                "prompt_dir": agent.prompt_dir,
                "use_global_prompt_files": agent.use_global_prompt_files,
                "skills_enabled": agent.skills_enabled,
            }
            for agent in agents
        ]

    async def _m_agents_save(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        count = self.control_plane.save_agents()
        return {"ok": True, "count": count}

    async def _m_agents_set(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        agent_id = params.get("id", "")
        if not agent_id:
            raise ValueError("id is required")
        agent = self.control_plane.set_agent(
            agent_id=agent_id,
            name=params.get("name"),
            personality=params.get("personality"),
            model=params.get("model"),
            dm_scope=params.get("dm_scope"),
            extra_system=params.get("extra_system"),
            tool_policy_mode=params.get("tool_policy_mode"),
            tool_names=list(params.get("tool_names", []))
            if isinstance(params.get("tool_names"), list)
            else None,
            memory_enabled=params.get("memory_enabled"),
            memory_auto_recall=params.get("memory_auto_recall"),
            memory_top_k=params.get("memory_top_k"),
            prompt_dir=params.get("prompt_dir"),
            use_global_prompt_files=params.get("use_global_prompt_files"),
            skills_enabled=params.get("skills_enabled"),
        )
        return {
            "ok": True,
            "agent": {
                "id": agent.id,
                "name": agent.name,
                "model": agent.model,
                "dm_scope": agent.dm_scope,
                "personality": agent.personality,
                "tool_policy_mode": agent.tool_policy_mode,
                "tool_names": list(agent.tool_names),
                "memory_enabled": agent.memory_enabled,
                "memory_auto_recall": agent.memory_auto_recall,
                "memory_top_k": agent.memory_top_k,
                "prompt_dir": agent.prompt_dir,
                "use_global_prompt_files": agent.use_global_prompt_files,
                "skills_enabled": agent.skills_enabled,
            },
        }

    async def _m_agents_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        agent_id = params.get("id", "")
        if not agent_id:
            raise ValueError("id is required")
        return {"ok": self.control_plane.remove_agent(agent_id)}

    async def _m_agents_template(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        agent_id = params.get("id", "")
        if not agent_id:
            raise ValueError("id is required")
        result = self.control_plane.generate_agent_template(
            agent_id=agent_id,
            name=params.get("name", ""),
            capability_tags=list(params.get("capability_tags", []))
            if isinstance(params.get("capability_tags"), list)
            else None,
            use_global_prompt_files=bool(params.get("use_global_prompt_files", True)),
            memory_enabled=bool(params.get("memory_enabled", True)),
            skills_enabled=bool(params.get("skills_enabled", True)),
            write_files=bool(params.get("write_files", True)),
        )
        return {"ok": True, **result}

    async def _m_agents_capabilities(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.control_plane is None:
            return []
        return self.control_plane.list_tool_capabilities()

    async def _m_agents_reload(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        agents = self.control_plane.reload_agents()
        return {"ok": True, "count": len(agents)}

    async def _m_profiles_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.control_plane is None:
            return []
        return self.control_plane.list_profiles()

    async def _m_profiles_save(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        count = self.control_plane.save_profiles()
        return {"ok": True, "count": count}

    async def _m_profiles_set(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        name = params.get("name", "")
        if not name:
            raise ValueError("name is required")
        profile = self.control_plane.set_profile(
            name=name,
            provider=params.get("provider"),
            api_key=params.get("api_key"),
            api_key_env=params.get("api_key_env"),
            base_url=params.get("base_url"),
            base_url_env=params.get("base_url_env"),
        )
        return {"ok": True, "profile": profile}

    async def _m_profiles_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        name = params.get("name", "")
        if not name:
            raise ValueError("name is required")
        return {"ok": self.control_plane.remove_profile(name)}

    async def _m_profiles_reload(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        profiles = self.control_plane.reload_profiles()
        return {"ok": True, "count": len(profiles)}

    async def _m_channels_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.control_plane is None:
            return []
        return self.control_plane.list_channels()

    async def _m_channels_save(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        count = self.control_plane.save_channels()
        return {"ok": True, "count": count}

    async def _m_channels_set(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        channel = params.get("channel", "")
        account_id = params.get("account_id", "")
        if not channel or not account_id:
            raise ValueError("channel and account_id are required")
        descriptor = await self.control_plane.set_channel(
            channel=channel,
            account_id=account_id,
            enabled=params.get("enabled"),
            label=params.get("label"),
            token=params.get("token"),
            token_env=params.get("token_env"),
            config=dict(params.get("config", {})) if isinstance(params.get("config"), dict) else None,
        )
        return {"ok": True, "channel_account": descriptor}

    async def _m_channels_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        channel = params.get("channel", "")
        account_id = params.get("account_id", "")
        if not channel or not account_id:
            raise ValueError("channel and account_id are required")
        return {"ok": await self.control_plane.remove_channel(channel, account_id)}

    async def _m_channels_reload(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        channels = await self.control_plane.reload_channels()
        return {"ok": True, "channels": channels}

    async def _m_feishu_long_connection_status(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.control_plane is None:
            return []
        runtime = getattr(self.control_plane, "feishu_long_connection_runtime", None)
        if runtime is None:
            return []
        return runtime.status()

    async def _m_feishu_onboarding_start(self, params: dict[str, Any]) -> dict[str, Any]:
        onboarding = self._require_feishu_onboarding()
        return onboarding.create_session(
            mode=params.get("mode", "personal"),
            account_id=params.get("account_id", "feishu-long-local"),
            agent_name=params.get("agent_name", ""),
            ttl_seconds=int(params.get("ttl_seconds", 900)),
        )

    async def _m_feishu_onboarding_status(self, params: dict[str, Any]) -> dict[str, Any]:
        onboarding = self._require_feishu_onboarding()
        session_id = params.get("session_id", "")
        if not session_id:
            raise ValueError("session_id is required")
        status = onboarding.status(session_id)
        if status is None:
            raise ValueError("session not found")
        return status

    async def _m_feishu_onboarding_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._require_feishu_onboarding().list_sessions()

    def _require_feishu_onboarding(self):
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        onboarding = getattr(self.control_plane, "feishu_onboarding", None)
        if onboarding is None:
            raise RuntimeError("feishu onboarding not configured")
        return onboarding

    async def _m_config_source(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        kind = params.get("kind", "")
        if not kind:
            raise ValueError("kind is required")
        return self.control_plane.get_source(kind)

    async def _m_sessions(self, params: dict[str, Any]) -> dict[str, int]:
        return self.sessions.list_sessions(params.get("agent_id", ""))

    async def _m_status(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "running": self._running,
            "uptime_seconds": round(time.monotonic() - self._start_time, 1),
            "agent_count": len(self.dispatcher.agents.list()),
            "binding_count": len(self.dispatcher.bindings.list_all()),
            "profile_count": len(getattr(self.control_plane.profiles, "profiles", []))
            if self.control_plane is not None
            else None,
            "channels": self.control_plane.channels.list_channels() if self.control_plane else [],
        }

    async def _m_runtime_status(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        return {
            "server": {
                "running": self._running,
                "uptime_seconds": round(time.monotonic() - self._start_time, 1),
            },
            **self.control_plane.runtime_status(),
        }

    async def _m_health_check(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        result = self.control_plane.health_check()
        result["server"] = {
            "running": self._running,
            "uptime_seconds": round(time.monotonic() - self._start_time, 1),
        }
        if not self._running:
            result["checks"].append(
                {
                    "name": "server.running",
                    "status": "warning",
                    "message": "gateway server is not running",
                }
            )
            result["summary"]["warning"] += 1
            if result["status"] == "ok":
                result["ok"] = False
                result["status"] = "degraded"
        return result

    async def _m_events_tail(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        return self.control_plane.tail_events(
            limit=int(params.get("limit", 100)),
            event_type=str(params.get("type", "")),
            component=str(params.get("component", "")),
            status=str(params.get("status", "")),
            correlation_id=str(params.get("correlation_id", "")),
            agent_id=str(params.get("agent_id", "")),
            channel=str(params.get("channel", "")),
            job_id=str(params.get("job_id", "")),
            delivery_id=str(params.get("delivery_id", "")),
        )

    async def _m_errors_recent(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        return self.control_plane.recent_errors(
            limit=int(params.get("limit", 50)),
            component=str(params.get("component", "")),
            correlation_id=str(params.get("correlation_id", "")),
        )

    async def _m_memory_recent(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        return self.control_plane.recent_memories(limit=int(params.get("limit", 20)))

    async def _m_ingest(self, params: dict[str, Any]) -> dict[str, Any]:
        inbound = InboundMessage(
            text=params.get("text", ""),
            sender_id=params.get("sender_id", ""),
            channel=params.get("channel", ""),
            account_id=params.get("account_id", ""),
            peer_id=params.get("peer_id", ""),
            guild_id=params.get("guild_id", ""),
            is_group=bool(params.get("is_group", False)),
            metadata=dict(params.get("metadata", {})),
        )
        result = await self.dispatcher.dispatch_inbound(
            inbound,
            forced_agent_id=params.get("agent_id", ""),
        )
        return {
            "agent_id": result.reply.agent_id,
            "session_key": result.reply.session_key,
            "reply": result.reply.text,
            "stop_reason": result.reply.stop_reason,
            "tool_calls": result.reply.tool_calls,
        }

    async def _m_heartbeat_status(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.autonomy is None:
            return {"enabled": False, "reason": "autonomy runtime not configured"}
        return self.autonomy.heartbeat.status()

    async def _m_heartbeat_trigger(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.autonomy is None:
            raise RuntimeError("autonomy runtime not configured")
        return {"result": await self.autonomy.heartbeat.trigger()}

    async def _m_cron_list(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        if self.autonomy is None:
            return []
        return self.autonomy.cron.list_jobs()

    async def _m_cron_trigger(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.autonomy is None:
            raise RuntimeError("autonomy runtime not configured")
        job_id = params.get("job_id", "")
        if not job_id:
            raise ValueError("job_id is required")
        return {"result": await self.autonomy.cron.trigger_job(job_id)}

    async def _m_delivery_stats(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        return self.control_plane.delivery_stats()

    async def _m_delivery_list(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        return self.control_plane.list_deliveries(
            state=params.get("state", "pending"),
            limit=int(params.get("limit", 50)),
            include_text=bool(params.get("include_text", False)),
        )

    async def _m_delivery_retry(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        delivery_id = params.get("delivery_id", params.get("id", ""))
        if not delivery_id:
            raise ValueError("delivery_id is required")
        return {"ok": self.control_plane.retry_delivery(str(delivery_id))}

    async def _m_delivery_discard(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        delivery_id = params.get("delivery_id", params.get("id", ""))
        if not delivery_id:
            raise ValueError("delivery_id is required")
        return {
            "ok": self.control_plane.discard_delivery(
                str(delivery_id),
                state=params.get("state", "any"),
            )
        }

    async def _m_delivery_flush(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.control_plane is None:
            raise RuntimeError("control plane not configured")
        return await self.control_plane.flush_delivery(rounds=int(params.get("rounds", 1)))
