from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

try:
    from anthropic import Anthropic
except ImportError:  # pragma: no cover - dependency may be absent during scaffold tests
    Anthropic = None  # type: ignore[assignment]

try:
    import httpx
except ImportError:  # pragma: no cover - dependency may be absent during scaffold tests
    httpx = None  # type: ignore[assignment]

from agent_gateway.config import GatewaySettings
from agent_gateway.sessions.context import ContextGuard
from agent_gateway.tools.registry import ToolRegistry


class FailoverReason(Enum):
    rate_limit = "rate_limit"
    auth = "auth"
    timeout = "timeout"
    billing = "billing"
    overflow = "overflow"
    bad_request = "bad_request"
    unknown = "unknown"


def classify_failure(exc: Exception) -> FailoverReason:
    message = str(exc).lower()
    if "400" in message or "badrequest" in type(exc).__name__.lower():
        return FailoverReason.bad_request
    if "rate" in message or "429" in message:
        return FailoverReason.rate_limit
    if "auth" in message or "401" in message or "invalid api key" in message:
        return FailoverReason.auth
    if "timeout" in message or "timed out" in message:
        return FailoverReason.timeout
    if "billing" in message or "quota" in message or "402" in message:
        return FailoverReason.billing
    if "context" in message or "token" in message or "overflow" in message:
        return FailoverReason.overflow
    return FailoverReason.unknown


@dataclass(slots=True)
class AuthProfile:
    name: str
    provider: str
    api_key: str
    base_url: str = ""
    cooldown_until: float = 0.0
    failure_reason: str | None = None
    last_good_at: float = 0.0


class ProfileManager:
    def __init__(self, profiles: list[AuthProfile]) -> None:
        self.profiles = profiles

    def select_profile(self) -> AuthProfile | None:
        now = time.time()
        for profile in self.profiles:
            if profile.api_key and now >= profile.cooldown_until:
                return profile
        return None

    def mark_failure(
        self,
        profile: AuthProfile,
        reason: FailoverReason,
        *,
        cooldown_seconds: float,
    ) -> None:
        profile.cooldown_until = time.time() + cooldown_seconds
        profile.failure_reason = reason.value

    def mark_success(self, profile: AuthProfile) -> None:
        profile.cooldown_until = 0.0
        profile.failure_reason = None
        profile.last_good_at = time.time()

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.time()
        rows = []
        for profile in self.profiles:
            rows.append(
                {
                    "name": profile.name,
                    "provider": profile.provider,
                    "has_key": bool(profile.api_key),
                    "cooldown_remaining": max(0.0, profile.cooldown_until - now),
                    "failure_reason": profile.failure_reason,
                    "last_good_at": profile.last_good_at,
                }
            )
        return rows

    def replace_profiles(self, profiles: list[AuthProfile]) -> None:
        previous = {profile.name: profile for profile in self.profiles}
        for profile in profiles:
            old = previous.get(profile.name)
            if old is None:
                continue
            profile.cooldown_until = old.cooldown_until
            profile.failure_reason = old.failure_reason
            profile.last_good_at = old.last_good_at
        self.profiles = profiles


@dataclass(slots=True)
class ResilienceResult:
    text: str
    stop_reason: str
    messages: list[dict[str, Any]]
    tool_calls: list[str]
    profile_name: str
    model: str


@dataclass(slots=True)
class AttemptFailure:
    profile_name: str
    model: str
    reason: FailoverReason
    error_type: str
    error: str

    def display(self) -> str:
        return (
            f"profile={self.profile_name}"
            f" model={self.model}"
            f" reason={self.reason.value}"
            f" error_type={self.error_type}"
            f" error={self.error}"
        )


class ResilienceRunner:
    def __init__(
        self,
        settings: GatewaySettings,
        profile_manager: ProfileManager,
        tools: ToolRegistry,
        context_guard: ContextGuard | None = None,
    ) -> None:
        self.settings = settings
        self.profile_manager = profile_manager
        self.tools = tools
        self.context_guard = context_guard or ContextGuard(
            safe_limit=settings.context_safe_limit,
            max_tool_chars=settings.max_tool_output_chars,
        )

    def run(
        self,
        system: str,
        messages: list[dict[str, Any]],
        *,
        model: str,
        allowed_tools: list[str] | None = None,
    ) -> ResilienceResult:
        current_messages = self._clone_messages(messages)
        attempted_profiles: set[str] = set()
        candidate_models = [model, *self.settings.fallback_models]
        failures: list[AttemptFailure] = []

        for candidate_model in candidate_models:
            for _ in range(max(1, len(self.profile_manager.profiles))):
                profile = self.profile_manager.select_profile()
                if profile is None or profile.name in attempted_profiles:
                    break
                attempted_profiles.add(profile.name)
                api_client = self._build_client(profile)
                layer_messages = self._clone_messages(current_messages)

                for attempt in range(self.settings.max_overflow_compaction):
                    try:
                        result = self._run_attempt(
                            api_client=api_client,
                            model=candidate_model,
                            system=system,
                            messages=layer_messages,
                            allowed_tools=allowed_tools,
                        )
                        self.profile_manager.mark_success(profile)
                        return ResilienceResult(
                            text=result["text"],
                            stop_reason=result["stop_reason"],
                            messages=result["messages"],
                            tool_calls=result["tool_calls"],
                            profile_name=profile.name,
                            model=candidate_model,
                        )
                    except Exception as exc:
                        reason = classify_failure(exc)
                        if (
                            reason == FailoverReason.overflow
                            and attempt < self.settings.max_overflow_compaction - 1
                        ):
                            layer_messages = self.context_guard.truncate_large_tool_results(
                                layer_messages
                            )
                            layer_messages = self.context_guard.compact_history(
                                layer_messages,
                                lambda text: self._summarize(api_client, candidate_model, text),
                            )
                            continue

                        cooldown = self._cooldown_for_reason(reason)
                        failures.append(
                            AttemptFailure(
                                profile_name=profile.name,
                                model=candidate_model,
                                reason=reason,
                                error_type=type(exc).__name__,
                                error=str(exc),
                            )
                        )
                        self.profile_manager.mark_failure(
                            profile,
                            reason,
                            cooldown_seconds=cooldown,
                        )
                        break

            attempted_profiles.clear()

        if failures:
            detail = " | ".join(failure.display() for failure in failures[-5:])
            raise RuntimeError(f"All profiles and models were exhausted: {detail}")
        raise RuntimeError("All profiles and models were exhausted: no available profile")

    def _run_attempt(
        self,
        *,
        api_client: Any,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        allowed_tools: list[str] | None = None,
    ) -> dict[str, Any]:
        current_messages = self._clone_messages(messages)
        tool_calls: list[str] = []
        tool_schemas = (
            self.tools.schemas_for(allowed_tools)
            if allowed_tools is not None
            else self.tools.schemas()
        )

        for _ in range(self.settings.max_iterations):
            response = api_client.messages.create(
                model=model,
                max_tokens=self.settings.max_tokens,
                system=system,
                tools=tool_schemas,
                messages=current_messages,
            )
            assistant_content = self._serialize_blocks(response.content)
            current_messages.append({"role": "assistant", "content": assistant_content})

            if response.stop_reason == "end_turn":
                return {
                    "text": self._extract_text(assistant_content),
                    "stop_reason": "end_turn",
                    "messages": current_messages,
                    "tool_calls": tool_calls,
                }

            if response.stop_reason != "tool_use":
                return {
                    "text": self._extract_text(assistant_content),
                    "stop_reason": response.stop_reason or "unknown",
                    "messages": current_messages,
                    "tool_calls": tool_calls,
                }

            tool_results = []
            for block in assistant_content:
                if block.get("type") != "tool_use":
                    continue
                tool_calls.append(block["name"])
                result = self.tools.dispatch(block["name"], block.get("input", {}))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": result,
                    }
                )
            current_messages.append({"role": "user", "content": tool_results})

        return {
            "text": "[max iterations reached]",
            "stop_reason": "max_iterations",
            "messages": current_messages,
            "tool_calls": tool_calls,
        }

    def _build_client(self, profile: AuthProfile) -> Any:
        if Anthropic is None:
            raise RuntimeError("anthropic is not installed")
        if not profile.api_key:
            raise RuntimeError(f"profile '{profile.name}' has no api key")
        client = httpx.Client(trust_env=False) if httpx is not None else None
        return Anthropic(
            api_key=profile.api_key,
            base_url=profile.base_url or None,
            http_client=client,
        )

    @staticmethod
    def _clone_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return json.loads(json.dumps(messages, ensure_ascii=False))

    def _summarize(self, api_client: Any, model: str, text: str) -> str:
        summary_response = api_client.messages.create(
            model=model,
            max_tokens=2048,
            system="You are a conversation summarizer. Be concise and factual.",
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Summarize the following conversation concisely, "
                        "preserving key facts and decisions. "
                        "Output only the summary, no preamble.\n\n"
                        + text
                    ),
                }
            ],
        )
        return self._extract_text(self._serialize_blocks(summary_response.content))

    @staticmethod
    def _cooldown_for_reason(reason: FailoverReason) -> float:
        if reason == FailoverReason.bad_request:
            return 0.0
        if reason == FailoverReason.timeout:
            return 60.0
        if reason == FailoverReason.rate_limit:
            return 120.0
        if reason in (FailoverReason.auth, FailoverReason.billing):
            return 300.0
        if reason == FailoverReason.overflow:
            return 600.0
        return 120.0

    @staticmethod
    def _serialize_blocks(blocks: Any) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for block in blocks:
            block_type = getattr(block, "type", "")
            if block_type == "text":
                serialized.append({"type": "text", "text": getattr(block, "text", "")})
            elif block_type == "tool_use":
                serialized.append(
                    {
                        "type": "tool_use",
                        "id": getattr(block, "id", ""),
                        "name": getattr(block, "name", ""),
                        "input": getattr(block, "input", {}),
                    }
                )
        return serialized

    @staticmethod
    def _extract_text(content: list[dict[str, Any]]) -> str:
        parts = [block.get("text", "") for block in content if block.get("type") == "text"]
        return "".join(parts).strip() or "[no text]"
