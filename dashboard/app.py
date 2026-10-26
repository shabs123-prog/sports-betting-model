import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from config import (
    DEFAULT_BANKROLL_AMOUNT,
    DEFAULT_MAX_SINGLE_ODDS,
    DEFAULT_MAX_UNITS_PER_BET,
    DEFAULT_MIN_EDGE_PCT,
    DEFAULT_MIN_SINGLE_ODDS,
    DEFAULT_TIER_UNITS_LEAN,
    DEFAULT_TIER_UNITS_MODERATE,
    DEFAULT_TIER_UNITS_STRONG,
    DEFAULT_UNIT_SIZE_PCT,
    SPORT_CONFIG,
    USER_BOOKMAKERS,
)
from db import database as db
from models import conversion_finder, matched_betting, multis
from models.risk import risk_label
from tips.generate_tips import ensure_fresh
from tips.generate_tips import run as run_pipeline

st.set_page_config(page_title="Shab Shooter", layout="wide")
db.init_db()
# Pure local-clock check, no API call -- runs every page load regardless of
# whether a refresh succeeded, so a game that already started stops
# showing as a live pick even if quota blocked today's pipeline run.
db.supersede_started_games()

st.title("🏀 Shab Shooter")
st.caption(
    "Decision support only — this surfaces priced edges and situational context. "
    "You place every bet yourself. Bet within your means; this is not financial advice."
)

RISK_ORDER = ["Not Risky", "Neutral", "Risky"]
RISK_COLOR = {"Not Risky": "green", "Neutral": "orange", "Risky": "red"}
MARKET_LABEL = {"h2h": "Win", "spreads": "Handicap", "totals": "Line", "btts": "BTTS"}
WEEK_AHEAD_DAYS = 14  # how far out each sport tab looks -- value isn't just day-of


def _odds_range() -> tuple[float, float]:
    lo = float(db.get_setting("min_single_odds", str(DEFAULT_MIN_SINGLE_ODDS)))
    hi = float(db.get_setting("max_single_odds", str(DEFAULT_MAX_SINGLE_ODDS)))
    return lo, hi


def _fmt_when(t) -> str:
    """DD/MM/YY + local kickoff/tip-off time, e.g. '26/10/26, 7:30 pm' --
    falls back to date-only for any row from before game_time_local existed."""
    try:
        date_str = date.fromisoformat(t["game_date"]).strftime("%d/%m/%y")
    except (KeyError, ValueError, TypeError):
        return str(t.get("game_date", ""))
    time_str = t.get("game_time_local")
    return f"{date_str}, {time_str}" if isinstance(time_str, str) and time_str else date_str


def _render_one_tip_card(t):
    with st.container(border=True):
        st.markdown(f"**{t['matchup']}** · {_fmt_when(t)}")
        st.markdown(
            f"**{t['market_label']}** · {t['selection']} @ **{t['odds_decimal']}** · {t['bookmaker']}"
        )
        c1, c2 = st.columns([1, 2])
        c1.metric("Units", f"{t['suggested_units']:g}")
        color = RISK_COLOR.get(t["risk"], "gray")
        c2.markdown(f":{color}[**{t['risk']}**] · {t['edge_pct']:+.1%} edge")
        st.caption(t["reasoning"])


def render_value_cards(tips: list, show_sport_header: bool = False):
    """
    Card feed for value-tip rows, hard-ranged to [min_single_odds,
    max_single_odds] (Settings, default 1.6-2.2) -- not a soft discount
    zone, an actual floor and ceiling. Anything outside it (a longshot)
    still gets generated/stored (useful data -- e.g. for Performance
    history, or as a short-priced multi leg) but is never shown here or
    anywhere else in the UI, per explicit feedback -- no "outside range"
    expander to dig through.
    """
    if not tips:
        st.info("No value bets for this view right now.")
        return

    df = pd.DataFrame(tips)
    df["risk"] = df["market_fair_prob"].map(risk_label)
    df["risk_rank"] = df["risk"].map(RISK_ORDER.index)
    df["market_label"] = df["market"].map(lambda m: MARKET_LABEL.get(m, m))

    lo, hi = _odds_range()
    in_range = df[(df["odds_decimal"] >= lo) & (df["odds_decimal"] <= hi)]

    if in_range.empty:
        st.info(f"No value bets in the {lo:g}-{hi:g} odds range for this view right now.")
        return

    sports = sorted(in_range["sport"].unique()) if show_sport_header else [None]
    for sport in sports:
        sport_df = in_range if sport is None else in_range[in_range["sport"] == sport]
        if sport_df.empty:
            continue
        shown = sport_df.sort_values(["risk_rank", "edge_pct"], ascending=[True, False])
        if sport is not None:
            st.markdown(f"#### {sport.upper()}")
        for _, t in shown.iterrows():
            _render_one_tip_card(t)


def render_knowledge_cards(tips: list, show_sport_header: bool = False):
    """
    Ball-knowledge cards -- the situational write-up, plus the bet it's
    attached to when one exists (the best value tip on that game, or failing
    that a prediction tip -- see linked_bet in tips/generate_tips.py). A note
    with no priced or predicted pick on its game just shows the description,
    since there's no real bet to show odds/units for.
    """
    if not tips:
        st.caption("No situational notes for this view right now.")
        return
    sports = sorted({t["sport"] for t in tips}) if show_sport_header else [None]
    for sport in sports:
        if sport is not None:
            st.markdown(f"#### {sport.upper()}")
        for t in tips:
            if sport is not None and t["sport"] != sport:
                continue
            with st.container(border=True):
                st.markdown(f"**{t['matchup']}** · {_fmt_when(t)}")
                st.write(t["reasoning"])
                if t.get("bookmaker") and t.get("odds_decimal"):
                    label = MARKET_LABEL.get(t["market"], t["market"])
                    st.markdown(
                        f"Linked bet: **{label}** · {t['selection']} @ **{t['odds_decimal']}** · "
                        f"{t['bookmaker']} · {t['suggested_units']:g}u"
                    )


MAX_PREDICTIONS_PER_SPORT = 4  # "3-4 bets a day" -- best 4 shown; rest in an expander, not dropped


def _render_one_prediction_card(t):
    with st.container(border=True):
        st.markdown(f"**{t['matchup']}** · {_fmt_when(t)}")
        st.markdown(
            f"**{t['market_label']}** · {t['selection']} @ **{t['odds_decimal']}** · {t['bookmaker']}"
        )
        c1, c2 = st.columns([1, 2])
        c1.metric("Units", f"{t['suggested_units']:g}")
        c2.markdown(f"Model confidence: **{t['model_prob']:.1%}**")
        st.caption(t["reasoning"])


def render_prediction_cards(tips: list, show_sport_header: bool = False):
    """
    Model leans -- no confirmed market edge, so unlike render_value_cards
    there's no risk/edge column (market_fair_prob and edge_pct are both None
    for these). Shows the model's own confidence instead. Best
    MAX_PREDICTIONS_PER_SPORT shown per sport, by model confidence; the rest
    aren't dropped, just tucked into an expander you can open.
    """
    if not tips:
        st.caption("No model predictions for this view right now.")
        return
    df = pd.DataFrame(tips)
    df["market_label"] = df["market"].map(lambda m: MARKET_LABEL.get(m, m))
    sports = sorted(df["sport"].unique()) if show_sport_header else [None]
    for sport in sports:
        sport_df = df if sport is None else df[df["sport"] == sport]
        if sport_df.empty:
            continue
        ranked = sport_df.sort_values("model_prob", ascending=False)
        shown, rest = ranked.head(MAX_PREDICTIONS_PER_SPORT), ranked.iloc[MAX_PREDICTIONS_PER_SPORT:]
        if sport is not None:
            st.markdown(f"#### {sport.upper()}")
        for _, t in shown.iterrows():
            _render_one_prediction_card(t)
        if not rest.empty:
            with st.expander(f"{len(rest)} more prediction(s)"):
                for _, t in rest.iterrows():
                    _render_one_prediction_card(t)


def render_sport_tab(sport: str):
    cfg = SPORT_CONFIG[sport]
    _, refresh_col = st.columns([4, 1])
    with refresh_col:
        if st.button("Refresh", key=f"refresh_{sport}"):
            with st.spinner(f"Refreshing {sport.upper()}..."):
                result = run_pipeline(sport=sport, log=lambda *_a, **_k: None)
                db.set_setting(f"last_run_date_{sport}", date.today().isoformat())
            st.success(
                f"{result['value_tips']} value tips, {result.get('prediction_tips', 0)} predictions, "
                f"{result['knowledge_notes']} knowledge notes."
            )
            st.rerun()

    if not cfg["has_history"]:
        st.caption("No results-history source wired up for this sport yet — devig value bets only, no Elo context or ball-knowledge notes.")
    if not cfg.get("auto_refresh"):
        st.caption("Not auto-refreshed daily (off-season or markets not posted yet) — use Refresh above to check manually.")

    today_str = date.today().isoformat()
    end_str = (date.today() + timedelta(days=WEEK_AHEAD_DAYS)).isoformat()

    st.subheader("Value Bets")
    value_tips = db.get_tips_in_range(today_str, end_str, tip_type="value", sport=sport)
    render_value_cards(value_tips)

    if cfg["has_history"]:
        st.divider()
        st.subheader("Predictions")
        st.caption("Model leans with no confirmed market edge — a lower-confidence claim than a Value Bet, not a detected price discrepancy.")
        prediction_tips = db.get_tips_in_range(today_str, end_str, tip_type="prediction", sport=sport)
        render_prediction_cards(prediction_tips)

        if cfg.get("show_knowledge", True):
            st.divider()
            st.subheader("Ball Knowledge")
            knowledge_tips = db.get_tips_in_range(today_str, end_str, tip_type="knowledge", sport=sport)
            render_knowledge_cards(knowledge_tips)


# ---------------------------------------------------------------- Daily auto-refresh
# Streamlit renders every tab's body on each script run (only the active one is
# shown), so this check runs once up front regardless of which tab is open.
auto_sports = [s for s, cfg in SPORT_CONFIG.items() if cfg.get("auto_refresh")]
already_checked_today = all(
    db.get_setting(f"last_run_date_{s}") == date.today().isoformat() for s in auto_sports
)
if not already_checked_today:
    with st.spinner(
        "First check today — refreshing " + ", ".join(s.upper() for s in auto_sports) +
        ". This can take under a minute (one-off per day); instant after this."
    ):
        for s in auto_sports:
            ensure_fresh(s, log=lambda *_a, **_k: None)
    st.rerun()

sport_tab_names = list(SPORT_CONFIG.keys())
tab_week, *sport_tabs, tab_horses, tab_bonus, tab_tracker, tab_perf, tab_settings = st.tabs(
    ["This Week"] + [s.upper() for s in sport_tab_names] + ["Horses", "Bonus Converter", "Bet Tracker", "Performance", "Settings"]
)

# ---------------------------------------------------------------- This Week
RECOMMENDED_COUNT = 5  # roughly what a "3-4 bets a week" cadence looks like with some to pick from
MUST_BET_MIN_EDGE = 0.015  # Moderate tier or better
MUST_BET_COUNT = 4  # "3-4 bets a week" -- the short list is meant to BE that cadence, not a sample of it
MODEL_LEANS_COUNT = 6  # across all sports combined -- the volume lever when value-tip edge is thin this week

with tab_week:
    st.caption(
        "Best picks across every sport for the week ahead — value doesn't have to be bet "
        "day-of. Line/Handicap markets especially can carry more value earlier in the week, "
        "before the line sharpens closer to kickoff."
    )

    today_str = date.today().isoformat()
    # WEEK_AHEAD_DAYS, not 7 -- Line/Handicap/Totals value routinely posts
    # days to a week+ before kickoff (that's literally the "catch it at
    # 192.5 before it drifts to 190.5" ask), so "This Week" has to look as
    # far out as the per-sport tabs do or it'd find the value today but
    # drop it from Must-Bet/Recommended for being "too far away."
    week_end_str = (date.today() + timedelta(days=WEEK_AHEAD_DAYS)).isoformat()
    week_tips = db.get_tips_in_range(today_str, week_end_str, tip_type="value")

    if week_tips:
        df = pd.DataFrame(week_tips)
        lo, hi = _odds_range()
        within_cap = df[(df["odds_decimal"] >= lo) & (df["odds_decimal"] <= hi)].copy()
        within_cap["is_line_handicap"] = within_cap["market"].isin(["spreads", "totals"])
        # Line/Handicap bumps a pick up, edge size breaks ties -- everything
        # here is already within the odds range, so no separate odds bonus needed.
        within_cap["priority"] = within_cap["is_line_handicap"].astype(int) + within_cap["edge_pct"]

        # Must-Bet is the single most important thing on this tab -- the short,
        # high-conviction subset of this week's in-range picks (Moderate-tier-
        # or-better edge AND Not Risky/Neutral win probability). It goes
        # first, with a deliberately loud styled banner (not just a
        # subheader) so it can't be missed or mistaken for just another
        # section -- this is specifically "place these if nothing
        # else," not one more list among several.
        within_cap["risk"] = within_cap["market_fair_prob"].map(risk_label)
        must_bet = within_cap[
            (within_cap["edge_pct"] >= MUST_BET_MIN_EDGE) & (within_cap["risk"] != "Risky")
        ].sort_values("priority", ascending=False).head(MUST_BET_COUNT)

        st.markdown(
            f"""<div style="background:#1d3324;border:2px solid #3dd56d;border-radius:12px;
            padding:16px 20px;margin-bottom:12px;">
            <div style="font-size:1.4rem;font-weight:800;color:#3dd56d;">
            🔒 MUST-BET THIS WEEK ({len(must_bet)})</div>
            <div style="color:#b7f3c8;font-size:0.92rem;margin-top:4px;">
            Moderate-or-better edge, Not Risky/Neutral win chance, within {lo:g}-{hi:g} odds —
            if you only place a few bets this week, place these.</div>
            </div>""",
            unsafe_allow_html=True,
        )
        if must_bet.empty:
            st.info("Nothing clears the Must-Bet bar this week — see Recommended below for the next best picks.")
        else:
            render_value_cards(must_bet.to_dict("records"), show_sport_header=True)

        st.divider()
        remaining = within_cap.drop(must_bet.index)
        recommended = remaining.sort_values("priority", ascending=False).head(RECOMMENDED_COUNT)
        st.subheader(f"Recommended bets this week ({len(recommended)})")
        st.caption(f"All within {lo:g}-{hi:g} odds, prioritising Line/Handicap — a short, curated list.")
        render_value_cards(recommended.to_dict("records"), show_sport_header=True)

        # Model leans (Predictions): no confirmed market edge, so kept
        # strictly separate from Must-Bet/Recommended above (those are only
        # ever built from tip_type="value" -- a verified price discrepancy)
        # rather than blended in. This is the volume lever for weeks where
        # real cross-bookmaker edge in the odds range is thin -- a handful
        # of bets a week doesn't have to come from value tips alone.
        st.divider()
        week_predictions = db.get_tips_in_range(today_str, week_end_str, tip_type="prediction")
        top_predictions = sorted(week_predictions, key=lambda t: -t["model_prob"])[:MODEL_LEANS_COUNT]
        st.subheader(f"This week's model leans ({len(top_predictions)})")
        st.caption(
            "No confirmed market edge -- the model just favours this side over the price. "
            "Lower confidence than Must-Bet/Recommended above; extra volume, not extra certainty."
        )
        render_prediction_cards(top_predictions, show_sport_header=True)

        def _render_multi_combo(combo):
            with st.container(border=True):
                tag = " · approximate (correlated legs)" if combo.get("approximate") else ""
                st.markdown(
                    f"**{combo['size']}-leg · {combo['bookmaker']} · "
                    f"combined odds {combo['combined_odds']}{tag}**"
                )
                for leg in combo["legs"]:
                    st.markdown(
                        f"- {leg['sport'].upper()}: {leg['matchup']} — "
                        f"{MARKET_LABEL.get(leg['market'], leg['market'])} {leg['selection']} "
                        f"@ {leg['odds_decimal']}"
                    )
                c1, c2, c3 = st.columns(3)
                c1.metric("Units", f"{combo['suggested_units']:g}")
                c2.metric("Combined edge", f"{combo['combined_edge_pct']:+.1%}")
                c3.metric("Chance all legs hit", f"{combo['implied_hit_pct']:.1%}")

        def _render_combo_group(combos):
            two_leg = [c for c in combos if c["size"] == 2][:4]
            three_leg = [c for c in combos if c["size"] == 3][:3]
            for combo in two_leg:
                _render_multi_combo(combo)
            if three_leg:
                with st.expander(f"3-leg — noticeably higher variance ({len(three_leg)})"):
                    for combo in three_leg:
                        _render_multi_combo(combo)

        # Multis/SGM only show up when there's actually something to show --
        # most weeks there isn't enough eligible picks to build one, and a
        # section that just says "not enough picks" every time is clutter,
        # not information.
        week_multis = multis.suggest_multis(df.to_dict("records"))
        if week_multis:
            st.divider()
            st.subheader("Multis (cross-game)")
            st.caption(
                "Combines several of this week's +EV singles from *different* games, all at the "
                "**same bookmaker** (so it's one bet slip you can actually place). Combined odds "
                "capped at 2.2, same ceiling as a single. Higher variance than a single: a multi "
                "only pays if every leg wins, so the 'chance all legs hit' number matters as much "
                "as the edge."
            )
            _render_combo_group(week_multis)

        week_sgms = multis.suggest_sgms(df.to_dict("records"))
        if week_sgms:
            st.divider()
            st.subheader("Same-Game Multis (SGM)")
            st.caption(
                "Combines different markets (Win/Handicap/Line) within the *same* game, same "
                "bookmaker. Marked **approximate** — these legs are correlated (a team winning and "
                "the game going under both lean the same way), so the combined edge/hit-chance "
                "shown is a rough estimate, not a verified number like the cross-game multis above."
            )
            _render_combo_group(week_sgms)
    else:
        st.info("No value tips for the week yet — refresh sports from their own tabs.")

    # Line Movement goes last -- it's supplementary context (and often just
    # "check back in a few days" while history is cold-starting), so it
    # shouldn't push the actually-actionable Must-Bet/Recommended lists
    # further down the page.
    st.divider()
    st.subheader("Line Movement")
    st.caption(
        "Every pipeline run records each line's fair probability (and point, for Line/Handicap) "
        "so a genuine move — e.g. a total posted at 192.5 drifting to 190.5 — becomes visible "
        "over time, not just a single snapshot. Needs a few days of history before anything "
        "shows up here; empty at first means it just started tracking, not that it's broken."
    )
    moved_lines = db.get_moved_lines(today_str, week_end_str)
    if not moved_lines:
        st.info("No line has 2+ days of tracked history yet — check back in a few days.")
    else:
        def _movement_size(m):
            first, latest = m["first"], m["latest"]
            if first["point"] is not None and latest["point"] is not None:
                return abs(latest["point"] - first["point"])
            return abs(latest["fair_prob"] - first["fair_prob"])

        for m in sorted(moved_lines, key=_movement_size, reverse=True)[:10]:
            first, latest = m["first"], m["latest"]
            with st.container(border=True):
                st.markdown(f"**{m['sport'].upper()}: {m['matchup']}** · {_fmt_when(m)}")
                label = MARKET_LABEL.get(m["market"], m["market"])
                if first["point"] is not None:
                    st.markdown(f"{label} · {m['outcome']}: **{first['point']:g} → {latest['point']:g}**")
                else:
                    st.markdown(
                        f"{label} · {m['outcome']}: fair probability "
                        f"**{first['fair_prob']:.1%} → {latest['fair_prob']:.1%}**"
                    )
                st.caption(f"Tracked over {m['days_tracked']} day(s), {m['snapshot_count']} snapshot(s).")

# ---------------------------------------------------------------- Per-sport tabs
for sport, tab in zip(sport_tab_names, sport_tabs):
    with tab:
        render_sport_tab(sport)

# ---------------------------------------------------------------- Horses
with tab_horses:
    st.caption(
        "Not wired up to live data yet. Horse racing needs a different data source "
        "(Betfair Exchange or Racing Australia, not The Odds API) and a different model — "
        "per-runner form/speed-figure probability, not team-vs-team Elo. Once that's built, "
        "notes here will look like Ball Knowledge: a description of the situation, no odds "
        "or unit size, since that needs real per-runner pricing this tool doesn't have yet."
    )
    st.info("No horse racing notes yet. Say the word if you want this built next.")

# ---------------------------------------------------------------- Bonus Converter
with tab_bonus:
    st.caption(
        "Turn bonus/free bet money into cash: back the bonus bet at your bookmaker, then lay "
        "the opposite outcome on an exchange (Betfair) to lock in close to the same result "
        "either way. You place both legs yourself."
    )

    mode = st.radio("Mode", ["Find the best game for me", "Enter odds manually"], horizontal=True)

    bet_kind = st.radio(
        "Bet type",
        ["Free / bonus bet (stake NOT returned)", "Qualifying bet (your own money, stake returned)"],
        help="Most bookmakers make you place a qualifying bet with your own money first, "
             "then release the bonus as a separate free bet you place afterwards.",
    )
    is_free_bet = bet_kind.startswith("Free")

    c1, c2 = st.columns(2)
    with c1:
        stake = st.number_input("Bonus / stake amount ($)", min_value=0.0, value=50.0, step=5.0)
    with c2:
        commission = st.slider(
            "Betfair commission (%) — check your current rate, it varies",
            0.0, 10.0, 5.0, 0.1,
        ) / 100

    def _show_result(result: dict, stake: float, is_free_bet: bool):
        st.metric("Lay this much on Betfair", f"${result['lay_stake']:.2f}")
        c1, c2, c3 = st.columns(3)
        c1.metric("If back bet wins", f"${result['profit_if_back_wins']:+.2f}")
        c2.metric("If back bet loses", f"${result['profit_if_back_loses']:+.2f}")
        c3.metric("Retained" if is_free_bet else "Cost to qualify", f"{result['retention_pct']:+.1%}")
        if is_free_bet:
            # Streamlit renders "$...$" pairs as LaTeX -- escape both or the caption gets mangled.
            st.caption(
                f"Guaranteed outcome regardless of result: \\${result['worst_case_profit']:+.2f} "
                f"cash from a \\${stake:.0f} bonus bet ({result['retention_pct']:.0%} retained)."
            )
        else:
            st.caption(
                "Cost (or small gain) of meeting a qualifying-bet requirement with your own "
                "money — typically a small loss, which unlocks the real bonus separately."
            )

    if mode == "Enter odds manually":
        c1, c2 = st.columns(2)
        with c1:
            back_odds = st.number_input("Back odds (at your bookmaker, decimal)", min_value=1.01, value=4.0, step=0.05)
        with c2:
            lay_odds = st.number_input("Lay odds (on Betfair Exchange, decimal)", min_value=1.01, value=4.2, step=0.05)

        if stake > 0 and lay_odds > commission:
            fn = matched_betting.free_bet if is_free_bet else matched_betting.qualifying_bet
            _show_result(fn(stake, back_odds, lay_odds, commission), stake, is_free_bet)
        else:
            st.warning("Lay odds must be greater than the commission rate for this to be calculable.")

    else:
        back_bookmaker = st.selectbox("Which app is the bonus from?", sorted(USER_BOOKMAKERS - {"Betfair"}))
        sports_to_scan = st.multiselect(
            "Scan which sports?", list(SPORT_CONFIG.keys()),
            default=["nba", "epl"], format_func=lambda s: s.upper(),
        )
        if st.button("Find best conversion game", type="primary"):
            scan_cfg = {s: SPORT_CONFIG[s] for s in sports_to_scan}
            with st.spinner(f"Scanning {', '.join(s.upper() for s in sports_to_scan)} for {back_bookmaker} vs Betfair..."):
                opportunities = conversion_finder.find_opportunities(
                    scan_cfg, back_bookmaker, stake, is_free_bet, commission, top_n=10,
                    log=lambda *_a, **_k: None,
                )
            if not opportunities:
                st.info(
                    f"No game found right now where both {back_bookmaker} and Betfair Exchange "
                    "price the same outcome. Try different sports, or use manual entry."
                )
            else:
                st.caption(f"Best {len(opportunities)} opportunities, ranked by cash retained:")
                for opp in opportunities:
                    with st.container(border=True):
                        st.markdown(f"**{opp['sport'].upper()} — {opp['matchup']}**")
                        st.markdown(
                            f"Back **{opp['selection']}** @ {opp['back_odds']} ({back_bookmaker}) · "
                            f"Lay @ {opp['lay_odds']} (Betfair)"
                        )
                        _show_result(opp, stake, is_free_bet)

# ---------------------------------------------------------------- Bet Tracker
with tab_tracker:
    st.subheader("Log a bet")
    all_tips = db.get_all_tips(limit=200)
    tip_options = {f"#{t['id']} — {t['matchup']} — {t['selection']} ({t['bookmaker']})": t for t in all_tips if t["tip_type"] == "value"}

    with st.form("log_bet_form"):
        linked_label = st.selectbox("Link to a tip (optional)", ["(manual entry)"] + list(tip_options.keys()))
        linked_tip = tip_options.get(linked_label)

        c1, c2, c3 = st.columns(3)
        with c1:
            sport_in = st.text_input("Sport", value=linked_tip["sport"] if linked_tip else "nba")
            matchup_in = st.text_input("Matchup", value=linked_tip["matchup"] if linked_tip else "")
        with c2:
            selection_in = st.text_input("Selection", value=linked_tip["selection"] if linked_tip else "")
            bookmaker_in = st.text_input("Bookmaker", value=linked_tip["bookmaker"] if linked_tip else "")
        with c3:
            odds_in = st.number_input("Decimal odds", min_value=1.01, value=float(linked_tip["odds_decimal"]) if linked_tip and linked_tip["odds_decimal"] else 2.0, step=0.01)
            units_in = st.number_input("Stake (units)", min_value=0.0, value=float(linked_tip["suggested_units"]) if linked_tip and linked_tip["suggested_units"] else 1.0, step=0.25)

        unit_size_pct = float(db.get_setting("unit_size_pct", "0.01"))
        bankroll = float(db.get_setting("bankroll_amount", "1000"))
        stake_amount = units_in * bankroll * unit_size_pct

        submitted = st.form_submit_button("Log bet")
        if submitted:
            db.insert_bet({
                "tip_id": linked_tip["id"] if linked_tip else None,
                "sport": sport_in,
                "matchup": matchup_in,
                "selection": selection_in,
                "bookmaker": bookmaker_in,
                "odds_decimal": odds_in,
                "stake_units": units_in,
                "stake_amount": stake_amount,
            })
            st.success("Bet logged.")

    st.subheader("Your bets")
    bets = db.get_all_bets()
    if bets:
        for b in bets:
            if b["result"] == "pending":
                cols = st.columns([4, 1, 1, 1])
                cols[0].write(f"**{b['matchup']}** — {b['selection']} @ {b['odds_decimal']} ({b['bookmaker']}) — {b['stake_units']}u")
                if cols[1].button("Win", key=f"win_{b['id']}"):
                    db.update_bet_result(b["id"], "win")
                    st.rerun()
                if cols[2].button("Loss", key=f"loss_{b['id']}"):
                    db.update_bet_result(b["id"], "loss")
                    st.rerun()
                if cols[3].button("Push", key=f"push_{b['id']}"):
                    db.update_bet_result(b["id"], "push")
                    st.rerun()
        st.divider()
        df_bets = pd.DataFrame(bets)[[
            "placed_at", "sport", "matchup", "selection", "bookmaker", "odds_decimal",
            "stake_units", "result", "profit_units",
        ]]
        st.dataframe(df_bets, use_container_width=True, hide_index=True)
    else:
        st.caption("No bets logged yet.")

# ---------------------------------------------------------------- Performance
with tab_perf:
    bets = db.get_all_bets()
    settled = [b for b in bets if b["result"] != "pending"]
    if not settled:
        st.info("Log and settle some bets to see performance here.")
    else:
        df = pd.DataFrame(settled)
        df["placed_at"] = pd.to_datetime(df["placed_at"])
        df = df.sort_values("placed_at")
        df["cum_profit_units"] = df["profit_units"].cumsum()

        total_staked = df["stake_units"].sum()
        total_profit = df["profit_units"].sum()
        win_rate = (df["result"] == "win").mean()
        roi = total_profit / total_staked if total_staked else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Bets settled", len(df))
        c2.metric("Win rate", f"{win_rate:.1%}")
        c3.metric("Profit (units)", f"{total_profit:+.2f}")
        c4.metric("ROI", f"{roi:+.1%}")

        st.line_chart(df.set_index("placed_at")["cum_profit_units"])

        st.subheader("By sport")
        by_sport = df.groupby("sport").agg(
            bets=("id", "count"),
            profit_units=("profit_units", "sum"),
            stake_units=("stake_units", "sum"),
        )
        by_sport["roi"] = by_sport["profit_units"] / by_sport["stake_units"]
        st.dataframe(by_sport, use_container_width=True)

# ---------------------------------------------------------------- Settings
with tab_settings:
    st.subheader("Bankroll")
    bankroll = st.number_input("Bankroll ($)", min_value=0.0, value=float(db.get_setting("bankroll_amount", str(DEFAULT_BANKROLL_AMOUNT))), step=50.0)
    unit_pct = st.number_input("Unit size (% of bankroll)", min_value=0.1, max_value=10.0, value=float(db.get_setting("unit_size_pct", str(DEFAULT_UNIT_SIZE_PCT))) * 100, step=0.1) / 100

    st.subheader("Stake size by edge tier")
    st.caption("A fixed unit count per tier, not a continuously-scaled fraction — bigger edge, bigger round number.")
    c1, c2, c3 = st.columns(3)
    with c1:
        tier_strong = st.number_input("Strong edge (≥3%)", min_value=0.25, value=float(db.get_setting("tier_units_strong", str(DEFAULT_TIER_UNITS_STRONG))), step=0.25)
    with c2:
        tier_moderate = st.number_input("Moderate edge (1.5-3%)", min_value=0.25, value=float(db.get_setting("tier_units_moderate", str(DEFAULT_TIER_UNITS_MODERATE))), step=0.25)
    with c3:
        tier_lean = st.number_input("Lean edge (below 1.5%)", min_value=0.25, value=float(db.get_setting("tier_units_lean", str(DEFAULT_TIER_UNITS_LEAN))), step=0.25)
    max_units = st.number_input("Max units per bet (hard cap, overrides the above)", min_value=0.5, value=float(db.get_setting("max_units_per_bet", str(DEFAULT_MAX_UNITS_PER_BET))), step=0.5)
    min_edge = st.slider("Minimum edge to flag a tip", 0.0, 0.2, float(db.get_setting("min_edge_pct", str(DEFAULT_MIN_EDGE_PCT))), 0.005, format="%.1f%%")

    st.subheader("Odds range")
    c1, c2 = st.columns(2)
    with c1:
        min_single_odds = st.number_input(
            "Min odds shown as a normal recommendation",
            min_value=1.01, max_value=5.0,
            value=float(db.get_setting("min_single_odds", str(DEFAULT_MIN_SINGLE_ODDS))), step=0.05,
        )
    with c2:
        max_single_odds = st.number_input(
            "Max odds shown as a normal recommendation",
            min_value=1.2, max_value=10.0,
            value=float(db.get_setting("max_single_odds", str(DEFAULT_MAX_SINGLE_ODDS))), step=0.1,
        )
    st.caption(
        "Hard range, not a soft discount -- singles/Predictions outside it still get found and "
        "stored (useful data, e.g. for Performance history, or as short-priced multi legs) but "
        "aren't shown anywhere in the UI, per explicit feedback -- no 'not shown by default' "
        "expander to dig through. Multis/SGM have no minimum leg odds -- a short leg below this "
        "range can still combine into a sensible-priced multi."
    )

    if st.button("Save settings"):
        db.set_setting("bankroll_amount", bankroll)
        db.set_setting("unit_size_pct", unit_pct)
        db.set_setting("tier_units_strong", tier_strong)
        db.set_setting("tier_units_moderate", tier_moderate)
        db.set_setting("tier_units_lean", tier_lean)
        db.set_setting("max_units_per_bet", max_units)
        db.set_setting("min_edge_pct", min_edge)
        db.set_setting("min_single_odds", min_single_odds)
        db.set_setting("max_single_odds", max_single_odds)
        st.success("Saved.")

    st.caption(f"1 unit = ${bankroll * unit_pct:,.2f} at current settings.")
