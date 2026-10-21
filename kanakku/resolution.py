"""Analyst decisions on exceptions. Every change is appended to RESOLUTION_LOG (never edited)."""

from __future__ import annotations

import pandas as pd
import pyexasol

from . import db

STATUSES = ["OPEN", "INVESTIGATING", "ESCALATED", "RESOLVED", "ACCEPTED_DIFFERENCE"]


def update_status(conn: pyexasol.ExaConnection, result_id: int, new_status: str,
                  note: str = "", analyst: str = "") -> dict:
    if new_status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    note, analyst = (note or "").strip()[:2000], (analyst or "").strip()[:100]
    row = db.query_df(conn, """SELECT RESULT_ID, RUN_ID, EXCEPTION_KEY, ISSUE_CODE, RESOLUTION_STATUS, IS_EXCEPTION
                               FROM RECONCILIATION_RESULTS WHERE RESULT_ID = {r!d}""", {"r": int(result_id)})
    if row.empty:
        raise LookupError(f"result {result_id} not found")
    r = row.iloc[0]
    if not r.is_exception:
        raise ValueError("matched records have no resolution workflow")
    conn.execute(
        """INSERT INTO RESOLUTION_LOG (EXCEPTION_KEY, RESULT_ID, RUN_ID, ISSUE_CODE, PREVIOUS_STATUS, NEW_STATUS,
                                       NOTE, ANALYST, CHANGED_AT)
           VALUES ({key}, {rid!d}, {run!d}, {code}, {prev}, {new}, {note}, {analyst}, CURRENT_TIMESTAMP)""",
        {"key": r.exception_key, "rid": int(r.result_id), "run": int(r.run_id), "code": r.issue_code,
         "prev": r.resolution_status, "new": new_status, "note": note or None, "analyst": analyst or None},
    )
    conn.execute("UPDATE RECONCILIATION_RESULTS SET RESOLUTION_STATUS = {s} WHERE RESULT_ID = {r!d}",
                 {"s": new_status, "r": int(result_id)})
    return {"result_id": int(result_id), "previous": r.resolution_status, "new": new_status}


def history(conn: pyexasol.ExaConnection, exception_key: str) -> pd.DataFrame:
    return db.query_df(conn, """SELECT CHANGED_AT, PREVIOUS_STATUS, NEW_STATUS, ANALYST, NOTE, RUN_ID
                                FROM RESOLUTION_LOG WHERE EXCEPTION_KEY = {k}
                                ORDER BY CHANGED_AT DESC, LOG_ID DESC""", {"k": exception_key})
