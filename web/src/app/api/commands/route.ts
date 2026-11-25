import { type NextRequest } from "next/server";
import { guarded } from "@/lib/auth";
import { queryJson } from "@/lib/db";
import { BadRequest } from "@/lib/params";

// `upstox_code` is not here: only the Upstox callback route queues that one.
const TYPES = ["kill", "resume", "flatten", "update_limits"];

/** The one thing the dashboard can ask of the engine. The engine decides what happens. */
export async function POST(request: NextRequest) {
  return guarded(request, async () => {
    if (!request.headers.get("content-type")?.startsWith("application/json")) throw new BadRequest("send JSON");
    const body = (await request.json().catch(() => null)) as { type?: unknown; payload?: unknown } | null;
    const type = body?.type;
    const payload = body?.payload ?? {};
    if (typeof type !== "string" || !TYPES.includes(type)) throw new BadRequest("unknown command");
    if (typeof payload !== "object" || payload === null || Array.isArray(payload)) throw new BadRequest("bad payload");
    const text = JSON.stringify(payload);
    if (text.length > 2000) throw new BadRequest("payload too large");
    return queryJson(
      "insert into public.commands (type, payload) values ($1, $2::jsonb) returning to_json(commands.*) as j",
      [type, text],
    );
  });
}
