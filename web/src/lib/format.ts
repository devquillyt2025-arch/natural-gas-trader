const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const px = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

export const DASH = "–";

export function price(value: number | null | undefined): string {
  return value === null || value === undefined || !Number.isFinite(value) ? DASH : px.format(value);
}

/** Rupee amount without sign, e.g. ₹1,500 */
export function rupees(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `₹${inr.format(Math.abs(Math.round(value)))}`;
}

/** Signed P&L, e.g. +₹250 or −₹1,075 */
export function pnl(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  const rounded = Math.round(value);
  const sign = rounded > 0 ? "+" : rounded < 0 ? "−" : "";
  return `${sign}₹${inr.format(Math.abs(rounded))}`;
}

export function rMultiple(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(2)}R`;
}

export function percent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${(value * 100).toFixed(digits)}%`;
}

export function toneClass(value: number | null | undefined): string {
  if (value === null || value === undefined || value === 0) return "text-ink";
  return value > 0 ? "text-up-ink" : "text-down-ink";
}

export function actionLabel(action: string): string {
  const labels: Record<string, string> = {
    ENTER_LONG: "Long",
    ENTER_SHORT: "Short",
    HOLD: "Hold",
    EXIT: "Exit",
    TRAIL_STOP: "Trail stop",
  };
  return labels[action] ?? action;
}

export const REASON_LABELS: Record<string, string> = {
  kill_switch: "Kill switch",
  orphan_position: "Orphan position",
  safety_halt: "Safety halt",
  control_channel_down: "Control channel down",
  already_in_position: "Already in a position",
  daily_loss_cap: "Daily loss cap",
  max_trades: "Max trades per day",
  outside_window: "Outside entry window",
  eia_blackout: "EIA blackout",
  cooldown: "Cooldown after loss",
  stale_data: "Stale market data",
  low_confidence: "Low confidence",
  no_atr: "No ATR",
  missing_levels: "Missing stop or target",
  wrong_side: "Levels on wrong side",
  stop_too_tight: "Stop too tight",
  stop_too_wide: "Stop too wide",
  poor_rr: "Reward:risk too low",
  bad_lots: "Invalid lots",
  risk_budget: "Over risk budget",
  trail_invalid: "Stop not tighter",
  no_position: "No position",
  bad_price: "No usable price",
  agent_error: "Model error",
  stale_decision: "Answer too late",
  position_changed: "Position changed",
  order_failed: "Order failed",
  no_price: "Price unavailable",
  unknown_action: "Unknown action",
};

export const reasonLabel = (code: string): string => REASON_LABELS[code] ?? code;
