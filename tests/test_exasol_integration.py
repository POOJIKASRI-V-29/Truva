"""End-to-end tests against a real Exasol database (schema KANAKKU_TEST).

Skipped automatically when Exasol is not reachable.
"""

from datetime import date

import pytest

import scenario
from kanakku import db, ingestion, priority, reconciliation, resolution
from kanakku.config import ReconConfig

pytestmark = pytest.mark.exasol


def _load(conn):
    stats = []
    for kind, df in scenario.frames().items():
        stats.append(ingestion.load(conn, ingestion.validate(kind, df), f"scenario_{kind}.csv"))
    return stats


@pytest.fixture(scope="module")
def loaded(exa):
    _load(exa)
    return exa


@pytest.fixture(scope="module")
def run1(loaded):
    return reconciliation.run(loaded, ReconConfig(as_of_date=scenario.AS_OF))


def _results(conn, run_id):
    df = db.query_df(conn, "SELECT * FROM RECONCILIATION_RESULTS WHERE RUN_ID = {r!d}", {"r": run_id})
    return df.set_index(["entity_level", "entity_id"])


def test_every_rule_fires_where_expected(loaded, run1):
    res = _results(loaded, run1["run_id"])
    wrong = {}
    for key, (code, stage) in scenario.EXPECTED.items():
        got = (res.loc[key, "issue_code"], res.loc[key, "match_stage"]) if key in res.index else None
        if got != (code, stage):
            wrong[key] = (got, (code, stage))
    assert not wrong, wrong


def test_no_unexpected_exceptions(loaded, run1):
    res = _results(loaded, run1["run_id"])
    exc = set(res[res.is_exception].index)
    expected = {k for k, (code, _) in scenario.EXPECTED.items() if code != "MATCHED"}
    assert exc == expected


def test_one_row_per_entity(loaded, run1):
    res = _results(loaded, run1["run_id"]).reset_index()
    assert not res.duplicated(["entity_level", "entity_id"]).any()
    assert (res.entity_level == "ORDER").sum() == len(scenario.ORDERS)


def test_tolerance_boundary_is_inclusive(loaded, run1):
    res = _results(loaded, run1["run_id"])
    assert res.loc[("ORDER", "ORD-T1"), "discrepancy_minor"] == 100
    assert res.loc[("ORDER", "ORD-T2"), "discrepancy_minor"] == 101


def test_tighter_tolerance_flags_rounding(loaded):
    r = reconciliation.run(loaded, ReconConfig(as_of_date=scenario.AS_OF, amount_tolerance_minor=99))
    assert _results(loaded, r["run_id"]).loc[("ORDER", "ORD-T1"), "issue_code"] == "AMOUNT_MISMATCH"


def test_fuzzy_can_be_switched_off(loaded):
    r = reconciliation.run(loaded, ReconConfig(as_of_date=scenario.AS_OF, fuzzy_enabled=False))
    res = _results(loaded, r["run_id"])
    assert res.loc[("ORDER", "ORD-Z1"), "issue_code"] == "MISSING_FROM_GATEWAY"
    assert res.loc[("GATEWAY_TXN", scenario.gid("Z1")), "issue_code"] == "UNRESOLVED"


def test_fee_and_batch_amounts_exact(loaded, run1):
    res = _results(loaded, run1["run_id"])
    f1 = res.loc[("ORDER", "ORD-F1")]
    assert f1.discrepancy_minor == -4484 and f1.evidence_type == "SUSPECTED"
    assert "fee + GST of ₹44.84" in f1.explanation
    b5 = res.loc[("SETTLEMENT_BATCH", scenario.B5)]
    assert b5.discrepancy_minor == -2000 and b5.exposure_minor == 2000
    b1 = res.loc[("SETTLEMENT_BATCH", scenario.B1)]
    assert b1.expected_amount_minor == scenario.B1_NET == b1.actual_amount_minor


def test_reversals_and_cancellations(loaded, run1):
    res = _results(loaded, run1["run_id"])
    assert "CANCELLED" in res.loc[("ORDER", "ORD-C1"), "explanation"]
    assert res.loc[("ORDER", "ORD-C1"), "exposure_minor"] == 30000
    assert "chargeback" in res.loc[("ORDER", "ORD-CB"), "explanation"]
    assert res.loc[("ORDER", "ORD-R1"), "issue_code"] == "MATCHED"


def test_duplicates_point_at_their_original(loaded, run1):
    res = _results(loaded, run1["run_id"])
    assert res.loc[("GATEWAY_TXN", scenario.gid("D1b")), "related_txn_id"] == scenario.gid("D1a")
    assert res.loc[("BANK_TXN", "BNK-2"), "related_txn_id"] == "BNK-1"


def test_explanations_never_claim_fraud(loaded, run1):
    res = _results(loaded, run1["run_id"])
    assert not res.explanation.str.contains("fraud", case=False).any()
    exc = res[res.is_exception]
    assert exc.explanation.str.match(r"^(Confirmed|Suspected|Unresolved):").all()


def test_sql_priority_matches_python(loaded, run1):
    res = _results(loaded, run1["run_id"]).reset_index()
    cs, ce = run1["close_period"]
    for r in res[res.is_exception].itertuples():
        ref = date.fromisoformat(str(r.reference_date))
        s = priority.score(r.issue_code, int(r.exposure_minor), int(r.age_days), ref, cs, ce)
        assert (r.score_amount, r.score_age, r.score_severity, r.score_close) == (s.amount, s.age, s.severity, s.close)
        assert r.priority_score == s.total and r.priority_band == s.band
    assert (res[~res.is_exception].priority_band == "NONE").all()


def test_reload_does_not_duplicate(loaded):
    before = {t: db.scalar(loaded, f"SELECT COUNT(*) FROM {t}")
              for t in ("ORDERS", "GATEWAY_TRANSACTIONS", "BANK_TRANSACTIONS")}
    stats = _load(loaded)
    after = {t: db.scalar(loaded, f"SELECT COUNT(*) FROM {t}") for t in before}
    assert before == after
    assert all(s["ROWS_INSERTED"] == 0 for s in stats)


def test_money_round_trips_through_exasol(loaded):
    total = db.scalar(loaded, "SELECT SUM(AMOUNT_MINOR) FROM ORDERS")
    assert int(total) == sum(o[2] for o in scenario.ORDERS)


def test_rerun_is_stable(loaded, run1):
    r2 = reconciliation.run(loaded, ReconConfig(as_of_date=scenario.AS_OF))
    a = _results(loaded, run1["run_id"])[["issue_code", "match_stage", "exposure_minor"]]
    b = _results(loaded, r2["run_id"])[["issue_code", "match_stage", "exposure_minor"]]
    assert a.sort_index().equals(b.sort_index())
    assert r2["run_id"] > run1["run_id"]


def test_resolution_is_logged_and_carried_forward(loaded, run1):
    res = _results(loaded, run1["run_id"])
    rid = int(res.loc[("ORDER", "ORD-M1"), "result_id"])
    resolution.update_status(loaded, rid, "INVESTIGATING", "asked gateway", "tester")
    resolution.update_status(loaded, rid, "RESOLVED", "payment never completed; order voided", "tester")
    key = res.loc[("ORDER", "ORD-M1"), "exception_key"]
    hist = resolution.history(loaded, key)
    assert list(hist.new_status) == ["RESOLVED", "INVESTIGATING"]
    assert list(hist.previous_status) == ["INVESTIGATING", "OPEN"]
    r2 = reconciliation.run(loaded, ReconConfig(as_of_date=scenario.AS_OF))
    assert _results(loaded, r2["run_id"]).loc[("ORDER", "ORD-M1"), "resolution_status"] == "RESOLVED"
    assert _results(loaded, r2["run_id"]).loc[("ORDER", "ORD-Z2"), "resolution_status"] == "OPEN"


def test_resolution_rejects_bad_input(loaded, run1):
    res = _results(loaded, run1["run_id"])
    with pytest.raises(ValueError):
        resolution.update_status(loaded, int(res.loc[("ORDER", "ORD-M1"), "result_id"]), "DELETED")
    with pytest.raises(ValueError):
        resolution.update_status(loaded, int(res.loc[("ORDER", "ORD-A1"), "result_id"]), "RESOLVED")
    with pytest.raises(LookupError):
        resolution.update_status(loaded, 1, "RESOLVED")


def test_failed_run_rolls_back(loaded, monkeypatch):
    before = db.scalar(loaded, "SELECT COUNT(*) FROM RECONCILIATION_RESULTS")
    monkeypatch.setattr(priority, "sql_update", lambda *a: ["SELECT * FROM TABLE_THAT_DOES_NOT_EXIST"])
    with pytest.raises(Exception):
        reconciliation.run(loaded, ReconConfig(as_of_date=scenario.AS_OF))
    assert db.scalar(loaded, "SELECT COUNT(*) FROM RECONCILIATION_RESULTS") == before
    assert db.scalar(loaded, "SELECT RUN_STATUS FROM RECON_RUNS ORDER BY RUN_ID DESC LIMIT 1") == "FAILED"


def test_invalid_parameters_rejected(loaded):
    with pytest.raises(ValueError):
        reconciliation.run(loaded, ReconConfig(amount_tolerance_minor=-1))


def test_every_dashboard_query_runs(loaded, run1):
    """Each read query the UI uses must be valid Exasol SQL (reserved words bit us twice)."""
    from kanakku import queries

    rid = run1["run_id"]
    assert queries.kpis(loaded, rid)["orders"] == len(scenario.ORDERS)
    for fn in (queries.by_issue, queries.by_stage, queries.by_band, queries.ageing, queries.daily_trend,
               queries.status_counts, queries.result_explanations):
        assert not fn(loaded, rid).empty, fn.__name__
    assert queries.runs(loaded).shape[0] >= 1
    assert queries.source_counts(loaded)["ORDERS"] == len(scenario.ORDERS)
    q = queries.queue(loaded, rid, codes=["MISSING_FROM_GATEWAY"], bands=["HIGH", "MEDIUM", "LOW"],
                      statuses=["OPEN"], levels=["ORDER"], search="ord-z")
    assert set(q.entity_id) == {"ORD-Z2", "ORD-Z3"}
    assert queries.queue(loaded, rid, codes=["NOT_A_CODE"]).empty  # unknown enum values never reach SQL
    for key in [("SETTLEMENT_BATCH", scenario.B5), ("ORDER", "ORD-D1"), ("BANK_TXN", "BNK-2")]:
        rid_ = int(_results(loaded, rid).loc[key, "result_id"])
        recs = queries.source_records(loaded, queries.result(loaded, rid_))
        assert any(not df.empty for df in recs.values()), key
    assert not queries.ingestion_log(loaded).empty
    queries.latest_rejects(loaded)
    queries.ground_truth_scenarios(loaded)
