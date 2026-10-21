"""Score the engine's output against the synthetic ground truth.

Evaluation happens after reconciliation and only reads results + GROUND_TRUTH;
the engine itself never sees the labels.
"""

from __future__ import annotations

import pandas as pd
import pyexasol

from . import db

NON_EXCEPTION = {"MATCHED", "AWAITING_SETTLEMENT"}


def compare(engine: pd.DataFrame, truth: pd.DataFrame) -> pd.DataFrame:
    """Join engine results to truth per entity. Missing on either side means MATCHED.

    engine: entity_level, entity_id, issue_code
    truth:  entity_level, entity_id, true_issue_code
    """
    e = engine[["entity_level", "entity_id", "issue_code"]].copy()
    t = truth[["entity_level", "entity_id", "true_issue_code"]].copy()
    m = e.merge(t, on=["entity_level", "entity_id"], how="outer")
    m["predicted"] = m["issue_code"].fillna("MATCHED").replace({c: "MATCHED" for c in NON_EXCEPTION})
    m["actual"] = m["true_issue_code"].fillna("MATCHED")
    m["pred_exception"] = m["predicted"] != "MATCHED"
    m["true_exception"] = m["actual"] != "MATCHED"
    return m


def _prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else None
    return p, r, f


def metrics(m: pd.DataFrame) -> dict:
    """Detection metrics (is it an exception?) and per-issue-code metrics (is the code right?)."""
    tp = int((m.pred_exception & m.true_exception).sum())
    fp = int((m.pred_exception & ~m.true_exception).sum())
    fn = int((~m.pred_exception & m.true_exception).sum())
    tn = int((~m.pred_exception & ~m.true_exception).sum())
    p, r, f = _prf(tp, fp, fn)
    codes = sorted((set(m.predicted) | set(m.actual)) - {"MATCHED"})
    per_code = []
    for c in codes:
        ctp = int(((m.predicted == c) & (m.actual == c)).sum())
        cfp = int(((m.predicted == c) & (m.actual != c)).sum())
        cfn = int(((m.predicted != c) & (m.actual == c)).sum())
        cp, cr, cf = _prf(ctp, cfp, cfn)
        per_code.append({"issue_code": c, "seeded": int((m.actual == c).sum()), "detected": int((m.predicted == c).sum()),
                         "true_positive": ctp, "false_positive": cfp, "missed": cfn,
                         "precision": cp, "recall": cr, "f1": cf})
    exc = m[m.true_exception & m.pred_exception]
    return {
        "entities": len(m), "true_positive": tp, "false_positive": fp, "missed": fn, "true_negative": tn,
        "precision": p, "recall": r, "f1": f,
        "code_accuracy_on_detected": (float((exc.predicted == exc.actual).mean()) if len(exc) else None),
        "per_code": pd.DataFrame(per_code),
        "false_positive_rows": m[m.pred_exception & (m.predicted != m.actual)],
        "missed_rows": m[m.true_exception & (m.predicted != m.actual)],
        "confusion": pd.crosstab(m.actual, m.predicted),
    }


def evaluate_run(conn: pyexasol.ExaConnection, run_id: int) -> dict | None:
    truth = db.query_df(conn, "SELECT ENTITY_LEVEL, ENTITY_ID, TRUE_ISSUE_CODE FROM GROUND_TRUTH")
    if truth.empty:
        return None
    engine = db.query_df(conn, "SELECT ENTITY_LEVEL, ENTITY_ID, ISSUE_CODE FROM RECONCILIATION_RESULTS "
                               "WHERE RUN_ID = {r!d}", {"r": run_id})
    return metrics(compare(engine, truth))
