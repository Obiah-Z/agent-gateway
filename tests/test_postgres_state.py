from agent_gateway.runtime.state.postgres import POSTGRES_STATE_TABLES


def test_postgres_state_tables_cover_core_runtime_entities() -> None:
    table_names = [table.name for table in POSTGRES_STATE_TABLES]

    assert table_names == [
        "sessions",
        "tasks",
        "runtime_events",
        "errors",
        "metrics",
        "memory_entries",
        "config_audits",
    ]
    assert POSTGRES_STATE_TABLES[0].primary_key == "id"
    assert "session_key" in POSTGRES_STATE_TABLES[0].columns
    assert "payload" in POSTGRES_STATE_TABLES[1].columns
    assert "correlation_id" in POSTGRES_STATE_TABLES[2].columns
    assert "category" in POSTGRES_STATE_TABLES[3].columns
    assert "labels" in POSTGRES_STATE_TABLES[4].columns
    assert "content" in POSTGRES_STATE_TABLES[5].columns
    assert "actor" in POSTGRES_STATE_TABLES[6].columns
