"use server";

import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";
import { passwordMatches } from "@/lib/auth";
import { execute, queryJson } from "@/lib/db";
import { createSession, SESSION_COOKIE, SESSION_SECONDS, setupProblem } from "@/lib/session";

export interface LoginState {
  message?: string;
}

const PER_ADDRESS = 5;
const IN_TOTAL = 50;

export async function signIn(_previous: LoginState, form: FormData): Promise<LoginState> {
  const token = await createSession();
  if (!token) return { message: `Not set up: ${setupProblem()}` };
  const password = String(form.get("password") ?? "");
  const ip = (await headers()).get("x-forwarded-for")?.split(",")[0]?.trim().slice(0, 64) || "local";
  try {
    // Counted before it is judged, so parallel guesses cannot slip under the limit.
    await execute("insert into public.login_attempts (ip) values ($1)", [ip]);
    const recent = await queryJson<{ mine: number; all: number }>(
      `select json_build_object('mine', count(*) filter (where ip = $1), 'all', count(*)) as j
         from public.login_attempts where ts > now() - interval '15 minutes'`,
      [ip],
    );
    if (recent.mine > PER_ADDRESS || recent.all > IN_TOTAL) return { message: "Too many attempts. Wait 15 minutes." };
    if (!passwordMatches(password)) return { message: "Wrong password." };
    await execute("delete from public.login_attempts where ip = $1 or ts < now() - interval '1 day'", [ip]);
  } catch (error) {
    console.error("sign-in failed:", error instanceof Error ? error.message : error);
    return { message: "The database did not answer. Try again." };
  }
  (await cookies()).set(SESSION_COOKIE, token, {
    httpOnly: true,
    sameSite: "lax", // must survive the redirect back from Upstox
    secure: process.env.NODE_ENV === "production",
    path: "/",
    maxAge: SESSION_SECONDS,
  });
  redirect("/");
}
