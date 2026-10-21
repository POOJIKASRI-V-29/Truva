"""Run the reconciliation pipeline inside Exasol and record the run."""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import date, timedelta

import pyexasol

from . import db, priority
from .config import ReconConfig


def default_close_period(as_of: date) -> tuple[date, date]:
    """The calendar month before the as-of month — the books being closed."""
    end = as_of.replace(day=1) - timedelta(days=1)
    return end.replace(day=1), end


def resolve_dates(conn: pyexasol.ExaConnection, cfg: ReconConfig) -> tuple[date, date, date]:
    as_of = cfg.as_of_date
    if as_of is None:
        latest = db.scalar(conn, "SELECT MAX(VALUE_DATE) FROM BANK_TRANSACTIONS")
        as_of = date.fromisoformat(str(latest)) if latest else date.today()
    cs, ce = default_close_period(as_of)
    return as_of, cfg.close_period_start or cs, cfg.close_period_end or ce


def _validate(cfg: ReconConfig) -> None:
    if not (0 <= cfg.amount_tolerance_minor <= 1_000_000):
        raise ValueError("amount tolerance must be between 0 and 1,000,000 paise")
    if not (0 <= cfg.delay_threshold_days <= 60):
        raise ValueError("delay threshold must be between 0 and 60 days")
    if not (0 <= cfg.batch_window_days <= 60):
        raise ValueError("batch window must be between 0 and 60 days")
    if not (0 <= cfg.fee_difference_max_pct <= 20):
        raise ValueError("fee band must be between 0 and 20 percent")
    if not (1 <= cfg.fuzzy_window_minutes <= 1440):
        raise ValueError("fuzzy window must be between 1 and 1440 minutes")


def run(conn: pyexasol.ExaConnection, cfg: ReconConfig | None = None) -> dict:
    """Execute all matching stages atomically. Returns the run summary."""
    cfg = cfg or ReconConfig()
    _validate(cfg)
    as_of, close_start, close_end = resolve_dates(conn, cfg)
    run_id = int(db.scalar(conn, "SELECT COALESCE(MAX(RUN_ID), 0) + 1 FROM RECON_RUNS"))
    params_json = json.dumps({k: (v.isoformat() if isinstance(v, date) else v)
                              for k, v in asdict(cfg).items() if k != "extra"})
    conn.execute(
        """INSERT INTO RECON_RUNS (RUN_ID, STARTED_AT, RUN_STATUS, AS_OF_DATE, CLOSE_PERIOD_START,
                                   CLOSE_PERIOD_END, PARAMS)
           VALUES ({run_id!d}, CURRENT_TIMESTAMP, 'RUNNING', {as_of}, {cs}, {ce}, {params})""",
        {"run_id": run_id, "as_of": as_of.isoformat(), "cs": close_start.isoformat(),
         "ce": close_end.isoformat(), "params": params_json},
    )
    sql_params = {
        "run_id": run_id, "as_of": as_of, "tol": int(cfg.amount_tolerance_minor),
        "delay_days": int(cfg.delay_threshold_days), "batch_window_days": int(cfg.batch_window_days),
        "fee_pct": float(cfg.fee_difference_max_pct), "fuzzy_enabled": bool(cfg.fuzzy_enabled),
        "fuzzy_window_sec": int(cfg.fuzzy_window_minutes) * 60, "fuzzy_window_min": int(cfg.fuzzy_window_minutes),
    }
    started = time.perf_counter()
    conn.set_autocommit(False)
    try:
        db.run_script(conn, "reconcile.sql", sql_params)
        for stmt in priority.sql_update(run_id, close_start, close_end):
            conn.execute(stmt)
        summary = db.query_df(conn, f"""
            SELECT COUNT(CASE WHEN ENTITY_LEVEL = 'ORDER' THEN 1 END) AS ORDERS_TOTAL,
                   COUNT(CASE WHEN ENTITY_LEVEL = 'ORDER' AND ISSUE_CODE = 'MATCHED' THEN 1 END) AS ORDERS_MATCHED,
                   COUNT(*) AS RESULTS_TOTAL,
                   COUNT(CASE WHEN IS_EXCEPTION THEN 1 END) AS EXCEPTIONS_TOTAL,
                   COALESCE(SUM(CASE WHEN IS_EXCEPTION THEN EXPOSURE_MINOR END), 0) AS EXPOSURE_MINOR
            FROM RECONCILIATION_RESULTS WHERE RUN_ID = {run_id}""").iloc[0].to_dict()
        duration_ms = int((time.perf_counter() - started) * 1000)
        conn.execute(f"""UPDATE RECON_RUNS SET RUN_STATUS = 'COMPLETED', FINISHED_AT = CURRENT_TIMESTAMP,
                ORDERS_TOTAL = {int(summary['orders_total'])}, ORDERS_MATCHED = {int(summary['orders_matched'])},
                RESULTS_TOTAL = {int(summary['results_total'])}, EXCEPTIONS_TOTAL = {int(summary['exceptions_total'])},
                EXPOSURE_MINOR = {int(summary['exposure_minor'])}, DURATION_MS = {duration_ms}
            WHERE RUN_ID = {run_id}""")
        conn.commit()
    except Exception as exc:
        conn.rollback()
        conn.execute("UPDATE RECON_RUNS SET RUN_STATUS = 'FAILED', FINISHED_AT = CURRENT_TIMESTAMP, "
                     "ERROR_MESSAGE = {msg} WHERE RUN_ID = {run_id!d}",
                     {"msg": str(exc)[:2000], "run_id": run_id})
        conn.commit()
        raise
    finally:
        conn.set_autocommit(True)
    return {"run_id": run_id, "as_of": as_of, "close_period": (close_start, close_end),
            "duration_ms": duration_ms, **{k: int(v) for k, v in summary.items()}}


def latest_run_id(conn: pyexasol.ExaConnection) -> int | None:
    rid = db.scalar(conn, "SELECT MAX(RUN_ID) FROM RECON_RUNS WHERE RUN_STATUS = 'COMPLETED'")
    return int(rid) if rid is not None else None
