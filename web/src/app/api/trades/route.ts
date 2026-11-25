import { type NextRequest } from "next/server";
import { guarded } from "@/lib/auth";
import { queryRows } from "@/lib/db";
import { oneOf } from "@/lib/params";

export const dynamic = "force-dynamic";

/** All trades of one account, oldest first. */
export async function GET(request: NextRequest) {
  return guarded(request, async () =>
    queryRows("select * from public.trades where mode = $1 order by closed_at", [
      oneOf(request.nextUrl.searchParams.get("mode"), ["paper", "live"], "paper"),
    ]),
  );
}
