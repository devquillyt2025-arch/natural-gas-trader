// Checks for values arriving from the browser. Everything reaches SQL as a parameter; this keeps it sane first.
export class BadRequest extends Error {}

export function wholeNumber(value: string | null, fallback: number): number {
  if (value === null || value === "") return fallback;
  const n = Number(value);
  if (!/^\d+$/.test(value) || !Number.isSafeInteger(n)) throw new BadRequest("not a whole number");
  return n;
}

export function instant(value: string | null): string | null {
  if (!value) return null;
  if (!/^\d{4}-\d{2}-\d{2}T/.test(value) || Number.isNaN(Date.parse(value))) throw new BadRequest("not a time");
  return value;
}

export function day(value: string | null): string | null {
  if (!value) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || Number.isNaN(Date.parse(value))) throw new BadRequest("not a date");
  return value;
}

export function oneOf<T extends string>(value: string | null, allowed: readonly T[], fallback: T): T {
  if (value === null || value === "") return fallback;
  if (!(allowed as readonly string[]).includes(value)) throw new BadRequest(`expected one of ${allowed.join(", ")}`);
  return value as T;
}

export function fraction(value: string | null): number | null {
  if (value === null || value === "") return null;
  const n = Number(value);
  if (!Number.isFinite(n)) throw new BadRequest("not a number");
  return n;
}
