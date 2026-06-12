from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from agent_gateway.models import ConversationMessage


class SessionStore:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def session_path(self, agent_id: str, session_key: str) -> Path:
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
        if role == "user" and isinstance(content, str):
            record = {"type": "user", "content": content, "ts": time.time()}
        elif role == "assistant":
            record = {"type": "assistant", "content": content, "ts": time.time()}
        else:
            record = {"role": role, "content": content, "ts": time.time()}
        path = self.session_path(agent_id, session_key)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def rewrite_messages(
        self,
        agent_id: str,
        session_key: str,
        messages: list[ConversationMessage],
    ) -> None:
        path = self.session_path(agent_id, session_key)
        tmp_path = path.with_suffix(".jsonl.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            for record in self._messages_to_records(messages):
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        tmp_path.replace(path)

    def load_messages(self, agent_id: str, session_key: str) -> list[ConversationMessage]:
        path = self.session_path(agent_id, session_key)
        if not path.exists():
            return []

        return self._rebuild_history(path)

    def list_sessions(self, agent_id: str = "") -> dict[str, int]:
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
        with path.open("r", encoding="utf-8") as handle:
            return sum(1 for _ in handle)

    def _rebuild_history(self, path: Path) -> list[ConversationMessage]:
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
        records: list[dict[str, Any]] = []
        now = time.time

        for message in messages:
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
                if not text_blocks and not tool_use_blocks:
                    records.append({"type": "assistant", "content": [], "ts": now()})
                continue

            records.append({"role": role, "content": content, "ts": now()})

        return records

    @staticmethod
    def _normalize_assistant_blocks(content: Any) -> list[dict[str, Any]]:
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
    def _is_tool_result_batch(content: Any) -> bool:
        return (
            isinstance(content, list)
            and bool(content)
            and all(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content
            )
        )
