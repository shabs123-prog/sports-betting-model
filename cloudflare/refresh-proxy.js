/**
 * Cloudflare Worker: lets the PWA's "Refresh NBA odds now" (etc) buttons
 * trigger the "Daily tips refresh" GitHub Action on demand, without ever
 * putting a GitHub token in public, client-side JS.
 *
 * The PWA (docs/app.js) is a static site with no server of its own -- it
 * can call this Worker's public URL, but can't safely hold the real
 * GitHub token needed to start a workflow. This Worker holds that token
 * as an encrypted secret and does the actual GitHub API call server-side.
 *
 * Setup (Cloudflare dashboard, no CLI needed):
 *   1. workers.cloudflare.com -> sign up free -> Create a Worker.
 *   2. Paste this whole file into the Worker's code editor, deploy it.
 *   3. Worker -> Settings -> Variables and Secrets -> add two:
 *        GITHUB_TOKEN  -- a GitHub Personal Access Token (classic) with
 *                          the "workflow" scope. Generate one at
 *                          github.com/settings/tokens -- "Generate new
 *                          token (classic)", check "workflow", no expiry
 *                          needed unless you want one. Mark it Secret,
 *                          not plain text, when adding it here.
 *        APP_SECRET    -- any random string you make up (a password
 *                          manager's "generate password" button is
 *                          fine). This is NOT the GitHub token -- it's a
 *                          much lower-stakes shared secret the button
 *                          sends so randoms who find the Worker's URL
 *                          can't spam-trigger refreshes. Mark it Secret
 *                          too.
 *   4. Copy the Worker's URL (shown on its dashboard page, looks like
 *      https://refresh-proxy.YOUR-SUBDOMAIN.workers.dev).
 *   5. In docs/app.js, set REFRESH_PROXY_URL to that URL and
 *      REFRESH_PROXY_SECRET to the same value you used for APP_SECRET
 *      above, then push. The per-sport refresh buttons go live
 *      automatically once REFRESH_PROXY_URL is non-empty.
 *
 * Called as: GET <worker-url>?sport=nba&key=<APP_SECRET>
 */

const REPO = "shabs123-prog/sports-betting-model";
const WORKFLOW_FILE = "daily-refresh.yml";
const ALLOWED_SPORTS = new Set(["all", "nba", "epl", "nbl", "nfl", "afl", "nrl", "bbl"]);

function jsonResponse(body, status, cors) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...cors, "Content-Type": "application/json" },
  });
}

export default {
  async fetch(request, env) {
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
    };
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: cors });
    }

    const url = new URL(request.url);
    const sport = url.searchParams.get("sport") || "all";
    const key = url.searchParams.get("key");

    if (!env.APP_SECRET || key !== env.APP_SECRET) {
      return jsonResponse({ error: "unauthorized" }, 401, cors);
    }
    if (!ALLOWED_SPORTS.has(sport)) {
      return jsonResponse({ error: "unknown sport" }, 400, cors);
    }
    if (!env.GITHUB_TOKEN) {
      return jsonResponse({ error: "worker misconfigured: GITHUB_TOKEN not set" }, 500, cors);
    }

    const ghResp = await fetch(
      `https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${env.GITHUB_TOKEN}`,
          Accept: "application/vnd.github+json",
          "User-Agent": "shab-shooter-refresh-proxy",
        },
        body: JSON.stringify({ ref: "main", inputs: { sport } }),
      }
    );

    // GitHub returns 204 No Content on a successful dispatch -- no body to read.
    if (ghResp.status === 204) {
      return jsonResponse({ ok: true, sport }, 200, cors);
    }
    const detail = await ghResp.text();
    return jsonResponse({ error: "github dispatch failed", status: ghResp.status, detail }, 502, cors);
  },
};
