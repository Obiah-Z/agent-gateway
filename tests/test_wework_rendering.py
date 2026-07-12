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
        self.upload_calls = []
        self.message_calls = []

    def get(self, url: str, *, params: dict) -> FakeResponse:
        self.get_calls.append((url, params))
        return FakeResponse({"errcode": 0, "access_token": "token-ok", "expires_in": 7200})

    def post(self, url: str, *, params: dict, json: dict | None = None, files=None) -> FakeResponse:
        self.post_calls.append((url, params, json, files))
        if files is not None:
            media = files["media"]
            self.upload_calls.append((url, params, media[0]))
            return FakeResponse({"errcode": 0, "errmsg": "ok", "media_id": "media-file-1"})
        self.message_calls.append((url, params, json))
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
    _, _, payload = fake_http.message_calls[0]
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
    _, _, payload = fake_http.message_calls[0]
    assert payload["msgtype"] == "text"
    assert payload["text"]["content"].startswith("| 维度 | 内容 |")


def test_wework_channel_uploads_metadata_file_attachment(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "workspace" / "reports" / "github-repos" / "仓库分析.md"
    report.parent.mkdir(parents=True)
    report.write_text("# report", encoding="utf-8")
    channel, fake_http = _channel()

    ok = channel.send(
        OutboundMessage(
            channel="wework",
            to="zhangsan",
            text="报告已生成。",
            metadata={"files": [{"path": "workspace/reports/github-repos/仓库分析.md"}]},
        )
    )

    assert ok is True
    assert len(fake_http.upload_calls) == 1
    assert fake_http.upload_calls[0][1]["type"] == "file"
    assert fake_http.upload_calls[0][2] == "仓库分析.md"
    assert len(fake_http.message_calls) == 2
    _, _, file_payload = fake_http.message_calls[1]
    assert file_payload["msgtype"] == "file"
    assert file_payload["file"] == {"media_id": "media-file-1"}
    assert file_payload["touser"] == "zhangsan"


def test_wework_channel_uploads_report_path_from_reply_text(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    report = tmp_path / "workspace" / "reports" / "diagrams" / "Agent执行流程.drawio"
    report.parent.mkdir(parents=True)
    report.write_text("<mxfile />", encoding="utf-8")
    channel, fake_http = _channel()

    ok = channel.send(
        OutboundMessage(
            channel="wework",
            to="zhangsan",
            text="流程图路径：workspace/reports/diagrams/Agent执行流程.drawio",
        )
    )

    assert ok is True
    assert len(fake_http.upload_calls) == 1
    _, _, file_payload = fake_http.message_calls[1]
    assert file_payload["msgtype"] == "file"
    assert file_payload["file"]["media_id"] == "media-file-1"


def test_wework_channel_ignores_unsafe_file_attachment(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    channel, fake_http = _channel()

    ok = channel.send(
        OutboundMessage(
            channel="wework",
            to="zhangsan",
            text="不要上传",
            metadata={"files": [{"path": str(secret)}]},
        )
    )

    assert ok is True
    assert fake_http.upload_calls == []
    assert len(fake_http.message_calls) == 1
