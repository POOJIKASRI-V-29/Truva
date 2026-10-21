"""Read-only SQL used by the dashboard. Every figure shown in the UI comes from here."""

from __future__ import annotations

import pandas as pd
import pyexasol

from . import db

ISSUE_CODES = ["MISSING_FROM_GATEWAY", "MISSING_FROM_BANK", "AMOUNT_MISMATCH", "POSSIBLE_DUPLICATE",
               "DELAYED_SETTLEMENT", "POSSIBLE_FEE_DIFFERENCE", "REVERSAL_OR_CANCELLATION", "UNRESOLVED"]
LEVELS = ["ORDER", "GATEWAY_TXN", "SETTLEMENT_BATCH", "BANK_TXN"]
BANDS = ["HIGH", "MEDIUM", "LOW"]


def _in_list(values: list[str], allowed: list[str]) -> str:
    """Quoted IN-list built only from known enum values (never free text)."""
    safe = [v for v in values if v in allowed]
    return ", ".join(f"'{v}'" for v in safe) or "NULL"


def runs(conn: pyexasol.ExaConnection) -> pd.DataFrame:
    return db.query_df(conn, "SELECT * FROM RECON_RUNS ORDER BY RUN_ID DESC")


def source_counts(conn: pyexasol.ExaConnection) -> dict:
    df = db.query_df(conn, """SELECT 'ORDERS' T, COUNT(*) N FROM ORDERS UNION ALL
                              SELECT 'GATEWAY', COUNT(*) FROM GATEWAY_TRANSACTIONS UNION ALL
                              SELECT 'BANK', COUNT(*) FROM BANK_TRANSACTIONS UNION ALL
                              SELECT 'GROUND_TRUTH', COUNT(*) FROM GROUND_TRUTH""")
    return dict(zip(df.t, df.n.astype(int)))


def kpis(conn: pyexasol.ExaConnection, run_id: int) -> dict:
    return db.query_df(conn, """
        SELECT COUNT(CASE WHEN ENTITY_LEVEL = 'ORDER' THEN 1 END) AS ORDERS,
               COUNT(CASE WHEN ENTITY_LEVEL = 'ORDER' AND ISSUE_CODE = 'MATCHED' THEN 1 END) AS ORDERS_MATCHED,
               COUNT(CASE WHEN ENTITY_LEVEL = 'SETTLEMENT_BATCH' THEN 1 END) AS BATCHES,
               COUNT(CASE WHEN ENTITY_LEVEL = 'SETTLEMENT_BATCH' AND ISSUE_CODE = 'MATCHED' THEN 1 END) AS BATCHES_MATCHED,
               COUNT(CASE WHEN IS_EXCEPTION THEN 1 END) AS EXCEPTIONS,
               COUNT(CASE WHEN IS_EXCEPTION AND RESOLUTION_STATUS NOT IN ('RESOLVED', 'ACCEPTED_DIFFERENCE') THEN 1 END) AS UNRESOLVED,
               COALESCE(SUM(CASE WHEN IS_EXCEPTION AND RESOLUTION_STATUS NOT IN ('RESOLVED', 'ACCEPTED_DIFFERENCE')
                                 THEN EXPOSURE_MINOR END), 0) AS OPEN_EXPOSURE,
               COUNT(CASE WHEN IS_EXCEPTION AND PRIORITY_BAND = 'HIGH'
                           AND RESOLUTION_STATUS NOT IN ('RESOLVED', 'ACCEPTED_DIFFERENCE') THEN 1 END) AS HIGH_OPEN,
               COUNT(CASE WHEN MATCH_STAGE = 'FUZZY' AND ISSUE_CODE = 'MATCHED' THEN 1 END) AS FUZZY_MATCHED,
               COUNT(CASE WHEN MATCH_STAGE = 'TOLERANCE' AND ISSUE_CODE = 'MATCHED' THEN 1 END) AS TOLERANCE_MATCHED
        FROM RECONCILIATION_RESULTS WHERE RUN_ID = {r!d}""", {"r": run_id}).iloc[0].to_dict()


def by_issue(conn: pyexasol.ExaConnection, run_id: int) -> pd.DataFrame:
    return db.query_df(conn, """
        SELECT ISSUE_CODE, ENTITY_LEVEL, COUNT(*) AS N, SUM(EXPOSURE_MINOR) AS EXPOSURE_MINOR
        FROM RECONCILIATION_RESULTS WHERE RUN_ID = {r!d} AND IS_EXCEPTION
        GROUP BY ISSUE_CODE, ENTITY_LEVEL ORDER BY N DESC""", {"r": run_id})


def by_stage(conn: pyexasol.ExaConnection, run_id: int) -> pd.DataFrame:
    return db.query_df(conn, """
        SELECT ENTITY_LEVEL, MATCH_STAGE, ISSUE_CODE, COUNT(*) AS N
        FROM RECONCILIATION_RESULTS WHERE RUN_ID = {r!d}
        GROUP BY ENTITY_LEVEL, MATCH_STAGE, ISSUE_CODE ORDER BY ENTITY_LEVEL, N DESC""", {"r": run_id})


def by_band(conn: pyexasol.ExaConnection, run_id: int) -> pd.DataFrame:
    return db.query_df(conn, """
        SELECT PRIORITY_BAND, COUNT(*) AS N, SUM(EXPOSURE_MINOR) AS EXPOSURE_MINOR
        FROM RECONCILIATION_RESULTS WHERE RUN_ID = {r!d} AND IS_EXCEPTION
        GROUP BY PRIORITY_BAND""", {"r": run_id})


def ageing(conn: pyexasol.ExaConnection, run_id: int) -> pd.DataFrame:
    return db.query_df(conn, """
        SELECT CASE WHEN AGE_DAYS < 7 THEN '0-6 days' WHEN AGE_DAYS < 14 THEN '7-13 days'
                    WHEN AGE_DAYS < 30 THEN '14-29 days' WHEN AGE_DAYS < 60 THEN '30-59 days'
                    ELSE '60+ days' END AS BUCKET,
               MIN(AGE_DAYS) AS SORT_KEY, COUNT(*) AS N, SUM(EXPOSURE_MINOR) AS EXPOSURE_MINOR
        FROM RECONCILIATION_RESULTS
        WHERE RUN_ID = {r!d} AND IS_EXCEPTION AND RESOLUTION_STATUS NOT IN ('RESOLVED', 'ACCEPTED_DIFFERENCE')
        GROUP BY 1 ORDER BY SORT_KEY""", {"r": run_id})


def daily_trend(conn: pyexasol.ExaConnection, run_id: int) -> pd.DataFrame:
    return db.query_df(conn, """
        SELECT CAST(ORDER_TS AS DATE) AS ORDER_DAY, COUNT(*) AS ORDERS,
               COUNT(CASE WHEN ISSUE_CODE = 'MATCHED' THEN 1 END) AS MATCHED_ORDERS,
               COUNT(CASE WHEN IS_EXCEPTION THEN 1 END) AS EXCEPTIONS,
               SUM(ORDER_AMOUNT_MINOR) AS ORDER_VALUE_MINOR
        FROM RECONCILIATION_RESULTS WHERE RUN_ID = {r!d} AND ENTITY_LEVEL = 'ORDER'
        GROUP BY 1 ORDER BY 1""", {"r": run_id})


def status_counts(conn: pyexasol.ExaConnection, run_id: int) -> pd.DataFrame:
    return db.query_df(conn, """SELECT RESOLUTION_STATUS, COUNT(*) AS N FROM RECONCILIATION_RESULTS
                                WHERE RUN_ID = {r!d} AND IS_EXCEPTION GROUP BY 1 ORDER BY 2 DESC""", {"r": run_id})


def queue(conn: pyexasol.ExaConnection, run_id: int, codes=None, bands=None, statuses=None,
          levels=None, search: str = "", limit: int = 2000) -> pd.DataFrame:
    where = ["RUN_ID = {r!d}", "IS_EXCEPTION"]
    if codes:
        where.append(f"ISSUE_CODE IN ({_in_list(codes, ISSUE_CODES)})")
    if bands:
        where.append(f"PRIORITY_BAND IN ({_in_list(bands, BANDS)})")
    if statuses:
        from .resolution import STATUSES
        where.append(f"RESOLUTION_STATUS IN ({_in_list(statuses, STATUSES)})")
    if levels:
        where.append(f"ENTITY_LEVEL IN ({_in_list(levels, LEVELS)})")
    params = {"r": run_id, "lim": int(limit)}
    if search.strip():
        where.append("""(UPPER(ENTITY_ID) LIKE {q} OR UPPER(COALESCE(ORDER_ID, '')) LIKE {q}
                         OR UPPER(COALESCE(GATEWAY_TXN_ID, '')) LIKE {q} OR UPPER(COALESCE(BANK_TXN_ID, '')) LIKE {q}
                         OR UPPER(COALESCE(SETTLEMENT_BATCH_ID, '')) LIKE {q} OR UPPER(EXPLANATION) LIKE {q})""")
        params["q"] = "%" + search.strip().upper().replace("%", "") + "%"
    return db.query_df(conn, f"""
        SELECT RESULT_ID, PRIORITY_SCORE, PRIORITY_BAND, ISSUE_CODE, ENTITY_LEVEL, ENTITY_ID, EVIDENCE_TYPE,
               MATCH_STAGE, EXPOSURE_MINOR, DISCREPANCY_MINOR, AGE_DAYS, REFERENCE_DATE, RESOLUTION_STATUS,
               ORDER_ID, GATEWAY_TXN_ID, BANK_TXN_ID, SETTLEMENT_BATCH_ID, EXPLANATION
        FROM RECONCILIATION_RESULTS WHERE {' AND '.join(where)}
        ORDER BY PRIORITY_SCORE DESC, EXPOSURE_MINOR DESC, RESULT_ID
        LIMIT {{lim!d}}""", params)


def result(conn: pyexasol.ExaConnection, result_id: int) -> dict | None:
    df = db.query_df(conn, "SELECT * FROM RECONCILIATION_RESULTS WHERE RESULT_ID = {r!d}", {"r": int(result_id)})
    return None if df.empty else df.iloc[0].to_dict()


def source_records(conn: pyexasol.ExaConnection, r: dict) -> dict[str, pd.DataFrame]:
    """Raw rows from the three sources that this result refers to."""
    out: dict[str, pd.DataFrame] = {}
    if r.get("order_id"):
        out["orders"] = db.query_df(conn, "SELECT * FROM ORDERS WHERE ORDER_ID = {o}", {"o": r["order_id"]})
    gw_ids = [x for x in (r.get("gateway_txn_id"), r.get("related_txn_id")) if x]
    if r.get("order_id") or gw_ids:
        out["gateway"] = db.query_df(conn, """
            SELECT * FROM GATEWAY_TRANSACTIONS
            WHERE UPPER(TRIM(ORDER_REF)) = {o} OR GATEWAY_TXN_ID = {g1} OR GATEWAY_TXN_ID = {g2}
            ORDER BY TXN_TS""", {"o": r.get("order_id") or "", "g1": gw_ids[0] if gw_ids else "",
                                 "g2": gw_ids[-1] if gw_ids else ""})
    if r.get("entity_level") == "SETTLEMENT_BATCH":
        out["batch_transactions"] = db.query_df(conn, """
            SELECT GATEWAY_TXN_ID, ORDER_REF, TXN_TYPE, TXN_TS, GROSS_AMOUNT_MINOR, FEE_MINOR, TAX_MINOR,
                   NET_AMOUNT_MINOR, PAYMENT_METHOD
            FROM GATEWAY_TRANSACTIONS WHERE SETTLEMENT_BATCH_ID = {b} AND TXN_STATUS = 'CAPTURED'
            ORDER BY TXN_TS""", {"b": r["settlement_batch_id"]})
    bank_ids = [x for x in (r.get("bank_txn_id"), r.get("related_txn_id")) if x and str(x).startswith("BNK")]
    bank_filter = " OR ".join(["BANK_TXN_ID = {b%d}" % i for i in range(len(bank_ids))]) or "FALSE"
    params = {f"b{i}": v for i, v in enumerate(bank_ids)}
    if r.get("settlement_batch_id"):
        bank_filter += " OR NARRATION LIKE {nar}"
        params["nar"] = "%" + r["settlement_batch_id"] + "%"
    if r.get("gateway_txn_id"):
        bank_filter += " OR NARRATION LIKE {nar2}"
        params["nar2"] = "%" + r["gateway_txn_id"] + "%"
    out["bank"] = db.query_df(conn, f"SELECT * FROM BANK_TRANSACTIONS WHERE {bank_filter} ORDER BY VALUE_DATE",
                              params or None)
    return out


def ingestion_log(conn: pyexasol.ExaConnection) -> pd.DataFrame:
    return db.query_df(conn, "SELECT * FROM INGESTION_LOG ORDER BY LOAD_ID DESC")


def latest_rejects(conn: pyexasol.ExaConnection) -> pd.DataFrame:
    return db.query_df(conn, """
        SELECT r.SOURCE_NAME, r.LINE_NO, r.RECORD_ID, r.REJECT_REASON, r.RAW_RECORD, r.LOAD_ID
        FROM INGESTION_REJECTS r
        JOIN (SELECT SOURCE_NAME, MAX(LOAD_ID) AS LOAD_ID FROM INGESTION_LOG GROUP BY SOURCE_NAME) l
          ON l.SOURCE_NAME = r.SOURCE_NAME AND l.LOAD_ID = r.LOAD_ID
        ORDER BY r.SOURCE_NAME, r.LINE_NO""")


def result_explanations(conn: pyexasol.ExaConnection, run_id: int) -> pd.DataFrame:
    return db.query_df(conn, """SELECT RESULT_ID, ENTITY_LEVEL, ENTITY_ID, EXPLANATION FROM RECONCILIATION_RESULTS
                                WHERE RUN_ID = {r!d}""", {"r": run_id})


def ground_truth_scenarios(conn: pyexasol.ExaConnection) -> pd.DataFrame:
    return db.query_df(conn, "SELECT ENTITY_LEVEL, ENTITY_ID, SCENARIO FROM GROUND_TRUTH")
