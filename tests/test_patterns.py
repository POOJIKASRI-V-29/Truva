import re

import pandas as pd
import pytest

from kanakku import patterns


def row(**kw):
    base = {"dimension": "PAYMENT_METHOD", "metric": "RATE", "issue_code": "POSSIBLE_DUPLICATE", "entity_level": None,
            "dimension_value": "WALLET", "n": 30, "group_size": 1000, "issue_total": 40, "population": 10000,
            "groups_tested": 4, "exposure_minor": 123456, "avg_age": 12.0, "max_age": 30, "examples": "11,12,13",
            "context": None}
    base.update(kw)
    return base


def test_wilson_interval():
    lo, hi = patterns.wilson(5, 10, 1.96)
    assert lo == pytest.approx(0.2366, abs=1e-3) and hi == pytest.approx(0.7634, abs=1e-3)
    assert patterns.wilson(0, 10, 1.96)[0] == 0.0
    assert patterns.wilson(0, 0, 1.96) == (0.0, 1.0)


def test_more_groups_means_stricter_bound():
    assert patterns.z_for(100, 0.05) > patterns.z_for(4, 0.05) > patterns.z_for(1, 0.05)


def test_clear_concentration_is_strong():
    out = patterns.assess(pd.DataFrame([row()]))
    r = out.iloc[0]
    assert r.verdict == "Strong" and r.supported
    assert r.baseline == pytest.approx(0.004) and r.lift == pytest.approx(7.5)


def test_noise_is_not_supported():
    out = patterns.assess(pd.DataFrame([row(n=6, group_size=1000, issue_total=20, population=4000)]))
    assert out.iloc[0].verdict == "Not supported"
    assert "chance" in out.iloc[0].reason


def test_too_few_exceptions_never_supported():
    out = patterns.assess(pd.DataFrame([row(n=2, group_size=10, issue_total=2, population=10000)]), min_support=3)
    assert out.iloc[0].verdict == "Not supported" and "only 2" in out.iloc[0].reason


def test_bonferroni_filters_one_of_many():
    # 8 of 100 against a 4% baseline: clears the bar alone, not when it is one of 200 batches compared
    alone = patterns.assess(pd.DataFrame([row(n=8, group_size=100, issue_total=400, population=10000, groups_tested=1)]),
                            alpha=0.2)
    many = patterns.assess(pd.DataFrame([row(n=8, group_size=100, issue_total=400, population=10000, groups_tested=200)]),
                           alpha=0.2)
    assert alone.iloc[0].supported and not many.iloc[0].supported


@pytest.mark.parametrize("n,total,verdict", [(90, 100, "Strong"), (70, 100, "Moderate"), (55, 100, "Not supported")])
def test_share_needs_a_clear_majority(n, total, verdict):
    r = row(dimension="AGEING", metric="SHARE", issue_code="UNRESOLVED", dimension_value="30-59 days",
            n=n, group_size=total, issue_total=total, population=None, groups_tested=5)
    assert patterns.assess(pd.DataFrame([r])).iloc[0].verdict == verdict


def test_supported_rows_rank_first():
    out = patterns.assess(pd.DataFrame([row(n=2, exposure_minor=10**9), row(n=30, exposure_minor=1)]))
    assert list(out.verdict) == ["Strong", "Not supported"]


FORBIDDEN = re.compile(r"root cause|proven|proves|definitely|certainly|fraud", re.I)
DIMENSIONS = [("PAYMENT_METHOD", "RATE", c) for c in patterns.ISSUE_WORDS if c != "ANY"] + [
    ("SETTLEMENT_BATCH", "RATE", "ANY"), ("AMOUNT_PATTERN", "SHARE", "AMOUNT_MISMATCH"),
    ("AGEING", "SHARE", "MISSING_FROM_GATEWAY"), ("AGEING", "SHARE", "UNRESOLVED")]


@pytest.mark.parametrize("dimension,metric,issue", DIMENSIONS)
def test_every_narrative_is_a_labelled_hypothesis(dimension, metric, issue):
    r = row(dimension=dimension, metric=metric, issue_code=issue,
            entity_level="ORDER" if dimension == "AMOUNT_PATTERN" else None,
            dimension_value="over by 1-5% of the expected amount" if dimension == "AMOUNT_PATTERN" else "X-1",
            population=None if metric == "SHARE" else 10000)
    a = patterns.assess(pd.DataFrame([r])).iloc[0].to_dict()
    text = patterns.narrative(a)
    assert text["hypothesis"].startswith("Hypothesis (not verified): ")
    assert set(text) == {"hypothesis", "evidence", "investigate", "confirm_or_refute"}
    for part in text.values():
        assert part and not FORBIDDEN.search(part), part
        assert "{" not in part  # every placeholder filled
    assert str(int(a["n"])) in text["evidence"]


def test_evidence_states_numbers_not_causes():
    a = patterns.assess(pd.DataFrame([row()])).iloc[0].to_dict()
    ev = patterns.evidence(a)
    assert "30 unresolved possible duplicates among 1,000 orders paid by WALLET" in ev
    assert "7.5× the overall rate" in ev and "₹1,234.56" in ev
    assert "may" not in ev  # interpretation lives in the hypothesis, not the evidence


def test_example_ids():
    assert patterns.example_ids({"examples": "5,4,3,2,1,0"}, 3) == [5, 4, 3]
    assert patterns.example_ids({"examples": None}) == []
