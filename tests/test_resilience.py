import os

import pytest

from agent_gateway.runtime.resilience import AuthProfile, FailoverReason, ProfileManager, classify_failure
from agent_gateway.runtime.resilience import ResilienceRunner
from agent_gateway.config import GatewaySettings
from agent_gateway.tools.registry import ToolRegistry


def test_classify_failure_categories() -> None:
    assert classify_failure(RuntimeError("Error code: 429 rate limit")) == FailoverReason.rate_limit
    assert classify_failure(RuntimeError("401 invalid api key")) == FailoverReason.auth
    assert classify_failure(RuntimeError("context window token overflow")) == FailoverReason.overflow
    assert classify_failure(RuntimeError("Error code: 400 invalid request")) == FailoverReason.bad_request


def test_profile_manager_replace_preserves_runtime_state() -> None:
    manager = ProfileManager(
        [
            AuthProfile(
                name="primary",
                provider="anthropic",
                api_key="a",
                cooldown_until=50.0,
                failure_reason="timeout",
                last_good_at=10.0,
            )
        ]
    )

    manager.replace_profiles(
        [
            AuthProfile(
                name="primary",
                provider="anthropic",
                api_key="b",
                base_url="https://new",
            )
        ]
    )

    assert manager.profiles[0].api_key == "b"
    assert manager.profiles[0].cooldown_until == 50.0
    assert manager.profiles[0].failure_reason == "timeout"
    assert manager.profiles[0].last_good_at == 10.0


def test_resilience_runner_build_client_ignores_proxy_env(monkeypatch) -> None:
    monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:7890/")
    runner = ResilienceRunner(
        GatewaySettings(),
        ProfileManager([AuthProfile(name="primary", provider="anthropic", api_key="k")]),
        ToolRegistry(),
    )

    client = runner._build_client(AuthProfile(name="primary", provider="anthropic", api_key="k"))

    assert os.environ["ALL_PROXY"] == "socks://127.0.0.1:7890/"
    assert client is not None


def test_resilience_runner_reports_last_attempt_failure(monkeypatch) -> None:
    runner = ResilienceRunner(
        GatewaySettings(max_overflow_compaction=1),
        ProfileManager([AuthProfile(name="primary", provider="anthropic", api_key="k")]),
        ToolRegistry(),
    )

    monkeypatch.setattr(runner, "_build_client", lambda profile: object())

    def fail_attempt(**kwargs):
        del kwargs
        raise RuntimeError("401 invalid api key")

    monkeypatch.setattr(runner, "_run_attempt", fail_attempt)

    with pytest.raises(RuntimeError) as exc_info:
        runner.run("system", [{"role": "user", "content": "hello"}], model="m1")

    message = str(exc_info.value)
    assert "All profiles and models were exhausted:" in message
    assert "profile=primary" in message
    assert "model=m1" in message
    assert "reason=auth" in message
    assert "error=401 invalid api key" in message


def test_resilience_runner_does_not_cool_down_profile_for_bad_request(monkeypatch) -> None:
    manager = ProfileManager([AuthProfile(name="primary", provider="anthropic", api_key="k")])
    runner = ResilienceRunner(
        GatewaySettings(max_overflow_compaction=1),
        manager,
        ToolRegistry(),
    )

    monkeypatch.setattr(runner, "_build_client", lambda profile: object())

    def fail_attempt(**kwargs):
        del kwargs
        raise RuntimeError("Error code: 400 invalid request")

    monkeypatch.setattr(runner, "_run_attempt", fail_attempt)

    with pytest.raises(RuntimeError) as exc_info:
        runner.run("system", [{"role": "user", "content": "hello"}], model="m1")

    assert "reason=bad_request" in str(exc_info.value)
    assert manager.snapshot()[0]["cooldown_remaining"] == 0.0
    assert manager.snapshot()[0]["failure_reason"] == "bad_request"
