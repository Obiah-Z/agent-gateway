"""JSONL 会话存储。

每个 Agent/session_key 对应一个 JSONL 文件。写入时保存为便于审计和追加的事件记录，
读取时重建成 Anthropic Messages API 可直接消费的对话历史。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from agent_gateway.runtime.domain.models import ConversationMessage


class SessionStore:
    """负责会话历史的持久化、重放和重写。"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.backup_sink = None
        self.read_backend: Any | None = None
        self.write_backend: Any | None = None

    def session_path(self, agent_id: str, session_key: str) -> Path:
        """返回会话文件路径。

        session_key 可能包含冒号等分隔符，因此文件名使用 URL quote 后的安全形式。
        """

        agent_dir = self.base_dir / "agents" / agent_id
        agent_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{quote(session_key, safe='')}.jsonl"
        return agent_dir / filename

    def append_message(
        self,
        agent_id: str,
        session_key: str,
        role: str,
        content: Any,
    ) -> None:
        """追加单条消息记录。

        当前主链路多数情况下使用 rewrite_messages 保持模型返回的完整历史；该方法保留给
        需要增量写入的场景。
        """

        self._write_primary(
            "write_session_message",
            agent_id,
            session_key,
            role,
            content,
        )
        self.append_message_to_disk(agent_id, session_key, role, content)

    def rewrite_messages(
        self,
        agent_id: str,
        session_key: str,
        messages: list[ConversationMessage],
    ) -> None:
        """用模型执行后的完整 messages 重写会话。

        先写临时文件再 replace，避免重写过程中中断导致原会话损坏。
        """

        messages = self.sanitize_messages(messages)
        self._write_primary(
            "rewrite_session_messages",
            agent_id,
            session_key,
            messages,
        )
        self.rewrite_messages_to_disk(agent_id, session_key, messages)

    def append_message_to_disk(
        self,
        agent_id: str,
        session_key: str,
        role: str,
        content: Any,
    ) -> None:
        """仅写入本地 JSONL，不触发双写镜像。"""

        if role == "user" and isinstance(content, str):
            record = {"type": "user", "content": content, "ts": time.time()}
        elif role == "assistant":
            record = {"type": "assistant", "content": content, "ts": time.time()}
        else:
            record = {"role": role, "content": content, "ts": time.time()}
        path = self.session_path(agent_id, session_key)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def rewrite_messages_to_disk(
        self,
        agent_id: str,
        session_key: str,
        messages: list[ConversationMessage],
    ) -> None:
        """仅重写本地 JSONL，不触发双写镜像。"""

        path = self.session_path(agent_id, session_key)
        tmp_path = path.with_suffix(".jsonl.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for record in self._messages_to_records(messages):
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        tmp_path.replace(path)

    def load_messages(self, agent_id: str, session_key: str) -> list[ConversationMessage]:
        """加载并重建可传给模型的 messages 历史。"""

        if self.read_backend is not None:
            try:
                if hasattr(self.read_backend, "read_session_messages"):
                    history = self.read_backend.read_session_messages(agent_id, session_key)
                    if history:
                        return self.sanitize_messages(history)
            except Exception:
                pass
        path = self.session_path(agent_id, session_key)
        if not path.exists():
            return []

        return self.sanitize_messages(self._rebuild_history(path))

    def list_sessions(self, agent_id: str = "") -> dict[str, int]:
        """列出会话文件及其消息条数，用于控制面查看。"""

        if self.read_backend is not None:
            try:
                rows = self.read_backend.list("sessions", limit=500, filters={"agent_id": agent_id} if agent_id else {})
                result: dict[str, int] = {}
                for row in rows:
                    session_key = str(row.get("session_key", ""))
                    if not session_key:
                        continue
                    result[session_key] = int(row.get("message_count", 0) or 0)
                if result:
                    return result
            except Exception:
                pass
        agents_dir = self.base_dir / "agents"
        if not agents_dir.exists():
            return {}

        targets = [agents_dir / agent_id] if agent_id else sorted(agents_dir.iterdir())
        result: dict[str, int] = {}

        for target in targets:
            if not target.exists() or not target.is_dir():
                continue
            for path in sorted(target.glob("*.jsonl")):
                key = unquote(path.stem)
                result[key] = self._count_lines(path)
        return result

    @staticmethod
    def _count_lines(path: Path) -> int:
        """统计单个会话文件中的记录数。"""

        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for _ in handle)

    def _rebuild_history(self, path: Path) -> list[ConversationMessage]:
        """把 JSONL 事件记录还原成 Anthropic Messages 结构。

        tool_use 需要挂在 assistant 消息上，tool_result 需要作为 user 消息返回模型；
        这是工具调用闭环能跨进程恢复的关键。
        """

        messages: list[ConversationMessage] = []
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                record_type = record.get("type")
                if record_type is None and "role" in record:
                    messages.append({"role": record["role"], "content": record["content"]})
                    continue

                if record_type == "user":
                    content = record["content"]
                    if self._content_has_payload(content):
                        messages.append({"role": "user", "content": content})
                    continue

                if record_type == "assistant":
                    content = record["content"]
                    if isinstance(content, str):
                        content = [{"type": "text", "text": content}]
                    content = self._normalize_assistant_blocks(content)
                    if self._content_has_payload(content):
                        messages.append({"role": "assistant", "content": content})
                    continue

                if record_type == "tool_use":
                    block = {
                        "type": "tool_use",
                        "id": record["tool_use_id"],
                        "name": record["name"],
                        "input": record["input"],
                    }
                    if messages and messages[-1]["role"] == "assistant":
                        existing = messages[-1]["content"]
                        if isinstance(existing, list):
                            existing.append(block)
                        else:
                            messages[-1]["content"] = [
                                {"type": "text", "text": str(existing)},
                                block,
                            ]
                    else:
                        messages.append({"role": "assistant", "content": [block]})
                    continue

                if record_type == "tool_result":
                    result_block = {
                        "type": "tool_result",
                        "tool_use_id": record["tool_use_id"],
                        "content": record["content"] or "[empty tool result]",
                    }
                    if (
                        messages
                        and messages[-1]["role"] == "user"
                        and isinstance(messages[-1]["content"], list)
                        and messages[-1]["content"]
                        and isinstance(messages[-1]["content"][0], dict)
                        and messages[-1]["content"][0].get("type") == "tool_result"
                    ):
                        messages[-1]["content"].append(result_block)
                    else:
                        messages.append({"role": "user", "content": [result_block]})

        return messages

    def _messages_to_records(self, messages: list[ConversationMessage]) -> list[dict[str, Any]]:
        """把 Anthropic Messages 拆成适合 JSONL 审计和增量恢复的记录。"""

        records: list[dict[str, Any]] = []
        now = time.time

        for message in self.sanitize_messages(messages):
            role = message["role"]
            content = message.get("content", "")

            if role == "user":
                if self._is_tool_result_batch(content):
                    for block in content:
                        records.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block["tool_use_id"],
                                "content": block.get("content", ""),
                                "ts": now(),
                            }
                        )
                else:
                    records.append({"type": "user", "content": content, "ts": now()})
                continue

            if role == "assistant":
                normalized_blocks = self._normalize_assistant_blocks(content)
                text_blocks = [
                    block for block in normalized_blocks if block.get("type") != "tool_use"
                ]
                tool_use_blocks = [
                    block for block in normalized_blocks if block.get("type") == "tool_use"
                ]
                if text_blocks:
                    records.append({"type": "assistant", "content": text_blocks, "ts": now()})
                for block in tool_use_blocks:
                    records.append(
                        {
                            "type": "tool_use",
                            "tool_use_id": block["id"],
                            "name": block["name"],
                            "input": block.get("input", {}),
                            "ts": now(),
                        }
                    )
                continue

            records.append({"role": role, "content": content, "ts": now()})

        return records

    @classmethod
    def sanitize_messages(cls, messages: list[Any]) -> list[ConversationMessage]:
        """清洗可传给模型的消息历史，过滤空 content 并修复空工具结果。"""

        sanitized: list[ConversationMessage] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "")).strip()
            content = message.get("content")
            if role == "assistant":
                content = cls._normalize_assistant_blocks(content)
            elif role == "user":
                content = cls._normalize_user_content(content)
            if not role or not cls._content_has_payload(content):
                continue
            sanitized.append({"role": role, "content": content})
        return sanitized

    def _mirror(self, method_name: str, *args: Any) -> None:
        """把主写入镜像到备份 sink，失败不影响主链路。"""

        sink = getattr(self, "backup_sink", None)
        if sink is None:
            return
        method = getattr(sink, method_name, None)
        if method is None:
            return
        try:
            method(*args)
        except Exception:
            pass

    def _write_primary(self, method_name: str, *args: Any) -> None:
        """优先写入数据库主存储；不可用时退回备份 sink。"""

        backend = getattr(self, "write_backend", None)
        if backend is not None:
            method = getattr(backend, method_name, None)
            if method is not None:
                try:
                    method(*args)
                    return
                except Exception:
                    pass
        self._mirror(method_name, *args)

    @staticmethod
    def _normalize_assistant_blocks(content: Any) -> list[dict[str, Any]]:
        """统一 assistant content block 格式，过滤空文本并保留合法 tool_use。"""

        if isinstance(content, str):
            return [{"type": "text", "text": content}] if content else []
        if not isinstance(content, list):
            text = str(content)
            return [{"type": "text", "text": text}] if text else []
        normalized: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        normalized.append({"type": "text", "text": text})
                elif block.get("type") == "tool_use":
                    tool_id = block.get("id", "")
                    name = block.get("name", "")
                    if tool_id and name:
                        normalized.append(
                            {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": name,
                                "input": block.get("input", {}),
                            }
                        )
                else:
                    normalized.append(block)
            else:
                text = str(block)
                if text:
                    normalized.append({"type": "text", "text": text})
        return normalized

    @staticmethod
    def _content_has_payload(content: Any) -> bool:
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            if not content:
                return False
            for block in content:
                if not isinstance(block, dict):
                    if str(block).strip():
                        return True
                    continue
                block_type = block.get("type")
                if block_type == "text" and str(block.get("text", "")).strip():
                    return True
                if block_type == "tool_use" and block.get("id") and block.get("name"):
                    return True
                if block_type == "tool_result" and str(block.get("content", "")).strip():
                    return True
            return False
        return content is not None

    @staticmethod
    def _normalize_user_content(content: Any) -> Any:
        if not isinstance(content, list):
            return content
        normalized: list[Any] = []
        for block in content:
            if not isinstance(block, dict):
                if str(block).strip():
                    normalized.append(block)
                continue
            if block.get("type") == "tool_result":
                normalized.append(
                    {
                        **block,
                        "content": block.get("content") or "[empty tool result]",
                    }
                )
                continue
            if block.get("type") == "text":
                text = str(block.get("text", ""))
                if text.strip():
                    normalized.append({"type": "text", "text": text})
                continue
            normalized.append(block)
        return normalized

    @staticmethod
    def _is_tool_result_batch(content: Any) -> bool:
        return (
            isinstance(content, list)
            and bool(content)
            and all(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content
            )
        )
