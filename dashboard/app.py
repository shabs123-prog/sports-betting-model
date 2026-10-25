import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from config import (
    DEFAULT_BANKROLL_AMOUNT,
    DEFAULT_KELLY_FRACTION,
    DEFAULT_MAX_UNITS_PER_BET,
    DEFAULT_MIN_EDGE_PCT,
    DEFAULT_UNIT_SIZE_PCT,
    SPORT_CONFIG,
    USER_BOOKMAKERS,
)
from db import database as db
from models import conversion_finder, matched_betting
from models.risk import risk_label
from tips.generate_tips import ensure_fresh
from tips.generate_tips import run as run_pipeline

st.set_page_config(page_title="Sports Betting Model", layout="wide")
db.init_db()

st.title("🏀 Sports Betting Model")
st.caption(
    "Decision support only — this surfaces priced edges and situational context. "
    "You place every bet yourself. Bet within your means; this is not financial advice."
)

tab_tips, tab_knowledge, tab_bonus, tab_run, tab_tracker, tab_perf, tab_settings = st.tabs(
    ["Today's Tips", "Ball Knowledge", "Bonus Converter", "Run Pipeline", "Bet Tracker", "Performance", "Settings"]
)

# ---------------------------------------------------------------- Today's Tips
RISK_ORDER = ["Not Risky", "Neutral", "Risky"]
RISK_COLOR = {"Not Risky": "green", "Neutral": "orange", "Risky": "red"}
MARKET_LABEL = {"h2h": "Win", "spreads": "Handicap", "totals": "Line"}
ODDS_BUCKET_ORDER = ["Preferred", "Other", "Longshot"]
MAX_LONGSHOTS_PER_SPORT = 3


def _odds_bucket(odds: float) -> str:
    if 1.7 <= odds <= 2.2:
        return "Preferred"
    if odds >= 3.0:
        return "Longshot"
    return "Other"

with tab_tips:
    auto_sports = [s for s, cfg in SPORT_CONFIG.items() if cfg.get("auto_refresh")]
    already_checked_today = all(
        db.get_setting(f"last_run_date_{s}") == date.today().isoformat() for s in auto_sports
    )
    if not already_checked_today:
        with st.spinner(
            "First check today — refreshing " + ", ".join(s.upper() for s in auto_sports) +
            ". This can take a minute or two (one-off per day); it'll be instant after this."
        ):
            for s in auto_sports:
                ensure_fresh(s, log=lambda *_a, **_k: None)
        st.rerun()

    selected_date = st.date_input("Game date", value=date.today())
    date_str = selected_date.isoformat()

    value_tips = db.get_tips_for_date(date_str, tip_type="value")

    if value_tips:
        df = pd.DataFrame(value_tips)
        df["risk"] = df["market_fair_prob"].map(risk_label)
        df["risk_rank"] = df["risk"].map(RISK_ORDER.index)
        df["market_label"] = df["market"].map(lambda m: MARKET_LABEL.get(m, m))
        df["odds_bucket"] = df["odds_decimal"].map(_odds_bucket)
        df["odds_bucket_rank"] = df["odds_bucket"].map(ODDS_BUCKET_ORDER.index)

        for sport in sorted(df["sport"].unique()):
            sport_df = df[df["sport"] == sport]
            longshots = sport_df[sport_df["odds_bucket"] == "Longshot"].sort_values("edge_pct", ascending=False)
            shown = pd.concat([sport_df[sport_df["odds_bucket"] != "Longshot"], longshots.head(MAX_LONGSHOTS_PER_SPORT)])
            shown = shown.sort_values(
                ["odds_bucket_rank", "risk_rank", "edge_pct"], ascending=[True, True, False]
            )
            hidden = len(longshots) - min(len(longshots), MAX_LONGSHOTS_PER_SPORT)

            st.subheader(sport.upper())
            for _, t in shown.iterrows():
                with st.container(border=True):
                    st.markdown(f"**{t['matchup']}**")
                    st.markdown(
                        f"**{t['market_label']}** · {t['selection']} @ **{t['odds_decimal']}** · {t['bookmaker']}"
                    )
                    c1, c2 = st.columns([1, 2])
                    c1.metric("Units", f"{t['suggested_units']:g}")
                    color = RISK_COLOR.get(t["risk"], "gray")
                    c2.markdown(f":{color}[**{t['risk']}**] · {t['edge_pct']:+.1%} edge")
                    st.caption(t["reasoning"])
            if hidden > 0:
                st.caption(f"+ {hidden} more longshot(s) (3.0+ odds) not shown, to keep the feed from being all underdogs.")
        st.caption(
            "Sorted odds 1.7-2.2 first, then other prices, with longshots (3.0+) capped and pushed "
            "last — within each, safest risk first. Units already scale down for weaker edges via Kelly."
        )
    else:
        st.info(
            "No value tips for this date. NBA/EPL auto-refresh daily — try again shortly, or "
            "check another sport from the 'Run Pipeline' tab."
        )

# ---------------------------------------------------------------- Ball Knowledge
with tab_knowledge:
    st.caption(
        "Situational context, not priced edges: rest/schedule, form streaks, home/away splits. "
        "Wired up for NBA, EPL, NBL and AFL (ESPN results history) — NRL and BBL don't have a "
        "results source yet, so no notes for those."
    )
    kn_date = st.date_input("Game date", value=date.today(), key="knowledge_date")
    knowledge_tips = db.get_tips_for_date(kn_date.isoformat(), tip_type="knowledge")

    if knowledge_tips:
        for sport in sorted({t["sport"] for t in knowledge_tips}):
            st.subheader(sport.upper())
            for t in knowledge_tips:
                if t["sport"] == sport:
                    with st.container(border=True):
                        st.markdown(f"**{t['matchup']}**")
                        st.write(t["reasoning"])
    else:
        st.info("No situational notes for this date yet. Run the NBA pipeline from the 'Run Pipeline' tab.")

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

# ---------------------------------------------------------------- Run Pipeline
with tab_run:
    st.write(
        "NBA and EPL refresh automatically (once a day) when you open Today's Tips — "
        "you shouldn't need this tab for those day-to-day. Use it to force a fresh pull "
        "mid-day (odds move), or to manually check a sport that isn't auto-refreshed yet "
        "(NBL/NRL/AFL/BBL — off-season right now)."
    )
    sport = st.selectbox("Sport", list(SPORT_CONFIG.keys()), index=0,
                          format_func=lambda s: s.upper())
    if st.button("Run pipeline now", type="primary"):
        log_box = st.empty()
        logs = []

        def log(msg):
            logs.append(msg)
            log_box.code("\n".join(logs))

        try:
            result = run_pipeline(sport=sport, log=log)
            db.set_setting(f"last_run_date_{sport}", date.today().isoformat())
            st.success(f"Done — {result['value_tips']} value tips, {result['knowledge_notes']} knowledge notes.")
        except Exception as e:
            st.error(f"Pipeline failed: {e}")

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
    st.subheader("Bankroll & staking")
    bankroll = st.number_input("Bankroll ($)", min_value=0.0, value=float(db.get_setting("bankroll_amount", str(DEFAULT_BANKROLL_AMOUNT))), step=50.0)
    unit_pct = st.number_input("Unit size (% of bankroll)", min_value=0.1, max_value=10.0, value=float(db.get_setting("unit_size_pct", str(DEFAULT_UNIT_SIZE_PCT))) * 100, step=0.1) / 100
    kelly_frac = st.slider("Kelly fraction (lower = more conservative)", 0.0, 1.0, float(db.get_setting("kelly_fraction", str(DEFAULT_KELLY_FRACTION))), 0.05)
    max_units = st.number_input("Max units per bet", min_value=0.5, value=float(db.get_setting("max_units_per_bet", str(DEFAULT_MAX_UNITS_PER_BET))), step=0.5)
    min_edge = st.slider("Minimum edge to flag a tip", 0.0, 0.2, float(db.get_setting("min_edge_pct", str(DEFAULT_MIN_EDGE_PCT))), 0.005, format="%.1f%%")

    if st.button("Save settings"):
        db.set_setting("bankroll_amount", bankroll)
        db.set_setting("unit_size_pct", unit_pct)
        db.set_setting("kelly_fraction", kelly_frac)
        db.set_setting("max_units_per_bet", max_units)
        db.set_setting("min_edge_pct", min_edge)
        st.success("Saved.")

    st.caption(f"1 unit = ${bankroll * unit_pct:,.2f} at current settings.")
