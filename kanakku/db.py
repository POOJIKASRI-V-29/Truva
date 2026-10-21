"""Exasol access through PyExasol. Everything Kanakku owns lives in one schema."""

from __future__ import annotations

import re
import ssl
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd
import pyexasol

from .config import SQL_DIR, DbSettings


def connect(settings: DbSettings | None = None) -> pyexasol.ExaConnection:
    settings = settings or DbSettings.from_env()
    sslopt = None if settings.cert_validation else {"cert_reqs": ssl.CERT_NONE}
    conn = pyexasol.connect(
        dsn=settings.dsn,
        user=settings.user,
        password=settings.password,
        websocket_sslopt=sslopt,
        fetch_dict=False,
        compression=True,
        client_name="kanakku",
    )
    conn.execute(f"CREATE SCHEMA IF NOT EXISTS {settings.schema}")
    conn.execute(f"OPEN SCHEMA {settings.schema}")
    conn.kanakku_schema = settings.schema  # type: ignore[attr-defined]
    return conn


@contextmanager
def session(settings: DbSettings | None = None) -> Iterator[pyexasol.ExaConnection]:
    conn = connect(settings)
    try:
        yield conn
    finally:
        conn.close()


def split_statements(sql: str) -> list[str]:
    """Split a script on ';' at line ends, dropping '--' comment-only lines."""
    lines = [ln for ln in sql.splitlines() if not ln.strip().startswith("--")]
    parts = re.split(r";\s*(?:\n|$)", "\n".join(lines))
    return [p.strip() for p in parts if p.strip()]


def render(sql: str, params: dict) -> str:
    """Substitute {name} placeholders with values that were already validated.

    Only ints, floats, bools, dates and pre-quoted literals are allowed, so
    nothing user-typed can be spliced into SQL.
    """
    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key not in params:
            raise KeyError(f"SQL parameter {key!r} missing")
        val = params[key]
        if isinstance(val, bool):
            return "TRUE" if val else "FALSE"
        if isinstance(val, (int, float)):
            return repr(val)
        if hasattr(val, "isoformat"):
            return f"DATE '{val.isoformat()}'"
        raise TypeError(f"Unsupported SQL parameter type for {key}: {type(val)}")

    return re.sub(r"\{([a-z_]+)\}", repl, sql)


def run_script(conn: pyexasol.ExaConnection, path: Path | str, params: dict | None = None) -> int:
    path = Path(path)
    if not path.is_absolute():
        path = SQL_DIR / path
    statements = split_statements(path.read_text())
    for stmt in statements:
        conn.execute(render(stmt, params or {}) if params else stmt)
    return len(statements)


def query_df(conn: pyexasol.ExaConnection, sql: str, params: dict | None = None) -> pd.DataFrame:
    """Run a SELECT and return a DataFrame with lower-case column names.

    `params` uses PyExasol's own safe formatter ({name} quoted, {name!d} numeric).
    """
    stmt = conn.execute(sql, params or None)
    cols = [c.lower() for c in stmt.column_names()]
    return pd.DataFrame(stmt.fetchall(), columns=cols)


def scalar(conn: pyexasol.ExaConnection, sql: str, params: dict | None = None):
    return conn.execute(sql, params or None).fetchval()


def table_exists(conn: pyexasol.ExaConnection, table: str) -> bool:
    return bool(
        scalar(
            conn,
            "SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = {s} AND TABLE_NAME = {t}",
            {"s": conn.kanakku_schema, "t": table.upper()},
        )
    )
