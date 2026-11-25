// A small, self-consistent snapshot of an engine mid-session, with timestamps relative to now.
const iso = (ms) => new Date(ms).toISOString();
const QUARTER = 900_000;

const KEYS = { candles: ["tf", "ts"], decisions: ["id"], trades: ["id"], events: ["id"], state: ["key"] };

async function upsert(db, table, rows) {
  const columns = Object.keys(rows[0]);
  const names = columns.map((c) => `"${c}"`).join(",");
  const updates = columns.filter((c) => !KEYS[table].includes(c)).map((c) => `"${c}" = excluded."${c}"`);
  await db.query(
    `insert into public.${table} (${names}) select ${names} from json_populate_recordset(null::public.${table}, $1::json)
     on conflict (${KEYS[table].join(",")}) do update set ${updates.join(",")}`,
    [JSON.stringify(rows)],
  );
}

export async function seed(db, now = Date.now()) {
  const slot = now - (now % QUARTER);
  const candles = [];
  let price = 298;
  for (let i = 119; i >= 0; i--) {
    const o = price;
    const c = Math.round((298 + Math.sin(i / 7) * 3 + Math.cos(i / 3)) * 10) / 10;
    candles.push({ tf: "15m", ts: iso(slot - (i + 1) * QUARTER), o, h: Math.max(o, c) + 0.6, l: Math.min(o, c) - 0.5, c, v: 200 + (i % 9) * 30 });
    price = c;
  }
  for (let i = 30; i >= 1; i--) {
    const c = 295 + Math.sin(i / 5) * 8;
    candles.push({ tf: "1d", ts: iso(now - i * 86_400_000), o: c - 1, h: c + 4, l: c - 4, c, v: 5000 });
  }
  await upsert(db, "candles", candles);

  const decision = (i, action, approved, code, verdict, extra = {}) => ({
    id: now - i * QUARTER,
    ts: iso(slot - i * QUARTER + 8000),
    price: 299.4,
    action,
    lots: action.startsWith("ENTER") ? 1 : null,
    stop_loss: action.startsWith("ENTER") ? 297.9 : null,
    target: action.startsWith("ENTER") ? 303.2 : null,
    confidence: 0.72,
    reasoning: "Pullback held the 298.5 swing low and reclaimed EMA9; stop sits below that low.",
    approved,
    verdict_code: code,
    verdict,
    snapshot: { time_ist: "mock", ltp: 299.4, hard_limits: { max_lots: 1 } },
    mode: "paper",
    ...extra,
  });
  const decisions = [
    decision(8, "ENTER_LONG", true, "approved", "approved: 1 lot, risking 375 INR"),
    decision(5, "ENTER_LONG", false, "already_in_position", "already in a position (exit first; no pyramiding or flipping)"),
    decision(3, "TRAIL_STOP", false, "trail_invalid", "new stop must tighten"),
    decision(1, "HOLD", true, "hold", "hold", { reasoning: "Trend intact above 298.5; nothing to change.", confidence: 0.55 }),
  ];
  await upsert(db, "decisions", decisions);

  const trade = (i, pnl, reason) => ({
    id: `t${i}`,
    opened_at: iso(now - i * 86_400_000 - 3_600_000),
    closed_at: iso(now - i * 86_400_000),
    side: i % 2 ? "LONG" : "SHORT",
    lots: 1,
    qty: 250,
    entry: 300,
    exit: 300 + pnl / 250,
    pnl,
    r_multiple: Math.round((pnl / 375) * 100) / 100,
    exit_reason: reason,
    reasoning: "seeded trade",
    decision_id: null,
    mode: "paper",
  });
  await upsert(db, "trades", [trade(1, 690, "target"), trade(2, -435, "stop"), trade(3, 210, "agent"), trade(4, -120, "forced: end of session")]);
  await upsert(db, "events", [{ id: 1, ts: iso(now), level: "info", msg: "engine started in PAPER mode (mock)" }]);

  const day = new Date(now + 19_800_000).toISOString().slice(0, 10);
  const state = {
    ltp: { price: 300.6, ts: iso(now) },
    position: {
      id: "p1", side: "LONG", lots: 1, multiplier: 250, qty: 250, entry: 299.5, stop_loss: 298, initial_stop: 298,
      target: 303.2, opened_at: iso(slot - 8 * QUARTER), reasoning: "seeded", mode: "paper", decision_id: null,
      flatten_reason: null, exchange_stop: false,
    },
    session: {
      symbol: "NATGASMINI", trading_symbol: "NATGASMINI FUT 24 DEC 26", expiry: "2026-12-24", lot_size: 250, mode: "paper",
      model: "claude-sonnet-5-5", market_open: true, market_close: "23:55", entries_allowed: false,
      block_code: "already_in_position", block_reason: "in a position", force_flat_at: `${day}T23:10:00+05:30`,
      next_eia: iso(now + 26 * 3_600_000), eia_verified: true, kill: false, orphan: false, safety_halt: null,
      windows: [], levels: { prev_day_high: 303.4, prev_day_low: 294.8, resistance: [302.6], support: [296.9] },
    },
    limits: {
      effective: {
        max_lots: 1, max_risk_inr: 1500, daily_loss_cap_inr: 3000, max_trades_per_day: 4, min_rr: 1.5, min_sl_atr: 0.5,
        max_sl_atr: 3, min_confidence: 0.6, cooldown_min_after_loss: 30, eia_before_min: 20, eia_after_min: 30,
        entry_start: "09:30", no_entry_after: "22:30",
      },
      ceilings: {
        max_lots: 1, max_risk_inr: 1500, daily_loss_cap_inr: 3000, max_trades_per_day: 4, min_rr: 1.5, min_sl_atr: 0.5,
        max_sl_atr: 3, min_confidence: 0.6, cooldown_min_after_loss: 30, eia_before_min: 20, eia_after_min: 30,
      },
      usage: { daily_pnl: -160, realized: -435, unrealized: 275, loss_used: 160, trades_today: 2, halted: false, risk_budget: 1500, date: day },
    },
    upstox_auth: { authenticated: false, error: null },
    news_brief: { text: "Mock brief: storage draw expected near the 5-year average.", ts: iso(now) },
  };
  await upsert(db, "state", Object.entries(state).map(([key, value]) => ({ key, value, updated_at: iso(now) })));
  await beat(db);
}

export async function beat(db) {
  const now = iso(Date.now());
  await upsert(db, "state", [
    { key: "heartbeat", value: { ts: now, engine_time: now, mode: "paper", instance: "mock", started_at: now, version: "1.0.0" }, updated_at: now },
  ]);
}
