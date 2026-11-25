import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LatestDecision, PositionCard, RiskBudget } from "@/components/cards";
import type { Decision, LimitsState, PositionState } from "@/lib/types";

afterEach(cleanup);

const NOW = Date.parse("2026-11-24T13:05:00+05:30");

const position: PositionState = {
  id: "t1", side: "LONG", lots: 1, multiplier: 250, qty: 250, entry: 300, stop_loss: 298, initial_stop: 298,
  target: 304, opened_at: "2026-11-24T12:00:00+05:30", reasoning: "", mode: "paper", decision_id: 1,
  flatten_reason: null, exchange_stop: false,
};

const limits = (daily: number, trades = 1, halted = false): LimitsState => ({
  effective: {
    max_lots: 1, max_risk_inr: 1500, daily_loss_cap_inr: 3000, max_trades_per_day: 4, min_rr: 1.5, min_sl_atr: 0.5,
    max_sl_atr: 3, min_confidence: 0.6, cooldown_min_after_loss: 30, eia_before_min: 20, eia_after_min: 30,
    entry_start: "09:30", no_entry_after: "22:30",
  },
  ceilings: {
    max_lots: 1, max_risk_inr: 1500, daily_loss_cap_inr: 3000, max_trades_per_day: 4, min_rr: 1.5, min_sl_atr: 0.5,
    max_sl_atr: 3, min_confidence: 0.6, cooldown_min_after_loss: 30, eia_before_min: 20, eia_after_min: 30,
  },
  usage: {
    daily_pnl: daily, realized: daily, unrealized: 0, loss_used: Math.max(0, -daily), trades_today: trades, halted,
    risk_budget: Math.max(0, Math.min(1500, 3000 + daily)), date: "2026-11-24",
  },
});

describe("PositionCard", () => {
  it("shows flat when there is no position", () => {
    render(<PositionCard position={null} ltp={300} now={NOW} />);
    expect(screen.getByText("Flat")).toBeTruthy();
  });

  it("computes unrealised rupees, R, distances and time in trade for a long", () => {
    render(<PositionCard position={position} ltp={302} now={NOW} />);
    expect(screen.getByTestId("unrealized").textContent).toBe("+₹500");
    expect(screen.getByTestId("unrealized-r").textContent).toBe("+1.00R");
    expect(screen.getByText("4.00 · ₹1,000")).toBeTruthy(); // to stop
    expect(screen.getByText("2.00 · ₹500")).toBeTruthy(); // to target
    expect(screen.getByText("1h 05m")).toBeTruthy();
  });

  it("gets the sign right for a short that is losing", () => {
    render(<PositionCard position={{ ...position, side: "SHORT", stop_loss: 302, initial_stop: 302, target: 296 }} ltp={301} now={NOW} />);
    expect(screen.getByTestId("unrealized").textContent).toBe("−₹250");
    expect(screen.getByTestId("unrealized-r").textContent).toBe("−0.50R");
  });

  it("keeps R measured against the original stop after trailing", () => {
    render(<PositionCard position={{ ...position, stop_loss: 301 }} ltp={304} now={NOW} />);
    expect(screen.getByTestId("unrealized-r").textContent).toBe("+2.00R");
  });

  it("warns when a live position has no exchange stop", () => {
    render(<PositionCard position={{ ...position, mode: "live" }} ltp={300} now={NOW} />);
    expect(screen.getByText("No exchange stop")).toBeTruthy();
  });

  it("does not invent numbers without a price", () => {
    render(<PositionCard position={position} ltp={null} now={NOW} />);
    expect(screen.getByTestId("unrealized").textContent).toBe("–");
  });
});

describe("RiskBudget", () => {
  const bar = () => screen.getByTestId("loss-bar");

  it.each([
    [500, "ok", "0%"], // profit uses none of the budget
    [-900, "ok", "30%"],
    [-1800, "high", "60%"],
    [-3000, "breached", "100%"],
    [-4500, "breached", "100%"],
  ])("daily P&L %i gives a %s bar %s wide", (pnl, level, width) => {
    render(<RiskBudget limits={limits(pnl)} />);
    expect(bar().getAttribute("data-level")).toBe(level);
    expect(bar().style.width).toBe(width);
  });

  it("exposes the meter to assistive tech and shows trades used", () => {
    render(<RiskBudget limits={limits(-900, 3)} />);
    const meter = screen.getByRole("meter");
    expect(meter.getAttribute("aria-valuenow")).toBe("900");
    expect(meter.getAttribute("aria-valuemax")).toBe("3000");
    expect(screen.getByTestId("trades-used").textContent).toBe("3/4");
  });

  it("says when entries are halted for the day", () => {
    render(<RiskBudget limits={limits(-3100, 2, true)} />);
    expect(screen.getByText("Halted for today")).toBeTruthy();
  });

  it("has an empty state", () => {
    render(<RiskBudget limits={null} />);
    expect(screen.getByText("No data from the engine yet")).toBeTruthy();
  });
});

describe("model text", () => {
  it("is rendered as text, never as HTML", () => {
    const decision: Decision = {
      id: 1, ts: "2026-11-24T12:15:08+05:30", price: 300, action: "HOLD", lots: null, stop_loss: null, target: null,
      confidence: 0.3, reasoning: '<img src=x onerror="alert(1)"><b>bold</b>', approved: false,
      verdict_code: "agent_error", verdict: "<script>x</script>", mode: "paper",
    };
    const { container } = render(<LatestDecision decision={decision} />);
    expect(container.querySelector("img, script, b")).toBeNull();
    expect(container.textContent).toContain("<b>bold</b>");
  });
});
