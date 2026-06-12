from pathlib import Path

from agent_gateway.sessions.store import SessionStore


def test_session_store_round_trip(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.append_message("main", "agent:main:direct:u-1", "user", "hello")
    store.append_message(
        "main",
        "agent:main:direct:u-1",
        "assistant",
        [{"type": "text", "text": "hi"}],
    )

    messages = store.load_messages("main", "agent:main:direct:u-1")
    assert messages == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]


def test_session_store_rebuilds_tool_transcript(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session_key = "agent:main:direct:u-2"

    store.rewrite_messages(
        "main",
        session_key,
        [
            {"role": "user", "content": "read file"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will inspect it."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "read_file",
                        "input": {"file_path": "README.md"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "file body",
                    }
                ],
            },
        ],
    )

    messages = store.load_messages("main", session_key)
    assert messages[0] == {"role": "user", "content": "read file"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"][1]["type"] == "tool_use"
    assert messages[2]["role"] == "user"
    assert messages[2]["content"][0]["type"] == "tool_result"


def test_session_store_omits_empty_assistant_content_and_patches_empty_tool_results(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path)
    session_key = "system:heartbeat:main"
    path = store.session_path("main", session_key)
    path.write_text(
        "\n".join(
            [
                '{"type":"user","content":"heartbeat"}',
                '{"type":"assistant","content":[]}',
                '{"type":"tool_result","tool_use_id":"toolu_1","content":""}',
                '{"type":"assistant","content":[{"type":"text","text":""}]}',
                '{"type":"assistant","content":[{"type":"text","text":"done"}]}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    messages = store.load_messages("main", session_key)

    assert messages == [
        {"role": "user", "content": "heartbeat"},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "[empty tool result]",
                }
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]
