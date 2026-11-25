import { type NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, validSession } from "@/lib/session";

export async function middleware(request: NextRequest) {
  const path = request.nextUrl.pathname;
  if (path === "/login" || (await validSession(request.cookies.get(SESSION_COOKIE)?.value))) return NextResponse.next();
  if (path.startsWith("/api/")) return NextResponse.json({ error: "unauthorised" }, { status: 401 });
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = "";
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg).*)"],
};
