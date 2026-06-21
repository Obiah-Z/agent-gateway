"""飞书低门槛接入流程。

该模块负责扫码/绑定码会话、首条私聊自动绑定、自动创建 Agent 和写入绑定规则。
它不直接处理飞书 HTTP 或长连接细节，只消费已经标准化的 InboundMessage / event。
"""

from __future__ import annotations

import json
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_gateway.runtime.domain.ids import normalize_agent_id
from agent_gateway.runtime.domain.models import Binding, InboundMessage, ProactiveTarget
from agent_gateway.runtime.execution.control_plane import GatewayControlPlane
from agent_gateway.runtime.execution.dispatcher import GatewayDispatcher


BINDING_CODE_PATTERN = re.compile(r"\bGATEWAY-[A-Z0-9]{4,10}\b", re.IGNORECASE)


@dataclass(slots=True)
class FeishuOnboardingSession:
    """一次短期飞书接入会话。"""

    session_id: str
    binding_code: str
    mode: str = "personal"
    status: str = "pending"
    account_id: str = "feishu-long-local"
    agent_id: str = ""
    agent_name: str = ""
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    bound_at: float = 0.0
    bound_peer_id: str = ""
    bound_sender_id: str = ""
    bound_is_group: bool = False
    last_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "binding_code": self.binding_code,
            "mode": self.mode,
            "status": self.status,
            "account_id": self.account_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "bound_at": self.bound_at,
            "bound_peer_id": self.bound_peer_id,
            "bound_sender_id": self.bound_sender_id,
            "bound_is_group": self.bound_is_group,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FeishuOnboardingSession":
        return cls(
            session_id=str(data.get("session_id", "")),
            binding_code=str(data.get("binding_code", "")),
            mode=str(data.get("mode", "personal")),
            status=str(data.get("status", "pending")),
            account_id=str(data.get("account_id", "feishu-long-local")),
            agent_id=str(data.get("agent_id", "")),
            agent_name=str(data.get("agent_name", "")),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            expires_at=float(data.get("expires_at", 0.0) or 0.0),
            bound_at=float(data.get("bound_at", 0.0) or 0.0),
            bound_peer_id=str(data.get("bound_peer_id", "")),
            bound_sender_id=str(data.get("bound_sender_id", "")),
            bound_is_group=bool(data.get("bound_is_group", False)),
            last_error=str(data.get("last_error", "")),
        )

    def is_expired(self, now: float | None = None) -> bool:
        return self.expires_at > 0 and (now if now is not None else time.time()) >= self.expires_at


class FeishuOnboardingSessionStore:
    """接入会话的本地 JSON 持久化存储。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.sessions_file = self.root / "sessions.json"

    def create(
        self,
        *,
        mode: str,
        account_id: str,
        agent_name: str = "",
        ttl_seconds: int = 900,
    ) -> FeishuOnboardingSession:
        """创建一个短期绑定会话和一次性绑定码。"""

        sessions = self._load_all()
        session = FeishuOnboardingSession(
            session_id=f"ob_{secrets.token_urlsafe(8).replace('-', '').replace('_', '')[:12]}",
            binding_code=self._new_binding_code({item.binding_code for item in sessions}),
            mode=self._normalize_mode(mode),
            account_id=account_id or "feishu-long-local",
            agent_name=agent_name,
            expires_at=time.time() + max(60, ttl_seconds),
        )
        sessions.append(session)
        self._save_all(sessions)
        return session

    def list(self) -> list[FeishuOnboardingSession]:
        """列出会话并顺手把过期 pending 会话标记为 expired。"""

        sessions = self._load_all()
        changed = False
        now = time.time()
        for session in sessions:
            if session.status == "pending" and session.is_expired(now):
                session.status = "expired"
                changed = True
        if changed:
            self._save_all(sessions)
        return sessions

    def get(self, session_id: str) -> FeishuOnboardingSession | None:
        for session in self.list():
            if session.session_id == session_id:
                return session
        return None

    def find_pending_by_code(self, code: str) -> FeishuOnboardingSession | None:
        """按用户发送的绑定码查找仍有效的会话。"""

        normalized = self._normalize_code(code)
        for session in self.list():
            if session.binding_code == normalized and session.status == "pending":
                return session
        return None

    def update(self, session: FeishuOnboardingSession) -> None:
        """更新或追加会话。"""

        sessions = self._load_all()
        for index, current in enumerate(sessions):
            if current.session_id == session.session_id:
                sessions[index] = session
                self._save_all(sessions)
                return
        sessions.append(session)
        self._save_all(sessions)

    def _load_all(self) -> list[FeishuOnboardingSession]:
        if not self.sessions_file.exists():
            return []
        try:
            payload = json.loads(self.sessions_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
        rows = payload.get("sessions", []) if isinstance(payload, dict) else []
        return [
            FeishuOnboardingSession.from_dict(row)
            for row in rows
            if isinstance(row, dict) and row.get("session_id") and row.get("binding_code")
        ]

    def _save_all(self, sessions: list[FeishuOnboardingSession]) -> None:
        """原子写入会话列表，避免进程中断留下半截 JSON。"""

        tmp = self.sessions_file.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"sessions": [session.to_dict() for session in sessions]},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tmp.replace(self.sessions_file)

    @staticmethod
    def _new_binding_code(existing: set[str]) -> str:
        for _ in range(100):
            code = f"GATEWAY-{secrets.token_hex(3).upper()}"
            if code not in existing:
                return code
        raise RuntimeError("failed to generate unique binding code")

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        normalized = str(mode or "personal").strip().lower()
        if normalized in {"personal", "group"}:
            return normalized
        return "personal"

    @staticmethod
    def _normalize_code(code: str) -> str:
        return str(code or "").strip().upper()


class FeishuOnboardingService:
    """飞书接入业务服务。

    主要入口有两个：用户消息中的绑定码/首条私聊自动绑定，以及机器人被拉群事件自动绑定。
    """

    def __init__(
        self,
        *,
        store: FeishuOnboardingSessionStore,
        control_plane: GatewayControlPlane,
        dispatcher: GatewayDispatcher,
        public_base_url: str = "",
        bot_link: str = "",
        auto_bind_first_message: bool = True,
        auto_bind_bot_added: bool = False,
    ) -> None:
        self.store = store
        self.control_plane = control_plane
        self.dispatcher = dispatcher
        self.public_base_url = public_base_url.rstrip("/")
        self.bot_link = bot_link.strip()
        self.auto_bind_first_message = auto_bind_first_message
        self.auto_bind_bot_added = auto_bind_bot_added

    def create_session(
        self,
        *,
        mode: str = "personal",
        account_id: str = "feishu-long-local",
        agent_name: str = "",
        ttl_seconds: int = 900,
    ) -> dict[str, Any]:
        """创建接入会话，并返回可展示给 onboarding 页面的数据。"""

        session = self.store.create(
            mode=mode,
            account_id=account_id,
            agent_name=agent_name,
            ttl_seconds=ttl_seconds,
        )
        return self._session_response(session)

    def status(self, session_id: str) -> dict[str, Any] | None:
        session = self.store.get(session_id)
        if session is None:
            return None
        return self._session_response(session)

    def list_sessions(self) -> list[dict[str, Any]]:
        return [self._session_response(session) for session in self.store.list()]

    async def try_consume_activation(self, inbound: InboundMessage) -> bool:
        """尝试消费一条飞书消息作为接入激活消息。

        返回 True 表示这条消息已经被 onboarding 流程处理，不再进入普通 Agent 对话。
        """

        if inbound.channel != "feishu":
            return False
        code = self.extract_binding_code(inbound.text)
        session = self.store.find_pending_by_code(code) if code else None
        if code and session is None:
            return False
        if session is None and self._should_auto_bind_first_message(inbound):
            session = self.store.create(
                mode="group" if inbound.is_group else "personal",
                account_id=inbound.account_id or "feishu-long-local",
                ttl_seconds=300,
            )
        if session is None:
            return False
        try:
            await self._bind_session(session, inbound)
        except Exception as exc:
            session.status = "failed"
            session.last_error = str(exc)
            self.store.update(session)
            await self._reply(inbound, f"绑定失败：{exc}")
        return True

    async def try_consume_event(self, event: dict[str, Any], account_id: str) -> bool:
        """尝试消费飞书原始事件，例如机器人被加入群聊。"""

        if not self.auto_bind_bot_added:
            return False
        event_type = self._event_type(event)
        if event_type != "im.chat.member.bot.added_v1":
            return False
        chat_id = str(self._event_body(event).get("chat_id", "") or "").strip()
        operator_id = self._operator_open_id(event)
        if not chat_id:
            return False
        inbound = InboundMessage(
            text="机器人已加入会话，自动接入 Gateway。",
            sender_id=operator_id,
            channel="feishu",
            account_id=account_id,
            peer_id=chat_id,
            is_group=True,
            raw=event,
            metadata={
                "receive_id_type": "chat_id",
                "kind": "onboarding",
                "feishu_event_type": event_type,
            },
        )
        session = self.store.create(mode="group", account_id=account_id, ttl_seconds=300)
        try:
            await self._bind_session(session, inbound)
        except Exception as exc:
            session.status = "failed"
            session.last_error = str(exc)
            self.store.update(session)
            return False
        return True

    async def _bind_session(self, session: FeishuOnboardingSession, inbound: InboundMessage) -> None:
        """把会话绑定到飞书 peer，并创建对应 Agent / Binding。"""

        peer_id = inbound.peer_id
        if not peer_id:
            raise ValueError("飞书消息缺少 peer_id，无法绑定")
        if session.account_id and inbound.account_id and session.account_id != inbound.account_id:
            raise ValueError(f"请使用账号 {session.account_id} 对应的机器人完成绑定")
        agent_id = self._build_agent_id(session, inbound)
        agent_name = session.agent_name or self._build_agent_name(session, inbound)
        prompt_dir = f"agents/{agent_id}"
        self.control_plane.set_agent(
            agent_id=agent_id,
            name=agent_name,
            personality="亲和、简洁、耐心，适合飞书日常对话",
            dm_scope="per-account-channel-peer",
            extra_system=(
                "你是通过飞书扫码接入 Gateway 的智能助手。"
                "优先用简洁中文回答，必要时再使用工具。"
            ),
            tool_policy_mode="allowlist",
            tool_names=[
                "get_current_time",
                "memory_search",
                "web_search",
                "fetch_url",
            ],
            memory_enabled=True,
            memory_auto_recall=False,
            memory_top_k=2,
            prompt_dir=prompt_dir,
            use_global_prompt_files=True,
            skills_enabled=True,
        )
        self._write_prompt_files(agent_id, agent_name, session, inbound)
        if not self._has_peer_binding(peer_id):
            self.control_plane.add_binding(
                Binding(
                    agent_id=agent_id,
                    tier=1,
                    match_key="peer_id",
                    match_value=peer_id,
                    priority=120,
                )
            )
            self.control_plane.save_bindings()
        session.status = "bound"
        session.agent_id = agent_id
        session.agent_name = agent_name
        session.bound_at = time.time()
        session.bound_peer_id = peer_id
        session.bound_sender_id = inbound.sender_id
        session.bound_is_group = inbound.is_group
        self.store.update(session)
        await self._reply(
            inbound,
            f"接入成功：当前{'群聊' if inbound.is_group else '私聊'}已创建 {agent_name}。你可以直接开始对话。",
        )

    def _should_auto_bind_first_message(self, inbound: InboundMessage) -> bool:
        """判断是否允许用用户第一条私聊消息直接创建个人 Agent。"""

        if not self.auto_bind_first_message:
            return False
        if inbound.channel != "feishu" or inbound.is_group:
            return False
        if not inbound.peer_id or not inbound.sender_id:
            return False
        if str(inbound.metadata.get("kind", "")) in {"onboarding", "card_action"}:
            return False
        agent_id, binding = self.control_plane.bindings.resolve(
            channel=inbound.channel,
            account_id=inbound.account_id,
            guild_id=inbound.guild_id,
            peer_id=inbound.peer_id,
        )
        return binding is None or (binding.tier == 5 and binding.match_key == "default")

    def _has_peer_binding(self, peer_id: str) -> bool:
        """检查该飞书 peer 是否已经有专属绑定。"""

        return any(
            binding.tier == 1
            and binding.match_key == "peer_id"
            and binding.match_value == peer_id
            for binding in self.control_plane.bindings.list_all()
        )

    async def _reply(self, inbound: InboundMessage, text: str) -> None:
        """通过可靠投递队列回复接入结果。"""

        metadata = dict(inbound.metadata)
        metadata.update(
            {
                "kind": "onboarding",
                "receive_id_type": inbound.metadata.get(
                    "receive_id_type",
                    "chat_id" if inbound.is_group else "open_id",
                ),
            }
        )
        await self.dispatcher.deliver_text(
            self.control_plane.channels,
            ProactiveTarget(
                channel=inbound.channel,
                account_id=inbound.account_id,
                peer_id=inbound.peer_id,
                agent_id="main",
            ),
            text,
            metadata=metadata,
        )

    def _write_prompt_files(
        self,
        agent_id: str,
        agent_name: str,
        session: FeishuOnboardingSession,
        inbound: InboundMessage,
    ) -> None:
        """为新建 Agent 写入局部 prompt 文件。"""

        root = self.control_plane.settings.workspace_root / "agents" / agent_id
        root.mkdir(parents=True, exist_ok=True)
        identity = root / "IDENTITY.md"
        soul = root / "SOUL.md"
        if not identity.exists():
            identity.write_text(
                f"# {agent_name}\n\n"
                "你是接入飞书会话的 Gateway 智能助手，负责在当前会话中提供清晰、可靠的帮助。\n",
                encoding="utf-8",
            )
        if not soul.exists():
            target = "群聊" if inbound.is_group else "个人私聊"
            soul.write_text(
                f"# 行为准则\n\n"
                f"- 当前绑定目标：{target}。\n"
                "- 回答保持简洁，优先给出可执行建议。\n"
                "- 不确定的信息应说明不确定性；需要实时信息时再使用检索工具。\n",
                encoding="utf-8",
            )

    def _session_response(self, session: FeishuOnboardingSession) -> dict[str, Any]:
        """转换为 dashboard/onboarding 页面可直接消费的数据。"""

        data = session.to_dict()
        data["activation_text"] = f"绑定 {session.binding_code}"
        data["onboarding_url"] = (
            f"{self.public_base_url}/onboarding/feishu?session_id={session.session_id}"
            if self.public_base_url
            else f"/onboarding/feishu?session_id={session.session_id}"
        )
        data["bot_link"] = self.bot_link
        data["qr_target"] = self.bot_link or data["onboarding_url"]
        data["auto_bind_first_message"] = self.auto_bind_first_message
        return data

    @staticmethod
    def extract_binding_code(text: str) -> str:
        """从用户消息中提取绑定码。"""

        match = BINDING_CODE_PATTERN.search(text or "")
        return match.group(0).upper() if match else ""

    @staticmethod
    def _build_agent_id(session: FeishuOnboardingSession, inbound: InboundMessage) -> str:
        """根据个人/群聊场景生成稳定 Agent ID。"""

        if session.mode == "group" or inbound.is_group:
            raw = f"feishu-group-{inbound.peer_id}"
        else:
            raw = f"feishu-user-{inbound.sender_id}"
        return normalize_agent_id(raw.replace("_", "-").replace(":", "-"))

    @staticmethod
    def _build_agent_name(session: FeishuOnboardingSession, inbound: InboundMessage) -> str:
        """生成默认 Agent 展示名。"""

        if session.mode == "group" or inbound.is_group:
            return "飞书群聊助手"
        return "我的飞书助手"

    @staticmethod
    def _event_type(event: dict[str, Any]) -> str:
        """兼容长连接和 webhook 事件中的事件类型字段。"""

        header = event.get("header", {})
        if isinstance(header, dict) and header.get("event_type"):
            return str(header.get("event_type", ""))
        return str(event.get("type") or event.get("event_type") or "")

    @staticmethod
    def _event_body(event: dict[str, Any]) -> dict[str, Any]:
        """返回飞书事件 body，兼容扁平事件和 envelope 事件。"""

        body = event.get("event", {})
        return body if isinstance(body, dict) else event

    @classmethod
    def _operator_open_id(cls, event: dict[str, Any]) -> str:
        """提取触发机器人入群事件的操作者 ID。"""

        operator = cls._event_body(event).get("operator_id", {})
        if isinstance(operator, dict):
            return str(operator.get("open_id") or operator.get("user_id") or "")
        return ""
