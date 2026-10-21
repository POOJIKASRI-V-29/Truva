"""Reproducible synthetic data for a merchant's order ledger, payment gateway and bank.

Everything here is invented: customers are CUST-nnnnn, the gateway is "SynthPay",
the bank account is fictional. A separate ground-truth file records which issue
each entity was seeded with; the reconciliation engine never reads it.
"""

from __future__ import annotations

import json
import random
import string
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from .money import from_minor
from .ratecard import FEE_BPS, fee_for

METHODS = (["UPI"] * 50) + (["CARD"] * 30) + (["NETBANKING"] * 12) + (["WALLET"] * 8)
BATCH_GROUP = {"UPI": "01", "CARD": "02", "NETBANKING": "02", "WALLET": "02"}

ORDER_COLUMNS = ["order_id", "order_ts", "customer_ref", "amount", "currency",
                 "order_status", "payment_method", "channel"]
GATEWAY_COLUMNS = ["gateway_txn_id", "order_ref", "txn_type", "txn_status", "txn_ts",
                   "gross_amount", "fee", "tax", "net_amount", "currency", "payment_method",
                   "settlement_mode", "settlement_batch_id", "expected_settlement_date"]
BANK_COLUMNS = ["bank_txn_id", "value_date", "posted_ts", "amount", "direction", "currency",
                "narration", "bank_reference"]
TRUTH_COLUMNS = ["entity_level", "entity_id", "true_issue_code", "scenario"]

TS_FMT = "%Y-%m-%d %H:%M:%S"


@dataclass
class GeneratorConfig:
    seed: int = 42
    n_orders: int = 10_000
    start_date: date = date(2026, 8, 12)
    days: int = 52
    as_of: date = date(2026, 10, 15)
    settlement_lag_days: int = 2
    instant_share: float = 0.08
    # share of orders per seeded order-level scenario
    missing_from_gateway: float = 0.015
    amount_mismatch: float = 0.012
    fee_difference: float = 0.010
    duplicate_payment: float = 0.008
    cancelled_but_captured: float = 0.004
    chargeback: float = 0.003
    refund_not_processed: float = 0.003
    clean_refund: float = 0.020
    cancelled_no_payment: float = 0.015
    fuzzy_reference: float = 0.008
    rounding_within_tolerance: float = 0.020
    failed_then_retried: float = 0.040
    instant_missing_bank: int = 25
    instant_delayed: int = 25
    orphan_gateway_payments: int = 20
    # pairs of same-amount, same-method orders minutes apart whose payments both lack a
    # reference: genuinely ambiguous, so a conservative matcher should refuse to guess
    ambiguous_fuzzy_pairs: int = 5
    # settlement-batch scenarios (counts)
    batch_missing: int = 4
    batch_delayed: int = 6
    batch_fee_difference: int = 4
    batch_amount_mismatch: int = 3
    bank_duplicate_credit: int = 3
    batch_unreferenced: int = 8
    orphan_bank_credits: int = 10


class _Ids:
    def __init__(self, rng: random.Random):
        self.rng = rng
        self.seen: set[str] = set()

    def gateway(self) -> str:
        alphabet = string.ascii_letters + string.digits
        while True:
            gid = "pay_" + "".join(self.rng.choice(alphabet) for _ in range(14))
            if gid not in self.seen:
                self.seen.add(gid)
                return gid

    def utr(self) -> str:
        while True:
            u = "UTR" + "".join(self.rng.choice(string.digits) for _ in range(12))
            if u not in self.seen:
                self.seen.add(u)
                return u


def _ts(d: datetime) -> str:
    return d.strftime(TS_FMT)


def generate(cfg: GeneratorConfig | None = None) -> dict:
    cfg = cfg or GeneratorConfig()
    rng = random.Random(cfg.seed)
    ids = _Ids(rng)

    # ------------------------------------------------------------------ orders
    raw = []
    for _ in range(cfg.n_orders):
        day = cfg.start_date + timedelta(days=rng.randrange(cfg.days))
        secs = rng.randrange(7 * 3600, 23 * 3600 + 1800)
        ts = datetime.combine(day, time()) + timedelta(seconds=secs)
        rupees = min(150_000.0, max(49.0, rng.lognormvariate(7.0, 1.0)))
        amount = int(rupees) * 100 if rng.random() < 0.7 else int(rupees * 100)
        raw.append((ts, amount))
    raw.sort()
    orders = []
    for i, (ts, amount) in enumerate(raw, start=1):
        orders.append({
            "order_id": f"ORD-{i:07d}", "order_ts": ts, "customer_ref": f"CUST-{rng.randint(1, 3000):05d}",
            "amount": amount, "currency": "INR", "order_status": "COMPLETED",
            "payment_method": rng.choice(METHODS), "channel": rng.choice(["WEB", "APP", "APP"]),
            "scenario": "CLEAN",
        })

    # assign one scenario per order (mutually exclusive)
    pool = list(range(len(orders)))
    rng.shuffle(pool)

    def take(n: int, pred=lambda o: True) -> list[int]:
        picked = []
        for idx in list(pool):
            if len(picked) >= n:
                break
            if pred(orders[idx]):
                picked.append(idx)
                pool.remove(idx)
        return picked

    n = cfg.n_orders
    late_cutoff = cfg.as_of - timedelta(days=12)
    plan = [
        ("MISSING_FROM_GATEWAY", round(n * cfg.missing_from_gateway), lambda o: True),
        ("AMOUNT_MISMATCH", round(n * cfg.amount_mismatch), lambda o: True),
        ("FEE_DIFFERENCE", round(n * cfg.fee_difference), lambda o: FEE_BPS[o["payment_method"]] > 0 and o["amount"] >= 20_000),
        ("DUPLICATE_PAYMENT", round(n * cfg.duplicate_payment), lambda o: True),
        ("CANCELLED_BUT_CAPTURED", round(n * cfg.cancelled_but_captured), lambda o: True),
        ("CHARGEBACK", round(n * cfg.chargeback), lambda o: o["order_ts"].date() <= late_cutoff),
        ("REFUND_NOT_PROCESSED", round(n * cfg.refund_not_processed), lambda o: True),
        ("CLEAN_REFUND", round(n * cfg.clean_refund), lambda o: True),
        ("CANCELLED_NO_PAYMENT", round(n * cfg.cancelled_no_payment), lambda o: True),
        ("FUZZY_REFERENCE", round(n * cfg.fuzzy_reference), lambda o: True),
        ("ROUNDING_WITHIN_TOLERANCE", round(n * cfg.rounding_within_tolerance), lambda o: True),
        ("FAILED_THEN_RETRIED", round(n * cfg.failed_then_retried), lambda o: True),
        ("INSTANT_MISSING_BANK", cfg.instant_missing_bank, lambda o: True),
        ("INSTANT_DELAYED", cfg.instant_delayed, lambda o: o["order_ts"].date() <= late_cutoff),
        ("INSTANT_CLEAN", round(n * cfg.instant_share), lambda o: True),
    ]
    for scenario, count, pred in plan:
        for idx in take(count, pred):
            orders[idx]["scenario"] = scenario
    for idx in take(cfg.ambiguous_fuzzy_pairs):
        o = orders[idx]
        o["scenario"] = "FUZZY_AMBIGUOUS_PAIR"
        orders.append({**o, "order_id": f"ORD-{len(orders) + 1:07d}",
                       "order_ts": o["order_ts"] + timedelta(seconds=120),
                       "customer_ref": f"CUST-{rng.randint(1, 3000):05d}"})

    # ------------------------------------------------------------------ gateway
    gateway: list[dict] = []
    truth: list[dict] = []

    def settle(ts: datetime, method: str, instant: bool) -> tuple[str, str | None, date]:
        if instant:
            return "INSTANT", None, ts.date()
        exp = ts.date() + timedelta(days=cfg.settlement_lag_days)
        return "BATCH", f"STL-{exp:%Y%m%d}-{BATCH_GROUP[method]}", exp

    def add_txn(order_ref, ttype, status, ts, gross, method, instant=False,
                fee_tax=None, net=None) -> dict:
        if ttype == "PAYMENT" and status == "CAPTURED":
            fee, tax = fee_tax if fee_tax is not None else fee_for(gross, method)
            net = gross - fee - tax if net is None else net
        else:
            fee, tax = 0, 0
            net = gross if (ttype != "PAYMENT" and net is None) else (net if net is not None else 0)
        if status == "CAPTURED":
            mode, batch, exp = settle(ts, method, instant)
        else:
            mode, batch, exp = None, None, None
        row = {"gateway_txn_id": ids.gateway(), "order_ref": order_ref, "txn_type": ttype,
               "txn_status": status, "txn_ts": ts, "gross_amount": gross, "fee": fee, "tax": tax,
               "net_amount": net, "currency": "INR", "payment_method": method,
               "settlement_mode": mode, "settlement_batch_id": batch,
               "expected_settlement_date": exp}
        gateway.append(row)
        return row

    instant_payments: dict[str, str] = {}  # gateway_txn_id -> scenario

    for o in orders:
        sc, oid, amt, m = o["scenario"], o["order_id"], o["amount"], o["payment_method"]
        pay_ts = o["order_ts"] + timedelta(seconds=rng.randint(5, 180))
        code = "MATCHED"
        if sc == "MISSING_FROM_GATEWAY":
            code = "MISSING_FROM_GATEWAY"
        elif sc == "AMOUNT_MISMATCH":
            fee, tax = fee_for(amt, m)
            while True:
                delta = rng.choice([-1, 1]) * rng.randint(500, 200_000)
                if amt + delta > 0 and abs(-delta - (fee + tax)) > 100:
                    break
            add_txn(oid, "PAYMENT", "CAPTURED", pay_ts, amt + delta, m)
            code = "AMOUNT_MISMATCH"
        elif sc == "FEE_DIFFERENCE":
            # gateway netted its fee at source and reported the net figure as gross,
            # without itemising the fee
            fee, tax = fee_for(amt, m)
            reported = amt - fee - tax
            add_txn(oid, "PAYMENT", "CAPTURED", pay_ts, reported, m, fee_tax=(0, 0), net=reported)
            code = "POSSIBLE_FEE_DIFFERENCE"
        elif sc == "DUPLICATE_PAYMENT":
            add_txn(oid, "PAYMENT", "CAPTURED", pay_ts, amt, m)
            dup = add_txn(oid, "PAYMENT", "CAPTURED", pay_ts + timedelta(seconds=rng.randint(60, 1800)), amt, m)
            truth.append({"entity_level": "GATEWAY_TXN", "entity_id": dup["gateway_txn_id"],
                          "true_issue_code": "POSSIBLE_DUPLICATE", "scenario": "DUPLICATE_PAYMENT"})
        elif sc == "CANCELLED_BUT_CAPTURED":
            o["order_status"] = "CANCELLED"
            add_txn(oid, "PAYMENT", "CAPTURED", pay_ts, amt, m)
            code = "REVERSAL_OR_CANCELLATION"
        elif sc == "CHARGEBACK":
            add_txn(oid, "PAYMENT", "CAPTURED", pay_ts, amt, m)
            cb_day = min(o["order_ts"].date() + timedelta(days=rng.randint(3, 20)), cfg.as_of - timedelta(days=6))
            cb_ts = datetime.combine(cb_day, time(11)) + timedelta(seconds=rng.randrange(20_000))
            add_txn(oid, "CHARGEBACK", "CAPTURED", max(cb_ts, pay_ts + timedelta(hours=1)), amt, m)
            code = "REVERSAL_OR_CANCELLATION"
        elif sc == "REFUND_NOT_PROCESSED":
            o["order_status"] = "REFUNDED"
            add_txn(oid, "PAYMENT", "CAPTURED", pay_ts, amt, m)
            code = "REVERSAL_OR_CANCELLATION"
        elif sc == "CLEAN_REFUND":
            o["order_status"] = "REFUNDED"
            add_txn(oid, "PAYMENT", "CAPTURED", pay_ts, amt, m)
            add_txn(oid, "REFUND", "CAPTURED", pay_ts + timedelta(days=rng.randint(1, 5), seconds=rng.randrange(3600)), amt, m)
        elif sc == "CANCELLED_NO_PAYMENT":
            o["order_status"] = "CANCELLED"
            if rng.random() < 0.5:
                add_txn(oid, "PAYMENT", "FAILED", pay_ts, amt, m)
        elif sc == "FUZZY_REFERENCE":
            variant = rng.choice(["blank", "dropped_digit", "case_and_space"])
            if variant == "blank":
                ref = ""
            elif variant == "dropped_digit":
                ref = "ORD-" + oid[4:-2] + oid[-1]  # one digit missing -> not a valid ledger id
            else:
                ref = "  " + oid.lower() + " "
            add_txn(ref, "PAYMENT", "CAPTURED", pay_ts, amt, m)
            o["scenario"] = f"FUZZY_REFERENCE_{variant.upper()}"
        elif sc == "FUZZY_AMBIGUOUS_PAIR":
            add_txn(None, "PAYMENT", "CAPTURED", pay_ts, amt, m)
        elif sc == "ROUNDING_WITHIN_TOLERANCE":
            add_txn(oid, "PAYMENT", "CAPTURED", pay_ts, amt + rng.choice([-1, 1]) * rng.randint(1, 100), m)
        elif sc == "FAILED_THEN_RETRIED":
            add_txn(oid, "PAYMENT", "FAILED", pay_ts, amt, m)
            add_txn(oid, "PAYMENT", "CAPTURED", pay_ts + timedelta(seconds=rng.randint(30, 600)), amt, m)
        elif sc.startswith("INSTANT"):
            row = add_txn(oid, "PAYMENT", "CAPTURED", pay_ts, amt, m, instant=True)
            instant_payments[row["gateway_txn_id"]] = sc
            if sc == "INSTANT_MISSING_BANK":
                code = "MISSING_FROM_BANK"
            elif sc == "INSTANT_DELAYED":
                code = "DELAYED_SETTLEMENT"
        else:
            add_txn(oid, "PAYMENT", "CAPTURED", pay_ts, amt, m)
        truth.append({"entity_level": "ORDER", "entity_id": oid, "true_issue_code": code,
                      "scenario": o["scenario"]})

    # payments the gateway captured for orders the ledger has never heard of
    for _ in range(cfg.orphan_gateway_payments):
        day = cfg.start_date + timedelta(days=rng.randrange(cfg.days))
        ts = datetime.combine(day, time(9)) + timedelta(seconds=rng.randrange(50_000))
        m = rng.choice(METHODS)
        row = add_txn(f"ORD-9{rng.randint(0, 999_999):06d}", "PAYMENT", "CAPTURED", ts,
                      rng.randint(10_000, 2_500_000) + 37, m)
        truth.append({"entity_level": "GATEWAY_TXN", "entity_id": row["gateway_txn_id"],
                      "true_issue_code": "UNRESOLVED", "scenario": "ORPHAN_GATEWAY_PAYMENT"})

    # ------------------------------------------------------------------ settlement batches
    batches: dict[str, dict] = {}
    for g in gateway:
        if g["txn_status"] != "CAPTURED" or g["settlement_mode"] != "BATCH":
            continue
        b = batches.setdefault(g["settlement_batch_id"], {"expected": g["expected_settlement_date"],
                                                          "net": 0, "gross": 0})
        sign = 1 if g["txn_type"] == "PAYMENT" else -1
        b["net"] += sign * g["net_amount"]
        b["gross"] += g["gross_amount"] if sign > 0 else 0

    batch_ids = sorted(batches)
    shuffled = batch_ids[:]
    rng.shuffle(shuffled)
    latest_ok = cfg.as_of - timedelta(days=10)
    batch_plan: dict[str, str] = {}
    for scenario, count in [("BATCH_DELAYED", cfg.batch_delayed), ("BATCH_MISSING", cfg.batch_missing),
                            ("BATCH_FEE_DIFFERENCE", cfg.batch_fee_difference),
                            ("BATCH_AMOUNT_MISMATCH", cfg.batch_amount_mismatch),
                            ("BANK_DUPLICATE_CREDIT", cfg.bank_duplicate_credit),
                            ("BATCH_UNREFERENCED", cfg.batch_unreferenced)]:
        eligible = [b for b in shuffled if b not in batch_plan and batches[b]["expected"] <= latest_ok
                    and batches[b]["net"] > 100_000]
        for bid in eligible[:count]:
            batch_plan[bid] = scenario

    bank: list[dict] = []

    def credit(value_date: date, amount: int, narration: str) -> dict | None:
        """Bank line for a settlement; a refund-heavy batch nets out as a debit."""
        if amount == 0:
            return None
        row = {"value_date": value_date, "posted_ts": datetime.combine(value_date, time(10)) + timedelta(seconds=rng.randrange(25_000)),
               "amount": abs(amount), "direction": "CR" if amount > 0 else "DR", "currency": "INR",
               "narration": narration if amount > 0 else narration.replace(" CR-", " DR-"),
               "bank_reference": ids.utr(), "_truth": None}
        bank.append(row)
        return row

    batch_code = {"BATCH_DELAYED": "DELAYED_SETTLEMENT", "BATCH_MISSING": "MISSING_FROM_BANK",
                  "BATCH_FEE_DIFFERENCE": "POSSIBLE_FEE_DIFFERENCE",
                  "BATCH_AMOUNT_MISMATCH": "AMOUNT_MISMATCH"}
    for bid in batch_ids:
        b = batches[bid]
        sc = batch_plan.get(bid, "CLEAN")
        narration = f"NEFT CR-SYNTHPAY SETTLEMENT/{bid}/MERCHANT DEMO"
        vd = b["expected"] + timedelta(days=rng.choice([0, 0, 0, 1]))
        if sc == "BATCH_MISSING":
            pass
        elif sc == "BATCH_DELAYED":
            credit(min(b["expected"] + timedelta(days=rng.randint(3, 9)), cfg.as_of), b["net"], narration)
        elif sc == "BATCH_FEE_DIFFERENCE":
            extra = max(100, round(b["gross"] * rng.uniform(0.002, 0.012)))
            credit(vd, b["net"] - extra, narration)
        elif sc == "BATCH_AMOUNT_MISMATCH":
            delta = round(b["net"] * rng.uniform(0.05, 0.25)) * rng.choice([-1, 1])
            credit(vd, b["net"] + delta, narration)
        elif sc == "BANK_DUPLICATE_CREDIT":
            # whichever of the two identical lines is posted later is the duplicate
            first = credit(vd, b["net"], narration)
            dup = credit(vd + timedelta(days=rng.choice([0, 1])), b["net"], narration)
            first["_pair"] = dup["_pair"] = bid
        elif sc == "BATCH_UNREFERENCED":
            credit(vd, b["net"], "NEFT CR-SYNTHPAY SETTLEMENT/MERCHANT DEMO")
        else:
            credit(vd, b["net"], narration)
        truth.append({"entity_level": "SETTLEMENT_BATCH", "entity_id": bid,
                      "true_issue_code": batch_code.get(sc, "MATCHED"), "scenario": sc})

    for g in gateway:
        sc = instant_payments.get(g["gateway_txn_id"])
        if sc is None:
            continue
        narration = f"IMPS CR-SYNTHPAY INSTANT/{g['gateway_txn_id']}"
        if sc == "INSTANT_MISSING_BANK":
            continue
        if sc == "INSTANT_DELAYED":
            vd = min(g["expected_settlement_date"] + timedelta(days=rng.randint(4, 9)), cfg.as_of)
        else:
            vd = g["expected_settlement_date"] + timedelta(days=rng.choice([0, 0, 1]))
        credit(vd, g["net_amount"], narration)

    for _ in range(cfg.orphan_bank_credits):
        vd = cfg.start_date + timedelta(days=rng.randrange(cfg.days + 5))
        row = credit(vd, rng.randint(50_000, 5_000_000) + 11,
                     rng.choice(["NEFT CR-UNKNOWN REMITTER", "UPI CR CUSTOMER DIRECT TRANSFER",
                                 "RTGS CR-REF MISSING"]))
        row["_truth"] = ("UNRESOLVED", "ORPHAN_BANK_CREDIT")

    bank.sort(key=lambda r: (r["value_date"], r["posted_ts"]))
    later_of_pair = {r["_pair"]: r for r in bank if r.get("_pair")}
    for r in later_of_pair.values():
        r["_truth"] = ("POSSIBLE_DUPLICATE", "BANK_DUPLICATE_CREDIT")
    for i, r in enumerate(bank, start=1):
        r["bank_txn_id"] = f"BNK-{i:08d}"
        if r["_truth"]:
            truth.append({"entity_level": "BANK_TXN", "entity_id": r["bank_txn_id"],
                          "true_issue_code": r["_truth"][0], "scenario": r["_truth"][1]})

    # ------------------------------------------------------------------ to CSV-shaped frames
    gateway.sort(key=lambda r: r["txn_ts"])
    orders_df = pd.DataFrame([{
        "order_id": o["order_id"], "order_ts": _ts(o["order_ts"]), "customer_ref": o["customer_ref"],
        "amount": from_minor(o["amount"]), "currency": o["currency"], "order_status": o["order_status"],
        "payment_method": o["payment_method"], "channel": o["channel"]} for o in orders], columns=ORDER_COLUMNS)
    gateway_df = pd.DataFrame([{
        **{k: g[k] for k in ("gateway_txn_id", "order_ref", "txn_type", "txn_status", "currency",
                             "payment_method", "settlement_mode", "settlement_batch_id")},
        "txn_ts": _ts(g["txn_ts"]), "gross_amount": from_minor(g["gross_amount"]),
        "fee": from_minor(g["fee"]), "tax": from_minor(g["tax"]), "net_amount": from_minor(g["net_amount"]),
        "expected_settlement_date": g["expected_settlement_date"].isoformat() if g["expected_settlement_date"] else "",
    } for g in gateway], columns=GATEWAY_COLUMNS)
    bank_df = pd.DataFrame([{
        "bank_txn_id": r["bank_txn_id"], "value_date": r["value_date"].isoformat(), "posted_ts": _ts(r["posted_ts"]),
        "amount": from_minor(r["amount"]), "direction": r["direction"], "currency": r["currency"],
        "narration": r["narration"], "bank_reference": r["bank_reference"]} for r in bank], columns=BANK_COLUMNS)
    truth_df = pd.DataFrame(truth, columns=TRUTH_COLUMNS)

    orders_df, gateway_df, bank_df, dq = _inject_data_quality_issues(rng, orders_df, gateway_df, bank_df)
    manifest = {
        "generator_config": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(cfg).items()},
        "rows": {"orders": len(orders_df), "gateway": len(gateway_df), "bank": len(bank_df), "ground_truth": len(truth_df)},
        "settlement_batches": len(batch_ids),
        "data_quality_injected": dq,
        "seeded_issue_counts": truth_df[truth_df.true_issue_code != "MATCHED"]
            .groupby(["entity_level", "true_issue_code"]).size().rename("n").reset_index().to_dict("records"),
    }
    return {"orders": orders_df, "gateway": gateway_df, "bank": bank_df, "ground_truth": truth_df, "manifest": manifest}


def _inject_data_quality_issues(rng, orders_df, gateway_df, bank_df):
    """Append malformed rows, exact duplicates and conflicting duplicates for ingestion to catch."""
    def junk_order(i, **kw):
        base = {"order_id": f"ORD-X{i:06d}", "order_ts": "2026-09-01 10:00:00", "customer_ref": "CUST-00001",
                "amount": "100.00", "currency": "INR", "order_status": "COMPLETED", "payment_method": "UPI",
                "channel": "WEB"}
        base.update(kw)
        return base

    bad_orders = [junk_order(1, order_id=""), junk_order(2, amount="12,499.00"), junk_order(3, amount="abc"),
                  junk_order(4, amount="-50.00"), junk_order(5, currency="USD"),
                  junk_order(6, order_ts="2026-13-45 10:00:00"), junk_order(7, order_status="SHIPPED"),
                  junk_order(8, amount="10.505")]

    def junk_gw(i, **kw):
        base = {"gateway_txn_id": f"pay_BAD{i:011d}", "order_ref": "ORD-X000099", "txn_type": "PAYMENT",
                "txn_status": "CAPTURED", "txn_ts": "2026-09-01 10:00:00", "gross_amount": "100.00", "fee": "1.90",
                "tax": "0.34", "net_amount": "97.76", "currency": "INR", "payment_method": "CARD",
                "settlement_mode": "BATCH", "settlement_batch_id": "STL-20260903-02",
                "expected_settlement_date": "2026-09-03"}
        base.update(kw)
        return base

    bad_gw = [junk_gw(1, net_amount="99.00"), junk_gw(2, gateway_txn_id=""), junk_gw(3, txn_type="PAYOUT"),
              junk_gw(4, txn_ts="yesterday"), junk_gw(5, gross_amount=""), junk_gw(6, currency="XXX")]

    def junk_bank(i, **kw):
        base = {"bank_txn_id": f"BNK-X{i:07d}", "value_date": "2026-09-05", "posted_ts": "2026-09-05 11:00:00",
                "amount": "500.00", "direction": "CR", "currency": "INR", "narration": "TEST", "bank_reference": "UTR0"}
        base.update(kw)
        return base

    bad_bank = [junk_bank(1, value_date="05/09/2026"), junk_bank(2, amount="1e5"), junk_bank(3, direction="XX"),
                junk_bank(4, bank_txn_id="")]

    exact_o = orders_df.sample(6, random_state=rng.randint(0, 10**6))
    exact_g = gateway_df.sample(6, random_state=rng.randint(0, 10**6))
    exact_b = bank_df.sample(3, random_state=rng.randint(0, 10**6))
    conflict_o = orders_df.sample(2, random_state=rng.randint(0, 10**6)).copy()
    conflict_o["amount"] = "1.00"

    orders_out = pd.concat([orders_df, pd.DataFrame(bad_orders), exact_o, conflict_o], ignore_index=True)
    gateway_out = pd.concat([gateway_df, pd.DataFrame(bad_gw), exact_g], ignore_index=True)
    bank_out = pd.concat([bank_df, pd.DataFrame(bad_bank), exact_b], ignore_index=True)
    dq = {
        "orders": {"invalid_rows": len(bad_orders), "exact_duplicates": len(exact_o), "conflicting_duplicates": len(conflict_o)},
        "gateway": {"invalid_rows": len(bad_gw), "exact_duplicates": len(exact_g), "conflicting_duplicates": 0},
        "bank": {"invalid_rows": len(bad_bank), "exact_duplicates": len(exact_b), "conflicting_duplicates": 0},
    }
    return orders_out, gateway_out, bank_out, dq


FILE_NAMES = {"orders": "orders.csv", "gateway": "gateway_transactions.csv",
              "bank": "bank_statement.csv", "ground_truth": "ground_truth.csv"}


def write(dataset: dict, out_dir: Path | str) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for key, fname in FILE_NAMES.items():
        p = out / fname
        dataset[key].to_csv(p, index=False)
        paths[key] = p
    (out / "manifest.json").write_text(json.dumps(dataset["manifest"], indent=2, default=str))
    paths["manifest"] = out / "manifest.json"
    return paths
