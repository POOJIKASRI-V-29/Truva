"""Recurring-break intelligence: which unresolved exceptions cluster, and what to check next.

`sql/patterns.sql` groups a run's open exceptions inside Exasol. This module decides
which groups are real concentrations rather than noise, and words each one as a
hypothesis with the evidence behind it and the check that would confirm or refute it.

A group counts as a supported pattern only when:
  * it has at least `min_support` exceptions, and
  * RATE groups: the lower bound of the group's rate is above the rate across all groups;
    SHARE groups: the lower bound of the group's share is above one half (a clear majority).
Lower bounds are Wilson score intervals, made stricter for the number of groups compared
(Bonferroni), so scanning ~100 settlement batches does not surface chance outliers.
Nothing here proves a cause: the output is a ranked list of things worth checking.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import pandas as pd
import pyexasol

from . import db
from .config import SQL_DIR
from .money import format_inr

DIMENSION_LABELS = {
    "PAYMENT_METHOD": "Payment method",
    "SETTLEMENT_BATCH": "Settlement batch",
    "AMOUNT_PATTERN": "Amount-difference shape",
    "AGEING": "Ageing",
}
ISSUE_WORDS = {
    "MISSING_FROM_GATEWAY": "orders missing from the gateway",
    "MISSING_FROM_BANK": "settlements missing from the bank",
    "AMOUNT_MISMATCH": "amount mismatches",
    "POSSIBLE_DUPLICATE": "possible duplicates",
    "DELAYED_SETTLEMENT": "delayed settlements",
    "POSSIBLE_FEE_DIFFERENCE": "possible fee differences",
    "REVERSAL_OR_CANCELLATION": "reversals or cancellations",
    "UNRESOLVED": "unresolved records",
    "ANY": "exceptions",
}


def wilson(k: int, n: int, z: float) -> tuple[float, float]:
    """Wilson score interval for k successes out of n."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    denom = 1 + z * z / n
    return max(0.0, (centre - margin) / denom), min(1.0, (centre + margin) / denom)


def z_for(groups_tested: int, alpha: float) -> float:
    """One-sided critical value with a Bonferroni correction for the groups compared."""
    return NormalDist().inv_cdf(1 - alpha / max(1, int(groups_tested)))


def compute(conn: pyexasol.ExaConnection, run_id: int) -> pd.DataFrame:
    """Run the grouping SQL in Exasol for one run."""
    sql = "\n".join(ln for ln in (SQL_DIR / "patterns.sql").read_text().splitlines()
                    if not ln.strip().startswith("--"))
    df = db.query_df(conn, db.render(sql, {"run_id": int(run_id)}))
    # PyExasol returns DECIMAL columns as text; counts and money must be numbers here
    for col in ("n", "group_size", "issue_total", "population", "groups_tested", "exposure_minor", "max_age"):
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df["avg_age"] = pd.to_numeric(df["avg_age"], errors="coerce")
    return df


def assess(raw: pd.DataFrame, min_support: int = 3, alpha: float = 0.05) -> pd.DataFrame:
    """Add rates, intervals and a verdict to every group. Pure Python, no database."""
    rows = []
    for r in raw.to_dict("records"):
        n, size = int(r["n"]), int(r["group_size"])
        z = z_for(r["groups_tested"], alpha)
        lo, hi = wilson(n, size, z)
        rate = n / size if size else 0.0
        if r["metric"] == "RATE":
            baseline = int(r["issue_total"]) / int(r["population"]) if r["population"] else 0.0
            lift = rate / baseline if baseline else None
            clears = lo > baseline
            strong = clears and baseline > 0 and lo / baseline >= 2
        else:  # SHARE
            baseline, lift = 0.5, None
            clears = lo > 0.5
            strong = clears and lo >= 0.75
        if n < min_support:
            verdict, reason = "Not supported", f"only {n} exception(s); at least {min_support} needed"
        elif not clears:
            reason = ("not clearly above the overall rate once chance is allowed for" if r["metric"] == "RATE"
                      else "not a clear majority of this issue once chance is allowed for")
            verdict = "Not supported"
        else:
            verdict, reason = ("Strong" if strong else "Moderate"), ""
        rows.append({**r, "rate": rate, "baseline": baseline, "lift": lift, "ci_low": lo, "ci_high": hi,
                     "confidence": 1 - alpha, "verdict": verdict, "reason": reason})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["supported"] = out.verdict != "Not supported"
    out["exposure_minor"] = pd.to_numeric(out["exposure_minor"], errors="coerce").fillna(0).astype("int64")
    rank = out.verdict.map({"Strong": 0, "Moderate": 1, "Not supported": 2})
    return out.assign(_rank=rank).sort_values(["_rank", "exposure_minor"], ascending=[True, False]).drop(columns="_rank")


def example_ids(r: dict, limit: int = 5) -> list[int]:
    return [int(x) for x in str(r.get("examples") or "").split(",") if x.strip()][:limit]


def evidence(r: dict) -> str:
    """What the data shows, in numbers. No interpretation."""
    issue = ISSUE_WORDS.get(r["issue_code"], r["issue_code"])
    conf = f"{r['confidence']:.0%}"
    age = f"average age {float(r['avg_age']):.0f} days" if r.get("avg_age") is not None else ""
    money = f"exposure {format_inr(int(r['exposure_minor']))}"
    if r["metric"] == "RATE":
        unit = "orders paid by" if r["dimension"] == "PAYMENT_METHOD" else "orders settled in"
        text = (f"{int(r['n'])} unresolved {issue} among {int(r['group_size']):,} {unit} {r['dimension_value']} "
                f"({r['rate']:.2%}), against {r['baseline']:.2%} across all {int(r['population']):,} comparable orders")
        if r["lift"]:
            text += f": {r['lift']:.1f}× the overall rate"
        text += f". {conf} lower bound of the rate: {r['ci_low']:.2%}."
    else:
        level = f" at {str(r['entity_level']).lower().replace('_', ' ')} level" if r.get("entity_level") else ""
        shape = f"{r['dimension_value']} old" if r["dimension"] == "AGEING" else r["dimension_value"]
        text = (f"{int(r['n'])} of {int(r['issue_total'])} unresolved {issue}{level} ({r['rate']:.0%}) are "
                f"{shape}. {conf} lower bound of that share: {r['ci_low']:.1%}.")
    extra = f" {r['context'][:1].upper()}{r['context'][1:]}." if r.get("context") else ""
    return f"{text} {money[:1].upper()}{money[1:]}, {age}.{extra}".replace(", .", ".")


# Each entry: (hypothesis, suggested investigation, what would confirm or refute it).
# Placeholders: {v} dimension value, {issue} issue in words.
_TEMPLATES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("PAYMENT_METHOD", "POSSIBLE_DUPLICATE"): (
        "{v} payment retries may create a second capture instead of reusing the first one.",
        "Pull the gateway attempt log for the example orders and check whether both captures came from the same "
        "checkout session, and whether the {v} integration retries on timeout.",
        "Confirmed if the second capture follows a timeout or retry on the same session; refuted if the customer "
        "deliberately paid twice (e.g. two separate checkouts)."),
    ("PAYMENT_METHOD", "MISSING_FROM_GATEWAY"): (
        "{v} checkouts may be creating ledger orders before the payment is confirmed, or the gateway export may "
        "omit some {v} payments.",
        "For the example orders, search the gateway dashboard by amount and time (not only by order id), including "
        "failed and abandoned attempts.",
        "Confirmed if no attempt or only failed attempts exist; refuted if captured payments exist under another "
        "reference (then it is a reference-mapping problem)."),
    ("PAYMENT_METHOD", "AMOUNT_MISMATCH"): (
        "The amount sent to the gateway for {v} payments may differ from the ledger amount, for example a surcharge, "
        "discount or partial capture applied on one side only.",
        "Compare the checkout request amount with the ledger amount for the example orders and list any {v}-specific "
        "charges or discounts.",
        "Confirmed if a consistent adjustment explains the differences; refuted if the differences vary randomly."),
    ("PAYMENT_METHOD", "POSSIBLE_FEE_DIFFERENCE"): (
        "The gateway may net its {v} fee and GST at source for some payments without itemising it.",
        "Request the gateway's pricing and settlement configuration for {v} and compare its fee lines with the "
        "example shortfalls.",
        "Confirmed if the fee report shows the same deductions; refuted if the {v} fee schedule differs from the "
        "rate card in FEE_RATE_CARD."),
    ("PAYMENT_METHOD", "REVERSAL_OR_CANCELLATION"): (
        "Cancellations or refunds on {v} may not be reaching the gateway, or {v} may attract more chargebacks.",
        "Trace each example from the cancellation or refund request in the order system to the gateway refund API "
        "call, and list chargeback reasons where present.",
        "Confirmed if refund calls are missing or failing for {v}; refuted if the gateway shows them pending."),
    ("PAYMENT_METHOD", "UNRESOLVED"): (
        "{v} payments may reach the gateway with order references the ledger does not recognise.",
        "Compare the reference format sent by the {v} integration with the ledger's order id format.",
        "Confirmed if the references follow a different or truncated format; refuted if the orders genuinely do "
        "not exist in the ledger."),
    ("PAYMENT_METHOD", "MISSING_FROM_BANK"): (
        "Instant settlements of {v} payments may be failing or queued at the gateway's payout step.",
        "Request payout status and UTRs for the example payments from the gateway.",
        "Confirmed if payouts show failed or pending; refuted if UTRs exist and the credits sit in another account."),
    ("PAYMENT_METHOD", "DELAYED_SETTLEMENT"): (
        "Payouts for {v} may follow a slower settlement cycle than the expected date assumes.",
        "Check the gateway's settlement schedule for {v} against the expected-date rule (T+2 for batches).",
        "Confirmed if the published cycle is longer; refuted if delays are irregular."),
    ("SETTLEMENT_BATCH", "ANY"): (
        "Something specific to batch {v} (an upstream file problem, an outage window or a partial export) may have "
        "affected many of its payments at once.",
        "Compare batch {v}'s settlement report line by line with the ledger, and check whether its exceptions share "
        "a time window, a method or an issue type.",
        "Supported if its exceptions share a time window or a cause; refuted if they are a mix of unrelated issues "
        "spread across the day."),
    ("AMOUNT_PATTERN", "AMOUNT_MISMATCH"): (
        "Most amount mismatches share one shape ({v}), so a systematic rule (a surcharge, a rounding rule, a "
        "currency or tax setting) is more likely than random keying errors.",
        "Take the examples, compute each difference as a percentage of the expected amount, and look for a common "
        "rule or a common configuration change date.",
        "Confirmed if one rule reproduces most differences; refuted if each difference has a different explanation."),
    ("AGEING", "*"): (
        "{issue} may have no owner or no working-off routine, so they accumulate instead of being cleared.",
        "Assign an owner, start with the oldest examples, and check whether any were already raised with the "
        "gateway or bank.",
        "Confirmed if no one was working these items; refuted if they are waiting on a known external response."),
}
_FALLBACK = (
    "These {issue} may share a cause linked to {v}.",
    "Review the example exceptions together and look for what they have in common beyond {v}.",
    "Confirmed if one cause explains most examples; refuted if causes differ from item to item.")


def narrative(r: dict) -> dict[str, str]:
    """Hypothesis, evidence, suggested investigation, and what would confirm or refute it."""
    key = (r["dimension"], "*") if r["dimension"] == "AGEING" else (r["dimension"], r["issue_code"])
    hyp, todo, test = _TEMPLATES.get(key, _FALLBACK)
    issue = ISSUE_WORDS.get(r["issue_code"], r["issue_code"])
    fill = {"v": r["dimension_value"], "issue": issue[:1].upper() + issue[1:] if key[1] == "*" else issue}
    return {
        "hypothesis": "Hypothesis (not verified): " + hyp.format(**fill),
        "evidence": evidence(r),
        "investigate": todo.format(**fill),
        "confirm_or_refute": test.format(**fill),
    }


def analyse(conn: pyexasol.ExaConnection, run_id: int, min_support: int = 3, alpha: float = 0.05) -> pd.DataFrame:
    """compute + assess + narrative, ready for the dashboard or CLI."""
    out = assess(compute(conn, run_id), min_support=min_support, alpha=alpha)
    if out.empty:
        return out
    texts = pd.DataFrame([narrative(r) for r in out.to_dict("records")], index=out.index)
    return pd.concat([out, texts], axis=1)
