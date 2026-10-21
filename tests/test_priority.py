from datetime import date

import pytest

from kanakku import priority

CS, CE = date(2026, 9, 1), date(2026, 9, 30)


@pytest.mark.parametrize("exposure,points", [
    (0, 0), (1, 4), (9_999, 4), (10_000, 8), (49_999, 8), (50_000, 16), (199_999, 16),
    (200_000, 24), (999_999, 24), (1_000_000, 32), (4_999_999, 32), (5_000_000, 40), (10**12, 40), (-5_000_000, 40),
])
def test_amount_thresholds(exposure, points):
    assert priority.score("AMOUNT_MISMATCH", exposure, 0, CE, CS, CE).amount == points


@pytest.mark.parametrize("age,points", [(0, 2), (2, 2), (3, 6), (6, 6), (7, 12), (13, 12), (14, 18), (29, 18), (30, 25), (365, 25), (-4, 2)])
def test_age_thresholds(age, points):
    assert priority.score("AMOUNT_MISMATCH", 0, age, CE, CS, CE).age == points


def test_close_period_buckets():
    assert priority.close_bucket(date(2026, 8, 31), CS, CE) == "PRIOR_PERIOD"
    assert priority.close_bucket(CS, CS, CE) == "IN_CLOSE_PERIOD"
    assert priority.close_bucket(CE, CS, CE) == "IN_CLOSE_PERIOD"
    assert priority.close_bucket(date(2026, 10, 1), CS, CE) == "AFTER_CLOSE_PERIOD"


def test_bands_and_maximum():
    worst = priority.score("MISSING_FROM_BANK", 10**9, 400, date(2026, 9, 15), CS, CE)
    assert worst.total == 100 and worst.band == "HIGH"
    assert priority.band_for(70) == "HIGH" and priority.band_for(69) == "MEDIUM"
    assert priority.band_for(50) == "MEDIUM" and priority.band_for(49) == "LOW"


def test_severity_orders_issue_types():
    sev = priority.SEVERITY_POINTS
    assert sev["MISSING_FROM_BANK"] > sev["AMOUNT_MISMATCH"] > sev["POSSIBLE_FEE_DIFFERENCE"] > sev["DELAYED_SETTLEMENT"]
    assert max(sev.values()) <= 25 and max(p for _, p in priority.AMOUNT_POINTS) <= 40


def test_score_is_deterministic():
    a = priority.score("POSSIBLE_DUPLICATE", 123_456, 12, date(2026, 9, 10), CS, CE)
    b = priority.score("POSSIBLE_DUPLICATE", 123_456, 12, date(2026, 9, 10), CS, CE)
    assert a == b and a.total == 16 + 12 + 20 + 10


def test_sql_mentions_every_weight():
    sql = "\n".join(priority.sql_update(7, CS, CE))
    for threshold, pts in priority.AMOUNT_POINTS + priority.AGE_POINTS:
        assert f">= {threshold} THEN {pts}" in sql
    for code, pts in priority.SEVERITY_POINTS.items():
        assert f"WHEN '{code}' THEN {pts}" in sql
    assert "RUN_ID = 7" in sql
