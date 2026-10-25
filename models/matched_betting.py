"""Bonus/free-bet conversion math (a.k.a. matched betting): back a bet at a
bookmaker offering bonus money, lay the opposite outcome on an exchange
(Betfair) to lock in close to the same result either way, extracting the
bonus as cash instead of just betting it outright and hoping.

These are the standard formulas used throughout the matched-betting
community -- not proprietary, just the algebra of equalizing both outcomes.
Two cases, because they're NOT the same bet:

  - Qualifying bet: your own money, stake returned if it wins. Bookmakers
    usually make you place one of these (and lose a little on it) before
    they release the actual bonus.
  - Free/bonus bet (SNR = stake not returned): if it wins you get the
    winnings only, not your stake back, because it was never really yours.
"""
from __future__ import annotations


def _result(stake: float, back_odds: float, lay_stake: float, lay_odds: float, commission: float, snr: bool) -> dict:
    # Winning the back bet pays the same shape either way (winnings minus lay
    # liability) -- what differs between qualifying/SNR is the losing case
    # (whether the stake itself was ever at risk) and how lay_stake was sized.
    profit_if_back_wins = stake * (back_odds - 1) - lay_stake * (lay_odds - 1)
    profit_if_lay_wins = (0.0 if snr else -stake) + lay_stake * (1 - commission)
    worst_case = min(profit_if_back_wins, profit_if_lay_wins)
    return {
        "lay_stake": round(lay_stake, 2),
        "profit_if_back_wins": round(profit_if_back_wins, 2),
        "profit_if_back_loses": round(profit_if_lay_wins, 2),
        "worst_case_profit": round(worst_case, 2),
        "retention_pct": round(worst_case / stake, 4) if stake else 0.0,
    }


def qualifying_bet(stake: float, back_odds: float, lay_odds: float, commission: float = 0.05) -> dict:
    """Own money, stake returned on a back win. Lay stake that equalizes both outcomes."""
    lay_stake = stake * back_odds / (lay_odds - commission)
    return _result(stake, back_odds, lay_stake, lay_odds, commission, snr=False)


def free_bet(stake: float, back_odds: float, lay_odds: float, commission: float = 0.05) -> dict:
    """Bonus/free bet, stake NOT returned on a back win (SNR)."""
    lay_stake = stake * (back_odds - 1) / (lay_odds - commission)
    return _result(stake, back_odds, lay_stake, lay_odds, commission, snr=True)
