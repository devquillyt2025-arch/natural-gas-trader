import { type NextRequest } from "next/server";
import { guarded } from "@/lib/auth";
import { queryJson } from "@/lib/db";
import { instant, wholeNumber } from "@/lib/params";
import { DECISION_COLUMNS } from "@/lib/types";

export const dynamic = "force-dynamic";

const list = (select: string) => `(select coalesce(json_agg(q), '[]'::json) from (${select}) q)`;
// rows newer than what the browser has, plus the latest 20, which can still change
const since = (table: string, param: string) =>
  `id > least(${param}::bigint, (select coalesce(min(id), 0) - 1 from (select id from public.${table} order by id desc limit 20) r))`;
// the intraday candle size the engine is running with
const BAR_TF = `(select coalesce((select value->>'candle_min' from public.state where key = 'session'), '15') || 'm')`;
const candles = (tf: string, param: string, overlap: string, limit: number) =>
  `select tf, ts, o, h, l, c, v from public.candles
    where tf = ${tf} and (${param}::timestamptz is null or ts >= ${param}::timestamptz - interval '${overlap}')
    order by ts desc limit ${limit}`;

/** Everything the cockpit shows, in one round trip. */
const FEED = `select json_build_object(
  'state', ${list("select key, value from public.state")},
  'commands', ${list("select * from public.commands order by id desc limit 30")},
  'decisions', ${list(`select ${DECISION_COLUMNS} from public.decisions where ${since("decisions", "$1")} order by id desc limit 1000`)},
  'events', ${list(`select * from public.events where ${since("events", "$2")} order by id desc limit 300`)},
  'intraday', ${list(candles(BAR_TF, "$3", "30 minutes", 600))},
  'candlesDaily', ${list(candles("'1d'", "$4", "3 days", 60))},
  'trades', (select json_build_object('count', count(*), 'last', max(closed_at)) from public.trades)
) as j`;

export async function GET(request: NextRequest) {
  return guarded(request, async () => {
    const p = request.nextUrl.searchParams;
    return queryJson(FEED, [
      wholeNumber(p.get("decision"), 0),
      wholeNumber(p.get("event"), 0),
      instant(p.get("bar")),
      instant(p.get("c1d")),
    ]);
  });
}
