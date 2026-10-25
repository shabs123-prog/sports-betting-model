# Getting this on your phone (Streamlit Community Cloud)

Free, and reachable from anywhere (not just home wifi). Two accounts you
need to create yourself — I can't sign up on your behalf — then a few
copy-paste steps.

## 1. Create a GitHub repo (if you don't already have one)

1. Go to https://github.com/new (log in / sign up first if needed).
2. Name it something like `sports-betting-model`, keep it **Private**
   (there's no secrets in the code itself, but no reason to make it public).
3. Don't initialize with a README/gitignore — this project already has them.
4. Copy the repo URL it gives you (e.g. `https://github.com/you/sports-betting-model.git`).

Then, in this project folder, run:

```bash
git remote add origin <paste the URL here>
git branch -M main
git push -u origin main
```

(First push will prompt you to authenticate with GitHub — follow whatever
it shows you, usually a browser sign-in.)

## 2. Create a Streamlit Community Cloud account

1. Go to https://share.streamlit.io and sign up with your GitHub account
   (this is also the point where you authorize Streamlit to see your repos).
2. Click **New app**, pick the repo you just pushed, branch `main`, and set
   the main file path to `dashboard/app.py`.
3. Before clicking deploy, open **Advanced settings → Secrets** and paste
   the contents of your local `.env` file, reformatted as TOML (quote every
   value):

```toml
ODDS_API_KEY = "<your key from .env>"
BANKROLL_AMOUNT = "1000"
UNIT_SIZE_PCT = "0.01"
KELLY_FRACTION = "0.3"
MAX_UNITS_PER_BET = "3"
MIN_EDGE_PCT = "0.005"
```

   Run `cat .env` locally to see your actual key values to copy in — this
   is how the app gets your keys on Streamlit Cloud without them ever being
   committed to the repo. Never paste real key values into this file or
   any other file that gets committed.
4. Click **Deploy**. Give it a minute or two.

You'll get a URL like `https://<something>.streamlit.app` — open that on
your phone and "Add to Home Screen" for an app-like icon.

## 3. Turn on the daily auto-refresh (so tips are just *there*, instantly)

Without this, the app refreshes NBA/EPL itself the first time you open it
each day — now around 30-60 seconds (ESPN's public API, no rate limiting
hit so far, much faster than the old balldontlie-based history source).
`.github/workflows/daily-refresh.yml` runs that refresh on a schedule
instead, before you'd realistically check your phone, so the app is instant
when you open it.

1. On GitHub: your repo → **Settings → Secrets and variables → Actions →
   New repository secret**. Add `ODDS_API_KEY` (same value as your local
   `.env` — this is the only key needed now).
2. That's it — it runs daily at 19:00 UTC (~6am AEDT, adjust the cron line
   in the workflow file for your timezone/DST). You can also trigger it
   manually any time from the repo's **Actions** tab → "Daily tips refresh"
   → **Run workflow**.

## Important: this means daily redeploys, which wipes bet history

Here's the real tradeoff of turning on step 3: the workflow commits the
refreshed `tips.db` back to the repo every day, and Streamlit Cloud
auto-redeploys on every push to `main` — so **every single day, the live
app's container resets.** Tips regenerate fine (that's the point), but any
bets you logged, or settings you changed, through the live cloud app
**get wiped on that day's redeploy** unless they happened to make it into
the committed `tips.db` first. This isn't a rare edge case anymore now that
refreshes are scheduled daily — it'll happen every day.

Two ways to handle it, your call:

- **Use the cloud app to check tips on the go, log bets locally.** Keep
  running `streamlit run dashboard/app.py` on this Mac for the Bet
  Tracker/Performance tabs where it actually matters, and use the phone/cloud
  version purely for browsing Today's Tips. Zero extra setup — this is the
  easy default.
- **Move bets/settings to a real persistent database** (e.g. a free Supabase
  or Neon Postgres instance) so they survive redeploys no matter which
  device logged them. Tips themselves can stay ephemeral (they're meant to
  regenerate daily anyway) — only `bets` and `settings` actually need this.
  Needs one more free account and maybe 20 minutes of me swapping
  `db/database.py` to talk to it. Worth doing if you want to log bets from
  your phone and trust they'll still be there tomorrow — say the word.
