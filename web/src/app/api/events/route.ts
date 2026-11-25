import { type NextRequest } from "next/server";
import { guarded } from "@/lib/auth";
import { queryRows } from "@/lib/db";
import { wholeNumber } from "@/lib/params";

export const dynamic = "force-dynamic";

/** Older events, 300 at a time. The newest arrive through /api/feed. */
export async function GET(request: NextRequest) {
  return guarded(request, async () =>
    queryRows("select * from public.events where id < $1 order by id desc limit 300", [
      wholeNumber(request.nextUrl.searchParams.get("before"), Number.MAX_SAFE_INTEGER),
    ]),
  );
}
