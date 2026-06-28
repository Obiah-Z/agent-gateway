from pathlib import Path

from agent_gateway.runtime.state.store import SessionStore


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
        {"role": "assistant", "content": [{"type": "text", "text": "done"}]},
    ]


def test_session_store_sanitizes_read_backend_history(tmp_path: Path) -> None:
    class ReadBackend:
        def read_session_messages(self, agent_id: str, session_key: str):
            return [
                {"role": "user", "content": "heartbeat"},
                {"role": "assistant", "content": []},
                {"role": "assistant", "content": [{"type": "text", "text": ""}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": ""}
                    ],
                },
                {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
            ]

    store = SessionStore(tmp_path)
    store.read_backend = ReadBackend()

    messages = store.load_messages("research", "system:heartbeat:research")

    assert messages == [
        {"role": "user", "content": "heartbeat"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]


def test_session_store_rewrite_does_not_persist_empty_assistant_messages(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path)
    session_key = "system:heartbeat:research"

    store.rewrite_messages(
        "research",
        session_key,
        [
            {"role": "user", "content": "heartbeat"},
            {"role": "assistant", "content": []},
            {"role": "assistant", "content": [{"type": "text", "text": ""}]},
            {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        ],
    )

    raw = store.session_path("research", session_key).read_text(encoding="utf-8")

    assert '"content": []' not in raw
    assert '"text": ""' not in raw
    assert store.load_messages("research", session_key) == [
        {"role": "user", "content": "heartbeat"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]


def test_session_store_drops_unmatched_tool_use_from_rebuilt_history(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path)
    session_key = "agent:main:direct:u-4"
    path = store.session_path("main", session_key)
    path.write_text(
        "\n".join(
            [
                '{"type":"user","content":"please use a tool"}',
                '{"type":"assistant","content":[{"type":"text","text":"I will inspect it."},{"type":"tool_use","id":"call_1","name":"read_file","input":{"file_path":"README.md"}}]}',
                '{"type":"assistant","content":[{"type":"text","text":"oops"}]}',
                '{"type":"user","content":"next question"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    messages = store.load_messages("main", session_key)

    assert messages == [
        {"role": "user", "content": "please use a tool"},
        {"role": "assistant", "content": [{"type": "text", "text": "I will inspect it."}]},
        {"role": "assistant", "content": [{"type": "text", "text": "oops"}]},
        {"role": "user", "content": "next question"},
    ]
