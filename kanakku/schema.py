"""Create Kanakku's tables (idempotent) and seed reference data."""

from __future__ import annotations

import pyexasol

from . import db
from .config import SQL_DIR
from .ratecard import FEE_BPS, GST_PCT

SOURCE_TABLES = ("ORDERS", "GATEWAY_TRANSACTIONS", "BANK_TRANSACTIONS")


def ensure_schema(conn: pyexasol.ExaConnection) -> None:
    db.run_script(conn, "schema.sql")
    # a PL function body contains ';', so the file is sent as one statement
    conn.execute((SQL_DIR / "functions.sql").read_text().strip())
    rows = " UNION ALL ".join(
        f"SELECT '{m}' AS PAYMENT_METHOD, {bps} AS FEE_BPS, {GST_PCT} AS GST_PCT FROM DUAL"
        for m, bps in FEE_BPS.items()
    )
    conn.execute(
        f"""MERGE INTO FEE_RATE_CARD t USING ({rows}) s ON t.PAYMENT_METHOD = s.PAYMENT_METHOD
            WHEN MATCHED THEN UPDATE SET t.FEE_BPS = s.FEE_BPS, t.GST_PCT = s.GST_PCT
            WHEN NOT MATCHED THEN INSERT VALUES (s.PAYMENT_METHOD, s.FEE_BPS, s.GST_PCT)"""
    )


def clear_source_data(conn: pyexasol.ExaConnection) -> None:
    """Explicit, opt-in reset of Kanakku's own source tables (used by --replace).

    Reconciliation history and the resolution log are left untouched.
    """
    for t in SOURCE_TABLES:
        conn.execute(f"TRUNCATE TABLE {t}")
