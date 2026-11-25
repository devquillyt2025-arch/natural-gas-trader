import { type NextRequest } from "next/server";
import { guarded } from "@/lib/auth";
import { queryRows } from "@/lib/db";
import { day, fraction, oneOf, wholeNumber } from "@/lib/params";
import { DECISION_COLUMNS } from "@/lib/types";

export const dynamic = "force-dynamic";

const ACTIONS = ["any", "ENTER_LONG", "ENTER_SHORT", "HOLD", "EXIT", "TRAIL_STOP"] as const;
const PAGE = 100;

function where(p: URLSearchParams, approved: "any" | "yes" | "no") {
  const values: unknown[] = [];
  const clauses: string[] = [];
  const add = (clause: (n: string) => string, value: unknown) => clauses.push(clause(`$${values.push(value)}`));

  add((n) => `mode = ${n}`, oneOf(p.get("mode"), ["paper", "live"], "paper"));
  const action = oneOf(p.get("action"), ACTIONS, "any");
  if (action !== "any") add((n) => `action = ${n}`, action);
  if (approved !== "any") add((n) => `approved = ${n}`, approved === "yes");
  const from = day(p.get("from"));
  if (from) add((n) => `ts >= ${n}::timestamptz`, `${from}T00:00:00+05:30`);
  const to = day(p.get("to"));
  if (to) add((n) => `ts < ${n}::timestamptz + interval '1 day'`, `${to}T00:00:00+05:30`);
  const min = fraction(p.get("minConf"));
  if (min !== null) add((n) => `confidence >= ${n}`, min);
  const max = fraction(p.get("maxConf"));
  if (max !== null) add((n) => `confidence <= ${n}`, max);
  const before = wholeNumber(p.get("before"), 0);
  if (before) add((n) => `id < ${n}`, before);
  return { sql: clauses.join(" and "), values };
}

export async function GET(request: NextRequest) {
  return guarded(request, async () => {
    const p = request.nextUrl.searchParams;
    const approved = oneOf(p.get("approved"), ["any", "yes", "no"], "any");
    const list = where(p, approved);
    const rows = await queryRows(
      `select ${DECISION_COLUMNS} from public.decisions where ${list.sql} order by id desc limit ${PAGE}`,
      list.values,
    );
    // why entries were turned down, over the same filters (skipped for "load more" and approved-only)
    if (p.get("before") || approved === "yes") return { rows, codes: [], more: rows.length === PAGE };
    const rejected = where(p, "no");
    const codes = await queryRows<{ verdict_code: string }>(
      `select verdict_code from public.decisions where ${rejected.sql} order by id desc limit 1000`,
      rejected.values,
    );
    return { rows, codes: codes.map((c) => c.verdict_code), more: rows.length === PAGE };
  });
}
