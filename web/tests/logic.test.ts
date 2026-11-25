import { describe, expect, it } from "vitest";
import { buildMarkers, COLORS, decisionBarTime, decisionsAt, futureSlots, shading } from "@/lib/chart";
import { activity, blockEnds, nextCandleClose } from "@/lib/status";
import { csvCell, tradesCsv } from "@/lib/csv";
import { type Feed, feedMessages, feedUrl, HIDDEN_POLL_MS, newCursor, nextPoll, POLL_MS, reducer } from "@/lib/data";
import { createSession, setupProblem, validSession } from "@/lib/session";
import { computeStats, goLiveChecklist, maxDrawdown } from "@/lib/stats";
import { chartTime, engineStatus, istDate, isStale } from "@/lib/time";
import { type Decision, EMPTY_STATE, type Trade, type SessionState } from "@/lib/types";

const trade = (i: number, pnl: number, r: number, day: string, reason = "stop"): Trade => ({
  id: `t${i}`, opened_at: `${day}T10:00:00+05:30`, closed_at: `${day}T11:0${i % 10}:00+05:30`, side: "LONG", lots: 1,
  qty: 250, entry: 300, exit: 300 + pnl / 250, pnl, r_multiple: r, exit_reason: reason, reasoning: "", decision_id: i,
  mode: "paper",
});

const decision = (id: number, ts: string, action: string, approved = true): Decision => ({
  id, ts, price: 300, action, lots: 1, stop_loss: null, target: null, confidence: 0.5, reasoning: "", approved,
  verdict_code: "x", verdict: "x", mode: "paper",
});

describe("trade statistics", () => {
  const trades = [
    trade(1, 600, 1.2, "2026-11-02", "target"),
    trade(2, -400, -0.8, "2026-11-02"),
    trade(3, -500, -1.0, "2026-11-03"),
    trade(4, 900, 1.8, "2026-11-04", "agent"),
    trade(5, -100, -0.2, "2026-11-04", "forced: end of session"),
  ];

  it("computes the headline numbers", () => {
    const s = computeStats(trades);
    expect(s.count).toBe(5);
    expect(s.winRate).toBeCloseTo(0.4);
    expect(s.netPnl).toBe(500);
    expect(s.avgWin).toBe(750);
    expect(s.avgLoss).toBeCloseTo(-333.33, 1);
    expect(s.expectancyR).toBeCloseTo(0.2);
    expect(s.profitFactor).toBeCloseTo(1.5);
    expect(s.bestDay).toEqual({ date: "2026-11-04", pnl: 800 });
    expect(s.worstDay).toEqual({ date: "2026-11-03", pnl: -500 });
    expect(s.exits).toEqual({ stop: 2, target: 1, agent: 1, forced: 1, other: 0 });
  });

  it("measures drawdown from the running peak", () => {
    expect(maxDrawdown(trades)).toBe(900); // 600 peak down to -300
    expect(maxDrawdown([trade(1, -200, -0.4, "2026-11-02")])).toBe(200); // peak starts at zero
    expect(maxDrawdown([])).toBe(0);
  });

  it("handles no trades and no losses", () => {
    const empty = computeStats([]);
    expect(empty.winRate).toBeNull();
    expect(empty.profitFactor).toBeNull();
    expect(computeStats([trade(1, 100, 0.2, "2026-11-02")]).profitFactor).toBeNull();
  });

  it("runs the go-live checklist", () => {
    const s = computeStats(trades);
    expect(goLiveChecklist(s, 15, 3000, 1500).map((i) => i.ok)).toEqual([true, true, true, true]);
    expect(goLiveChecklist(s, 14, 200, 100).map((i) => i.ok)).toEqual([false, true, false, false]);
    expect(goLiveChecklist(computeStats([]), 0, 3000, 1500).every((i) => !i.ok)).toBe(true);
  });
});

describe("CSV export", () => {
  it("neutralises formulas in text but keeps negative numbers numeric", () => {
    expect(csvCell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvCell("@cmd")).toBe("'@cmd");
    expect(csvCell(-500)).toBe("-500");
    expect(csvCell('say "hi", then\nleave')).toBe('"say ""hi"", then\nleave"');
    expect(csvCell(null)).toBe("");
  });

  it("writes a header and one line per trade", () => {
    const lines = tradesCsv([trade(1, -400, -0.8, "2026-11-02")]).trim().split("\r\n");
    expect(lines).toHaveLength(2);
    expect(lines[0]).toContain("r_multiple");
    expect(lines[1]).toContain(",-400,-0.8,stop,");
  });
});

describe("time", () => {
  it("uses IST regardless of the browser zone", () => {
    expect(istDate("2026-11-23T20:00:00Z")).toBe("2026-11-24");
    expect(chartTime("2026-11-24T09:00:00+05:30")).toBe(Date.parse("2026-11-24T09:00:00Z") / 1000);
  });

  it("calls the engine offline after 30 seconds without a heartbeat", () => {
    const now = Date.parse("2026-11-24T12:00:00+05:30");
    const at = (sec: number) => new Date(now - sec * 1000).toISOString();
    expect(isStale(at(29), null, now)).toBe(false);
    expect(isStale(at(31), null, now)).toBe(true);
    expect(isStale(undefined, null, now)).toBe(true);
    // phone clock two minutes fast, but a heartbeat has just arrived
    expect(isStale(at(120), now - 2000, now)).toBe(false);
  });
});

describe("chart data", () => {
  const bar = chartTime("2026-11-24T12:00:00+05:30");

  it("puts a decision on the bar that had just closed", () => {
    expect(decisionBarTime("2026-11-24T12:15:08+05:30", "15m")).toBe(bar);
    expect(decisionBarTime("2026-11-24T12:15:00+05:30", "15m")).toBe(bar);
    expect(decisionBarTime("2026-11-24T12:15:08+05:30", "1d")).toBe(Date.parse("2026-11-24T00:00:00Z") / 1000);
  });

  it("builds sorted markers by outcome and hides holds on request", () => {
    const list = [
      decision(3, "2026-11-24T12:30:08+05:30", "EXIT"),
      decision(2, "2026-11-24T12:15:09+05:30", "ENTER_LONG", false),
      decision(1, "2026-11-24T12:15:08+05:30", "HOLD"),
      decision(4, "2026-11-23T12:15:08+05:30", "ENTER_SHORT"),
    ];
    const bars = new Set([bar, bar + 900]);
    const all = buildMarkers(list, bars, "15m", true);
    expect(all.map((m) => m.time)).toEqual([...all.map((m) => m.time)].sort((a, b) => a - b));
    expect(all).toHaveLength(3); // the one off the chart is skipped
    expect(buildMarkers(list, bars, "15m", false).map((m) => m.id).sort()).toEqual(["2", "3"]);
    expect(all.find((m) => m.id === "3")?.text).toBe("✕");
    expect(decisionsAt(list, bar, "15m").map((d) => d.id)).toEqual([2, 1]);
  });

  it("shades EIA over the no-entry windows and extends to the session close", () => {
    const windows = [
      { kind: "no_entry" as const, start: "2026-11-25T22:30+05:30", end: "2026-11-25T23:55+05:30" },
      { kind: "eia" as const, start: "2026-11-25T22:10+05:30", end: "2026-11-25T23:00+05:30" },
    ];
    const at = (t: string) => chartTime(`2026-11-25T${t}:00+05:30`);
    const shades = shading([at("21:45"), at("22:00"), at("22:30"), at("23:00"), at("23:15")], windows);
    expect(shades[0]!.value).toBeUndefined();
    expect(shades[1]!.color).toBe(COLORS.shadeEia); // 22:00-22:15 touches the 22:10 start
    expect(shades[2]!.color).toBe(COLORS.shadeEia);
    expect(shades[3]!.color).toBe(COLORS.shadeClosed);
    const last = { tf: "15m" as const, ts: "2026-11-25T23:00:00+05:30", o: 1, h: 1, l: 1, c: 1, v: 0 };
    const slots = futureSlots(last, Date.parse("2026-11-25T23:16:00+05:30"), "23:55");
    expect(slots).toEqual([at("23:15"), at("23:30"), at("23:45")]);
    expect(futureSlots(last, Date.parse("2026-11-26T09:00:00+05:30"), "23:55")).toEqual([]);
  });
});

describe("live data reducer", () => {
  const base = {
    ready: true, loadError: null, connection: "live" as const, state: EMPTY_STATE, heartbeatSeenAt: null,
    decisions: [], commands: [], events: [], intraday: [], candlesDaily: [], tradesVersion: 0,
  };

  it("keeps decisions newest first, updates in place and drops the heavy snapshot", () => {
    let data = reducer(base, { type: "decision", row: { ...decision(1, "2026-11-24T12:00:00+05:30", "HOLD"), snapshot: { a: 1 } } });
    data = reducer(data, { type: "decision", row: decision(3, "2026-11-24T12:30:00+05:30", "HOLD") });
    data = reducer(data, { type: "decision", row: decision(2, "2026-11-24T12:15:00+05:30", "HOLD") });
    data = reducer(data, { type: "decision", row: decision(2, "2026-11-24T12:15:00+05:30", "EXIT") });
    expect(data.decisions.map((d) => [d.id, d.action])).toEqual([[3, "HOLD"], [2, "EXIT"], [1, "HOLD"]]);
    expect(data.decisions.every((d) => !("snapshot" in d))).toBe(true);
  });

  it("merges candles by time", () => {
    const c = (t: string, close: number) => ({ tf: "15m" as const, ts: `2026-11-24T${t}:00+05:30`, o: 1, h: 1, l: 1, c: close, v: 0 });
    let data = reducer(base, { type: "candle", row: c("09:15", 2) });
    data = reducer(data, { type: "candle", row: c("09:00", 1) });
    data = reducer(data, { type: "candle", row: c("09:15", 3) });
    expect(data.intraday.map((x) => x.c)).toEqual([1, 3]);
  });

  it("never turns an acknowledged command back into pending", () => {
    const command = (status: "pending" | "acked") => ({
      id: 7, ts: "2026-11-24T12:00:00+05:30", type: "resume" as const, payload: {}, status, result: status === "acked" ? "resumed" : null,
    });
    // a poll that already saw the acknowledgement wins the race against the browser's own insert response
    let data = reducer(base, { type: "command", row: command("acked") });
    data = reducer(data, { type: "command", row: command("pending") });
    expect(data.commands).toEqual([command("acked")]);
  });

  it("ignores unknown state keys", () => {
    expect(reducer(base, { type: "state", key: "secret", value: 1, at: 0 })).toBe(base);
    const next = reducer(base, { type: "state", key: "ltp", value: { price: 5, ts: "x" }, at: 7 });
    expect(next.state.ltp?.price).toBe(5);
  });
});

describe("polling", () => {
  const feed = (heartbeat: string, extra: Partial<Feed> = {}): Feed => ({
    state: [{ key: "heartbeat", value: { ts: heartbeat } }, { key: "ltp", value: { price: 300, ts: "x" } }],
    commands: [],
    decisions: [decision(2, "2026-11-24T12:15:00+05:30", "HOLD"), decision(1, "2026-11-24T12:00:00+05:30", "HOLD")],
    events: [{ id: 9, ts: "2026-11-24T12:00:00+05:30", level: "info", msg: "hi" }],
    intraday: [{ tf: "15m", ts: "2026-11-24T12:00:00+05:30", o: 1, h: 1, l: 1, c: 1, v: 0 }],
    candlesDaily: [],
    trades: { count: 0, last: null },
    ...extra,
  });
  const empty = {
    ready: false, loadError: null, connection: "connecting" as const, state: EMPTY_STATE, heartbeatSeenAt: null,
    decisions: [], commands: [], events: [], intraday: [], candlesDaily: [], tradesVersion: 0,
  };
  const apply = (data: Parameters<typeof reducer>[0], messages: Parameters<typeof reducer>[1][]) =>
    messages.reduce(reducer, data);

  it("loads everything first, then asks only for news", () => {
    const cursor = newCursor();
    expect(feedUrl(cursor)).toBe("/api/feed?decision=0&event=0");
    const data = apply(empty, feedMessages(feed("a"), cursor, true, 1000));
    expect(data.ready && data.decisions.map((d) => d.id)).toEqual([2, 1]);
    expect(feedUrl(cursor)).toBe("/api/feed?decision=2&event=9&bar=2026-11-24T12%3A00%3A00%2B05%3A30");
  });

  it("does not let an unchanged heartbeat look fresh", () => {
    const cursor = newCursor();
    let data = apply(empty, feedMessages(feed("a"), cursor, true, 1000));
    const same = apply(data, feedMessages(feed("a"), cursor, false, 50_000));
    expect(same.heartbeatSeenAt).toBeNull();
    expect(same).toBe(data); // nothing new: nothing re-renders
    data = apply(data, feedMessages(feed("b"), cursor, false, 60_000));
    expect(data.heartbeatSeenAt).toBe(60_000);
  });

  it("starts the candles afresh when the engine's candle size changes", () => {
    const bars = (tf: "5m" | "15m", ts: string) => feed("a", { intraday: [{ tf, ts, o: 1, h: 1, l: 1, c: 1, v: 0 }] });
    const cursor = newCursor();
    let data = apply(empty, feedMessages(bars("15m", "2026-11-24T12:00:00+05:30"), cursor, true, 0));
    data = apply(data, feedMessages(bars("5m", "2026-11-24T12:05:00+05:30"), cursor, false, 0));
    expect(data.intraday.map((c) => c.tf)).toEqual(["5m"]);
    expect(feedUrl(cursor)).not.toContain("bar="); // so the whole 5-minute history is fetched next
    feedMessages(bars("5m", "2026-11-24T12:05:00+05:30"), cursor, false, 0);
    expect(feedUrl(cursor)).toContain("bar=");
  });

  it("reports new trades only when the trade list changed", () => {
    const cursor = newCursor();
    feedMessages(feed("a"), cursor, true, 0);
    const types = (f: Feed) => feedMessages(f, cursor, false, 0).map((m) => m.type);
    expect(types(feed("a"))).not.toContain("trades");
    expect(types(feed("a", { trades: { count: 1, last: "2026-11-24T12:00:00+00:00" } }))).toContain("trades");
  });
});

describe("a dashboard in a background tab", () => {
  const now = Date.parse("2026-11-24T12:00:00Z");

  it("keeps polling while the engine is talking, and rests when it is not", () => {
    expect(nextPoll(false, null, now)).toBe(POLL_MS);
    expect(nextPoll(true, now - 5_000, now)).toBe(HIDDEN_POLL_MS);
    expect(nextPoll(true, now - 121_000, now)).toBeNull();
    expect(nextPoll(true, null, now)).toBeNull();
  });

  it("does not call the engine offline from old information", () => {
    const old = new Date(now - 90_000).toISOString();
    const fresh = new Date(now - 4_000).toISOString();
    expect(engineStatus(fresh, null, now - 60_000, true, now)).toBe("ok");
    expect(engineStatus(old, now - 60_000, now - 60_000, true, now)).toBe("checking"); // just came back to the tab
    expect(engineStatus(old, now - 60_000, now - 2_000, true, now)).toBe("offline"); // asked, and it is still old
    expect(engineStatus(old, null, now - 60_000, false, now)).toBe("offline"); // cannot reach the server at all
    expect(engineStatus(undefined, null, now - 2_000, true, now)).toBe("offline");
  });
});

describe("5-minute bars on the chart", () => {
  it("puts a decision on the 5-minute bar that just closed", () => {
    const at = (hhmm: string) => Date.parse(`2026-11-24T${hhmm}:00Z`) / 1000;
    expect(decisionBarTime("2026-11-24T12:05:08+05:30", "5m")).toBe(at("12:00"));
    expect(decisionBarTime("2026-11-24T12:05:08+05:30", "15m")).toBe(at("12:00"));
    expect(decisionBarTime("2026-11-24T12:10:08+05:30", "5m")).toBe(at("12:05"));
  });
});

describe("session cookie", () => {
  const env = { APP_PASSWORD: "correct horse", SESSION_SECRET: "s".repeat(32) };
  const withEnv = async (values: Record<string, string>, work: () => Promise<void>) => {
    const before = { ...process.env };
    Object.assign(process.env, values);
    try {
      await work();
    } finally {
      process.env = before;
    }
  };

  it("accepts what it issued until it expires, and nothing else", async () => {
    await withEnv(env, async () => {
      const now = Date.parse("2026-11-24T00:00:00Z");
      const token = (await createSession(now))!;
      expect(await validSession(token, now)).toBe(true);
      expect(await validSession(token, now + 31 * 86_400_000)).toBe(false);
      const [expires, signature] = token.split(".") as [string, string];
      expect(await validSession(`${Number(expires) + 1}.${signature}`, now)).toBe(false);
      expect(await validSession(`${expires}.${"0".repeat(64)}`, now)).toBe(false);
      expect(await validSession(undefined, now)).toBe(false);
      process.env.APP_PASSWORD = "a new password"; // signs everyone out
      expect(await validSession(token, now)).toBe(false);
    });
  });

  it("lets nobody in when it is not set up, and says which setting is wrong", async () => {
    await withEnv(env, async () => expect(setupProblem()).toBeNull());
    await withEnv({ ...env, SESSION_SECRET: "short" }, async () => {
      expect(await createSession()).toBeNull();
      expect(setupProblem()).toContain("SESSION_SECRET");
    });
    await withEnv({ ...env, APP_PASSWORD: "1234" }, async () => {
      expect(await createSession()).toBeNull();
      expect(setupProblem()).toContain("APP_PASSWORD in web/.env.local needs at least 8 characters");
    });
  });
});

describe("saying what it is doing", () => {
  const at = (hhmm: string) => Date.parse(`2026-11-25T${hhmm}:00+05:30`);
  const window = (kind: "eia" | "no_entry", from: string, to: string) => ({
    kind, start: `2026-11-25T${from}+05:30`, end: `2026-11-25T${to}+05:30`,
  });
  const windows = [window("no_entry", "09:00", "09:30"), window("no_entry", "22:30", "23:55"), window("eia", "22:10", "23:00")];
  const session = (extra: Partial<SessionState> = {}): SessionState => ({
    candle_min: 5, symbol: "NATGASMINI", trading_symbol: null, expiry: null, lot_size: 250, mode: "paper", model: "m",
    market_open: true, market_close: "23:55", entries_allowed: true, block_code: null, block_reason: null,
    force_flat_at: "2026-11-25T23:10:00+05:30", next_eia: "2026-11-25T22:30:00+05:30", eia_verified: true, kill: false,
    orphan: false, safety_halt: null, windows, levels: {}, ...extra,
  });
  const say = (input: Partial<Parameters<typeof activity>[0]>) =>
    activity({ engine: "ok", authenticated: true, session: session(), position: null, now: at("12:02"), ...input });

  it("counts down to just after the next candle closes", () => {
    expect(nextCandleClose(at("12:02"), 5)).toBe(at("12:05") + 8000);
    expect(nextCandleClose(at("12:05") + 3000, 5)).toBe(at("12:05") + 8000);
    expect(nextCandleClose(at("12:05") + 9000, 5)).toBe(at("12:10") + 8000);
    expect(nextCandleClose(at("12:02"), 15)).toBe(at("12:15") + 8000);
  });

  it("follows windows that overlap", () => {
    expect(blockEnds(windows, at("22:15"))).toBe(at("23:55")); // the report window runs into the cut-off
    expect(blockEnds(windows, at("09:10"))).toBe(at("09:30"));
    expect(blockEnds(windows, at("12:00"))).toBeNull();
  });

  it("explains a quiet evening instead of looking stopped", () => {
    expect(say({})).toMatchObject({ kind: "scanning", nextLook: at("12:05") + 8000 });
    const blocked = session({ entries_allowed: false, block_code: "eia_blackout", block_reason: "EIA blackout" });
    const evening = say({ session: blocked, now: at("22:29") });
    expect(evening.kind).toBe("paused");
    expect(evening.detail).toBe("EIA blackout. No more new trades today. Claude isn't asked while there is no position.");
    expect(evening.nextLook).toBeNull();
    const morning = say({ session: session({ entries_allowed: false, block_reason: "outside entry window" }), now: at("09:10") });
    expect(morning.detail).toContain("Until 09:30 IST.");
  });

  it("puts the most urgent thing first", () => {
    expect(say({ engine: "offline" }).kind).toBe("offline");
    expect(say({ engine: "checking" }).kind).toBe("waiting");
    expect(say({ authenticated: false }).kind).toBe("login");
    expect(say({ session: session({ kill: true }) }).kind).toBe("killed");
    expect(say({ session: session({ market_open: false }) }).kind).toBe("closed");
    expect(say({ session: null }).kind).toBe("waiting");
  });
});
