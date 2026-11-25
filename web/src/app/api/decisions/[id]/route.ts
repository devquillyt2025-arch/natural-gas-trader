import { type NextRequest } from "next/server";
import { guarded } from "@/lib/auth";
import { queryRows } from "@/lib/db";
import { wholeNumber } from "@/lib/params";

/** The exact JSON sent to the model for one decision. Large, so fetched only when asked for. */
export async function GET(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  return guarded(request, async () => {
    const rows = await queryRows<{ snapshot: unknown }>("select snapshot from public.decisions where id = $1", [
      wholeNumber(id, 0),
    ]);
    return { snapshot: rows[0]?.snapshot ?? null };
  });
}
