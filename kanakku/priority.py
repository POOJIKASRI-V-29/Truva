"""Deterministic investigation-priority score (0-100). Not a fraud probability.

score = amount points (0-40) + ageing points (0-25) + severity points (0-25)
        + financial-close impact points (0-10)

The same tables drive both the Python reference implementation (`score`) and
the SQL UPDATE that scores every exception inside Exasol (`sql_update`), and an
integration test checks the two agree row for row.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from .money import format_inr

# (minimum exposure in paise, points) — first threshold the exposure reaches wins
AMOUNT_POINTS = [(5_000_000, 40), (1_000_000, 32), (200_000, 24), (50_000, 16), (10_000, 8), (1, 4)]
# (minimum age in days, points)
AGE_POINTS = [(30, 25), (14, 18), (7, 12), (3, 6), (0, 2)]
SEVERITY_POINTS = {
    "MISSING_FROM_BANK": 25,         # cash we expected never arrived
    "REVERSAL_OR_CANCELLATION": 22,  # money owed back to a customer or clawed back
    "POSSIBLE_DUPLICATE": 20,        # customer may have been charged twice
    "AMOUNT_MISMATCH": 18,
    "MISSING_FROM_GATEWAY": 15,      # order booked as revenue with no payment
    "UNRESOLVED": 15,                # cash or payment we cannot explain
    "POSSIBLE_FEE_DIFFERENCE": 8,    # likely explained by gateway pricing
    "DELAYED_SETTLEMENT": 6,         # money arrived, just late
}
CLOSE_POINTS = {"IN_CLOSE_PERIOD": 10, "PRIOR_PERIOD": 7, "AFTER_CLOSE_PERIOD": 3}
BANDS = [(70, "HIGH"), (50, "MEDIUM"), (0, "LOW")]


@dataclass(frozen=True)
class Score:
    amount: int
    age: int
    severity: int
    close: int

    @property
    def total(self) -> int:
        return self.amount + self.age + self.severity + self.close

    @property
    def band(self) -> str:
        return band_for(self.total)


def _first(table, value) -> int:
    for threshold, pts in table:
        if value >= threshold:
            return pts
    return 0


def band_for(total: int) -> str:
    return next(name for threshold, name in BANDS if total >= threshold)


def close_bucket(ref: date, close_start: date, close_end: date) -> str:
    if ref < close_start:
        return "PRIOR_PERIOD"
    if ref > close_end:
        return "AFTER_CLOSE_PERIOD"
    return "IN_CLOSE_PERIOD"


def score(issue_code: str, exposure_minor: int, age_days: int, ref: date,
          close_start: date, close_end: date) -> Score:
    return Score(
        amount=_first(AMOUNT_POINTS, abs(int(exposure_minor or 0))),
        age=_first(AGE_POINTS, max(0, int(age_days))),
        severity=SEVERITY_POINTS.get(issue_code, 15),
        close=CLOSE_POINTS[close_bucket(ref, close_start, close_end)],
    )


def reason(issue_code: str, exposure_minor: int, age_days: int, s: Score, bucket: str) -> str:
    label = {"IN_CLOSE_PERIOD": "in the period being closed", "PRIOR_PERIOD": "prior (closed) period",
             "AFTER_CLOSE_PERIOD": "after the close period"}[bucket]
    return (f"Exposure {format_inr(exposure_minor)} (+{s.amount}) · age {age_days}d (+{s.age}) · "
            f"{issue_code} (+{s.severity}) · {label} (+{s.close}) = {s.total}")


# ---------------------------------------------------------------------- SQL

def _case_threshold(expr: str, table) -> str:
    whens = " ".join(f"WHEN {expr} >= {t} THEN {p}" for t, p in table)
    return f"CASE {whens} ELSE 0 END"


def sql_update(run_id: int, close_start: date, close_end: date) -> list[str]:
    """Statements that score this run's exceptions in place, inside Exasol."""
    sev = " ".join(f"WHEN '{code}' THEN {pts}" for code, pts in SEVERITY_POINTS.items())
    cs, ce = f"DATE '{close_start.isoformat()}'", f"DATE '{close_end.isoformat()}'"
    close_case = (f"CASE WHEN REFERENCE_DATE < {cs} THEN {CLOSE_POINTS['PRIOR_PERIOD']} "
                  f"WHEN REFERENCE_DATE > {ce} THEN {CLOSE_POINTS['AFTER_CLOSE_PERIOD']} "
                  f"ELSE {CLOSE_POINTS['IN_CLOSE_PERIOD']} END")
    close_label = (f"CASE WHEN REFERENCE_DATE < {cs} THEN 'prior (closed) period' "
                   f"WHEN REFERENCE_DATE > {ce} THEN 'after the close period' "
                   f"ELSE 'in the period being closed' END")
    band = " ".join(f"WHEN PRIORITY_SCORE >= {t} THEN '{name}'" for t, name in BANDS)
    where = f"WHERE RUN_ID = {int(run_id)} AND IS_EXCEPTION"
    return [
        f"""UPDATE RECONCILIATION_RESULTS SET
              SCORE_AMOUNT = {_case_threshold('ABS(COALESCE(EXPOSURE_MINOR, 0))', AMOUNT_POINTS)},
              SCORE_AGE = {_case_threshold('GREATEST(0, AGE_DAYS)', AGE_POINTS)},
              SCORE_SEVERITY = CASE ISSUE_CODE {sev} ELSE 15 END,
              SCORE_CLOSE = {close_case}
            {where}""",
        f"""UPDATE RECONCILIATION_RESULTS SET
              PRIORITY_SCORE = SCORE_AMOUNT + SCORE_AGE + SCORE_SEVERITY + SCORE_CLOSE
            {where}""",
        f"""UPDATE RECONCILIATION_RESULTS SET
              PRIORITY_BAND = CASE {band} ELSE 'LOW' END,
              PRIORITY_REASON = 'Exposure ' || FMT_INR(EXPOSURE_MINOR) || ' (+' || SCORE_AMOUNT || ') · age '
                  || AGE_DAYS || 'd (+' || SCORE_AGE || ') · ' || ISSUE_CODE || ' (+' || SCORE_SEVERITY
                  || ') · ' || {close_label} || ' (+' || SCORE_CLOSE || ') = ' || PRIORITY_SCORE
            {where}""",
        f"""UPDATE RECONCILIATION_RESULTS SET SCORE_AMOUNT = 0, SCORE_AGE = 0, SCORE_SEVERITY = 0,
              SCORE_CLOSE = 0, PRIORITY_SCORE = 0, PRIORITY_BAND = 'NONE', PRIORITY_REASON = NULL
            WHERE RUN_ID = {int(run_id)} AND NOT IS_EXCEPTION""",
    ]
