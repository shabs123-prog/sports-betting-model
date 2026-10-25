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
BALLDONTLIE_API_KEY = "<your key from .env>"
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

## Important: bet history won't persist there yet

The dashboard stores tips, logged bets, and settings in a local SQLite file
(`tips.db`). On Streamlit Cloud, that file survives while the app is just
sitting idle, but **every time new code gets pushed (a redeploy), the
container resets and the database goes with it.** Since this project is
still actively being built, that means logged bets could disappear whenever
an update ships — not great for "track performance over time."

Two sane ways to handle this, your call:

- **Use the cloud app to check tips on the go, log bets locally.** Keep
  running `streamlit run dashboard/app.py` on this Mac for the Bet
  Tracker/Performance tabs where it matters, and use the phone/cloud
  version for browsing Today's Tips. Zero extra setup.
- **Move to a real persistent database** (e.g. a free Supabase or Neon
  Postgres instance) so bet history survives redeploys from any device.
  This needs one more free account and maybe 20 minutes of me swapping
  `db/database.py` to talk to it instead of local SQLite — happy to do
  this next if you want full mobile bet logging.
