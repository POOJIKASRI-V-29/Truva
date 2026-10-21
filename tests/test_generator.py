from pathlib import Path

import pytest

from kanakku import generator, ingestion
from kanakku.config import SQL_DIR
from kanakku.money import to_minor
from kanakku.ratecard import fee_for


@pytest.fixture(scope="module")
def data():
    return generator.generate(generator.GeneratorConfig(seed=11, n_orders=2_000))


def test_reproducible(data):
    again = generator.generate(generator.GeneratorConfig(seed=11, n_orders=2_000))
    for k in ("orders", "gateway", "bank", "ground_truth"):
        assert data[k].equals(again[k])
    other = generator.generate(generator.GeneratorConfig(seed=12, n_orders=2_000))
    assert not data["orders"].equals(other["orders"])


def test_default_size_is_about_ten_thousand_orders():
    d = generator.generate()
    valid = ingestion.validate("orders", d["orders"])
    assert 10_000 <= valid.rows_valid <= 10_020


def test_every_valid_order_has_one_label(data):
    orders = ingestion.validate("orders", data["orders"]).valid
    labels = data["ground_truth"].query("entity_level == 'ORDER'")
    assert set(labels.entity_id) == set(orders.ORDER_ID)
    assert labels.entity_id.is_unique


def test_labels_are_not_in_source_files(data):
    for k in ("orders", "gateway", "bank"):
        assert not {"true_issue_code", "scenario"} & set(data[k].columns)


def test_injected_quality_problems_are_caught(data):
    dq = data["manifest"]["data_quality_injected"]
    for kind in ("orders", "gateway", "bank"):
        res = ingestion.validate(kind, data[kind])
        assert res.exact_duplicates == dq[kind]["exact_duplicates"]
        assert res.conflicting_duplicates == dq[kind]["conflicting_duplicates"]
        assert res.rows_rejected == dq[kind]["invalid_rows"] + dq[kind]["conflicting_duplicates"]


def test_gateway_is_internally_consistent(data):
    gw = ingestion.validate("gateway", data["gateway"]).valid
    cap = gw[gw.TXN_STATUS == "CAPTURED"]
    assert (cap.NET_AMOUNT_MINOR == cap.GROSS_AMOUNT_MINOR - cap.FEE_MINOR - cap.TAX_MINOR).all()


def test_seeded_scenarios_present(data):
    codes = set(data["ground_truth"].true_issue_code)
    assert {"MATCHED", "MISSING_FROM_GATEWAY", "MISSING_FROM_BANK", "AMOUNT_MISMATCH", "POSSIBLE_DUPLICATE",
            "DELAYED_SETTLEMENT", "POSSIBLE_FEE_DIFFERENCE", "REVERSAL_OR_CANCELLATION", "UNRESOLVED"} <= codes
    levels = set(data["ground_truth"].entity_level)
    assert levels == {"ORDER", "GATEWAY_TXN", "SETTLEMENT_BATCH", "BANK_TXN"}


def test_fee_rounding_is_integer_half_up():
    assert fee_for(200_000, "CARD") == (3_800, 684)
    assert fee_for(12_345, "UPI") == (0, 0)
    assert fee_for(7_895, "CARD") == (150, 27)   # 149.99..5 -> 150, 27.0 -> 27


def test_amounts_have_at_most_two_decimals(data):
    for amt in data["orders"].amount.head(500):
        to_minor(amt)


def test_reconciliation_sql_never_reads_ground_truth():
    for path in Path(SQL_DIR).glob("*.sql"):
        if path.name == "schema.sql":
            continue
        assert "GROUND_TRUTH" not in path.read_text().upper(), path.name
