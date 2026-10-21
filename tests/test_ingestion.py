import pandas as pd
import pytest

from kanakku import ingestion
from kanakku.ingestion import CsvSchemaError, read_csv, validate

ORDER_HEADER = "order_id,order_ts,customer_ref,amount,currency,order_status,payment_method,channel\n"


def orders_csv(*rows: str) -> pd.DataFrame:
    return read_csv((ORDER_HEADER + "\n".join(rows) + "\n").encode())


def test_valid_order_is_typed():
    res = validate("orders", orders_csv("ORD-1,2026-09-01 10:00:00,C1,1499.50,INR,COMPLETED,UPI,WEB"))
    assert res.rows_valid == 1 and res.rows_rejected == 0
    row = res.valid.iloc[0]
    assert row.AMOUNT_MINOR == 149950 and row.ORDER_ID == "ORD-1"


@pytest.mark.parametrize("row,reason", [
    (",2026-09-01 10:00:00,C1,10.00,INR,COMPLETED,UPI,WEB", "order_id is missing"),
    ("ORD-2,2026-09-01 10:00:00,C1,abc,INR,COMPLETED,UPI,WEB", "not a decimal"),
    ("ORD-3,2026-09-01 10:00:00,C1,10.505,INR,COMPLETED,UPI,WEB", "not a decimal"),
    ("ORD-4,2026-09-01 10:00:00,C1,-5.00,INR,COMPLETED,UPI,WEB", "must be positive"),
    ("ORD-5,2026-09-01 10:00:00,C1,0.00,INR,COMPLETED,UPI,WEB", "must be positive"),
    ("ORD-6,2026-09-01 10:00:00,C1,10.00,USD,COMPLETED,UPI,WEB", "currency"),
    ("ORD-7,01/09/2026,C1,10.00,INR,COMPLETED,UPI,WEB", "timestamp"),
    ("ORD-8,2026-09-01 10:00:00,C1,10.00,INR,SHIPPED,UPI,WEB", "order_status"),
    ("ORD 9,2026-09-01 10:00:00,C1,10.00,INR,COMPLETED,UPI,WEB", "invalid characters"),
    ('ORD-10,2026-09-01 10:00:00,C1,"1,000.00",INR,COMPLETED,UPI,WEB', "not a decimal"),
])
def test_malformed_rows_are_rejected_with_reason(row, reason):
    res = validate("orders", orders_csv(row))
    assert res.rows_valid == 0 and res.rows_rejected == 1
    assert reason in res.rejects[0]["REJECT_REASON"]
    assert res.rejects[0]["LINE_NO"] == 2


def test_exact_and_conflicting_duplicates():
    good = "ORD-1,2026-09-01 10:00:00,C1,10.00,INR,COMPLETED,UPI,WEB"
    conflict = "ORD-1,2026-09-01 10:00:00,C1,99.00,INR,COMPLETED,UPI,WEB"
    res = validate("orders", orders_csv(good, good, conflict))
    assert res.rows_valid == 1 and res.exact_duplicates == 1 and res.conflicting_duplicates == 1
    assert "conflicting duplicate" in res.rejects[0]["REJECT_REASON"]
    assert res.valid.iloc[0].AMOUNT_MINOR == 1000  # first occurrence wins


def test_missing_columns_rejects_whole_file():
    with pytest.raises(CsvSchemaError, match="missing columns"):
        validate("orders", read_csv(b"order_id,amount\nORD-1,10.00\n"))


def test_unreadable_file():
    with pytest.raises(CsvSchemaError):
        read_csv(b"")


def test_header_case_and_spaces_are_tolerated():
    df = read_csv((ORDER_HEADER.upper().replace(",", " ,") + "ORD-1,2026-09-01 10:00:00,C1,10.00,INR,COMPLETED,UPI,WEB\n").encode())
    assert validate("orders", df).rows_valid == 1


GW_HEADER = ",".join(ingestion.SPECS["gateway"].columns) + "\n"


def test_gateway_net_must_equal_gross_minus_fees():
    ok = "pay_1,ORD-1,PAYMENT,CAPTURED,2026-09-01 10:00:00,100.00,1.90,0.34,97.76,INR,CARD,BATCH,STL-20260903-02,2026-09-03"
    bad = ok.replace("pay_1", "pay_2").replace("97.76", "98.00")
    res = validate("gateway", read_csv((GW_HEADER + ok + "\n" + bad + "\n").encode()))
    assert res.rows_valid == 1 and "net_amount" in res.rejects[0]["REJECT_REASON"]


def test_gateway_order_ref_whitespace_is_preserved():
    row = 'pay_1,"  ord-1 ",PAYMENT,CAPTURED,2026-09-01 10:00:00,1.00,0.00,0.00,1.00,INR,UPI,BATCH,STL-20260903-01,2026-09-03'
    res = validate("gateway", read_csv((GW_HEADER + row + "\n").encode()))
    assert res.valid.iloc[0].ORDER_REF == "  ord-1 "  # normalised later, in SQL


def test_batch_payment_needs_batch_id():
    row = "pay_1,ORD-1,PAYMENT,CAPTURED,2026-09-01 10:00:00,1.00,0.00,0.00,1.00,INR,UPI,BATCH,,2026-09-03"
    res = validate("gateway", read_csv((GW_HEADER + row + "\n").encode()))
    assert res.rows_rejected == 1 and "batch" in res.rejects[0]["REJECT_REASON"]


def test_failed_payment_needs_no_settlement():
    row = "pay_1,ORD-1,PAYMENT,FAILED,2026-09-01 10:00:00,1.00,0.00,0.00,0.00,INR,UPI,,,"
    assert validate("gateway", read_csv((GW_HEADER + row + "\n").encode())).rows_valid == 1


BANK_HEADER = ",".join(ingestion.SPECS["bank"].columns) + "\n"


@pytest.mark.parametrize("row,reason", [
    ("BNK-1,05/09/2026,,10.00,CR,INR,x,UTR1", "date"),
    ("BNK-1,2026-09-05,,1e5,CR,INR,x,UTR1", "not a decimal"),
    ("BNK-1,2026-09-05,,10.00,XX,INR,x,UTR1", "direction"),
])
def test_bank_rejects(row, reason):
    res = validate("bank", read_csv((BANK_HEADER + row + "\n").encode()))
    assert res.rows_rejected == 1 and reason in res.rejects[0]["REJECT_REASON"]
