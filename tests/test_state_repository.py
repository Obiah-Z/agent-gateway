from agent_gateway.runtime.state import STATE_TABLES, StateRepository


def test_state_repository_exposes_expected_tables() -> None:
    assert STATE_TABLES == (
        "sessions",
        "tasks",
        "runtime_events",
        "errors",
        "metrics",
        "memory_entries",
        "config_audits",
    )


def test_state_repository_protocol_is_runtime_checkable() -> None:
    class DummyRepository:
        def list(self, table, *, limit=50, cursor="", filters=None):
            return []

        def get(self, table, key):
            return None

        def append(self, table, row):
            return row

        def upsert(self, table, row):
            return row

        def delete(self, table, key):
            return True

        def query(self, table, *, sql, params=None):
            return []

    assert isinstance(DummyRepository(), StateRepository)
