# NatGas Trader

Autonomous MCX Natural Gas (intraday) trader. Claude reads the chart data every 15-minute candle close and decides;
a code-enforced risk layer approves or rejects; orders go to Upstox (or a paper ledger). Paper mode is the default.

```
Upstox candles/LTP ─► snapshot (OHLC, EMA, RSI, ATR, swings, position, limits, news)
                         │
                    Claude decides  ── submit_decision {ENTER_LONG|ENTER_SHORT|HOLD|EXIT|TRAIL_STOP, SL, TP, confidence, reasoning}
                         │
                    risk layer (cannot be overridden) ──► rejected: logged, nothing happens
                         │ approved
                    broker: Paper (simulated)  |  Live (Upstox market entry + exchange-side SL-M)
```

## Run it

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                   # fill ANTHROPIC_API_KEY + Upstox keys
python -m pytest                                       # 39 tests, no network needed
python -m app.simulate                                 # offline replay on synthetic data, no API key
uvicorn app.main:app --host 127.0.0.1 --port 8001      # dashboard at http://localhost:8001
```

**Every trading day:** open `http://localhost:8001`, click *Log in to Upstox*. Upstox tokens expire around 03:30 IST,
so this is needed daily (the dashboard shows "Upstox login needed" and can ping Telegram). Paper mode needs the login
too, for market data.

Upstox app setup: create an app in the Upstox developer console and set its redirect URI to exactly
`http://localhost:8001/auth/callback` (same as `UPSTOX_REDIRECT_URI`).

## What Claude cannot override (all in `app/risk.py`, tested)

| Rule | Default |
|---|---|
| Max lots | 1 |
| Max risk per trade (stop distance × lot size) | ₹1,500 |
| Daily loss cap (realized + unrealized) → blocks entries, flattens | ₹3,000 |
| Max trades / day | 4 |
| Min reward:risk | 1.5 |
| Stop distance | 0.5–3.0 × 15m ATR |
| Min confidence | 0.6 |
| Cooldown after a losing trade | 30 min |
| Entry window (IST) | 09:30–22:30 |
| No entries around EIA storage report | −20 / +30 min |
| Force flat | 23:10 (never holds overnight) |
| Stops | mandatory, can only be tightened, never widened |
| Kill switch | dashboard button: blocks entries, flattens |

Size is clipped down to fit the risk budget; a trade that cannot fit even 1 lot is rejected.
Claude is not called at all when flat and entries are not allowed (saves cost).

## Going live: do not skip

1. Run paper for **2–3 weeks** and read the decision log, not just the P&L: are the reasons sane, are rejects frequent?
2. Keep `SYMBOL=NATGASMINI` and `MAX_LOTS=1` at first.
3. Set `MODE=live` **and** `LIVE_CONFIRM=I_ACCEPT_THE_RISK`. The app refuses to start live otherwise.
4. Watch the first live days. The live broker places a market entry, then an exchange-side stop. If the stop cannot be
   placed the position is flattened immediately. If the stop already fired, an exit is never sent (it would open the
   opposite side). A broker position the app does not know about blocks all entries and alerts you.

## Things to know

- **The simulation uses synthetic prices.** It proves the plumbing and the risk limits, not that any strategy makes money.
  There is no backtest of Claude's judgement; paper trading is how you test it.
- **Not tested against live Upstox.** Endpoints were checked against Upstox docs and the broker logic is tested with a
  fake client, but the first real calls are yours. Check on day one: contract resolution (`NATGASMINI` vs your account's
  MCX access), whether the exchange accepts `SL-M` on this contract (the code falls back to `SL` limit), and candle
  timestamps.
- **EIA calendar:** Thursday 10:30 ET (21:00 IST in winter), Wednesday 12:00 ET in Thanksgiving week (22:30 IST today).
  Christmas/New Year shifts are not modelled; check <https://ir.eia.gov/ngs/schedule.html> and switch the bot off.
- MCX closes 23:30 (23:55 during US daylight saving); the bot flattens at 23:10 regardless.
- The chart you analysed was USD Nat Gas (TradingView). This trades INR MCX futures, which track Henry Hub × USDINR plus
  MCX basis, so levels differ.
- Cost: about 50 Claude calls per trading day, each a few thousand tokens, plus an optional hourly web-search news brief
  (`USE_NEWS=false` disables it).
- `ADMIN_TOKEN` protects kill/resume if you expose the dashboard beyond localhost. Don't expose it without one.
- This is a tool, not financial advice. Natural gas is highly volatile and you can lose money.

## Layout

```
app/config.py     settings + every limit          app/risk.py      the guard (pure functions)
app/agent.py      Claude prompt + decision tool    app/broker.py    Paper + Live brokers
app/upstox.py     REST client                      app/engine.py    loop, scheduling, forced exits
app/feed.py       Upstox + synthetic feeds         app/snapshot.py  what Claude sees
app/sessions.py   MCX hours + EIA calendar         app/main.py      API + dashboard (app/static)
```
