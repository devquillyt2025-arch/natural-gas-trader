// Shapes written by the engine (engine/engine.py, engine/models.py) and read by the UI.

export type Mode = "paper" | "live";
export type Side = "LONG" | "SHORT";
export type Action = "ENTER_LONG" | "ENTER_SHORT" | "HOLD" | "EXIT" | "TRAIL_STOP";

export interface Decision {
  id: number;
  ts: string;
  price: number | null;
  action: Action | string;
  lots: number | null;
  stop_loss: number | null;
  target: number | null;
  confidence: number | null;
  reasoning: string;
  approved: boolean;
  verdict_code: string;
  verdict: string;
  mode: Mode;
  snapshot?: unknown;
}

export interface Trade {
  id: string;
  opened_at: string;
  closed_at: string;
  side: Side;
  lots: number;
  qty: number;
  entry: number;
  exit: number;
  pnl: number;
  r_multiple: number | null;
  exit_reason: string;
  reasoning: string;
  decision_id: number | null;
  mode: Mode;
}

export interface Candle {
  tf: "5m" | "15m" | "1d";
  ts: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export type CommandType = "kill" | "resume" | "flatten" | "update_limits" | "upstox_code";

export interface Command {
  id: number;
  ts: string;
  type: CommandType;
  payload: Record<string, unknown>;
  status: "pending" | "acked" | "failed";
  result: string | null;
}

export interface EventRow {
  id: number;
  ts: string;
  level: "info" | "warn" | "error" | "trade";
  msg: string;
}

export interface Heartbeat {
  ts: string;
  engine_time: string;
  mode: Mode;
  instance: string;
  started_at: string;
  version: string;
}

export interface PositionState {
  id: string;
  side: Side;
  lots: number;
  multiplier: number;
  qty: number;
  entry: number;
  stop_loss: number;
  initial_stop: number;
  target: number | null;
  opened_at: string;
  reasoning: string;
  mode: Mode;
  decision_id: number | null;
  flatten_reason: string | null;
  exchange_stop: boolean;
}

export interface TimeWindow {
  kind: "eia" | "no_entry";
  start: string;
  end: string;
}

export interface Levels {
  prev_day_high?: number | null;
  prev_day_low?: number | null;
  resistance?: number[];
  support?: number[];
}

export interface SessionState {
  candle_min?: number;
  symbol: string;
  trading_symbol: string | null;
  expiry: string | null;
  lot_size: number;
  mode: Mode;
  model: string;
  market_open: boolean;
  market_close: string;
  entries_allowed: boolean;
  block_code: string | null;
  block_reason: string | null;
  force_flat_at: string;
  next_eia: string;
  eia_verified: boolean;
  kill: boolean;
  orphan: boolean;
  safety_halt: string | null;
  windows: TimeWindow[];
  levels: Levels;
}

export interface LimitValues {
  max_lots: number;
  max_risk_inr: number;
  daily_loss_cap_inr: number;
  max_trades_per_day: number;
  min_rr: number;
  min_sl_atr: number;
  max_sl_atr: number;
  min_confidence: number;
  cooldown_min_after_loss: number;
  eia_before_min: number;
  eia_after_min: number;
}

export interface LimitsState {
  effective: LimitValues & { entry_start: string; no_entry_after: string };
  ceilings: LimitValues;
  usage: {
    daily_pnl: number;
    realized: number;
    unrealized: number;
    loss_used: number;
    trades_today: number;
    halted: boolean;
    risk_budget: number;
    date: string;
  };
}

export interface UpstoxAuthState {
  authenticated: boolean;
  issued_at?: string | null;
  expires_at?: string | null;
  error?: string | null;
  simulated?: boolean;
}

export interface EngineError {
  source: string;
  msg: string;
  ts: string;
}

export interface StateMap {
  heartbeat: Heartbeat | null;
  ltp: { price: number; ts: string } | null;
  position: PositionState | null;
  session: SessionState | null;
  limits: LimitsState | null;
  upstox_auth: UpstoxAuthState | null;
  engine_error: EngineError | null;
  news_brief: { text: string; ts: string } | null;
  forming: Candle | null; // the candle still being made
}

export const EMPTY_STATE: StateMap = {
  heartbeat: null,
  ltp: null,
  position: null,
  session: null,
  limits: null,
  upstox_auth: null,
  engine_error: null,
  news_brief: null,
  forming: null,
};

/** Everything except the (large) snapshot, which is fetched only when a row is opened. */
export const DECISION_COLUMNS =
  "id,ts,price,action,lots,stop_loss,target,confidence,reasoning,approved,verdict_code,verdict,mode";
