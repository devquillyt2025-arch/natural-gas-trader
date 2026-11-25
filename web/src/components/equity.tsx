"use client";

import { AreaSeries, ColorType, createChart, LineStyle, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import { COLORS } from "@/lib/chart";
import { pnl, toneClass } from "@/lib/format";
import { equityCurve } from "@/lib/stats";
import { chartTime } from "@/lib/time";
import type { Trade } from "@/lib/types";
import { Empty } from "./ui";

export function EquityCurve({ trades }: { trades: Trade[] }) {
  const container = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<number | null>(null);
  const points = equityCurve(trades);
  const last = points.at(-1)?.equity ?? 0;

  useEffect(() => {
    if (!container.current || trades.length === 0) return;
    const chart = createChart(container.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: COLORS.dim, fontSize: 11, fontFamily: "JetBrains Mono Variable, monospace" },
      grid: { vertLines: { visible: false }, horzLines: { color: COLORS.grid } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderColor: COLORS.line, timeVisible: false },
      localization: { priceFormatter: (v: number) => pnl(v) },
    });
    const series = chart.addSeries(AreaSeries, {
      lineColor: COLORS.accent,
      topColor: "rgba(91,140,255,0.38)",
      bottomColor: "rgba(91,140,255,0.02)",
      lineWidth: 2,
      priceLineVisible: false,
      pointMarkersVisible: trades.length < 40,
      // keep zero in view so the curve reads as profit or loss, and one trade does not collapse the axis
      autoscaleInfoProvider: (original: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const range = original()?.priceRange;
        return { priceRange: { minValue: Math.min(0, range?.minValue ?? 0), maxValue: Math.max(0, range?.maxValue ?? 0) } };
      },
    });
    // the chart needs unique, ascending times: keep the last value for any repeated second
    const unique = new Map(equityCurve(trades).map((p) => [chartTime(p.ts), p.equity]));
    series.setData([...unique].map(([time, value]) => ({ time: time as UTCTimestamp, value })));
    series.createPriceLine({ price: 0, color: "rgba(255,255,255,0.22)", lineStyle: LineStyle.Dashed, lineWidth: 1, axisLabelVisible: false, title: "" });
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove((p) => {
      const point = p.seriesData.get(series) as { value?: number } | undefined;
      setHover(point?.value ?? null);
    });
    return () => chart.remove();
  }, [trades]);

  if (trades.length === 0) return <Empty icon="chart">No trades yet</Empty>;
  return (
    <div>
      <p className="num px-4 pt-3 text-sm text-dim">
        {hover === null ? "Cumulative" : "At cursor"}: <span className={`text-xl font-semibold ${toneClass(hover ?? last)}`}>{pnl(hover ?? last)}</span>
        <span className="ml-3 text-xs text-faint">{points.length} {points.length === 1 ? "trade" : "trades"}</span>
      </p>
      <div ref={container} className="h-64" role="img" aria-label={`Equity curve ending at ${pnl(last)}`} />
    </div>
  );
}
