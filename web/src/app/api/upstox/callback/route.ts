import { type NextRequest, NextResponse } from "next/server";
import { refuse } from "@/lib/auth";
import { execute } from "@/lib/db";
import { CODE_PATTERN, STATE_COOKIE } from "../shared";

/**
 * Upstox redirects here. We do NOT exchange the code: we queue it for the engine, which holds the
 * client secret and keeps the access token. This app never sees either.
 */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const done = (query: string) => {
    const response = NextResponse.redirect(new URL(`/?${query}`, request.nextUrl.origin));
    response.cookies.set(STATE_COOKIE, "", { maxAge: 0, path: "/api/upstox" });
    return response;
  };
  const fail = (reason: string) => done(`upstox=error&reason=${encodeURIComponent(reason)}`);

  const refusal = await refuse(request);
  if (refusal) return refusal;

  const expected = request.cookies.get(STATE_COOKIE)?.value;
  if (!expected || params.get("state") !== expected) {
    return fail("login session expired or did not match; press Log in to Upstox again");
  }
  const code = params.get("code");
  if (!code || !CODE_PATTERN.test(code)) return fail("Upstox did not return a valid code");

  try {
    await execute("insert into public.commands (type, payload) values ('upstox_code', $1::jsonb)", [JSON.stringify({ code })]);
  } catch (error) {
    console.error("could not queue the Upstox code:", error instanceof Error ? error.message : error);
    return fail("could not hand the code to the engine");
  }
  return done("upstox=sent");
}
