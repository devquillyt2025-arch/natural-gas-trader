// SERVER ONLY. Single user: one password, a signed session cookie.
import { createHash, timingSafeEqual } from "node:crypto";
import { type NextRequest, NextResponse } from "next/server";
import { BadRequest } from "./params";
import { MIN_PASSWORD, SESSION_COOKIE, validSession } from "./session";

const digest = (text: string) => createHash("sha256").update(text).digest();

export function passwordMatches(given: string): boolean {
  const expected = process.env.APP_PASSWORD ?? "";
  return expected.length >= MIN_PASSWORD && timingSafeEqual(digest(given), digest(expected));
}

/**
 * A browser request made by another site (or another app on localhost). The cookie is SameSite=Lax already;
 * this is the second lock.
 */
export function crossSite(headers: Headers): boolean {
  const site = headers.get("sec-fetch-site");
  if (site) return site !== "same-origin" && site !== "none";
  const origin = headers.get("origin");
  if (!origin) return false; // not a browser
  try {
    return new URL(origin).host !== headers.get("host");
  } catch {
    return true; // "null"
  }
}

/** For route handlers. The middleware checks too; this is defence in depth. Returns a refusal, or null. */
export async function refuse(request: NextRequest): Promise<NextResponse | null> {
  if (!(await validSession(request.cookies.get(SESSION_COOKIE)?.value))) {
    return NextResponse.json({ error: "unauthorised" }, { status: 401 });
  }
  if (request.method !== "GET" && crossSite(request.headers)) {
    return NextResponse.json({ error: "cross-site request refused" }, { status: 403 });
  }
  return null;
}

/** Runs a data route for the signed-in owner, turning trouble into a short message without details. */
export async function guarded(request: NextRequest, work: () => Promise<unknown>): Promise<NextResponse> {
  const refusal = await refuse(request);
  if (refusal) return refusal;
  try {
    return NextResponse.json(await work(), { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    if (error instanceof BadRequest) return NextResponse.json({ error: error.message }, { status: 400 });
    console.error("database request failed:", error instanceof Error ? error.message : error);
    return NextResponse.json({ error: "database unavailable" }, { status: 503 });
  }
}
