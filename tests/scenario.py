"""A small hand-built dataset where every reconciliation rule fires exactly where we expect.

Used by the Exasol integration tests. Rows are CSV-shaped (all text) so they go
through the real validation and loading path.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from kanakku.ingestion import SPECS
from kanakku.money import from_minor

AS_OF = date(2026, 9, 30)


def gid(s: str) -> str:
    """Gateway ids look like pay_ + 14 alphanumerics, as the bank narration regex expects."""
    return "pay_" + s.ljust(14, "0")


ORDERS = [
    # id,      ts,                     amount, status,      method
    ("ORD-A1", "2026-09-01 10:00:00", 100000, "COMPLETED", "UPI"),
    ("ORD-T1", "2026-09-01 10:05:00", 100000, "COMPLETED", "UPI"),
    ("ORD-T2", "2026-09-01 10:10:00", 100000, "COMPLETED", "UPI"),
    ("ORD-F1", "2026-09-01 10:15:00", 200000, "COMPLETED", "CARD"),
    ("ORD-D1", "2026-09-01 10:20:00", 50000, "COMPLETED", "UPI"),
    ("ORD-C1", "2026-09-01 10:25:00", 30000, "CANCELLED", "UPI"),
    ("ORD-CB", "2026-09-01 10:30:00", 40000, "COMPLETED", "UPI"),
    ("ORD-R1", "2026-09-01 10:35:00", 25000, "REFUNDED", "UPI"),
    ("ORD-M1", "2026-09-01 10:40:00", 70000, "COMPLETED", "UPI"),
    ("ORD-Z1", "2026-09-01 11:00:00", 12345, "COMPLETED", "UPI"),
    ("ORD-Z2", "2026-09-01 12:00:00", 55500, "COMPLETED", "UPI"),
    ("ORD-Z3", "2026-09-01 12:02:00", 55500, "COMPLETED", "UPI"),
    ("ORD-I1", "2026-09-01 13:00:00", 80000, "COMPLETED", "CARD"),
    ("ORD-I2", "2026-09-01 13:10:00", 60000, "COMPLETED", "UPI"),
    ("ORD-U1", "2026-09-02 09:00:00", 90000, "COMPLETED", "UPI"),
    ("ORD-X1", "2026-09-08 09:00:00", 33300, "COMPLETED", "UPI"),
    ("ORD-Y1", "2026-09-09 09:00:00", 44400, "COMPLETED", "UPI"),
    ("ORD-W1", "2026-09-10 09:00:00", 500000, "COMPLETED", "UPI"),
    ("ORD-V1", "2026-09-11 09:00:00", 300000, "COMPLETED", "UPI"),
]

B1, BF, BR, BCB = "STL-20260903-01", "STL-20260903-02", "STL-20260904-01", "STL-20260907-01"
B3, B4, B5, B6 = "STL-20260910-01", "STL-20260911-01", "STL-20260912-01", "STL-20260913-01"
BATCH_DATES = {B1: "2026-09-03", BF: "2026-09-03", BR: "2026-09-04", BCB: "2026-09-07",
               B3: "2026-09-10", B4: "2026-09-11", B5: "2026-09-12", B6: "2026-09-13"}

# id, order_ref, type, ts, gross, fee, tax, method, mode, batch
GATEWAY = [
    (gid("A1"), "ORD-A1", "PAYMENT", "2026-09-01 10:00:30", 100000, 0, 0, "UPI", "BATCH", B1),
    (gid("T1"), "ORD-T1", "PAYMENT", "2026-09-01 10:05:30", 100100, 0, 0, "UPI", "BATCH", B1),   # +100 paise: inside tolerance
    (gid("T2"), "ORD-T2", "PAYMENT", "2026-09-01 10:10:30", 100101, 0, 0, "UPI", "BATCH", B1),   # +101 paise: outside
    (gid("F1"), "ORD-F1", "PAYMENT", "2026-09-01 10:15:30", 195516, 0, 0, "CARD", "BATCH", BF),  # 200000 - 1.9% fee - 18% GST
    (gid("D1a"), "ORD-D1", "PAYMENT", "2026-09-01 10:20:30", 50000, 0, 0, "UPI", "BATCH", B1),
    (gid("D1b"), "ORD-D1", "PAYMENT", "2026-09-01 10:25:30", 50000, 0, 0, "UPI", "BATCH", B1),   # charged twice
    (gid("C1"), "ORD-C1", "PAYMENT", "2026-09-01 10:25:30", 30000, 0, 0, "UPI", "BATCH", B1),    # cancelled yet captured
    (gid("CB"), "ORD-CB", "PAYMENT", "2026-09-01 10:30:30", 40000, 0, 0, "UPI", "BATCH", B1),
    (gid("CBx"), "ORD-CB", "CHARGEBACK", "2026-09-05 12:00:00", 40000, 0, 0, "UPI", "BATCH", BCB),
    (gid("R1"), "ORD-R1", "PAYMENT", "2026-09-01 10:35:30", 25000, 0, 0, "UPI", "BATCH", B1),
    (gid("R1x"), "ORD-R1", "REFUND", "2026-09-02 12:00:00", 25000, 0, 0, "UPI", "BATCH", BR),
    (gid("Z1"), "", "PAYMENT", "2026-09-01 11:00:30", 12345, 0, 0, "UPI", "BATCH", B1),           # no reference, unique
    (gid("Z2"), "", "PAYMENT", "2026-09-01 12:00:20", 55500, 0, 0, "UPI", "BATCH", B1),           # ambiguous pair
    (gid("Z3"), "", "PAYMENT", "2026-09-01 12:02:20", 55500, 0, 0, "UPI", "BATCH", B1),
    (gid("O1"), "ORD-999", "PAYMENT", "2026-09-01 14:00:00", 99999, 0, 0, "UPI", "BATCH", B1),    # unknown order
    (gid("I1"), "ORD-I1", "PAYMENT", "2026-09-01 13:00:30", 80000, 1520, 274, "CARD", "INSTANT", None),
    (gid("I2"), "ORD-I2", "PAYMENT", "2026-09-01 13:10:30", 60000, 0, 0, "UPI", "INSTANT", None),
    (gid("U1"), "ORD-U1", "PAYMENT", "2026-09-02 09:00:30", 90000, 0, 0, "UPI", "BATCH", BR),
    (gid("X1"), "ORD-X1", "PAYMENT", "2026-09-08 09:00:30", 33300, 0, 0, "UPI", "BATCH", B3),
    (gid("Y1"), "ORD-Y1", "PAYMENT", "2026-09-09 09:00:30", 44400, 0, 0, "UPI", "BATCH", B4),
    (gid("W1"), "ORD-W1", "PAYMENT", "2026-09-10 09:00:30", 500000, 0, 0, "UPI", "BATCH", B5),
    (gid("V1"), "ORD-V1", "PAYMENT", "2026-09-11 09:00:30", 300000, 0, 0, "UPI", "BATCH", B6),
    (gid("FAIL"), "ORD-A1", "PAYMENT", "2026-09-01 09:59:00", 100000, 0, 0, "UPI", None, None),   # failed attempt
]


def batch_net(batch: str) -> int:
    total = 0
    for g in GATEWAY:
        if g[9] == batch:
            net = g[4] - g[5] - g[6]
            total += net if g[2] == "PAYMENT" else -net
    return total


B1_NET = batch_net(B1)

# id, value_date, amount, direction, narration
BANK = [
    ("BNK-1", "2026-09-03", B1_NET, "CR", f"NEFT CR-SYNTHPAY SETTLEMENT/{B1}/MERCHANT"),
    ("BNK-2", "2026-09-04", B1_NET, "CR", f"NEFT CR-SYNTHPAY SETTLEMENT/{B1}/MERCHANT"),          # duplicate credit
    ("BNK-3", "2026-09-03", batch_net(BF), "CR", f"NEFT CR-SYNTHPAY SETTLEMENT/{BF}/MERCHANT"),
    ("BNK-4", "2026-09-04", batch_net(BR), "CR", "NEFT CR-SYNTHPAY SETTLEMENT/MERCHANT"),         # no reference
    ("BNK-5", "2026-09-07", 40000, "DR", f"NEFT DR-SYNTHPAY SETTLEMENT/{BCB}/MERCHANT"),          # chargeback batch
    ("BNK-6", "2026-09-16", batch_net(B4), "CR", f"NEFT CR-SYNTHPAY SETTLEMENT/{B4}/MERCHANT"),   # 5 days late
    ("BNK-7", "2026-09-12", batch_net(B5) - 2000, "CR", f"NEFT CR-SYNTHPAY SETTLEMENT/{B5}/MERCHANT"),  # small shortfall
    ("BNK-8", "2026-09-13", 250000, "CR", f"NEFT CR-SYNTHPAY SETTLEMENT/{B6}/MERCHANT"),          # large shortfall
    ("BNK-9", "2026-09-08", 60000, "CR", f"IMPS CR-SYNTHPAY INSTANT/{gid('I2')}"),                 # instant, 7 days late
    ("BNK-10", "2026-09-20", 777700, "CR", "NEFT CR-UNKNOWN REMITTER"),                          # orphan
]

EXPECTED = {
    ("ORDER", "ORD-A1"): ("MATCHED", "EXACT"),
    ("ORDER", "ORD-T1"): ("MATCHED", "TOLERANCE"),
    ("ORDER", "ORD-T2"): ("AMOUNT_MISMATCH", "KEY_ONLY"),
    ("ORDER", "ORD-F1"): ("POSSIBLE_FEE_DIFFERENCE", "KEY_ONLY"),
    ("ORDER", "ORD-D1"): ("MATCHED", "EXACT"),
    ("ORDER", "ORD-C1"): ("REVERSAL_OR_CANCELLATION", "EXACT"),
    ("ORDER", "ORD-CB"): ("REVERSAL_OR_CANCELLATION", "EXACT"),
    ("ORDER", "ORD-R1"): ("MATCHED", "EXACT"),
    ("ORDER", "ORD-M1"): ("MISSING_FROM_GATEWAY", "NONE"),
    ("ORDER", "ORD-Z1"): ("MATCHED", "FUZZY"),
    ("ORDER", "ORD-Z2"): ("MISSING_FROM_GATEWAY", "NONE"),
    ("ORDER", "ORD-Z3"): ("MISSING_FROM_GATEWAY", "NONE"),
    ("ORDER", "ORD-I1"): ("MISSING_FROM_BANK", "EXACT"),
    ("ORDER", "ORD-I2"): ("DELAYED_SETTLEMENT", "EXACT"),
    ("ORDER", "ORD-U1"): ("MATCHED", "EXACT"),
    ("ORDER", "ORD-X1"): ("MATCHED", "EXACT"),
    ("ORDER", "ORD-Y1"): ("MATCHED", "EXACT"),
    ("ORDER", "ORD-W1"): ("MATCHED", "EXACT"),
    ("ORDER", "ORD-V1"): ("MATCHED", "EXACT"),
    ("GATEWAY_TXN", gid("D1b")): ("POSSIBLE_DUPLICATE", "KEY"),
    ("GATEWAY_TXN", gid("Z2")): ("UNRESOLVED", "NONE"),
    ("GATEWAY_TXN", gid("Z3")): ("UNRESOLVED", "NONE"),
    ("GATEWAY_TXN", gid("O1")): ("UNRESOLVED", "NONE"),
    ("SETTLEMENT_BATCH", B1): ("MATCHED", "BATCH_REF"),
    ("SETTLEMENT_BATCH", BF): ("MATCHED", "BATCH_REF"),
    ("SETTLEMENT_BATCH", BR): ("MATCHED", "BATCH_CONSTRAINED"),
    ("SETTLEMENT_BATCH", BCB): ("MATCHED", "BATCH_REF"),
    ("SETTLEMENT_BATCH", B3): ("MISSING_FROM_BANK", "NONE"),
    ("SETTLEMENT_BATCH", B4): ("DELAYED_SETTLEMENT", "BATCH_REF"),
    ("SETTLEMENT_BATCH", B5): ("POSSIBLE_FEE_DIFFERENCE", "BATCH_REF"),
    ("SETTLEMENT_BATCH", B6): ("AMOUNT_MISMATCH", "BATCH_REF"),
    ("BANK_TXN", "BNK-2"): ("POSSIBLE_DUPLICATE", "NONE"),
    ("BANK_TXN", "BNK-10"): ("UNRESOLVED", "NONE"),
}


def frames() -> dict[str, pd.DataFrame]:
    orders = pd.DataFrame([{
        "order_id": o[0], "order_ts": o[1], "customer_ref": "CUST-00001", "amount": from_minor(o[2]),
        "currency": "INR", "order_status": o[3], "payment_method": o[4], "channel": "WEB"} for o in ORDERS])
    gw = []
    for g in GATEWAY:
        captured = g[8] is not None
        exp = BATCH_DATES.get(g[9], g[3][:10]) if captured else ""
        net = g[4] - g[5] - g[6] if captured else 0
        gw.append({"gateway_txn_id": g[0], "order_ref": g[1], "txn_type": g[2],
                   "txn_status": "CAPTURED" if captured else "FAILED", "txn_ts": g[3],
                   "gross_amount": from_minor(g[4]), "fee": from_minor(g[5]), "tax": from_minor(g[6]),
                   "net_amount": from_minor(net), "currency": "INR", "payment_method": g[7],
                   "settlement_mode": g[8] or "", "settlement_batch_id": g[9] or "", "expected_settlement_date": exp})
    bank = pd.DataFrame([{
        "bank_txn_id": b[0], "value_date": b[1], "posted_ts": b[1] + " 11:00:00", "amount": from_minor(b[2]),
        "direction": b[3], "currency": "INR", "narration": b[4], "bank_reference": f"UTR{i:012d}"}
        for i, b in enumerate(BANK)])
    out = {"orders": orders, "gateway": pd.DataFrame(gw), "bank": bank}
    for k, df in out.items():
        assert list(df.columns) == SPECS[k].columns
    return out
