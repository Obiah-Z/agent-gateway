from __future__ import annotations

from agent_gateway.gateways.messaging.base import ChannelAccount
from agent_gateway.gateways.wework.channel import WeWorkChannel
from agent_gateway.runtime.domain.models import OutboundMessage


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def json(self) -> dict:
        return self.payload


class FakeHTTPClient:
    def __init__(self) -> None:
        self.get_calls = []
        self.post_calls = []

    def get(self, url: str, *, params: dict) -> FakeResponse:
        self.get_calls.append((url, params))
        return FakeResponse({"errcode": 0, "access_token": "token-ok", "expires_in": 7200})

    def post(self, url: str, *, params: dict, json: dict) -> FakeResponse:
        self.post_calls.append((url, params, json))
        return FakeResponse({"errcode": 0, "errmsg": "ok"})

    def close(self) -> None:
        pass


def _channel(render_mode: str = "auto") -> tuple[WeWorkChannel, FakeHTTPClient]:
    account = ChannelAccount(
        channel="wework",
        account_id="wework-main",
        token="callback-token",
        config={
            "corp_id": "ww1234567890abcdef",
            "agent_id": "1000002",
            "secret": "secret",
            "render_mode": render_mode,
        },
    )
    channel = WeWorkChannel(account)
    fake_http = FakeHTTPClient()
    channel._http = fake_http
    return channel, fake_http


def test_wework_table_markdown_is_sent_as_markdown_by_default() -> None:
    channel, fake_http = _channel()

    ok = channel.send(
        OutboundMessage(
            channel="wework",
            to="zhangsan",
            text=(
                "今日复盘 7/6\n"
                "| 维度 | 内容 |\n"
                "|------|------|\n"
                "| ✅ 完成 | 饮食推荐 Agent 搭建与适配 |\n"
                "| ⚠ 卡点 | 面试准备范围不清晰 |\n"
                "| 🔜 明天第一步 | 继续背诵八股 |\n"
                "关于焦虑：建议同步补充项目深挖。"
            ),
        )
    )

    assert ok is True
    _, _, payload = fake_http.post_calls[0]
    assert payload["msgtype"] == "markdown"
    assert payload["markdown"]["content"].startswith("今日复盘 7/6")
    assert "card_action" not in payload


def test_wework_forced_text_sends_plain_text() -> None:
    channel, fake_http = _channel(render_mode="text")

    ok = channel.send(
        OutboundMessage(
            channel="wework",
            to="zhangsan",
            text="| 维度 | 内容 |\n|------|------|\n| 完成 | 继续背诵八股 |",
        )
    )

    assert ok is True
    _, _, payload = fake_http.post_calls[0]
    assert payload["msgtype"] == "text"
    assert payload["text"]["content"].startswith("| 维度 | 内容 |")
