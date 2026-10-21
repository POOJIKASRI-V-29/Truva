"""Published gateway pricing (merchant discount rate + GST). Reference data, not ground truth.

The engine uses this to recognise a shortfall that equals the expected fee.
"""

FEE_BPS = {"UPI": 0, "CARD": 190, "NETBANKING": 150, "WALLET": 170}
GST_PCT = 18


def fee_for(gross_minor: int, method: str) -> tuple[int, int]:
    """Gateway fee and GST on it, rounded half-up to the paisa with integer maths.

    Mirrored in SQL as FLOOR((amount * bps + 5000) / 10000) so both agree exactly.
    """
    fee = (gross_minor * FEE_BPS[method] + 5_000) // 10_000
    tax = (fee * GST_PCT + 50) // 100
    return fee, tax
