# Sports Betting Model

A value-betting tool: pulls live odds across AU bookmakers, compares them
against a sharp reference line, suggests a stake in units, and tracks your
results. NBA/EPL/NBL/AFL/NFL have Elo model context + ball-knowledge notes;
NRL/BBL are devig-only (see `config.SPORT_CONFIG`). Australian horse racing
has a placeholder tab but no real data behind it yet (see "Extending to
other sports").

**Only recommends bookmakers you can actually bet at.** `config.USER_BOOKMAKERS`
restricts every tip's "best price" to accounts you hold — the fair-probability
consensus still uses every AU bookmaker The Odds API returns (broader = more
accurate), but it'll never tell you to bet somewhere you don't have an
account. **Bet365 and Dabble do not appear in The Odds API's data at all**
(confirmed by testing, not assumed) — this tool literally cannot price or
recommend bets at either. Run `python -m scripts.list_bookmakers` any time to
re-check which bookmakers are currently in the data and update the list in
`config.py` if your accounts change.

## How it finds "value"

Fair probability comes from a **sharp-reference chain**, tried in order per
market/outcome (`tips/generate_tips.py`):

1. **Pinnacle** (`config.SHARP_REFERENCE_BOOKMAKER`) — low-margin, used
   industry-wide as a "true odds" benchmark. The Odds API doesn't carry it
   under the `au` region, so `config.ODDS_API_REGION` also pulls `eu` (costs
   roughly double the API quota per call — see "API quota" below).
2. **Betfair Exchange** (`config.SECONDARY_SHARP_REFERENCE_BOOKMAKER`) — a
   two-sided market, sharper than a fixed-odds book, used when Pinnacle
   doesn't quote that exact market/point.
3. **AU bookmaker consensus** (`models/edge.consensus_fair_probs`) — every
   AU book's own price, devigged and averaged. Fallback only: a cluster of
   recreational books mostly copies each other, which converges tightly on
   itself without necessarily being accurate, so this is used only when
   neither sharp source covers the market.

Whichever source applies, the devig/comparison math is the same: strip the
bookmaker's margin (`models/devig.py`), and if one of *your* bookmakers'
prices beats that fair line, that's an edge — the same idea behind tools
like OddsJam/Outlier/your friend's "Lineboard". Works for spreads and totals
too, except the comparison only happens between books quoting the *same*
line (`odds_api.consensus_points`; a -4.5 price is never compared against a
-6.5 one).

**Elo model context** (NBA/EPL/NBL/AFL/NFL, supporting signal — never sizes
a stake). `models/elo.py` is a margin-of-victory Elo rating system (the
approach FiveThirtyEight popularised for NBA), trained on this season's
results via ESPN. Reported alongside the fair-probability numbers on h2h
tips as a sanity check — "model roughly agrees" or "model disagrees
sharply, treat as noise." It does **not** gate or size anything; an earlier
version of this code let a noisy single-season Elo number override the
market, which would've pushed max-size bets on model error. Elo doesn't
predict spread/total outcomes, so it's h2h-only.

### Odds range, Multis, and SGM

`config.DEFAULT_MIN_SINGLE_ODDS`/`DEFAULT_MAX_SINGLE_ODDS` (1.6-2.2,
Settings-configurable) is a hard range on the dashboard's main "Value
Bets"/"Predictions" views — not a soft discount zone. Predictions are
filtered to this range at generation time (no point surfacing a 1.24-odds
"lean"); value tips are still found/stored outside it regardless (useful
for Performance history and as multi-leg candidates — multis/SGM have no
minimum leg odds, so a short leg below the range can still combine into a
sensible-priced multi) but are tucked into a collapsed "not shown by
default" expander instead of the normal feed.

`models/multis.py` has two combo builders, both under the same two hard
rules: every leg must be at the **same bookmaker** (a multi is one bet slip
at one app — combining the best-priced leg from Sportsbet with another from
Betfair isn't a bet you can actually place), and the **combined** price is
capped at `MAX_COMBINED_ODDS` (2.2, same ceiling as a single), with each
leg individually capped at `MAX_LEG_ODDS` (2.2). An earlier version let
three longshots compound into ~400-to-1 combined odds at a ~4% hit chance —
technically +EV, not a sensible recommendation.

- `suggest_multis` — cross-game, legs from *different* matches. Multiplying
  independent probabilities together is mathematically correct here.
- `suggest_sgms` — same-game, legs from *different markets* (Win/Handicap/
  Line) within one match. Marked `approximate: True` on every result: those
  legs are correlated (a team winning and the game going under both lean
  the same way), so the combined edge/hit-chance is a rough estimate, not a
  verified number like the cross-game multis. Shipped anyway, deliberately,
  on the basis that an approximate "here's the combo I'd lean towards" is
  still a useful tip as long as it's labelled as an estimate rather than a
  verified edge — real bookmaker-priced SGM odds aren't available through
  this data source (checked — `sgm` isn't a valid market on The Odds API).

Because both caps are strict, some days turn up very few or zero
multis/SGMs — that's the caps working as intended, not a bug to loosen
without asking. Each combo gets a flat, conservative suggested stake too
(`MULTI_UNITS` 0.5, `SGM_UNITS` 0.25 — smaller than a single since it only
pays if every leg lands, smaller again for the correlated/approximate SGMs).

### Predictions (`tip_type="prediction"`)

A separate, explicitly lower-confidence tip category: the Elo model's own
lean on a game, shown even when no cross-bookmaker price discrepancy was
found. Different claim than a value tip and never mixed into its count,
sizing, or the min-edge threshold — flat `PREDICTION_UNITS` stake, its own
`tip_type`, reasoning that says plainly "no confirmed market edge."
Gated to `PREDICTION_ELIGIBLE_SPORTS` (nba/nbl/nfl/afl) because the Elo
model only ever splits probability between home/away — for a 3-way sport
like EPL (draws are ~25% of results) that would overstate both teams' true
win probability by ignoring the draw entirely, so EPL doesn't get this
until the model is draw-aware. Also requires `model_prob >= 0.55` and
`model_prob >= implied_prob` of the price (the model has to actually think
the price is fair-or-better, not just be split 51/49), and respects the
same `min_single_odds`/`max_single_odds` range as everything else. Best
`MAX_PREDICTIONS_PER_SPORT` (4) shown per sport in the dashboard by model
confidence — a model can clear the bar on a dozen games in one slate, which
isn't "a few bets a day" any more — the rest aren't dropped, just tucked
into an expander.

### Line Movement (`line_history` table, This Week tab)

Catching a line before it moves (e.g. a total posted at 192.5 drifting to
190.5 a few days later) is a different, legitimate technique from the
devig/sharp-reference comparison everything else here uses -- and it needs
genuine historical odds. The Odds API's historical endpoint does exactly
this but is paid-tier only (confirmed by testing against this account's
free key, not assumed: `HISTORICAL_UNAVAILABLE_ON_FREE_USAGE_PLAN`). Rather
than pay for it or fake the signal, `run()` now records every outcome's
fair probability (and point, for Line/Handicap) to `line_history` on every
pipeline run, tip-worthy or not. `db.get_moved_lines()` finds any line with
snapshots spanning 2+ distinct calendar days and reports first-seen vs
latest. This is a cold start: empty for the first week or two after this
shipped, by design -- there's no backfill, just forward accumulation.
Verified the query logic works correctly by manually inserting a synthetic
older snapshot and confirming it's picked up and rendered right (see git
history around when this landed) -- the only thing actually missing right
now is time for real data to accumulate.

### API quota

Billing is `markets × regions`, and every call now requests `au,eu`
(for Pinnacle) regardless of whether Pinnacle ends up covering that
market — so 3 markets x 2 regions = 6 credits per sport per refresh, up
from 3 before. With NBA/EPL/NFL auto-refreshing daily: 18 credits/day ≈
540/month, which is **over** the free tier's 500/month if run every single
day without fail. Drop a sport from `auto_refresh` in `config.SPORT_CONFIG`,
or run less than daily, if you're hitting the limit -- or upgrade to a paid
Odds API tier if you want to auto-refresh more sports/more often.

Every value tip gets two independent labels:

- **Edge tier** (`models/edge.tier_label`) — Strong / Moderate / Lean, how
  big the devig edge is.
- **Risk** (`models/risk.risk_label`) — Not Risky / Neutral / Risky, based on
  how likely the pick is to actually win (favourite vs underdog), regardless
  of edge size.

Each sport tab groups bets by risk — so you can see "safe anchors" vs
"risky longshots" the way a tipping app typically lays it out. Stake sizing
is a fixed unit count per edge tier (Strong/Moderate/Lean → configurable
units, default 2.5/1.75/1.0), capped by a hard max-units-per-bet setting —
rounder, more intuitive numbers than a continuously-scaled Kelly fraction,
while still scaling with how confident the edge is.
`models/kelly.py`'s actual Kelly-criterion math still exists as a tested
utility if you want to switch back to it, just isn't wired into the default
pipeline.

**Dates are AU-local, not UTC.** Both The Odds API's `commence_time` and
ESPN's `date` are UTC; `_local_game_date`/`_local_date` convert to
`Australia/Sydney` before taking the calendar date. Found this the hard
way: AEDT is UTC+11, so a game late UTC evening is already "tomorrow" in
Sydney -- taking the UTC date directly filed some games under yesterday,
which made them silently vanish from the dashboard's "today onwards"
window right after local midnight. Same conversion applies to historical
results, so back-to-back/rest-day detection uses the right calendar day too.

## "Ball knowledge" tips (NBA, EPL, NBL, AFL)

`knowledge/situational.py` is a separate rules engine, not tied to a priced
edge. From schedule + results alone it flags things a sharp bettor would
notice by hand: back-to-backs, rest advantages, win/loss streaks, and teams
that are unusually bad at home or strong on the road. These get attached as
extra reasoning on Value/Prediction tips, **and** always get their own
standalone Ball Knowledge card too -- an earlier version skipped the
standalone card whenever the game already had a priced tip, which made the
tab look far sparser than the actual situational-note coverage really was.
Needs a results-history
source to work from -- `data/espn.py` (ESPN's public site API, no key
needed) covers NBA/EPL/NBL/AFL (see `config.SPORT_CONFIG`'s `has_history`
flag). NRL isn't on ESPN's public API (checked, 404s); cricket uses a
different ESPN API shape not wired up yet. It's deliberately simple in v1 —
the obvious next upgrade is wiring in an injury-news feed.

## Bonus Converter (matched betting)

A separate tab, unrelated to the value-tip pipeline: turns bonus/free bet
money into cash. Back the bonus bet at the bookmaker offering it (e.g.
Ladbrokes), lay the opposite outcome on an exchange (Betfair) at odds you
enter by hand, and `models/matched_betting.py` works out the lay stake that
equalizes both outcomes — so you end up with close to the same result either
way instead of just betting the bonus outright and hoping. Handles both
qualifying bets (your own money, typically a small cost to unlock the real
bonus) and free/SNR bets (stake not returned, where you're extracting cash
from the bonus itself — usually 60-80% of it depending on the odds and
exchange commission). Pure math, no live odds pulled — you type in the two
prices from your own apps.

## Setup

```bash
cd ~/sports-betting-model
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

You need one free API key — sign up yourself (not something I can do for
you), then paste it into `.env`:

- **The Odds API** — https://the-odds-api.com — free tier is 500 requests/
  month. This is what gives you real AU bookmaker odds (Sportsbet, TAB,
  Neds, Ladbrokes, Unibet, Betfair, etc.) to compare, for every sport.

That's it for keys. Results history (for Elo + ball-knowledge notes) comes
from ESPN's public site API (`data/espn.py`) — no signup, no key.

Then run the dashboard:

```bash
streamlit run dashboard/app.py
```

Open http://localhost:8501 — NBA/EPL/NFL refresh themselves automatically
(once a day, on first load). For other sports, open that sport's tab and hit
**Refresh**.

To run it without the dashboard (e.g. for a cron job):

```bash
python3 -m scripts.run_daily nba
python3 -m scripts.run_daily epl
```

See [DEPLOY.md](DEPLOY.md) for getting this hosted so you can check it from
your phone.

## Using it day to day

1. **This Week** — best picks across every sport for the next 7 days, not
   just today/tonight, all within the odds cap (2.2 default). A 🔥 Big
   Value Alert when something clears a high bar (3%+ edge), a curated
   5-pick Recommended list prioritising Line/Handicap, same-bookmaker
   cross-game Multis (2-leg front and centre, 3-leg tucked in an expander),
   then everything else (including above-cap picks) collapsed below.
2. **One tab per sport** (NBA, EPL, NBL, NFL, NRL, AFL, BBL) — that sport's
   Value Bets (pick, bookmaker, price, edge, units, risk) and Ball Knowledge
   notes together, looking ~2 weeks ahead, with a manual Refresh button.
3. **Horses** — placeholder for now, see "Extending to other sports."
4. **Bonus Converter** — see above.
5. **Bet Tracker** — log what you actually bet (can link to a tip or enter
   manually), then mark it Win/Loss/Push once it settles.
6. **Performance** — win rate, ROI, cumulative units over time, broken down
   by sport.
7. **Settings** — bankroll, unit size, per-tier unit counts, max units per
   bet (default 3), minimum edge threshold to bother flagging a tip.

## Extending to other sports

The pattern in `tips/generate_tips.py`: pull finished games (if the sport
has a history source) → train Elo → pull live odds for every configured
market in one call → devig + compare at matching lines → write tips. To add
a new sport, add an entry to `config.SPORT_CONFIG`:

```python
"nfl": {"odds_key": "americanfootball_nfl", "has_history": False, "markets": ["h2h", "spreads", "totals"]},
```

That alone gets you devig value tips with zero new accounts (The Odds API
covers most sports). To get Elo context + ball-knowledge notes too, check if
ESPN's public site API covers it first (`data/espn.py` -- try
`site.api.espn.com/apis/site/v2/sports/<espn_sport>/<espn_league>/teams` for
the sport/league slug, same pattern as NBA/EPL/NBL/AFL), add
`espn_sport`/`espn_league` to the config entry, work out that league's
season-param convention (see `_espn_season_years` in
`tips/generate_tips.py` -- it differs per league, verify empirically, don't
guess), and set `has_history: True`. NFL is almost certainly on ESPN's API
(`espn_sport="football"`, `espn_league="nfl"`) but untested here.

Australian horse racing is a different shape of problem — no Elo-style team
rating, instead form/speed-figure based probability per runner, and data
typically comes from the Betfair Exchange API or Racing Australia / Punting
Form rather than The Odds API. Worth treating as its own module
(`models/horses.py` + a dedicated data client) rather than forcing it through
the team-sport pipeline.

## Responsible gambling

This is decision support, not a guarantee — "value" here means positive
expected value over a long run of similarly-priced bets, not that any single
bet will win. The Elo model is especially unreliable early in a season.
Only bet what you can afford to lose, keep the unit size honest relative to
a bankroll you've actually set aside for this, and don't chase losses by
overriding the suggested stake. If betting stops being fun or you're
struggling to stick to your own limits, Gambling Help Online
(https://www.gamblinghelponline.org.au, 1800 858 858) is free and
confidential.
