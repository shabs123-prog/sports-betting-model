"""Risky / Neutral / Not Risky labelling, independent of edge strength.

Edge tier (models/edge.tier_label) says how confident we are there's value.
Risk here says how likely the pick is to actually land -- a heavy favourite
priced short is "Not Risky" (low variance, low payout) even if the edge on
it is small; a mispriced underdog is "Risky" (high variance, high payout)
even with a big edge. Both get shown together so you can build a slate the
way a tipping app does: a few safe anchors, a few leans, a couple of risky
longshots.
"""
from __future__ import annotations

RISK_BANDS = [
    (0.60, "Not Risky"),
    (0.40, "Neutral"),
]


def risk_label(win_prob: float) -> str:
    for threshold, label in RISK_BANDS:
        if win_prob >= threshold:
            return label
    return "Risky"
