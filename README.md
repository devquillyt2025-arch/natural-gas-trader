# NatGas Trader

An autonomous intraday trader for MCX Natural Gas futures (NATGASMINI by default). Claude makes every trade
decision at each candle close (15 minutes by default, 5 if you set it); a code-enforced risk layer approves or rejects it; a dashboard shows
everything and holds the kill switch. **Paper mode is the default.** This is a tool, not financial advice, and
natural gas can move violently enough to lose money faster than you expect.

## Read this before planning to go live

1. **Upstox currently rejects MCX orders placed through its API.** Its order docs list
   [`UDAPI1161` "MCX orders via API are temporarily disabled"](https://upstox.com/developer/api-documentation/v3/place-order/),
   and community reports date this to the April 2026 algo-trading rules. I found no notice that it has been
   lifted. Paper trading uses only market data and is not affected. The live broker is built and tested against a
   fake exchange; if it meets this error it halts new entries and tells you.
2. Order APIs need a **registered static IP** for the engine's host.
3. Upstox may **refuse plain MARKET orders** from the API. Its current docs say MARKET orders go through with
   "market protection"; other reports say they are not allowed since April 2026. If one is refused, the live
   broker switches to a LIMIT order priced through the market (0.3% for entries, 1% for exits) for the rest of
   that day.
4. **Nothing here has been run against real Upstox, or against Claude with an API key.** Both are tested against
   fakes. The [subscription route](#claude-on-your-subscription-instead-of-an-api-key) has made real Claude requests,
   on made-up prices only. See [Day one with real data](#day-one-with-real-data).

## How it fits together

```
          your PC / VPS (outbound connections only)                       Vercel
 ┌──────────────────────────────────────────────────┐             ┌────────────────────┐
 │ engine (Python)                                  │             │ web (Next.js)      │
 │  Upstox candles/LTP ─► snapshot ─► Claude        │             │  password sign-in  │
 │                                      │ decision  │             │  chart, decisions, │
 │                          risk layer ◄┘ (code)    │             │  trades, control   │
 │                              │ approved          │             └─────────┬──────────┘
 │           PaperBroker | LiveBroker ──► Upstox    │                       │ its server routes read,
 │  local SQLite = source of truth ──── mirror ─────┼──►  Neon  ◄───────────┘ and insert `commands`
 │  polls `commands` every 4 s ◄────────────────────┼───  (Postgres)
 └──────────────────────────────────────────────────┘
```

- The engine has **no inbound port**. The web app never holds the Anthropic key, the Upstox secret or the access
  token. It logs in to the database as `natgas_web`, which may read what the dashboard shows and insert a command,
  nothing else. The browser never connects to the database; it asks the app's own server routes, every 3 s while
  the page is visible.
- The engine reads its position, today's trades and the kill switch from local disk, so a database outage cannot
  blind it. If it cannot read the command queue for 90 s it stops opening trades, because your kill switch would
  not reach it.

## Run it

```bash
npm run dev
```

That one command, from this folder or from `web/`, starts everything; `Ctrl+C` stops it. (`npm run dev:web` in
`web/` starts the dashboard alone, which needs `web/.env.local`.) What it starts depends on your settings:

| Settings files | `npm run dev` starts |
|---|---|
| neither `engine/.env` nor `web/.env.local` | the offline demo |
| both | the real engine (paper unless `engine/.env` says otherwise) and the dashboard |
| both, but no Anthropic or Upstox keys yet | the dashboard alone, on your database; it says the engine is not running |
| only one | nothing; it names the missing file |

**The demo** needs no accounts or keys: fake prices, a fake trader that keeps trying to break the limits, and a
throwaway local database (your migration, in PGlite), so nothing goes to Neon. Open the address it prints and enter
the password `demo1234`. The simulated day lasts about 11 minutes; restart for a fresh one. `npm run demo` starts
the demo even after you have added real settings, and cannot touch your real database.

## First-time setup

Needs Python 3.11+ and Node 20+. From the repository root:

```bash
python -m venv .venv
.venv\Scripts\activate                 # macOS/Linux: source .venv/bin/activate
pip install -r engine/requirements-dev.txt
npm install --prefix web
```

Other offline checks (with the virtual environment active):

```bash
python -m pytest                       # engine tests, no network
python -m engine.simulate              # replay 25 synthetic days with a stub model
python -m engine.simulate --agent reckless    # a model that tries to break every limit
```

`python -m engine.simulate --serve` writes synthetic trades, so it refuses a database that is not on this machine
unless you pass `--allow-remote`. Never point it at your real database: fake trades would count towards your go-live
checklist.

## Set it up for real (paper)

### 1. Neon (the database)

1. Create a **new** Neon project for this app, ideally in the region nearest you (Singapore from India). Do not
   reuse another app's database; setup refuses one that already has tables with the same names.
2. Copy its connection string into `engine/.env` as `DATABASE_URL=postgresql://…` (copy
   [`engine/.env.example`](engine/.env.example) to `engine/.env` first).
3. Run `npm run db:setup`. It creates the tables, gives the web app its own restricted login, and writes
   `web/.env.local` with that login, a dashboard password (`APP_PASSWORD`) and a session key. It is safe to run
   again.

The engine uses the owner's connection string. Never put that one in the web app.

**Free plan:** Neon's Free plan includes
[100 compute-hours a month](https://neon.com/faqs/free-plan-limits-and-quotas) and puts the database to sleep only
after 5 idle minutes. A running engine polls every few seconds, so it never sleeps: at the smallest size that is
about 400 hours of engine time a month. Running it on weekdays during market hours (about 15.5 h a day, 340 h a
month) fits, with little to spare; leaving it running day and night (730 h) does not, and the database would stop
until the next month. Check the usage page in the Neon console, and use a paid plan before going live.

### 2. Upstox developer app

Create an app in the Upstox developer console. Set its redirect URI to **exactly**
`https://YOUR-APP/api/upstox/callback`. Use the same string in the web app and the engine.

Upstox may allow only one API app per user. If you already use one for another project, changing its redirect
URI will break that project's login.

### 3. Web app on Vercel

Import the repository, set the **root directory to `web`**, and add the variables from
[`web/.env.example`](web/.env.example). `npm run db:setup` has already written the first three to `web/.env.local`:

| Variable | Value |
|---|---|
| `DATABASE_URL` | the `natgas_web` string from `web/.env.local` (never the owner's) |
| `APP_PASSWORD` | your dashboard password; use a long one, the page is public |
| `SESSION_SECRET` | from `web/.env.local` |
| `UPSTOX_CLIENT_ID`, `UPSTOX_REDIRECT_URI` | from the Upstox app; `https://YOUR-APP.vercel.app/api/upstox/callback` |

Pick a Vercel function region close to the database. Sign-in allows 5 attempts per address (50 in total) per
15 minutes, so someone hammering the page can block new sign-ins for a while; sessions already open keep working.
Changing `APP_PASSWORD` signs everyone out.

### 4. Engine

```bash
# in engine/.env: the Upstox values, and ANTHROPIC_API_KEY or the subscription settings below
# (DATABASE_URL is there from step 1)
python -m engine
```

To run the engine and a local dashboard together use `npm run dev`. For a local dashboard, both
`UPSTOX_REDIRECT_URI` values are `http://localhost:PORT/api/upstox/callback`, and that port is the one the dashboard
uses. For unattended trading run `python -m engine` on its own, as below.

Keep it running on a machine whose clock is NTP-synced and that does not sleep during market hours (09:00 to
23:55 IST). Use Task Scheduler on Windows or a systemd service with `Restart=always` on a VPS. Run only one
engine per account; a second one on the same machine is refused.

Local state is in `engine/data/` (`paper.db`, `live.db`). The Upstox token is stored there and nowhere else,
so protect that folder like a password.

### Claude on your subscription instead of an API key

For paper trading the engine can call Claude through the Claude Code command-line tool, signed in to your Claude Pro
or Max plan, so no API key is needed. **Live mode refuses to start this way.**

```bash
npm i -g @anthropic-ai/claude-code@latest    # 2.1.332 or newer
claude setup-token                           # prints a token that lasts a year
```

In `engine/.env` (paste the token there yourself; treat it like a password):

```
CLAUDE_AUTH=subscription
CLAUDE_CODE_OAUTH_TOKEN=...
```

Then `npm run claude:check` makes one real request on made-up prices and shows the answer and how much of your limits
are used. Without a token the engine uses this machine's Claude Code sign-in, which expires after a few weeks.

- **Your limits are shared** with your own Claude and Claude Code use: a decision per candle while entries
  are possible or a position is open, plus an hourly news search (`USE_NEWS=false` removes it). Whether a Pro plan
  lasts a whole day has not been measured. Events warns at 80% of the 5-hour or weekly limit.
- **At the limit every decision is a HOLD** until it resets. Stops, targets, the daily loss cap and force-flat are
  code and keep working, but the model cannot exit or trail a position meanwhile.
- **Check the terms yourself.** Anthropic says Agent SDK and `claude -p` use
  [draws from subscription limits](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
  (as of November 2026; a change was announced and paused earlier in 2026), and that plan limits
  [assume ordinary, individual use](https://code.claude.com/docs/en/legal-and-compliance). This is for your own use
  only. Anything you offer to others needs API keys.
- **The tool is locked down for every request:** no file, terminal or connector tools (web search only, for the news
  brief), none of your Claude Code settings, memory or `CLAUDE.md`, an empty working folder, and none of the
  engine's other secrets in its environment. If it reports loading anything else, the request is stopped and the
  decision is a HOLD ([`engine/agent_cli.py`](engine/agent_cli.py)).
- Setting `CLAUDE_AUTH=api` goes back to the API key. An `ANTHROPIC_API_KEY` left in the file is not passed to the
  tool, so subscription mode never bills it.

### Day one with real data

After logging in (from the web banner, or `python -m engine login`), run the read-only check. It places no orders:

```bash
python -m engine doctor
```

Verify against your trading terminal:

- [ ] Contract: the right symbol and expiry, and your account has MCX access.
- [ ] Intraday candle timestamps are bar **open** times in IST, and the forming bar is not included.
- [ ] LTP and daily candles match.
- [ ] No `lot_size` error. The engine refuses to trade if Upstox lists anything other than 1 or the contract size.

Before any live order, also verify (these need a real order and cannot be checked offline):

- [ ] Upstox accepts MCX orders by API at all (see the top of this page).
- [ ] `quantity` is a number of **lots** for MCX, as Upstox documents.
- [ ] Whether MARKET orders are accepted (Events says when the limit fallback is used).
- [ ] Whether `SL-M` is accepted on this contract (the engine falls back to `SL` with a price buffer).
- [ ] What the positions endpoint reports for one lot (the engine accepts 1 or 250 and learns which).
- [ ] The order status strings match (`trigger pending`, `complete`, `rejected`, …).

## Daily routine

1. **Before 09:30 IST**: open the dashboard and press **Log in to Upstox** (tokens die around 03:30 IST, daily).
2. Check the header: PAPER/LIVE, *Engine ok*, *Upstox connected*, *Kill off*. Check the next EIA time.
3. During the day: the Live page on your phone; the red button at the bottom is the kill switch.
4. After the close: read the day's decisions, especially rejections and the reasoning on losers.

## Watching it

The engine runs in its terminal whether or not the dashboard is open. It stops only when you press `Ctrl+C`, close
that terminal, or the PC sleeps. A dashboard in a background tab keeps updating (the browser may slow it to once a
minute) and catches up the moment you return. The newest candle on the chart is the one still being made; it moves at
every price check (`MONITOR_SEC`). Claude only ever sees finished candles.

## Candle size

`CANDLE_MIN=15` (default) or `CANDLE_MIN=5` in `engine/.env`; restart the engine after changing it. It sets how often
Claude decides, which candles it sees and the chart shows, and the ATR the stop limits are measured in. An existing
database needs `npm run db:setup` once before 5-minute candles can be stored.

At 5 minutes there are about three times as many Claude calls, which matters on a subscription. Stops are also
tighter, so the roughly ₹110 of paper brokerage and slippage per round trip is a bigger share of each trade's risk.
Neither size has been shown to be profitable. Positions are still checked about every 20 s at either size.

## Hard limits (the model cannot override them)

All are checked in [`engine/risk.py`](engine/risk.py), a pure module with every rule tested at its boundaries.

| Rule | Default | Env |
|---|---|---|
| Max lots per trade | 1 | `MAX_LOTS` |
| Max risk per trade (stop distance × 250 or 1250 mmBtu) | ₹1,500 | `MAX_RISK_INR` |
| Daily loss cap, realised + unrealised: blocks entries **and flattens**, latched for the day | ₹3,000 | `DAILY_LOSS_CAP_INR` |
| Max trades per day | 4 | `MAX_TRADES_PER_DAY` |
| Min reward:risk | 1.5 | `MIN_RR` |
| Stop distance, in ATR of the decision candle (15m or 5m) | 0.5 to 3.0 | `MIN_SL_ATR`, `MAX_SL_ATR` |
| Min confidence | 0.6 | `MIN_CONFIDENCE` |
| Cooldown after a losing trade | 30 min | `COOLDOWN_MIN_AFTER_LOSS` |
| New entries only | 09:30–22:30 IST, Mon–Fri | `ENTRY_START`, `NO_ENTRY_AFTER` |
| No entries around the EIA storage report | −20 / +30 min | `EIA_BEFORE_MIN`, `EIA_AFTER_MIN` |
| Force flat, no model involved | 23:10 IST | `FORCE_FLAT` |

Also: every entry needs a stop and a target on the correct sides; lots are only ever sized **down**; no adding or
flipping; a trailed stop must tighten and stay at least max(0.05, 0.25 × ATR) from price; any model error, timeout
or malformed answer is a HOLD; the price and position are re-checked after the model answers; the model is not
called at all when flat and an entry could not be accepted.

**Editing limits from the UI**: tightening applies immediately. Loosening is accepted only up to `CEIL_*` in the
engine env, which defaults to the configured value, so by default the UI can only tighten (or return to the env
value). Every change and rejection is logged to Events.

### Live-order safety

- A position is never left without an exchange-side stop. If one cannot be placed or confirmed, it is flattened.
- An exit is never sent if the stop already filled, or while the stop cannot be cancelled.
- Order writes are never retried blindly. Unknown outcomes are resolved from the order book and positions; when in
  doubt the engine flattens, **halts new entries until you press Resume**, and alerts.
- A natural gas position the app does not know about blocks all entries. The app never trades it.
- State is persisted before the stop is placed, so a restart recovers or flattens; stray stops are swept.

## EIA calendar

The storage report is normally Thursday 10:30 ET (21:00 IST in US winter, 20:00 in summer). Holiday weeks move
it. The engine has the published shifts through 2027 (Veterans Day weeks as well as Thanksgiving) and the
Thanksgiving rule (Wednesday 12:00 ET) beyond that. Add others with `EIA_OVERRIDES`. The UI warns when the next
date is past what was verified. Official schedule: <https://ir.eia.gov/ngs/schedule.html>.

## Go-live checklist

The Control page computes the first four from your paper results.

- [ ] At least **15 paper trading days**.
- [ ] Positive expectancy in R, and you have looked at win rate and average win and loss.
- [ ] Max drawdown within 3 × the daily loss cap.
- [ ] No day worse than the daily cap plus one trade's risk.
- [ ] You have read the decision log: are the reasons sensible, and why are things rejected?
- [ ] Upstox confirms MCX API orders are enabled for your account, and your static IP is registered.
- [ ] Every box under [Day one with real data](#day-one-with-real-data).
- [ ] Telegram alerts configured, and you know how to square off in the Upstox app by hand.

Then set `MODE=live` **and** `LIVE_CONFIRM=I_ACCEPT_THE_RISK` in `engine/.env` and restart. The UI cannot do this.
Stay at `NATGASMINI`, 1 lot, and watch the first days in person.

## Limits of this system

- **The synthetic simulation proves plumbing and risk limits, not profitability.** Its prices are a random walk and
  its "agent" is a stub. There is no backtest of Claude's judgement; paper trading is the only test.
- **A loss can exceed the limits.** Gaps and fast markets fill stops worse than their price; the `SL` fallback is a
  limit order that may not fill until the monitor exits at market; slippage and costs are on top of the risk
  figure; the daily cap is checked every 20 s.
- **If the engine's host dies, only the exchange stop protects you.** No target, no force-flat, no kill switch.
  Do not rely on the broker's own square-off.
- Paper fills are an approximation: price checked every 20 s plus completed 1-minute highs and lows, ₹0.10
  slippage, ₹60 per lot round trip. Real charges and fills differ. Live P&L also uses the ₹60 estimate.
- Exchange holidays are not known to the engine. Use `NO_TRADE_DATES`; stale quotes also block entries.
- One instrument, one position, one engine. No overnight positions, options or backtesting.
- Prices here are INR MCX futures, which follow Henry Hub × USDINR plus basis. Levels from a USD chart differ.
- Claude's decisions are not deterministic, and the news brief can be wrong or stale.
- **Running cost** (API key; a subscription uses your plan's limits instead): roughly US$1–3 per trading day (about 35–50 decision calls of ~2–3k input
  tokens, plus an hourly web-search news brief billed with search fees). This is an estimate, not a measurement:
  check the Anthropic Console after the first day. `USE_NEWS=false` removes the brief.

## Decisions made while building (and deviations from the brief)

| Decision | Why |
|---|---|
| **Next.js 15.5, not 14** | Next 14 has unpatched critical advisories (including unauthenticated RCE); `npm audit` is clean on 15.5. Same App Router. |
| **`tool_choice: auto` + strict tool**, not a forced call | Current Claude models reject forced tool use. No tool call, or a malformed one, is a HOLD. |
| Server-side fallback on (`claude-sonnet-5-5`) | If the model declines, Anthropic may answer with another Claude model. Events records when that happens. |
| Optional subscription route, paper only | Your request. A plan's limit can run out mid-session, which is not acceptable with real money. |
| Subscription route goes through the `claude` executable, not the Python Agent SDK | Anthropic documents the setup token for it, and every start-up report can be checked. |
| Order `quantity` = **lots** | Upstox documents commodity quantity in lots. The reference code sent mmBtu (250 lots). |
| MCX close: **23:30 in US daylight saving, 23:55 in US winter** | The brief had these reversed. Force-flat at 23:10 is safe either way. |
| One extra Upstox call, `GET /v2/order/retrieve-all` | Read-only; needed to resolve an unknown order outcome by tag and to remove stray stops. |
| Orders use the v2 endpoint as specified | Upstox marks it deprecated in favour of v3. |
| Local SQLite is authoritative, Neon mirrors it | A bus outage must not blind the engine. |
| **Neon instead of Supabase** | Your choice. Neon is plain Postgres, so sign-in, live updates and access rules are now in this app. |
| One password instead of an email magic link | Neon sends no email. Single user, signed 30-day cookie, attempts rate limited. |
| The dashboard polls every 3 s | No realtime channel in plain Postgres; hidden tabs stop asking. |
| Extra columns and objects: `verdict_code`, `login_attempts`, view `decision_days` | Rejection chart, sign-in limit, paper-day count. |
| Snapshot stored as `jsonb` | Same content as sent; Postgres does not keep key order. |
| Snapshot holds 80 intraday and 40 daily bars (~2k tokens) | Enough structure, well within the budget. |
| `kill` and `flatten` never expire; other commands expire after 10 min | Risk-reducing actions are always honoured; a stale `resume` is not. |
| `TRAIL_STOP` allowed while killed | It can only reduce risk. |
| Original code moved to [`reference/`](reference/) | It was the working Python version; its files were not edited. |

## Tests

```bash
python -m pytest                 # engine: risk rules, calendar, live broker vs fake exchange, replay invariants
python -m mypy && python -m ruff check engine
cd db && npm install && npm test         # migration and web-role permission attacks on real Postgres (PGlite)
cd web && npm run typecheck && npm test      # component and logic tests
cd web && npm run e2e            # Playwright smoke test, desktop and phone, seeded local database
cd web && npm run e2e:full       # engine → local database → UI, including kill and limit changes
```

Playwright needs a browser: `npx playwright install chromium`, or set `PW_CHANNEL=msedge` (or `chrome`).

## Layout

```
package.json       npm run dev / demo / db:setup / claude:check  (scripts/)
engine/            risk.py  sessions.py  limits.py  models.py  config.py     (pure rules)
                   engine.py  commands.py  snapshot.py  agent.py  agent_cli.py   (loop and Claude)
                   brokers/paper.py  brokers/live.py  upstox.py  feed.py     (markets and orders)
                   store/  notify.py  logs.py  simulate.py  __main__.py  tests/
web/               src/app  src/components  src/lib   tests/  e2e/  e2e-full/  mock/
db/                migrations/  tests/
reference/         the original single-process Python version, kept as it was
```
