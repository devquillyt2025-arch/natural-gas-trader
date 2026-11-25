"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { getJson, postJson } from "./api";
import {
  type Candle,
  type Command,
  type CommandType,
  type Decision,
  EMPTY_STATE,
  type EventRow,
  type StateMap,
} from "./types";

const KEEP = { decisions: 1500, commands: 30, events: 500, intraday: 600, daily: 60 };
/** How often a visible dashboard asks for news. */
export const POLL_MS = 3000;
/** Pace while the tab is hidden. Browsers may slow it further, to once a minute. */
export const HIDDEN_POLL_MS = 10_000;
/** A hidden tab stops asking once the engine has said nothing for this long: no point keeping the database awake. */
const ENGINE_QUIET_MS = 120_000;

/** Milliseconds until the next poll, or null to wait until the tab is looked at again. */
export function nextPoll(hidden: boolean, engineSpokeAt: number | null, now: number): number | null {
  if (!hidden) return POLL_MS;
  return engineSpokeAt !== null && now - engineSpokeAt < ENGINE_QUIET_MS ? HIDDEN_POLL_MS : null;
}

export type Connection = "connecting" | "live" | "offline";

export interface CockpitData {
  ready: boolean;
  loadError: string | null;
  connection: Connection;
  state: StateMap;
  heartbeatSeenAt: number | null;
  decisions: Decision[]; // newest first
  commands: Command[]; // newest first
  events: EventRow[]; // newest first
  intraday: Candle[]; // the engine's candle size, oldest first
  candlesDaily: Candle[];
  tradesVersion: number;
}

/** One answer from /api/feed. Lists are newest first. */
export interface Feed {
  state: { key: string; value: unknown }[];
  commands: Command[];
  decisions: Decision[];
  events: EventRow[];
  intraday: Candle[];
  candlesDaily: Candle[];
  trades: { count: number; last: string | null };
}

export interface Cockpit extends CockpitData {
  /** When the server last answered (this browser's clock). */
  heardAt: () => number | null;
  sendCommand: (type: CommandType, payload?: Record<string, unknown>) => Promise<Command>;
}

const initial: CockpitData = {
  ready: false,
  loadError: null,
  connection: "connecting",
  state: EMPTY_STATE,
  heartbeatSeenAt: null,
  decisions: [],
  commands: [],
  events: [],
  intraday: [],
  candlesDaily: [],
  tradesVersion: 0,
};

export type Msg =
  | { type: "loaded"; data: Partial<CockpitData> }
  | { type: "failed"; error: string }
  | { type: "connection"; connection: Connection }
  | { type: "state"; key: string; value: unknown; at: number }
  | { type: "decision"; row: Decision }
  | { type: "command"; row: Command }
  | { type: "event"; row: EventRow }
  | { type: "candle"; row: Candle }
  | { type: "trades" };

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

function upsertNewest<T extends { id: number }>(list: T[], row: T, keep: number): T[] {
  const known = list.find((x) => x.id === row.id);
  if (known && same(known, row)) return list;
  const rest = list.filter((x) => x.id !== row.id);
  const at = rest.findIndex((x) => x.id < row.id);
  rest.splice(at === -1 ? rest.length : at, 0, row);
  return rest.slice(0, keep);
}

function upsertCandle(held: Candle[], row: Candle, keep: number): Candle[] {
  // bars of another size (the engine's candle setting changed) must not be mixed in
  const list = held[0] && held[0].tf !== row.tf ? [] : held;
  const t = Date.parse(row.ts);
  let i = list.length - 1;
  while (i >= 0 && Date.parse(list[i]!.ts) > t) i--;
  if (i >= 0 && same(list[i], row)) return list;
  const next = [...list];
  if (i >= 0 && Date.parse(list[i]!.ts) === t) next[i] = row;
  else next.splice(i + 1, 0, row);
  return next.slice(-keep);
}

export function reducer(data: CockpitData, msg: Msg): CockpitData {
  const next = reduce(data, msg);
  // the same lists mean nothing changed: keep the same object so nothing re-renders
  const unchanged = (Object.keys(next) as (keyof CockpitData)[]).every((key) => next[key] === data[key]);
  return unchanged ? data : next;
}

function reduce(data: CockpitData, msg: Msg): CockpitData {
  switch (msg.type) {
    case "loaded":
      return { ...data, ...msg.data, ready: true, loadError: null };
    case "failed":
      return { ...data, ready: true, loadError: msg.error };
    case "connection":
      return { ...data, connection: msg.connection };
    case "state":
      if (!(msg.key in EMPTY_STATE)) return data;
      return {
        ...data,
        state: { ...data.state, [msg.key]: msg.value },
        heartbeatSeenAt: msg.key === "heartbeat" ? msg.at : data.heartbeatSeenAt,
      };
    case "decision": {
      const { snapshot: _dropped, ...slim } = msg.row;
      return { ...data, decisions: upsertNewest(data.decisions, slim, KEEP.decisions) };
    }
    case "command": {
      // The engine can acknowledge before the browser's own insert response arrives, so a late
      // "pending" copy must never replace a settled one.
      const known = data.commands.find((c) => c.id === msg.row.id);
      if (known && known.status !== "pending" && msg.row.status === "pending") return data;
      return { ...data, commands: upsertNewest(data.commands, msg.row, KEEP.commands) };
    }
    case "event":
      return { ...data, events: upsertNewest(data.events, msg.row, KEEP.events) };
    case "candle":
      return msg.row.tf === "1d"
        ? { ...data, candlesDaily: upsertCandle(data.candlesDaily, msg.row, KEEP.daily) }
        : { ...data, intraday: upsertCandle(data.intraday, msg.row, KEEP.intraday) };
    case "trades":
      return { ...data, tradesVersion: data.tradesVersion + 1 };
  }
}

const Context = createContext<Cockpit | null>(null);

export function useCockpit(): Cockpit {
  const value = useContext(Context);
  if (!value) throw new Error("useCockpit must be used inside <CockpitProvider>");
  return value;
}

/** For tests: provide fixed data without a server. */
export function StaticCockpit({ value, children }: { value: Partial<Cockpit>; children: React.ReactNode }) {
  const rejected = () => Promise.reject(new Error("static"));
  const full: Cockpit = { ...initial, ready: true, sendCommand: rejected, heardAt: () => Date.now(), ...value };
  return <Context.Provider value={full}>{children}</Context.Provider>;
}

/** What the browser already has, so each poll asks only for news. */
export interface Cursor {
  decision: number;
  event: number;
  bar: string;
  barTf: string;
  c1d: string;
  state: Map<string, string>;
  trades: string;
}

export const newCursor = (): Cursor => ({ decision: 0, event: 0, bar: "", barTf: "", c1d: "", state: new Map(), trades: "" });

export function feedUrl(cursor: Cursor): string {
  const query = new URLSearchParams({ decision: String(cursor.decision), event: String(cursor.event) });
  if (cursor.bar) query.set("bar", cursor.bar);
  if (cursor.c1d) query.set("c1d", cursor.c1d);
  return `/api/feed?${query}`;
}

/**
 * Turns one answer into reducer messages and moves the cursor on. The first answer loads everything.
 * A state value is reported only when it CHANGED: the heartbeat's age is judged from when it last
 * changed, so repeating an old one would make a dead engine look alive.
 */
export function feedMessages(feed: Feed, cursor: Cursor, first: boolean, at: number): Msg[] {
  const changed = feed.state.filter(({ key, value }) => cursor.state.get(key) !== JSON.stringify(value));
  for (const { key, value } of changed) cursor.state.set(key, JSON.stringify(value));

  const trades = JSON.stringify(feed.trades);
  const tradesChanged = cursor.trades !== "" && cursor.trades !== trades;
  cursor.trades = trades;

  cursor.decision = Math.max(cursor.decision, feed.decisions[0]?.id ?? 0);
  cursor.event = Math.max(cursor.event, feed.events[0]?.id ?? 0);
  const size = feed.intraday[0]?.tf ?? "";
  if (size && cursor.barTf && size !== cursor.barTf) cursor.bar = ""; // new candle size: fetch its history next time
  else cursor.bar = feed.intraday[0]?.ts ?? cursor.bar;
  cursor.barTf = size || cursor.barTf;
  cursor.c1d = feed.candlesDaily[0]?.ts ?? cursor.c1d;

  if (first) {
    const state: StateMap = { ...EMPTY_STATE };
    for (const { key, value } of feed.state) {
      if (key in state) (state as unknown as Record<string, unknown>)[key] = value;
    }
    const data: Partial<CockpitData> = {
      state,
      decisions: feed.decisions,
      commands: feed.commands,
      events: feed.events,
      intraday: [...feed.intraday].reverse(),
      candlesDaily: [...feed.candlesDaily].reverse(),
    };
    return [{ type: "loaded", data }];
  }
  const messages: Msg[] = changed.map(({ key, value }) => ({ type: "state", key, value, at }));
  for (const row of [...feed.decisions].reverse()) messages.push({ type: "decision", row });
  for (const row of [...feed.commands].reverse()) messages.push({ type: "command", row });
  for (const row of [...feed.events].reverse()) messages.push({ type: "event", row });
  for (const row of [...feed.intraday, ...feed.candlesDaily].reverse()) messages.push({ type: "candle", row });
  if (tradesChanged) messages.push({ type: "trades" });
  return messages;
}

export function CockpitProvider({ children }: { children: React.ReactNode }) {
  const [data, dispatch] = useReducer(reducer, initial);
  const heard = useRef<number | null>(null);
  const heardAt = useCallback(() => heard.current, []);

  useEffect(() => {
    const cursor = newCursor();
    let first = true;
    let busy = false;
    let stopped = false;
    let timer: number | undefined;
    let engineSpokeAt: number | null = null;

    const poll = async () => {
      if (busy || stopped) return;
      busy = true;
      window.clearTimeout(timer);
      try {
        const feed = await getJson<Feed>(feedUrl(cursor));
        if (stopped) return;
        const beat = feed.state.find((s) => s.key === "heartbeat")?.value as { ts?: string } | undefined;
        const spoke = first
          ? Date.now() - Date.parse(beat?.ts ?? "") < ENGINE_QUIET_MS
          : !!beat && cursor.state.get("heartbeat") !== JSON.stringify(beat);
        if (spoke) engineSpokeAt = Date.now();
        heard.current = Date.now();
        for (const message of feedMessages(feed, cursor, first, Date.now())) dispatch(message);
        first = false;
        dispatch({ type: "connection", connection: "live" });
      } catch (e) {
        if (stopped) return;
        if (first) dispatch({ type: "failed", error: e instanceof Error ? e.message : "could not load data" });
        dispatch({ type: "connection", connection: "offline" });
      } finally {
        busy = false;
        const wait = nextPoll(document.visibilityState !== "visible", engineSpokeAt, Date.now());
        if (!stopped && wait !== null) timer = window.setTimeout(() => void poll(), wait);
      }
    };

    const wake = () => {
      if (document.visibilityState === "visible") void poll();
    };
    document.addEventListener("visibilitychange", wake);
    void poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", wake);
    };
  }, []);

  const sendCommand = useCallback(async (type: CommandType, payload: Record<string, unknown> = {}) => {
    const command = await postJson<Command>("/api/commands", { type, payload });
    dispatch({ type: "command", row: command });
    return command;
  }, []);

  const value = useMemo<Cockpit>(() => ({ ...data, sendCommand, heardAt }), [data, sendCommand, heardAt]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

/** A once-a-second clock for countdowns and heartbeat staleness: the only timer in the UI. */
export function useNow(): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

/** Engine time minus wall time (non-zero only in accelerated mock runs). */
export function engineOffset(state: StateMap): number {
  const hb = state.heartbeat;
  return hb?.engine_time ? Date.parse(hb.engine_time) - Date.parse(hb.ts) : 0;
}
