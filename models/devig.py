"""Remove bookmaker margin (vig/overround) from a set of odds to get fair probabilities."""


def implied_prob(decimal_odds: float) -> float:
    return 1.0 / decimal_odds


def devig_book(outcome_prices: dict) -> dict:
    """
    Multiplicative devig: normalize raw implied probabilities so they sum to 1.
    outcome_prices: {outcome_name: decimal_odds}
    returns: {outcome_name: fair_probability}
    """
    raw = {name: implied_prob(price) for name, price in outcome_prices.items()}
    overround = sum(raw.values())
    if overround <= 0:
        raise ValueError("Invalid odds: overround <= 0")
    return {name: p / overround for name, p in raw.items()}
