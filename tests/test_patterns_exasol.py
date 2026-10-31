"""Recurring-break SQL against real Exasol (schema KANAKKU_TEST), with a planted pattern."""

import pandas as pd
import pytest

from kanakku import db, patterns

pytestmark = pytest.mark.exasol

RUN = 9000  # a results-only run, so the reconciliation tests' runs are untouched


def _planted() -> pd.DataFrame:
    rows = []

    def add(level, eid, code, method, batch, exc, status="OPEN", disc=None, expected=None, exposure=0):
        rows.append({"RESULT_ID": RUN * 10_000_000 + len(rows) + 1, "RUN_ID": RUN, "ENTITY_LEVEL": level,
                     "ENTITY_ID": eid, "EXCEPTION_KEY": f"{level}:{eid}:{code}", "ISSUE_CODE": code,
                     "IS_EXCEPTION": "TRUE" if exc else "FALSE", "PAYMENT_METHOD": method,
                     "SETTLEMENT_BATCH_ID": batch, "EXPOSURE_MINOR": exposure, "DISCREPANCY_MINOR": disc,
                     "EXPECTED_AMOUNT_MINOR": expected, "AGE_DAYS": 40,
                     "RESOLUTION_STATUS": status if exc else "CLEARED"})

    # 400 orders: 200 UPI + 200 WALLET, 100 per batch P-0..P-3
    for i in range(400):
        method = "UPI" if i < 200 else "WALLET"
        code, exc, disc, exp = "MATCHED", False, None, None
        if i < 10:  # 10 UPI amount mismatches, all over by 0.5%
            code, exc, disc, exp = "AMOUNT_MISMATCH", True, 500, 100_000
        elif 200 <= i < 203:  # 3 WALLET fee differences
            code, exc, disc, exp = "POSSIBLE_FEE_DIFFERENCE", True, -2_000, 100_000
        add("ORDER", f"PO-{i}", code, method, f"STL-P-{i % 4}", exc, disc=disc, expected=exp,
            exposure=abs(disc or 0))
    # planted: 20 WALLET duplicate captures, all in batch P-1; 5 of them already resolved
    for i in range(20):
        add("GATEWAY_TXN", f"pay_W{i}", "POSSIBLE_DUPLICATE", "WALLET", "STL-P-1", True,
            status="RESOLVED" if i < 5 else "OPEN", exposure=50_000)
    # background: 2 UPI duplicates elsewhere
    for i in range(2):
        add("GATEWAY_TXN", f"pay_U{i}", "POSSIBLE_DUPLICATE", "UPI", "STL-P-2", True, exposure=50_000)
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def planted(exa):
    exa.execute(f"DELETE FROM RECONCILIATION_RESULTS WHERE RUN_ID = {RUN}")
    df = _planted()
    exa.import_from_pandas(df, "RECONCILIATION_RESULTS", import_params={"columns": list(df.columns)})
    yield patterns.analyse(exa, RUN)
    exa.execute(f"DELETE FROM RECONCILIATION_RESULTS WHERE RUN_ID = {RUN}")


def _get(df, dimension, issue, value):
    hit = df[(df.dimension == dimension) & (df.issue_code == issue) & (df.dimension_value == value)]
    assert len(hit) == 1, (dimension, issue, value, df[["dimension", "issue_code", "dimension_value"]])
    return hit.iloc[0]


def test_planted_method_pattern_is_found(planted):
    r = _get(planted, "PAYMENT_METHOD", "POSSIBLE_DUPLICATE", "WALLET")
    assert r.n == 15  # the 5 resolved duplicates are excluded
    assert (r.group_size, r.issue_total, r.population) == (200, 17, 400)
    assert r.supported and r.hypothesis.startswith("Hypothesis (not verified):")
    assert not _get(planted, "PAYMENT_METHOD", "POSSIBLE_DUPLICATE", "UPI").supported


def test_planted_batch_hotspot_is_found(planted):
    r = _get(planted, "SETTLEMENT_BATCH", "ANY", "STL-P-1")
    # 15 open WALLET duplicates + 3 amount mismatches + 1 fee difference whose order index % 4 == 1
    assert (r.n, r.group_size, r.groups_tested) == (19, 100, 4)
    assert r.supported and "POSSIBLE_DUPLICATE" in r.context


def test_amount_shape_pattern_is_found(planted):
    r = _get(planted, "AMOUNT_PATTERN", "AMOUNT_MISMATCH", "over by under 1% of the expected amount")
    # 10 of 10 is a small sample: with 8 possible shapes compared the lower bound is ~62%, so Moderate
    assert (r.n, r.issue_total) == (10, 10) and r.verdict == "Moderate"


def test_fee_baseline_skips_methods_without_a_fee(planted):
    r = _get(planted, "PAYMENT_METHOD", "POSSIBLE_FEE_DIFFERENCE", "WALLET")
    assert r.population == 200  # UPI's 200 orders are left out: its rate-card fee is 0
    assert not (planted.dimension_value.eq("UPI") & planted.issue_code.eq("POSSIBLE_FEE_DIFFERENCE")).any()
    assert not (planted.dimension.eq("AMOUNT_PATTERN") & planted.issue_code.eq("POSSIBLE_FEE_DIFFERENCE")).any()


def test_ageing_share(planted):
    r = _get(planted, "AGEING", "POSSIBLE_DUPLICATE", "30-59 days")
    assert (r.n, r.issue_total) == (17, 17) and r.supported


def test_examples_point_at_real_results(exa, planted):
    r = _get(planted, "PAYMENT_METHOD", "POSSIBLE_DUPLICATE", "WALLET").to_dict()
    ids = patterns.example_ids(r, 5)
    found = db.query_df(exa, f"SELECT RESULT_ID, ENTITY_LEVEL, PAYMENT_METHOD, RESOLUTION_STATUS FROM RECONCILIATION_RESULTS "
                             f"WHERE RESULT_ID IN ({', '.join(map(str, ids))})")
    assert len(found) == 5 and set(found.payment_method) == {"WALLET"} and "RESOLVED" not in set(found.resolution_status)


def test_counts_match_direct_sql_on_reconciled_scenario(exa):
    """On a real reconciliation run, every method group's count equals a direct count of open exceptions."""
    import scenario
    from kanakku import ingestion, reconciliation
    from kanakku.config import ReconConfig

    for kind, df in scenario.frames().items():
        ingestion.load(exa, ingestion.validate(kind, df), f"scenario_{kind}.csv")
    run = reconciliation.run(exa, ReconConfig(as_of_date=scenario.AS_OF))["run_id"]
    got = patterns.compute(exa, run)
    direct = db.query_df(exa, f"""
        SELECT ISSUE_CODE, PAYMENT_METHOD, COUNT(*) AS N FROM RECONCILIATION_RESULTS
        WHERE RUN_ID = {run} AND IS_EXCEPTION AND ENTITY_LEVEL IN ('ORDER', 'GATEWAY_TXN')
          AND PAYMENT_METHOD IS NOT NULL AND RESOLUTION_STATUS NOT IN ('RESOLVED', 'ACCEPTED_DIFFERENCE')
        GROUP BY 1, 2""")
    m = got[got.dimension == "PAYMENT_METHOD"].set_index(["issue_code", "dimension_value"]).n.astype(int)
    d = direct.set_index(["issue_code", "payment_method"]).n.astype(int)
    d.index.names = m.index.names
    assert m.sort_index().equals(d.sort_index())
    ageing_total = got[got.dimension == "AGEING"].n.astype(int).sum()
    open_total = db.scalar(exa, f"SELECT COUNT(*) FROM RECONCILIATION_RESULTS WHERE RUN_ID = {run} AND IS_EXCEPTION "
                                "AND RESOLUTION_STATUS NOT IN ('RESOLVED', 'ACCEPTED_DIFFERENCE')")
    assert ageing_total == int(open_total)
