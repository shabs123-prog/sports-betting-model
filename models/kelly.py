"""Fractional-Kelly stake sizing, expressed in 'units' rather than raw dollars."""


def kelly_fraction(prob: float, decimal_odds: float) -> float:
    """Full Kelly fraction of bankroll to stake. Can be negative (no bet) or large."""
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    q = 1.0 - prob
    f = (b * prob - q) / b
    return max(f, 0.0)


def suggested_stake(
    prob: float,
    decimal_odds: float,
    bankroll: float,
    unit_size_pct: float,
    kelly_frac_multiplier: float,
    max_units: float,
) -> dict:
    """
    Returns {"units": float, "amount": float, "kelly_full_pct": float}.
    Uses a fraction of full Kelly (e.g. 0.3 = 30% Kelly) to cut variance,
    and caps the result at max_units regardless of what Kelly suggests.
    """
    full_kelly = kelly_fraction(prob, decimal_odds)
    stake_fraction = full_kelly * kelly_frac_multiplier
    amount = stake_fraction * bankroll
    unit_value = bankroll * unit_size_pct
    units = amount / unit_value if unit_value > 0 else 0.0
    units = min(units, max_units)
    amount = units * unit_value
    return {"units": round(units, 2), "amount": round(amount, 2), "kelly_full_pct": round(full_kelly * 100, 2)}
