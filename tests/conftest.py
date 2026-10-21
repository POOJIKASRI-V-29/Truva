import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

TEST_SCHEMA = "KANAKKU_TEST"


def pytest_configure(config):
    config.addinivalue_line("markers", "exasol: needs a running Exasol database (skipped otherwise)")


@pytest.fixture(scope="session")
def exa():
    """A connection to a throw-away KANAKKU_TEST schema. Never touches any other schema."""
    from kanakku import db, schema
    from kanakku.config import DbSettings

    try:
        settings = DbSettings.from_env(schema=TEST_SCHEMA)
        conn = db.connect(settings)
    except Exception as exc:  # pragma: no cover - depends on the machine
        pytest.skip(f"Exasol not reachable: {exc}")
    assert conn.kanakku_schema == TEST_SCHEMA
    conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    conn.execute(f"CREATE SCHEMA {TEST_SCHEMA}")
    conn.execute(f"OPEN SCHEMA {TEST_SCHEMA}")
    schema.ensure_schema(conn)
    yield conn
    conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    conn.close()
