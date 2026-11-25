"use client";

import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  HistogramSeries,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  LineSeries,
  LineStyle,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";
import { buildMarkers, COLORS, decisionsAt, futureSlots, shading, type Timeframe } from "@/lib/chart";
import { price } from "@/lib/format";
import { ema } from "@/lib/indicators";
import { chartTime } from "@/lib/time";
import type { Candle, Decision, PositionState, SessionState } from "@/lib/types";
import { Empty, Panel, Skeleton } from "./ui";

interface Refs {
  chart: IChartApi;
  candles: ISeriesApi<"Candlestick">;
  emas: ISeriesApi<"Line">[];
  volume: ISeriesApi<"Histogram">;
  shade: ISeriesApi<"Histogram">;
  markers: ISeriesMarkersPluginApi<Time>;
  lines: IPriceLine[];
}

const EMAS = [
  { n: 9, color: COLORS.ema9 },
  { n: 21, color: COLORS.ema21 },
  { n: 50, color: COLORS.ema50 },
];
const VISIBLE_BARS = { "5m": 130, "15m": 130, "1d": 60 };

const t = (seconds: number) => seconds as UTCTimestamp;

export function PriceChart({
  intraday,
  forming,
  candlesDaily,
  decisions,
  position,
  session,
  now,
  ready,
  onPick,
}: {
  intraday: Candle[];
  forming: Candle | null;
  candlesDaily: Candle[];
  decisions: Decision[];
  position: PositionState | null;
  session: SessionState | null;
  now: number;
  ready: boolean;
  onPick: (decisions: Decision[]) => void;
}) {
  const [daily, setDaily] = useState(false);
  const barTf: Timeframe = session?.candle_min === 5 ? "5m" : "15m";
  const tf: Timeframe = daily ? "1d" : barTf;
  const [volumeOn, setVolumeOn] = useState(true);
  const [holdsOn, setHoldsOn] = useState(true);
  const [levelsOn, setLevelsOn] = useState(true);
  const [hover, setHover] = useState<Candle | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const refs = useRef<Refs | null>(null);
  const fitted = useRef<string>("");
  // the closed bars plus the one still being made, which moves with the price
  const bars = useMemo(() => {
    const last = intraday.at(-1);
    const live = forming && forming.tf === barTf && last && Date.parse(forming.ts) > Date.parse(last.ts);
    return live ? [...intraday, forming] : intraday;
  }, [intraday, forming, barTf]);
  const candles = daily ? candlesDaily : bars;
  const byTime = useMemo(() => new Map(candles.map((c) => [chartTime(c.ts), c])), [candles]);
  // keep the latest values reachable from chart callbacks without re-creating the chart
  const latest = useRef({ decisions, tf, byTime, onPick });
  latest.current = { decisions, tf, byTime, onPick };

  useEffect(() => {
    if (!container.current) return;
    const chart = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: COLORS.dim,
        fontSize: 11,
        fontFamily: "JetBrains Mono Variable, monospace",
      },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.18 } },
      timeScale: { borderColor: COLORS.line, timeVisible: true, secondsVisible: false },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(143,176,255,0.55)", labelBackgroundColor: "#3b6cf5" },
        horzLine: { color: "rgba(143,176,255,0.55)", labelBackgroundColor: "#3b6cf5" },
      },
      localization: { priceFormatter: (p: number) => p.toFixed(2) },
    });
    const shade = chart.addSeries(HistogramSeries, { priceScaleId: "shade", lastValueVisible: false, priceLineVisible: false });
    chart.priceScale("shade").applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 }, visible: false });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: COLORS.up,
      downColor: COLORS.down,
      wickUpColor: COLORS.up,
      wickDownColor: COLORS.down,
      borderVisible: false,
    });
    const emas = EMAS.map((e) =>
      chart.addSeries(LineSeries, {
        color: e.color,
        lineWidth: 1,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      }),
    );
    const markers = createSeriesMarkers(series, []);
    refs.current = { chart, candles: series, emas, volume, shade, markers, lines: [] };

    chart.subscribeCrosshairMove((param) => {
      setHover(param.time === undefined ? null : (latest.current.byTime.get(param.time as number) ?? null));
    });
    chart.subscribeClick((param) => {
      if (param.time === undefined) return;
      const found = decisionsAt(latest.current.decisions, param.time as number, latest.current.tf);
      if (found.length) latest.current.onPick(found);
    });
    return () => {
      chart.remove();
      refs.current = null;
      fitted.current = "";
    };
  }, []);

  const future = useMemo(
    () => (daily ? [] : futureSlots(bars.at(-1), now, session?.market_close)),
    // recompute when a bar arrives or the day changes, not every second
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [daily, bars.at(-1)?.ts, session?.market_close, Math.floor(now / 300_000)],
  );

  useEffect(() => {
    const r = refs.current;
    if (!r) return;
    const times = candles.map((c) => chartTime(c.ts));
    r.candles.setData(candles.map((c) => ({ time: t(chartTime(c.ts)), open: c.o, high: c.h, low: c.l, close: c.c })));
    EMAS.forEach((e, i) => {
      const values = ema(candles.map((c) => c.c), e.n);
      r.emas[i]!.setData(values.map((value, j) => ({ time: t(times[j]!), value })));
    });
    r.volume.setData(
      volumeOn
        ? candles.map((c) => ({ time: t(chartTime(c.ts)), value: c.v, color: c.c >= c.o ? "rgba(0,184,148,0.3)" : "rgba(245,69,92,0.3)" }))
        : [],
    );
    r.shade.setData(
      tf !== "1d" && session
        ? shading([...times, ...future], session.windows, tf).map((s) =>
            s.value === undefined ? { time: t(s.time) } : { time: t(s.time), value: s.value, color: s.color },
          )
        : [],
    );
    r.chart.applyOptions({ timeScale: { timeVisible: tf !== "1d" } });
    if (candles.length && fitted.current !== tf) {
      fitted.current = tf;
      r.chart.timeScale().setVisibleLogicalRange({
        from: candles.length - VISIBLE_BARS[tf],
        to: candles.length + Math.min(future.length, 24) + 2,
      });
    }
  }, [candles, tf, volumeOn, session, future]);

  useEffect(() => {
    const r = refs.current;
    if (!r) return;
    const times = new Set(candles.map((c) => chartTime(c.ts)));
    r.markers.setMarkers(
      buildMarkers(decisions, times, tf, holdsOn).map((m) => ({ ...m, time: t(m.time) }) as SeriesMarker<Time>),
    );
  }, [decisions, candles, tf, holdsOn]);

  const levels = session?.levels;
  useEffect(() => {
    const r = refs.current;
    if (!r) return;
    for (const line of r.lines) r.candles.removePriceLine(line);
    r.lines = [];
    const add = (value: number | null | undefined, color: string, title: string, style: LineStyle, axis: boolean, width: 1 | 2 = 1) => {
      if (value === null || value === undefined) return;
      r.lines.push(r.candles.createPriceLine({ price: value, color, title, lineStyle: style, lineWidth: width, axisLabelVisible: axis }));
    };
    if (levelsOn && levels) {
      add(levels.prev_day_high, "#7f8aa2", "PDH", LineStyle.Dashed, true);
      add(levels.prev_day_low, "#7f8aa2", "PDL", LineStyle.Dashed, true);
      for (const x of levels.resistance ?? []) add(x, "rgba(255,117,134,0.5)", "swing R", LineStyle.Dotted, false);
      for (const x of levels.support ?? []) add(x, "rgba(46,230,182,0.45)", "swing S", LineStyle.Dotted, false);
    }
    if (position) {
      add(position.entry, COLORS.accent, "ENTRY", LineStyle.Solid, true, 2);
      add(position.stop_loss, COLORS.down, "STOP", LineStyle.Solid, true, 2);
      add(position.target, COLORS.up, "TARGET", LineStyle.Solid, true, 2);
    }
  }, [position, levels, levelsOn, candles.length]);

  const shown = hover ?? candles.at(-1) ?? null;
  const toggle = (label: string, on: boolean, set: (v: boolean) => void) => (
    <button className={`seg-item uppercase ${on ? "seg-on" : ""}`} aria-pressed={on} onClick={() => set(!on)}>
      {label}
    </button>
  );

  return (
    <Panel
      testId="price-chart"
      title="Price"
      icon="chart"
      delay={60}
      actions={
        <span className="flex flex-wrap justify-end gap-2">
          <span className="seg" role="group" aria-label="Candle size">
            {([barTf, "1d"] as const).map((x) => (
              <button key={x} className={`seg-item uppercase ${tf === x ? "seg-on" : ""}`} aria-pressed={tf === x} onClick={() => setDaily(x === "1d")}>
                {x === "1d" ? "Daily" : x}
              </button>
            ))}
          </span>
          <span className="seg" role="group" aria-label="Chart layers">
            {toggle("Vol", volumeOn, setVolumeOn)}
            {toggle("Levels", levelsOn, setLevelsOn)}
            {toggle("Holds", holdsOn, setHoldsOn)}
          </span>
        </span>
      }
    >
      <div className="num flex min-h-8 flex-wrap items-center gap-x-4 px-4 py-1.5 text-xs text-dim" aria-live="off" data-testid="ohlc">
        {shown && (
          <>
            <span>O <span className="text-ink">{price(shown.o)}</span></span>
            <span>H <span className="text-ink">{price(shown.h)}</span></span>
            <span>L <span className="text-ink">{price(shown.l)}</span></span>
            <span className={shown.c >= shown.o ? "text-up-ink" : "text-down-ink"}>C {price(shown.c)}</span>
            {!hover && !daily && forming && shown.ts === forming.ts && (
              <span className="inline-flex items-center gap-1.5 text-up-ink">
                <span className="live-dot pulse" aria-hidden /> live candle
              </span>
            )}
          </>
        )}
      </div>
      <div className="relative h-[340px] md:h-[540px]">
        <div ref={container} className="absolute inset-0" data-testid="chart-canvas" />
        {candles.length === 0 && (
          <div className="absolute inset-0 bg-panel/80">
            {ready ? <Empty icon="chart">Waiting for candles from the engine</Empty> : <Skeleton lines={8} />}
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-4 py-2 text-xxs text-dim">
        {EMAS.map((e) => (
          <span key={e.n} className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4 rounded-full" style={{ background: e.color }} />
            EMA {e.n}
          </span>
        ))}
        <span><span className="text-up-ink">▲</span> long</span>
        <span><span className="text-down-ink">▼</span> short</span>
        <span>✕ exit</span>
        <span><span className="text-accent">●</span> trail</span>
        <span><span className="text-warn">●</span> rejected</span>
        <span><span className="text-faint">•</span> hold</span>
        {tf !== "1d" && (
          <>
            <span className="flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded" style={{ background: "rgba(247,185,69,0.45)" }} />EIA blackout</span>
            <span className="flex items-center gap-1.5"><span className="inline-block h-3 w-3 rounded" style={{ background: "rgba(163,173,194,0.22)" }} />no new entries</span>
          </>
        )}
        <span className="ml-auto text-faint">Click a candle for its decisions</span>
      </div>
    </Panel>
  );
}
