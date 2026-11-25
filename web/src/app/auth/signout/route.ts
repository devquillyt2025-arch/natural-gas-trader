import { type NextRequest, NextResponse } from "next/server";
import { crossSite } from "@/lib/auth";
import { SESSION_COOKIE } from "@/lib/session";

export async function POST(request: NextRequest) {
  if (crossSite(request.headers)) return new NextResponse(null, { status: 403 });
  const response = NextResponse.redirect(new URL("/login", request.nextUrl.origin), 303);
  response.cookies.set(SESSION_COOKIE, "", { maxAge: 0, path: "/" });
  return response;
}
