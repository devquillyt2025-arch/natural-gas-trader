import { type NextRequest } from "next/server";
import { guarded } from "@/lib/auth";
import { queryJson } from "@/lib/db";

export const dynamic = "force-dynamic";

/** Distinct IST days on which the engine recorded a paper decision (go-live checklist). */
export async function GET(request: NextRequest) {
  return guarded(request, async () =>
    queryJson("select json_build_object('days', count(*)) as j from public.decision_days where mode = 'paper'"),
  );
}
