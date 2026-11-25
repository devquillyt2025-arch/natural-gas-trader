// The signed session cookie. Web Crypto only, so the middleware (edge runtime) can verify it too.
export const SESSION_COOKIE = "natgas_session";
export const SESSION_SECONDS = 30 * 86_400;
export const MIN_PASSWORD = 8;
const MIN_SECRET = 32;

const encoder = new TextEncoder();

/** Why nobody can sign in, or null if the settings are good. */
export function setupProblem(): string | null {
  if ((process.env.APP_PASSWORD ?? "").length < MIN_PASSWORD) {
    return `APP_PASSWORD in web/.env.local needs at least ${MIN_PASSWORD} characters.`;
  }
  if ((process.env.SESSION_SECRET ?? "").length < MIN_SECRET) {
    return `SESSION_SECRET in web/.env.local needs at least ${MIN_SECRET} characters. npm run db:setup writes one.`;
  }
  return null;
}

/** null = not set up, so nobody can sign in. The password is part of the key: changing it signs everyone out. */
async function signingKey(): Promise<CryptoKey | null> {
  if (setupProblem()) return null;
  const material = encoder.encode(`${process.env.SESSION_SECRET}\n${process.env.APP_PASSWORD}`);
  return crypto.subtle.importKey("raw", material, { name: "HMAC", hash: "SHA-256" }, false, ["sign", "verify"]);
}

const signed = (expires: number) => encoder.encode(`natgas-session:${expires}`);

export async function createSession(nowMs = Date.now()): Promise<string | null> {
  const key = await signingKey();
  if (!key) return null;
  const expires = Math.floor(nowMs / 1000) + SESSION_SECONDS;
  const signature = new Uint8Array(await crypto.subtle.sign("HMAC", key, signed(expires)));
  return `${expires}.${Array.from(signature, (b) => b.toString(16).padStart(2, "0")).join("")}`;
}

export async function validSession(token: string | undefined, nowMs = Date.now()): Promise<boolean> {
  const match = /^(\d{1,12})\.([0-9a-f]{64})$/.exec(token ?? "");
  if (!match) return false;
  const expires = Number(match[1]);
  if (expires * 1000 <= nowMs) return false;
  const key = await signingKey();
  if (!key) return false;
  const signature = Uint8Array.from(match[2]!.match(/../g)!, (h) => Number.parseInt(h, 16));
  return crypto.subtle.verify("HMAC", key, signature, signed(expires));
}
