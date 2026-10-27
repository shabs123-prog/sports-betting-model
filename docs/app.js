const DATA_URL = "./data.json";
const POLL_MS = 60000;
const MAX_PREDICTIONS_PER_SPORT = 4;
const SPORT_ORDER = ["nba", "epl", "nbl", "nfl", "nrl", "afl", "bbl"];

// Per-sport "refresh now" button -- calls a small Cloudflare Worker proxy
// (see cloudflare/refresh-proxy.js) which holds the real GitHub token
// server-side and triggers daily-refresh.yml's workflow_dispatch with
// this sport. Never calls GitHub directly from here -- a token that could
// do that would be sitting in public, readable JS, which is exactly what
// the proxy exists to avoid. REFRESH_PROXY_SECRET is a much lower-stakes
// shared secret (worst case if someone else finds it: they can trigger
// extra refreshes and burn a few quota credits, not touch the repo or
// account) -- still worth treating as not-quite-public, but the blast
// radius is bounded on purpose. Both blank until the Worker is deployed;
// the button below hides itself until they're filled in.
const REFRESH_PROXY_URL = "";
const REFRESH_PROXY_SECRET = "";

let state = { data: null };
let editingTipKey = null; // which card (by tipKey) currently has its log-bet form open

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function fmtOdds(v) {
  return v === null || v === undefined ? "-" : Number(v).toFixed(2).replace(/\.00$/, "");
}

function fmtUnits(v) {
  return v === null || v === undefined ? "0" : Number(v).toFixed(2).replace(/\.00$/, "").replace(/0$/, "").replace(/\.$/, "");
}

function fmtPct(v) {
  return v === null || v === undefined ? "" : (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
}

function fmtMoney(v, signed) {
  const sign = v < 0 ? "-" : signed ? "+" : "";
  return `${sign}$${Math.abs(v).toFixed(2)}`;
}

function fmtWhen(t) {
  if (!t || !t.game_date) return "";
  const parts = String(t.game_date).split("-");
  if (parts.length !== 3) return t.game_date;
  const [y, m, d] = parts;
  const dateStr = `${d}/${m}/${y.slice(2)}`;
  return t.game_time_local ? `${dateStr}, ${t.game_time_local}` : dateStr;
}

function marketLabel(m) {
  return (state.data.market_label && state.data.market_label[m]) || m;
}

function riskClass(risk) {
  if (risk === "Not Risky") return "risk-Not-Risky";
  if (risk === "Neutral") return "risk-Neutral";
  if (risk === "Risky") return "risk-Risky";
  return "";
}

// Risk (win probability band) and edge tier (size of the mispricing) are
// separate signals -- a pick can be "Strong" edge and "Risky" at once (a
// real mispriced longshot), or "Lean" edge and "Not Risky" (barely-there
// value on a heavy favourite). Risk alone answers "how likely is this to
// land," not "how good a bet is this" -- tier is the one that actually
// answers that, so it gets its own badge too. Mirrors models/edge.py's
// EDGE_TIERS exactly.
function tierLabel(edgePct) {
  if (edgePct === null || edgePct === undefined) return null;
  if (edgePct >= 0.03) return "Strong";
  if (edgePct >= 0.015) return "Moderate";
  return "Lean";
}
function tierClass(tier) {
  if (tier === "Strong") return "risk-Not-Risky";
  if (tier === "Moderate") return "risk-Neutral";
  return "tier-Lean";
}

// Mirrors models/edge.py's COMBINED_RATING exactly -- edge tier is the
// dominant axis (the actual +EV signal), risk is a secondary variance
// flag baked into the label text itself.
const COMBINED_RATING = {
  "Strong|Not Risky": "Elite",
  "Strong|Neutral": "Strong Value",
  "Strong|Risky": "High-Upside Swing",
  "Moderate|Not Risky": "Safe Value",
  "Moderate|Neutral": "Good Value",
  "Moderate|Risky": "Speculative Value",
  "Lean|Not Risky": "Safe Lean",
  "Lean|Neutral": "Lean Value",
  "Lean|Risky": "Longshot Lean",
};
function combinedRating(tier, risk) {
  return COMBINED_RATING[`${tier}|${risk}`] || `${tier} / ${risk}`;
}

function tipCard(t) {
  const hasPrice = t.bookmaker && t.odds_decimal;
  const tier = tierLabel(t.edge_pct);
  const riskEdge = t.risk
    ? `<span class="${tierClass(tier)}">${combinedRating(tier, t.risk)}</span>` +
      (t.edge_pct !== null && t.edge_pct !== undefined ? ` · ${fmtPct(t.edge_pct)}` : "")
    : (t.model_prob !== null && t.model_prob !== undefined ? `Model confidence: <b>${(t.model_prob * 100).toFixed(1)}%</b>` : "");
  return `
    <div class="card">
      <div class="matchup">${escapeHtml(t.matchup)}</div>
      <div class="date">${escapeHtml(fmtWhen(t))}</div>
      ${hasPrice ? `<div class="pick-line"><b>${escapeHtml(marketLabel(t.market))}</b> · ${escapeHtml(t.selection)} @ <b>${fmtOdds(t.odds_decimal)}</b> · ${escapeHtml(t.bookmaker)}</div>` : ""}
      <div class="meta-row">
        <div><div class="units">${fmtUnits(t.suggested_units)}</div><div class="units-label">Units</div></div>
        <div class="risk-edge">${riskEdge}</div>
      </div>
      ${t.reasoning ? `<div class="reasoning">${escapeHtml(t.reasoning)}</div>` : ""}
      ${hasPrice ? `<div class="card-actions">${logBetButton(t)}</div>` : ""}
    </div>`;
}

function knowledgeCard(t) {
  const linked = t.bookmaker && t.odds_decimal
    ? `<div class="linked-bet">Linked bet: <b>${escapeHtml(marketLabel(t.market))}</b> · ${escapeHtml(t.selection)} @ <b>${fmtOdds(t.odds_decimal)}</b> · ${escapeHtml(t.bookmaker)} · ${fmtUnits(t.suggested_units)}u</div>
       <div class="card-actions">${logBetButton(t)}</div>`
    : "";
  return `
    <div class="card">
      <div class="matchup">${escapeHtml(t.matchup)}</div>
      <div class="date">${escapeHtml(fmtWhen(t))}</div>
      <div class="reasoning" style="margin-top:8px;">${escapeHtml(t.reasoning)}</div>
      ${linked}
    </div>`;
}

function comboCard(c) {
  const tag = c.approximate ? " · approximate (correlated legs)" : "";
  const legs = c.legs.map(
    (l) => `<div class="combo-leg">${escapeHtml(l.sport.toUpperCase())}: ${escapeHtml(l.matchup)} — ${escapeHtml(marketLabel(l.market))} ${escapeHtml(l.selection)} @ ${fmtOdds(l.odds_decimal)}</div>`
  ).join("");
  return `
    <div class="card">
      <div class="matchup">${c.size}-leg · ${escapeHtml(c.bookmaker)} · combined odds ${fmtOdds(c.combined_odds)}${tag}</div>
      ${legs}
      <div class="combo-metrics">
        <div><span class="v">${fmtUnits(c.suggested_units)}</span><span class="l">Units</span></div>
        <div><span class="v">${fmtPct(c.combined_edge_pct)}</span><span class="l">Combined edge</span></div>
        <div><span class="v">${(c.implied_hit_pct * 100).toFixed(1)}%</span><span class="l">Hit chance</span></div>
      </div>
    </div>`;
}

function expander(label, bodyHtml) {
  return `<details class="expander"><summary>${escapeHtml(label)}</summary><div class="expander-body">${bodyHtml}</div></details>`;
}

function comboGroup(combos) {
  if (!combos || combos.length === 0) {
    return `<div class="muted-note">Not enough eligible picks this week to build one.</div>`;
  }
  const twoLeg = combos.filter((c) => c.size === 2).slice(0, 4);
  const threeLeg = combos.filter((c) => c.size === 3).slice(0, 3);
  let html = twoLeg.map(comboCard).join("");
  if (threeLeg.length) {
    html += expander(`3-leg — noticeably higher variance (${threeLeg.length})`, threeLeg.map(comboCard).join(""));
  }
  return html;
}

function renderThisWeek() {
  const tw = state.data.this_week;
  const [lo, hi] = state.data.odds_range;

  // Must-Bet is the single most important thing on this screen -- goes
  // first, in a loud banner (not just a heading) so it can't be mistaken
  // for just another section.
  let html = `<div class="must-bet-banner">
    <div class="must-bet-title">🔒 MUST-BET THIS WEEK (${tw.must_bet.length})</div>
    <div class="must-bet-sub">Moderate-or-better edge, Not Risky/Neutral win chance, within ${lo}-${hi} odds — if you only place a few bets this week, place these.</div>
  </div>`;
  html += tw.must_bet.length
    ? tw.must_bet.map(tipCard).join("")
    : `<div class="info-box">Nothing clears the Must-Bet bar this week — see Recommended below.</div>`;

  html += `<section>
    <h2>Recommended bets this week (${tw.recommended.length})</h2>
    <p class="caption">All within ${lo}-${hi} odds, prioritising Line/Handicap.</p>`;
  html += tw.recommended.length ? tw.recommended.map(tipCard).join("") : `<div class="muted-note">No value bets for this view right now.</div>`;
  html += `</section>`;

  html += `<section>
    <h2>This week's model leans (${tw.model_leans.length})</h2>
    <p class="caption">No confirmed market edge — the model just favours this side over the price. Lower confidence than Must-Bet/Recommended above; extra volume, not extra certainty.</p>`;
  html += tw.model_leans.length ? tw.model_leans.map(tipCard).join("") : `<div class="muted-note">No model leans for this view right now.</div>`;
  html += `</section>`;

  // Multis/SGM only show up when there's actually something to show --
  // most weeks there isn't enough eligible picks to build one, and a
  // section that just says "not enough picks" every time is clutter, not
  // information.
  if (tw.multis.length) {
    html += `<section><h2>Multis (cross-game)</h2>
      <p class="caption">Combines several of this week's +EV singles from different games, all at the same bookmaker. Combined odds capped at 2.2.</p>
      ${comboGroup(tw.multis)}</section>`;
  }
  if (tw.sgms.length) {
    html += `<section><h2>Same-Game Multis (SGM)</h2>
      <p class="caption">Different markets, same game, same bookmaker. Marked approximate — legs are correlated, not independently verified.</p>
      ${comboGroup(tw.sgms)}</section>`;
  }

  return html;
}

// Fire-and-forget: tells the proxy to kick off the GitHub Action for this
// sport, then leaves it running -- the workflow itself still takes about
// a minute to pull odds and push, so this can only confirm "started",
// not "done". Separate from the existing header ↻ button, which just
// reloads whatever data.json already says; this one asks for NEW data
// first.
async function triggerRefresh(sport, buttonEl, statusEl) {
  buttonEl.disabled = true;
  statusEl.textContent = "Starting refresh…";
  try {
    const url = `${REFRESH_PROXY_URL}?sport=${encodeURIComponent(sport)}&key=${encodeURIComponent(REFRESH_PROXY_SECRET)}`;
    const resp = await fetch(url);
    const body = await resp.json().catch(() => ({}));
    if (resp.ok && body.ok) {
      statusEl.textContent = "Started — takes about a minute, then tap ↻ above to load it.";
    } else {
      statusEl.textContent = `Couldn't start it (${body.error || resp.status}).`;
    }
  } catch (e) {
    statusEl.textContent = "Couldn't reach the refresh service.";
  }
  buttonEl.disabled = false;
}

function refreshSportButton(sport) {
  if (!REFRESH_PROXY_URL) return ""; // hidden until the Worker is deployed and this is filled in
  return `
    <div class="refresh-sport">
      <button class="refresh-sport-btn" data-refresh-sport="${sport}">↻ Refresh ${sport.toUpperCase()} odds now</button>
      <div class="refresh-sport-status" data-refresh-status="${sport}"></div>
    </div>`;
}

function renderSport(sport) {
  const s = state.data.sports[sport];
  const [lo, hi] = state.data.odds_range;
  let html = refreshSportButton(sport);
  if (!s.has_history) {
    html += `<p class="caption">No results-history source wired up for this sport yet — devig value bets only, no Elo context or ball-knowledge notes.</p>`;
  }
  if (!s.auto_refresh) {
    html += `<p class="caption">Not auto-refreshed daily (off-season or markets not posted yet).</p>`;
  }

  html += `<section><h2>Value Bets</h2>`;
  html += s.value_in_range.length
    ? s.value_in_range.map(tipCard).join("")
    : `<div class="muted-note">No value bets in the ${lo}-${hi} odds range for this view right now.</div>`;
  html += `</section>`;

  if (s.has_history) {
    html += `<section><h2>Predictions</h2><p class="caption">Model leans with no confirmed market edge — lower confidence than a Value Bet.</p>`;
    if (!s.predictions.length) {
      html += `<div class="muted-note">No model predictions for this view right now.</div>`;
    } else {
      const shown = s.predictions.slice(0, MAX_PREDICTIONS_PER_SPORT);
      const rest = s.predictions.slice(MAX_PREDICTIONS_PER_SPORT);
      html += shown.map(tipCard).join("");
      if (rest.length) html += expander(`${rest.length} more prediction(s)`, rest.map(tipCard).join(""));
    }
    html += `</section>`;

    if (s.show_knowledge) {
      html += `<section><h2>Ball Knowledge</h2>`;
      html += s.knowledge.length ? s.knowledge.map(knowledgeCard).join("") : `<div class="muted-note">No situational notes for this view right now.</div>`;
      html += `</section>`;
    }
  }

  return html || `<div class="muted-note">Nothing here right now.</div>`;
}

function renderHorses() {
  return `<p class="caption">Not wired up to live data yet. Horse racing needs a different data source (Betfair Exchange or Racing Australia, not The Odds API) and a different model — per-runner form/speed-figure probability, not team-vs-team Elo.</p>`;
}

// ------------------------------------------------------------ My Bets
// Lives entirely in this browser's localStorage -- no backend, no account,
// free. Tradeoff: bets logged on your phone stay on your phone (nothing
// synced to another device, and clearing site data wipes them), which is
// fine for one person on one device but worth knowing before relying on it.
const BETS_KEY = "vb_bets_v1";

function loadBets() {
  try {
    return JSON.parse(localStorage.getItem(BETS_KEY) || "[]");
  } catch (e) {
    return [];
  }
}

function saveBets(bets) {
  try {
    localStorage.setItem(BETS_KEY, JSON.stringify(bets));
  } catch (e) {
    // Storage unavailable (private browsing, full, disabled) -- logging
    // just won't persist past this page view rather than throwing.
  }
}

function tipKey(t) {
  return [t.sport, t.matchup, t.market, t.selection, t.bookmaker].join("|");
}

function findPendingBetForTip(t) {
  const key = tipKey(t);
  return loadBets().find((b) => b.key === key && b.result === "pending");
}

// base64 round-trip (not raw JSON) so the encoded tip can sit safely inside
// an HTML attribute without fighting quote-escaping.
function encodeTip(t) {
  return btoa(unescape(encodeURIComponent(JSON.stringify(t))));
}
function decodeTip(encoded) {
  return JSON.parse(decodeURIComponent(escape(atob(encoded))));
}

function logBetFromTip(t, overrides) {
  const o = overrides || {};
  const bets = loadBets();
  bets.unshift({
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 7),
    ts: new Date().toISOString(),
    key: tipKey(t),
    sport: t.sport,
    matchup: t.matchup,
    market: t.market,
    selection: t.selection,
    bookmaker: o.bookmaker || t.bookmaker,
    odds_decimal: o.odds_decimal || t.odds_decimal,
    units: o.units || t.suggested_units || 1,
    notes: o.notes || "",
    result: "pending",
    // Captured at log time (not recomputed later) so the By Tier
    // breakdown reflects what the tip actually looked like when you bet
    // it, same as odds/units -- a tier is a point-in-time call tied to
    // that edge_pct, not something that should drift with later code
    // changes to tier thresholds.
    tier: tierLabel(t.edge_pct),
  });
  saveBets(bets);
}

function settleBet(id, result, autoGraded) {
  const bets = loadBets();
  const bet = bets.find((b) => b.id === id);
  if (!bet) return;
  bet.result = result;
  if (result === "win") bet.profit_units = bet.units * (bet.odds_decimal - 1);
  else if (result === "loss") bet.profit_units = -bet.units;
  else bet.profit_units = 0;
  bet.auto_graded = !!autoGraded;
  saveBets(bets);
}

function revertBetToPending(id) {
  const bets = loadBets();
  const bet = bets.find((b) => b.id === id);
  if (!bet) return;
  bet.result = "pending";
  bet.profit_units = undefined;
  bet.auto_graded = false;
  saveBets(bets);
}

function deleteBet(id) {
  saveBets(loadBets().filter((b) => b.id !== id));
}

// ---- Auto-grading: match a pending bet against free ESPN results
// (state.data.results, built server-side in pwa/build_site.py -- no API
// quota involved) and settle it automatically. Only grades when the
// matchup string matches exactly and the market/selection parse cleanly;
// anything ambiguous is left pending for the manual Win/Loss/Push buttons
// rather than risk a wrong auto-grade.
function parseSelection(selection) {
  const m = selection.match(/^(.*)\s([+-]?\d+(?:\.\d+)?)$/);
  if (m) return { name: m[1], point: parseFloat(m[2]) };
  return { name: selection, point: null };
}

function gradeBet(bet, result) {
  const { name, point } = parseSelection(bet.selection);
  const isHome = name === result.home_team;
  const isAway = name === result.away_team;

  if (bet.market === "h2h") {
    if (name.toLowerCase() === "draw") {
      return result.home_score === result.away_score ? "push" : "loss";
    }
    if (!isHome && !isAway) return null;
    const mine = isHome ? result.home_score : result.away_score;
    const theirs = isHome ? result.away_score : result.home_score;
    if (mine === theirs) return "push";
    return mine > theirs ? "win" : "loss";
  }

  if (bet.market === "spreads") {
    if ((!isHome && !isAway) || point === null) return null;
    const mine = isHome ? result.home_score : result.away_score;
    const theirs = isHome ? result.away_score : result.home_score;
    const margin = mine - theirs + point;
    if (margin === 0) return "push";
    return margin > 0 ? "win" : "loss";
  }

  if (bet.market === "totals") {
    if (point === null) return null;
    const total = result.home_score + result.away_score;
    if (total === point) return "push";
    const side = name.toLowerCase();
    if (side === "over") return total > point ? "win" : "loss";
    if (side === "under") return total < point ? "win" : "loss";
    return null;
  }

  if (bet.market === "btts") {
    const bothScored = result.home_score > 0 && result.away_score > 0;
    const wantsYes = name.toLowerCase() === "yes";
    return wantsYes === bothScored ? "win" : "loss";
  }

  return null;
}

function autoGradeBets() {
  const resultsBySport = (state.data && state.data.results) || {};
  const bets = loadBets();
  let changed = false;
  for (const bet of bets) {
    if (bet.result !== "pending") continue;
    const sportResults = resultsBySport[bet.sport] || [];
    const match = sportResults.find((r) => r.matchup === bet.matchup);
    if (!match) continue;
    const outcome = gradeBet(bet, match);
    if (!outcome) continue;
    bet.result = outcome;
    if (outcome === "win") bet.profit_units = bet.units * (bet.odds_decimal - 1);
    else if (outcome === "loss") bet.profit_units = -bet.units;
    else bet.profit_units = 0;
    bet.auto_graded = true;
    changed = true;
  }
  if (changed) saveBets(bets);
}

function bookmakerOptions(selected) {
  const books = (state.data && state.data.user_bookmakers) || [selected];
  const list = books.includes(selected) ? books : [selected, ...books];
  return list.map((b) => `<option value="${escapeHtml(b)}" ${b === selected ? "selected" : ""}>${escapeHtml(b)}</option>`).join("");
}

function logBetButton(t) {
  if (!(t.bookmaker && t.odds_decimal)) return "";
  if (findPendingBetForTip(t)) {
    return `<button class="log-btn logged" disabled>✓ Logged</button>`;
  }
  const key = tipKey(t);
  if (editingTipKey === key) {
    return `
      <div class="log-form" data-tip="${encodeTip(t)}">
        <label class="form-label">Odds you got</label>
        <input type="number" class="form-input log-odds-input" value="${t.odds_decimal}" step="0.01" min="1.01">
        <label class="form-label">Units</label>
        <input type="number" class="form-input log-units-input" value="${t.suggested_units || 1}" step="0.25" min="0.25">
        <label class="form-label">App / bookmaker</label>
        <select class="form-input log-bookmaker-input">${bookmakerOptions(t.bookmaker)}</select>
        <label class="form-label">Notes (optional)</label>
        <input type="text" class="form-input log-notes-input" placeholder="Why you took this, anything you want to remember">
        <button class="log-btn" data-confirm-log="1">Confirm</button>
      </div>`;
  }
  return `<button class="log-btn" data-edit-log="${encodeTip(t)}">+ Log bet</button>`;
}

function betCard(b) {
  const actions = b.result === "pending"
    ? `<div class="bet-actions">
        <button class="bet-btn win" data-settle="${b.id}" data-result="win">Win</button>
        <button class="bet-btn loss" data-settle="${b.id}" data-result="loss">Loss</button>
        <button class="bet-btn push" data-settle="${b.id}" data-result="push">Push</button>
      </div>`
    : `<div class="bet-settled-row">
        <div class="bet-result-tag ${escapeHtml(b.result)}">${escapeHtml(b.result.toUpperCase())}${
          b.profit_units !== undefined ? ` · ${b.profit_units >= 0 ? "+" : ""}${b.profit_units.toFixed(2)}u` : ""
        }${b.auto_graded ? ` · auto` : ""}</div>
        <button class="edit-result-btn" data-revert="${b.id}">Change</button>
      </div>`;
  return `
    <div class="card">
      <button class="delete-bet" data-delete-bet="${b.id}" aria-label="Delete">×</button>
      <div class="matchup">${escapeHtml(b.sport.toUpperCase())}: ${escapeHtml(b.matchup)}</div>
      <div class="pick-line"><b>${escapeHtml(marketLabel(b.market))}</b> · ${escapeHtml(b.selection)} @ <b>${fmtOdds(b.odds_decimal)}</b> · ${escapeHtml(b.bookmaker)} · ${fmtUnits(b.units)}u</div>
      ${b.notes ? `<div class="reasoning">"${escapeHtml(b.notes)}"</div>` : ""}
      ${actions}
    </div>`;
}

// Breaks settled bets down by the tier they were logged at (captured on
// the bet itself at log time, from the tip's edge_pct -- see
// logBetFromTip) so you can actually see whether a tier's sizing matches
// its real results, not just one all-time net-units number that hides
// whether e.g. Lean bets are dragging down otherwise-good Strong picks.
// Bets logged before this field existed have no tier stored -- grouped
// under "Unrated" rather than silently dropped from the breakdown.
const TIER_ORDER = ["Strong", "Moderate", "Lean", "Unrated"];

function renderTierBreakdown(settled) {
  if (!settled.length) return "";
  const byTier = {};
  for (const b of settled) {
    const tier = b.tier || "Unrated";
    (byTier[tier] = byTier[tier] || []).push(b);
  }
  const rows = TIER_ORDER.filter((t) => byTier[t] && byTier[t].length)
    .map((tier) => {
      const tierBets = byTier[tier];
      const w = tierBets.filter((b) => b.result === "win").length;
      const net = tierBets.reduce((s, b) => s + (b.profit_units || 0), 0);
      const wr = ((w / tierBets.length) * 100).toFixed(0);
      const cls = tier === "Strong" ? "risk-Not-Risky" : tier === "Moderate" ? "risk-Neutral" : "tier-Lean";
      return `<div class="tier-row">
        <span class="${cls} tier-row-label">${tier}</span>
        <span class="tier-row-stats">${tierBets.length} bet${tierBets.length === 1 ? "" : "s"} · ${wr}% win · ${net >= 0 ? "+" : ""}${net.toFixed(2)}u</span>
      </div>`;
    })
    .join("");
  return `<details class="expander" style="margin-bottom:16px;">
    <summary>By tier</summary>
    <div class="expander-body">${rows}</div>
  </details>`;
}

function renderMyBets() {
  autoGradeBets();
  const bets = loadBets();
  let html = `<p class="caption">Lives only on this device — tap "+ Log bet" on any tip to add it here, nothing synced anywhere. Settles itself once a game finishes (marked "auto") using free public results — if a bet doesn't auto-settle (e.g. it's not matched yet), use Win/Loss/Push yourself.</p>`;

  if (!bets.length) {
    html += `<div class="muted-note">No bets logged yet.</div>`;
    return html;
  }

  const pending = bets.filter((b) => b.result === "pending");
  const settled = bets.filter((b) => b.result !== "pending");
  const netUnits = settled.reduce((sum, b) => sum + (b.profit_units || 0), 0);
  const totalStaked = settled.reduce((sum, b) => sum + (b.units || 0), 0);
  const roiPct = totalStaked ? (netUnits / totalStaked) * 100 : 0;
  const wins = settled.filter((b) => b.result === "win").length;
  const winRate = settled.length ? ((wins / settled.length) * 100).toFixed(1) : "0.0";

  html += `<div class="bets-summary">
    <div><span class="v">${settled.length}</span><span class="l">Settled</span></div>
    <div><span class="v">${winRate}%</span><span class="l">Win rate</span></div>
    <div><span class="v">${netUnits >= 0 ? "+" : ""}${netUnits.toFixed(2)}u</span><span class="l">Net units</span></div>
  </div>`;
  if (totalStaked > 0) {
    html += `<div class="bets-summary">
      <div><span class="v">${roiPct >= 0 ? "+" : ""}${roiPct.toFixed(1)}%</span><span class="l">ROI (net ÷ staked)</span></div>
      <div><span class="v">${totalStaked.toFixed(2)}u</span><span class="l">Total staked</span></div>
    </div>`;
  }
  html += renderTierBreakdown(settled);

  if (pending.length) {
    html += `<section><h2>Pending (${pending.length})</h2>${pending.map(betCard).join("")}</section>`;
  }
  if (settled.length) {
    html += `<section><h2>Settled (${settled.length})</h2>${settled.map(betCard).join("")}</section>`;
  }
  return html;
}

// ------------------------------------------------------------ Bonus Converter
// Pure math, mirrors models/matched_betting.py exactly -- back the bonus bet
// at your bookmaker, lay the opposite outcome on an exchange (Betfair) to
// lock in close to the same result either way. Manual entry only: finding
// the best game automatically needs live odds (models/conversion_finder.py),
// which stays dashboard-only since this is a static site with no backend.
function bonusResult(stake, backOdds, layStake, layOdds, commission, snr) {
  const profitIfBackWins = stake * (backOdds - 1) - layStake * (layOdds - 1);
  const profitIfLayWins = (snr ? 0 : -stake) + layStake * (1 - commission);
  const worstCase = Math.min(profitIfBackWins, profitIfLayWins);
  return {
    lay_stake: layStake,
    profit_if_back_wins: profitIfBackWins,
    profit_if_back_loses: profitIfLayWins,
    worst_case_profit: worstCase,
    retention_pct: stake ? worstCase / stake : 0,
  };
}

function qualifyingBet(stake, backOdds, layOdds, commission) {
  const layStake = (stake * backOdds) / (layOdds - commission);
  return bonusResult(stake, backOdds, layStake, layOdds, commission, false);
}

function freeBet(stake, backOdds, layOdds, commission) {
  const layStake = (stake * (backOdds - 1)) / (layOdds - commission);
  return bonusResult(stake, backOdds, layStake, layOdds, commission, true);
}

function renderBonusCalc() {
  return `
    <p class="caption">Turn bonus/free bet money into cash: back the bonus bet at your bookmaker, then lay the opposite outcome on an exchange (Betfair) to lock in close to the same result either way. You place both legs yourself. Manual entry only — finding the best game automatically needs live odds, which stays on the dashboard.</p>
    <div class="card">
      <label class="form-label">Bet type</label>
      <select id="bc-type" class="form-input">
        <option value="free">Free / bonus bet (stake NOT returned)</option>
        <option value="qualifying">Qualifying bet (your own money, stake returned)</option>
      </select>

      <label class="form-label">Bonus / stake amount ($)</label>
      <input id="bc-stake" type="number" class="form-input" value="50" min="0" step="5">

      <label class="form-label">Betfair commission (%) — check your current rate, it varies</label>
      <input id="bc-commission" type="number" class="form-input" value="5" min="0" max="10" step="0.1">

      <label class="form-label">Back odds (at your bookmaker, decimal)</label>
      <input id="bc-back" type="number" class="form-input" value="4.0" min="1.01" step="0.05">

      <label class="form-label">Lay odds (on Betfair Exchange, decimal)</label>
      <input id="bc-lay" type="number" class="form-input" value="4.2" min="1.01" step="0.05">
    </div>
    <div id="bc-result"></div>
  `;
}

function recomputeBonusCalc() {
  const resultEl = document.getElementById("bc-result");
  if (!resultEl) return;
  const isFree = document.getElementById("bc-type").value === "free";
  const stake = parseFloat(document.getElementById("bc-stake").value) || 0;
  const commission = (parseFloat(document.getElementById("bc-commission").value) || 0) / 100;
  const backOdds = parseFloat(document.getElementById("bc-back").value) || 0;
  const layOdds = parseFloat(document.getElementById("bc-lay").value) || 0;

  if (!(stake > 0) || !(layOdds > commission) || !(backOdds > 1)) {
    resultEl.innerHTML = `<div class="info-box">Lay odds must be greater than the commission rate, and stake/back odds must be positive, for this to be calculable.</div>`;
    return;
  }

  const r = isFree ? freeBet(stake, backOdds, layOdds, commission) : qualifyingBet(stake, backOdds, layOdds, commission);
  const noteLabel = isFree ? "Retained" : "Cost to qualify";
  const note = isFree
    ? `Guaranteed outcome regardless of result: ${fmtMoney(r.worst_case_profit)} cash from a $${stake.toFixed(0)} bonus bet (${(r.retention_pct * 100).toFixed(0)}% retained).`
    : "Cost (or small gain) of meeting a qualifying-bet requirement with your own money — typically a small loss, which unlocks the real bonus separately.";

  resultEl.innerHTML = `
    <div class="card">
      <div class="bets-summary" style="margin-bottom:12px;">
        <div><span class="v">$${r.lay_stake.toFixed(2)}</span><span class="l">Lay this much on Betfair</span></div>
      </div>
      <div class="bets-summary">
        <div><span class="v">${fmtMoney(r.profit_if_back_wins, true)}</span><span class="l">If back wins</span></div>
        <div><span class="v">${fmtMoney(r.profit_if_back_loses, true)}</span><span class="l">If back loses</span></div>
        <div><span class="v">${(r.retention_pct * 100).toFixed(1)}%</span><span class="l">${noteLabel}</span></div>
      </div>
      <p class="caption" style="margin-top:10px;">${note}</p>
    </div>`;
}

function setupBonusCalc() {
  ["bc-type", "bc-stake", "bc-commission", "bc-back", "bc-lay"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", recomputeBonusCalc);
  });
  recomputeBonusCalc();
}

const TAB_RENDERERS = {
  week: renderThisWeek,
  mybets: renderMyBets,
  bonus: renderBonusCalc,
  horses: renderHorses,
};
SPORT_ORDER.forEach((s) => { TAB_RENDERERS[s] = () => renderSport(s); });

function renderTab(tab) {
  const main = document.getElementById("content");
  if (!state.data) {
    main.innerHTML = `<div class="loading">Loading…</div>`;
    return;
  }
  const fn = TAB_RENDERERS[tab];
  main.innerHTML = fn ? fn() : `<div class="muted-note">Unknown tab.</div>`;
  if (tab === "bonus") setupBonusCalc();
}

function buildTabBar() {
  const nav = document.getElementById("tabbar");
  const tabs = [["week", "This Week"], ...SPORT_ORDER.map((s) => [s, s.toUpperCase()]), ["horses", "Horses"], ["bonus", "Bonus Calc"], ["mybets", "My Bets"]];
  nav.innerHTML = tabs.map(([id, label]) => `<button data-tab="${id}">${label}</button>`).join("");
  nav.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
  });
}

let activeTab = "week";
function setActiveTab(tab) {
  activeTab = tab;
  document.querySelectorAll("nav.tabbar button").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
  renderTab(tab);
  window.scrollTo(0, 0);
}

async function loadData({ silent } = {}) {
  try {
    const resp = await fetch(DATA_URL, { cache: "no-store" });
    const fresh = await resp.json();
    const changed = !state.data || state.data.generated_at !== fresh.generated_at;
    state.data = fresh;
    document.getElementById("generated-at").textContent = "Updated " + new Date(fresh.generated_at).toLocaleString();
    if (!silent || changed) {
      renderTab(activeTab);
    }
    return changed;
  } catch (e) {
    if (!silent && !state.data) {
      document.getElementById("content").innerHTML = `<div class="loading">Couldn't load tips — check connection.</div>`;
    }
    return false;
  }
}

function init() {
  buildTabBar();
  setActiveTab("week");
  loadData();

  let swRegistration = null;
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("./sw.js").then((reg) => {
      swRegistration = reg;
    }).catch(() => {
      // Offline caching/installability is a nice-to-have, not load-bearing --
      // data.json still loads fine over a plain fetch without it.
    });
    // Once a new service worker (newer CACHE_NAME, see sw.js) takes
    // control, reload so the page actually uses the fresh shell instead of
    // silently continuing to run the old cached app.js until the next
    // manual refresh -- this is what caused the dashboard to look stuck on
    // an old version after shell updates.
    let reloadedForUpdate = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (reloadedForUpdate) return;
      reloadedForUpdate = true;
      location.reload();
    });
  }

  setInterval(() => loadData({ silent: true }), POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    loadData({ silent: true });
    // The browser checks for a new service worker on its own schedule,
    // which can be slow (sometimes 24h+) -- force an immediate check every
    // time the app is actually opened/foregrounded, so a shipped update
    // reaches you the same day instead of "eventually, maybe."
    if (swRegistration) swRegistration.update();
  });

  // One delegated listener handles every button added across renders --
  // simpler and cheaper than rebinding handlers each time renderTab() runs.
  document.getElementById("content").addEventListener("click", (e) => {
    const editBtn = e.target.closest("[data-edit-log]");
    if (editBtn) {
      editingTipKey = tipKey(decodeTip(editBtn.dataset.editLog));
      renderTab(activeTab);
      return;
    }
    const confirmBtn = e.target.closest("[data-confirm-log]");
    if (confirmBtn) {
      const formEl = confirmBtn.closest("[data-tip]");
      const t = decodeTip(formEl.dataset.tip);
      const odds = parseFloat(formEl.querySelector(".log-odds-input").value);
      const units = parseFloat(formEl.querySelector(".log-units-input").value);
      const bookmaker = formEl.querySelector(".log-bookmaker-input").value.trim();
      const notes = formEl.querySelector(".log-notes-input").value.trim();
      logBetFromTip(t, {
        odds_decimal: odds > 1 ? odds : undefined,
        units: units > 0 ? units : undefined,
        bookmaker: bookmaker || undefined,
        notes: notes || undefined,
      });
      editingTipKey = null;
      renderTab(activeTab);
      return;
    }
    const settleBtn = e.target.closest("[data-settle]");
    if (settleBtn) {
      settleBet(settleBtn.dataset.settle, settleBtn.dataset.result, false);
      renderTab(activeTab);
      return;
    }
    const revertBtn = e.target.closest("[data-revert]");
    if (revertBtn) {
      revertBetToPending(revertBtn.dataset.revert);
      renderTab(activeTab);
      return;
    }
    const delBtn = e.target.closest("[data-delete-bet]");
    if (delBtn) {
      deleteBet(delBtn.dataset.deleteBet);
      renderTab(activeTab);
      return;
    }
    const refreshSportBtn = e.target.closest("[data-refresh-sport]");
    if (refreshSportBtn) {
      const sport = refreshSportBtn.dataset.refreshSport;
      const statusEl = document.querySelector(`[data-refresh-status="${sport}"]`);
      triggerRefresh(sport, refreshSportBtn, statusEl);
    }
  });

  const refreshBtn = document.getElementById("refresh-btn");
  if (refreshBtn) {
    refreshBtn.addEventListener("click", async () => {
      refreshBtn.disabled = true;
      refreshBtn.textContent = "…";
      await loadData();
      refreshBtn.disabled = false;
      refreshBtn.textContent = "↻";
    });
  }
}

document.addEventListener("DOMContentLoaded", init);
