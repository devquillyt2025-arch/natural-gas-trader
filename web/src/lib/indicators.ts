/** Same recursion as engine/indicators.py, so chart overlays match what the model saw. */
export function ema(values: number[], n: number): number[] {
  if (values.length === 0) return [];
  const k = 2 / (n + 1);
  const out: number[] = [values[0]!];
  for (let i = 1; i < values.length; i++) out.push(values[i]! * k + out[i - 1]! * (1 - k));
  return out;
}
