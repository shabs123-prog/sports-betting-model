const DATA_URL = "./data.json";
const POLL_MS = 60000;
const MAX_PREDICTIONS_PER_SPORT = 4;
const SPORT_ORDER = ["nba", "epl", "nbl", "nfl", "nrl", "afl", "bbl"];

let state = { data: null };

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

function marketLabel(m) {
  return (state.data.market_label && state.data.market_label[m]) || m;
}

function riskClass(risk) {
  if (risk === "Not Risky") return "risk-Not-Risky";
  if (risk === "Neutral") return "risk-Neutral";
  if (risk === "Risky") return "risk-Risky";
  return "";
}

function tipCard(t) {
  const hasPrice = t.bookmaker && t.odds_decimal;
  const riskEdge = t.risk
    ? `<span class="${riskClass(t.risk)}">${t.risk}</span>` + (t.edge_pct !== null && t.edge_pct !== undefined ? ` · ${fmtPct(t.edge_pct)} edge` : "")
    : (t.model_prob !== null && t.model_prob !== undefined ? `Model confidence: <b>${(t.model_prob * 100).toFixed(1)}%</b>` : "");
  return `
    <div class="card">
      <div class="matchup">${escapeHtml(t.matchup)}</div>
      <div class="date">${escapeHtml(t.game_date)}</div>
      ${hasPrice ? `<div class="pick-line"><b>${escapeHtml(marketLabel(t.market))}</b> · ${escapeHtml(t.selection)} @ <b>${fmtOdds(t.odds_decimal)}</b> · ${escapeHtml(t.bookmaker)}</div>` : ""}
      <div class="meta-row">
        <div><div class="units">${fmtUnits(t.suggested_units)}</div><div class="units-label">Units</div></div>
        <div class="risk-edge">${riskEdge}</div>
      </div>
      ${t.reasoning ? `<div class="reasoning">${escapeHtml(t.reasoning)}</div>` : ""}
    </div>`;
}

function knowledgeCard(t) {
  const linked = t.bookmaker && t.odds_decimal
    ? `<div class="linked-bet">Linked bet: <b>${escapeHtml(marketLabel(t.market))}</b> · ${escapeHtml(t.selection)} @ <b>${fmtOdds(t.odds_decimal)}</b> · ${escapeHtml(t.bookmaker)} · ${fmtUnits(t.suggested_units)}u</div>`
    : "";
  return `
    <div class="card">
      <div class="matchup">${escapeHtml(t.matchup)}</div>
      <div class="date">${escapeHtml(t.game_date)}</div>
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

function lineMoveCard(m) {
  const { first, latest } = m;
  let moveLine;
  if (first.point !== null && first.point !== undefined) {
    moveLine = `${escapeHtml(marketLabel(m.market))} · ${escapeHtml(m.outcome)}: <b>${first.point} → ${latest.point}</b>`;
  } else {
    moveLine = `${escapeHtml(marketLabel(m.market))} · ${escapeHtml(m.outcome)}: fair probability <b>${(first.fair_prob * 100).toFixed(1)}% → ${(latest.fair_prob * 100).toFixed(1)}%</b>`;
  }
  return `
    <div class="card">
      <div class="matchup">${escapeHtml(m.sport.toUpperCase())}: ${escapeHtml(m.matchup)}</div>
      <div class="date">${escapeHtml(m.game_date)}</div>
      <div class="pick-line">${moveLine}</div>
      <div class="caption" style="margin-top:6px;">Tracked over ${m.days_tracked} day(s), ${m.snapshot_count} snapshot(s).</div>
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
  let html = `<section>
    <h2>Line Movement</h2>
    <p class="caption">Every pipeline run records each line's fair probability (and point, for Line/Handicap) so a genuine move — e.g. a total posted at 192.5 drifting to 190.5 — becomes visible over time. Needs a few days of history before anything shows up; empty at first just means it started tracking.</p>`;
  if (!tw.line_movement.length) {
    html += `<div class="info-box">No line has 2+ days of tracked history yet — check back in a few days.</div>`;
  } else {
    html += tw.line_movement.map(lineMoveCard).join("");
  }
  html += `</section>`;

  if (tw.big_value) {
    const bv = tw.big_value;
    html += `<div class="alert">🔥 <b>Big value alert</b> — ${escapeHtml(bv.sport.toUpperCase())}: ${escapeHtml(bv.matchup)} (${escapeHtml(bv.game_date)}). ${escapeHtml(marketLabel(bv.market))} · ${escapeHtml(bv.selection)} @ ${fmtOdds(bv.odds_decimal)} (${escapeHtml(bv.bookmaker)}) · ${fmtUnits(bv.suggested_units)}u — ${fmtPct(bv.edge_pct)} edge, well above the usual bar.</div>`;
  }

  html += `<section>
    <h2>🔒 Must-bet this week (${tw.must_bet.length})</h2>
    <p class="caption">Moderate-or-better edge, Not Risky/Neutral win chance, within ${lo}-${hi} odds — if you only place a few bets this week, place these.</p>`;
  html += tw.must_bet.length
    ? tw.must_bet.map(tipCard).join("")
    : `<div class="info-box">Nothing clears the Must-Bet bar this week — see Recommended below.</div>`;
  html += `</section>`;

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

  html += `<section><h2>Multis (cross-game)</h2>
    <p class="caption">Combines several of this week's +EV singles from different games, all at the same bookmaker. Combined odds capped at 2.2.</p>
    ${comboGroup(tw.multis)}</section>`;

  html += `<section><h2>Same-Game Multis (SGM)</h2>
    <p class="caption">Different markets, same game, same bookmaker. Marked approximate — legs are correlated, not independently verified.</p>
    ${comboGroup(tw.sgms)}</section>`;

  if (tw.everything_else.length) {
    html += `<section>${expander(`Everything else this week (${tw.everything_else.length})`, tw.everything_else.map(tipCard).join(""))}</section>`;
  }

  return html;
}

function renderSport(sport) {
  const s = state.data.sports[sport];
  const [lo, hi] = state.data.odds_range;
  let html = "";
  if (!s.has_history) {
    html += `<p class="caption">No results-history source wired up for this sport yet — devig value bets only, no Elo context or ball-knowledge notes.</p>`;
  }
  if (!s.auto_refresh) {
    html += `<p class="caption">Not auto-refreshed daily (off-season or markets not posted yet).</p>`;
  }

  html += `<section><h2>Value Bets</h2>`;
  if (!s.value_in_range.length) {
    html += `<div class="muted-note">No value bets in the ${lo}-${hi} odds range for this view right now.</div>`;
  } else {
    html += s.value_in_range.map(tipCard).join("");
  }
  if (s.value_outside_range.length) {
    html += expander(`Outside ${lo}-${hi} odds, not shown by default (${s.value_outside_range.length})`, s.value_outside_range.map(tipCard).join(""));
  }
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

    html += `<section><h2>Ball Knowledge</h2>`;
    html += s.knowledge.length ? s.knowledge.map(knowledgeCard).join("") : `<div class="muted-note">No situational notes for this view right now.</div>`;
    html += `</section>`;
  }

  return html || `<div class="muted-note">Nothing here right now.</div>`;
}

function renderHorses() {
  return `<p class="caption">Not wired up to live data yet. Horse racing needs a different data source (Betfair Exchange or Racing Australia, not The Odds API) and a different model — per-runner form/speed-figure probability, not team-vs-team Elo.</p>`;
}

const TAB_RENDERERS = {
  week: renderThisWeek,
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
}

function buildTabBar() {
  const nav = document.getElementById("tabbar");
  const tabs = [["week", "This Week"], ...SPORT_ORDER.map((s) => [s, s.toUpperCase()]), ["horses", "Horses"]];
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

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("./sw.js").catch(() => {
      // Offline caching/installability is a nice-to-have, not load-bearing --
      // data.json still loads fine over a plain fetch without it.
    });
  }

  setInterval(() => loadData({ silent: true }), POLL_MS);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") loadData({ silent: true });
  });
}

document.addEventListener("DOMContentLoaded", init);
