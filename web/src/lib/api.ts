// Browser calls to this app's own routes. The session cookie goes along automatically.
async function read<T>(response: Response): Promise<T> {
  if (response.status === 401) {
    window.location.assign("/login");
    throw new Error("signed out");
  }
  const data = (await response.json().catch(() => null)) as { error?: string } | null;
  if (!response.ok || data === null) throw new Error(data?.error ?? `request failed (${response.status})`);
  return data as T;
}

export const getJson = async <T>(url: string): Promise<T> => read<T>(await fetch(url, { cache: "no-store" }));

export const postJson = async <T>(url: string, body: unknown): Promise<T> =>
  read<T>(await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }));
