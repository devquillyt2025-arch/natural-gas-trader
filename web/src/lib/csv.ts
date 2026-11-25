import type { Trade } from "./types";

const COLUMNS: (keyof Trade)[] = [
  "id", "mode", "opened_at", "closed_at", "side", "lots", "qty", "entry", "exit", "pnl", "r_multiple",
  "exit_reason", "reasoning", "decision_id",
];

/**
 * Text from the model must not be executable in a spreadsheet, so strings that start like a formula
 * are prefixed with an apostrophe. Numbers are left alone (a negative P&L must stay a number).
 */
export function csvCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  let text = String(value);
  if (typeof value === "string" && /^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function tradesCsv(trades: Trade[]): string {
  const rows = trades.map((t) => COLUMNS.map((c) => csvCell(t[c])).join(","));
  return [COLUMNS.join(","), ...rows].join("\r\n") + "\r\n";
}

export function download(filename: string, content: string, type = "text/csv;charset=utf-8"): void {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
