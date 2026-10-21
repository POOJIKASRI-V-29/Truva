import pandas as pd

from kanakku.evaluation import compare, metrics


def _frames():
    engine = pd.DataFrame([
        ("ORDER", "O1", "MATCHED"),
        ("ORDER", "O2", "MISSING_FROM_GATEWAY"),     # true positive
        ("ORDER", "O3", "AMOUNT_MISMATCH"),          # false positive (truth: matched)
        ("ORDER", "O4", "MATCHED"),                  # missed (truth: reversal)
        ("ORDER", "O5", "POSSIBLE_FEE_DIFFERENCE"),  # detected, wrong code (truth: amount mismatch)
        ("ORDER", "O6", "AWAITING_SETTLEMENT"),      # not an exception
        ("GATEWAY_TXN", "G1", "POSSIBLE_DUPLICATE"), # true positive, truth only lists exceptions
        ("BANK_TXN", "K9", "UNRESOLVED"),            # false positive: no truth row means MATCHED
    ], columns=["entity_level", "entity_id", "issue_code"])
    truth = pd.DataFrame([
        ("ORDER", "O1", "MATCHED"), ("ORDER", "O2", "MISSING_FROM_GATEWAY"), ("ORDER", "O3", "MATCHED"),
        ("ORDER", "O4", "REVERSAL_OR_CANCELLATION"), ("ORDER", "O5", "AMOUNT_MISMATCH"), ("ORDER", "O6", "MATCHED"),
        ("GATEWAY_TXN", "G1", "POSSIBLE_DUPLICATE"),
        ("BANK_TXN", "K1", "POSSIBLE_DUPLICATE"),     # missed: engine produced no row at all
    ], columns=["entity_level", "entity_id", "true_issue_code"])
    return engine, truth


def test_detection_counts():
    m = metrics(compare(*_frames()))
    # predicted exceptions: O2, O3, O5, G1, K9 ; true exceptions: O2, O4, O5, G1, K1
    assert (m["true_positive"], m["false_positive"], m["missed"]) == (3, 2, 2)
    assert m["precision"] == 3 / 5 and m["recall"] == 3 / 5
    assert m["true_negative"] == 2  # O1, O6


def test_per_code_and_wrong_code():
    m = metrics(compare(*_frames()))
    per = m["per_code"].set_index("issue_code")
    assert per.loc["MISSING_FROM_GATEWAY", "precision"] == 1.0
    assert per.loc["AMOUNT_MISMATCH", "true_positive"] == 0
    assert per.loc["AMOUNT_MISMATCH", "false_positive"] == 1 and per.loc["AMOUNT_MISMATCH", "missed"] == 1
    assert m["code_accuracy_on_detected"] == 2 / 3  # O2, G1 right; O5 wrong
    assert set(m["missed_rows"].entity_id) == {"O4", "O5", "K1"}
    assert set(m["false_positive_rows"].entity_id) == {"O3", "O5", "K9"}


def test_perfect_engine():
    _, truth = _frames()
    engine = truth.rename(columns={"true_issue_code": "issue_code"})
    m = metrics(compare(engine, truth))
    assert m["precision"] == m["recall"] == m["f1"] == 1.0
