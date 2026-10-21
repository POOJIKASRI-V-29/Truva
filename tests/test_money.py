import pytest

from kanakku.money import MoneyError, format_inr, from_minor, to_minor


@pytest.mark.parametrize("text,minor", [
    ("1499.50", 149950), ("1499.5", 149950), ("1499", 149900), ("0.01", 1), ("0", 0),
    ("-12.34", -1234), (" 7.00 ", 700), ("9999999999999.99", 999999999999999),
])
def test_to_minor_parses_exactly(text, minor):
    assert to_minor(text) == minor


@pytest.mark.parametrize("bad", ["", None, "abc", "12,499.00", "10.505", "1e5", "₹100", "1 000", "--1", ".5"])
def test_to_minor_rejects_malformed(bad):
    with pytest.raises(MoneyError):
        to_minor(bad)


def test_no_float_drift():
    # 0.1 + 0.2 != 0.3 in floating point; in paise it is exact
    assert to_minor("0.10") + to_minor("0.20") == to_minor("0.30")
    assert sum(to_minor("0.01") for _ in range(100_000)) == to_minor("1000.00")


@pytest.mark.parametrize("minor", [0, 1, 99, 100, 149950, -1234, 10**15 - 1])
def test_round_trip(minor):
    assert to_minor(from_minor(minor)) == minor


def test_indian_grouping():
    assert format_inr(123456789) == "₹12,34,567.89"
    assert format_inr(100000) == "₹1,000.00"
    assert format_inr(-5) == "-₹0.05"
    assert format_inr(None) == "—"
