from __future__ import annotations

from pathlib import Path

from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.messaging.telegram import TelegramChannel


class FakeTelegramStateRepository:
    enabled = True

    def __init__(self, offset: int | None = None, *, fail: bool = False) -> None:
        self.offset = offset
        self.fail = fail
        self.written: list[tuple[str, str, int]] = []

    def read_channel_offset(self, channel: str, account_id: str) -> int | None:
        if self.fail:
            raise RuntimeError("postgres unavailable")
        assert channel == "telegram"
        assert account_id == "telegram-main"
        return self.offset

    def write_channel_offset(self, channel: str, account_id: str, offset: int):
        if self.fail:
            raise RuntimeError("postgres unavailable")
        self.written.append((channel, account_id, offset))
        self.offset = offset
        return {"offset_value": offset}


class FakeTelegramHttpClient:
    def __init__(self, updates) -> None:
        self.updates = updates
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, json: dict[str, object]):
        self.calls.append({"url": url, "json": json})

        class Response:
            def __init__(self, updates) -> None:
                self.updates = updates

            def json(self):
                return {"ok": True, "result": self.updates}

        return Response(self.updates)

    def close(self) -> None:
        return None


def _account() -> ChannelAccount:
    return ChannelAccount(
        channel="telegram",
        account_id="telegram-main",
        token="test-token",
    )


def _clear_proxy_env(monkeypatch) -> None:
    for name in ("ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)


def test_telegram_channel_prefers_postgres_offset(tmp_path: Path, monkeypatch) -> None:
    _clear_proxy_env(monkeypatch)
    local_offset = tmp_path / "telegram" / "offset-telegram-main.txt"
    local_offset.parent.mkdir(parents=True)
    local_offset.write_text("3", encoding="utf-8")
    repo = FakeTelegramStateRepository(offset=99)

    channel = TelegramChannel(_account(), tmp_path, read_backend=repo, write_backend=repo)

    assert channel._offset == 99


def test_telegram_channel_writes_postgres_and_local_offset(tmp_path: Path, monkeypatch) -> None:
    _clear_proxy_env(monkeypatch)
    repo = FakeTelegramStateRepository(offset=0)
    channel = TelegramChannel(_account(), tmp_path, read_backend=repo, write_backend=repo)
    channel._http = FakeTelegramHttpClient(
        [
            {
                "update_id": 10,
                "message": {
                    "chat": {"id": 123, "type": "private"},
                    "from": {"id": 123},
                    "text": "hello",
                },
            }
        ]
    )

    channel.poll()

    assert repo.written[-1] == ("telegram", "telegram-main", 11)
    assert (tmp_path / "telegram" / "offset-telegram-main.txt").read_text(encoding="utf-8") == "11"


def test_telegram_channel_falls_back_to_local_offset_when_postgres_fails(tmp_path: Path, monkeypatch) -> None:
    _clear_proxy_env(monkeypatch)
    local_offset = tmp_path / "telegram" / "offset-telegram-main.txt"
    local_offset.parent.mkdir(parents=True)
    local_offset.write_text("7", encoding="utf-8")
    repo = FakeTelegramStateRepository(offset=99, fail=True)

    channel = TelegramChannel(_account(), tmp_path, read_backend=repo, write_backend=repo)

    assert channel._offset == 7
