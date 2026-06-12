from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from agent_gateway.models import ConversationMessage


def serialize_messages_for_summary(messages: list[ConversationMessage]) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.get("role", "unknown")
        content = message.get("content", "")
        parts.append(f"{role.upper()}: {content}")
    return "\n".join(parts)


@dataclass(slots=True)
class ContextGuard:
    safe_limit: int = 180_000
    max_tool_chars: int = 20_000

    def estimate_tokens(self, messages: list[ConversationMessage]) -> int:
        total_chars = sum(len(str(message.get("content", ""))) for message in messages)
        return total_chars // 4

    def truncate_large_tool_results(
        self,
        messages: list[ConversationMessage],
    ) -> list[ConversationMessage]:
        truncated: list[ConversationMessage] = []
        for message in messages:
            content = message.get("content")
            if isinstance(content, list):
                next_blocks: list[dict[str, Any]] = []
                for block in content:
                    if block.get("type") == "tool_result":
                        text = str(block.get("content", ""))
                        if len(text) > self.max_tool_chars:
                            block = dict(block)
                            block["content"] = (
                                text[: self.max_tool_chars]
                                + f"\n... [truncated, {len(text)} total chars]"
                            )
                    next_blocks.append(block)
                truncated.append({"role": message["role"], "content": next_blocks})
            else:
                truncated.append(message)
        return truncated

    def compact_history(
        self,
        messages: list[ConversationMessage],
        summarizer: Callable[[str], str],
    ) -> list[ConversationMessage]:
        if len(messages) < 8:
            return messages

        keep_count = max(4, int(len(messages) * 0.2))
        compress_count = min(max(2, int(len(messages) * 0.5)), len(messages) - keep_count)
        old_messages = messages[:compress_count]
        recent_messages = messages[compress_count:]
        summary = summarizer(serialize_messages_for_summary(old_messages))
        compacted = [
            {"role": "user", "content": "[Previous conversation summary]\n" + summary},
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "Understood, I have the context."}],
            },
        ]
        compacted.extend(recent_messages)
        return compacted

    def guard_api_call(
        self,
        api_client: Any,
        model: str,
        system: str,
        messages: list[ConversationMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        summarizer: Callable[[str], str] | None = None,
        max_retries: int = 2,
    ) -> Any:
        current_messages = messages
        for attempt in range(max_retries + 1):
            try:
                kwargs = {"tools": tools} if tools else {}
                return api_client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=current_messages,
                    **kwargs,
                )
            except Exception as exc:
                error = str(exc).lower()
                is_overflow = "context" in error or "token" in error or "overflow" in error
                if not is_overflow or attempt >= max_retries:
                    raise
                if attempt == 0:
                    current_messages = self.truncate_large_tool_results(current_messages)
                    continue
                if summarizer is not None:
                    current_messages = self.compact_history(current_messages, summarizer)
                    continue
                raise

        raise RuntimeError("context guard exhausted retries without returning a response")
