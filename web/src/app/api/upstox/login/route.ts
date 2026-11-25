import { randomBytes } from "node:crypto";
import { type NextRequest, NextResponse } from "next/server";
import { refuse } from "@/lib/auth";
import { STATE_COOKIE } from "../shared";

/** Sends the owner to Upstox with a one-time `state` that the callback must see again. */
export async function GET(request: NextRequest) {
  const refusal = await refuse(request);
  if (refusal) return refusal;

  const clientId = process.env.UPSTOX_CLIENT_ID;
  const redirectUri = process.env.UPSTOX_REDIRECT_URI;
  if (!clientId || !redirectUri) {
    return NextResponse.redirect(new URL("/?upstox=error&reason=UPSTOX_CLIENT_ID+or+UPSTOX_REDIRECT_URI+is+not+set", request.nextUrl.origin));
  }
  const state = randomBytes(16).toString("hex");
  const dialog = new URL("https://api.upstox.com/v2/login/authorization/dialog");
  dialog.search = new URLSearchParams({ client_id: clientId, redirect_uri: redirectUri, response_type: "code", state }).toString();
  const response = NextResponse.redirect(dialog);
  response.cookies.set(STATE_COOKIE, state, {
    httpOnly: true,
    sameSite: "lax", // must survive the top-level redirect back from Upstox
    secure: redirectUri.startsWith("https://"),
    maxAge: 600,
    path: "/api/upstox",
  });
  return response;
}
