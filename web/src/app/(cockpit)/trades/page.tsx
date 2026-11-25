"use client";

import { useEffect, useMemo, useState } from "react";
import { EquityCurve } from "@/components/equity";
import { Icon, type IconName } from "@/components/icons";
import { PnlCalendar } from "@/components/pnl-calendar";
import { Chip, delay, Empty, Panel, Ring, Skeleton } from "@/components/ui";
import { download, tradesCsv } from "@/lib/csv";
import { useCockpit } from "@/lib/data";
import { DASH, percent, pnl, price, rMultiple, rupees, toneClass } from "@/lib/format";
import { computeStats, type Stats } from "@/lib/stats";
import { dayTime } from "@/lib/time";
import { useTrades } from "@/lib/trades";
import type { Mode, Trade } from "@/lib/types";

function Tile({ label, value, className = "", note, icon, at }: { label: string; value: string; className?: string; note?: string; icon: IconName; at: number }) {
  return (
    <div className="glass rise px-4 py-3" style={delay(at)}>
      <p className="label flex items-center gap-1.5">
        <Icon name={icon} size={13} className="text-faint" />
        {label}
      </p>
      <p className={`num mt-1.5 text-xl font-semibold ${className}`}>{value}</p>
      {note && <p className="num text-xxs text-faint">{note}</p>}
    </div>
  );
}

function StatTiles({ s }: { s: Stats }) {
  const share = (n: number) => (s.count ? percent(n / s.count) : DASH);
  const glow = !s.count || s.netPnl === 0 ? "" : s.netPnl > 0 ? "border-up/40 shadow-glow-up" : "border-down/40 shadow-glow-down";
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="stats">
      <div className={`glass rise col-span-2 flex items-center justify-between gap-4 px-5 py-4 md:row-span-2 md:px-8 ${glow}`}>
        <div>
          <p className="label">Net P&L</p>
          <p className={`num mt-1 text-4xl font-semibold tracking-tight ${toneClass(s.netPnl)}`}>{pnl(s.count ? s.netPnl : null)}</p>
          <p className="num text-xxs text-faint">after estimated costs</p>
        </div>
        <div className="text-center">
          <Ring share={s.winRate ?? 0} size={84} color="#2ee6b6">
            <span className="text-sm">{percent(s.winRate)}</span>
          </Ring>
          <p className="label mt-1">Win rate</p>
          <p className="num text-xxs text-faint">{s.wins} won / {s.losses} lost</p>
        </div>
      </div>
      <Tile at={40} icon="swap" label="Trades" value={String(s.count)} note={`${s.tradingDays} ${s.tradingDays === 1 ? "day" : "days"} with trades`} />
      <Tile at={70} icon="target" label="Expectancy" value={rMultiple(s.expectancyR)} className={toneClass(s.expectancyR)} note="mean R per trade" />
      <Tile at={100} icon="gauge" label="Avg win / loss" value={`${rupees(s.avgWin)} / ${rupees(s.avgLoss)}`} />
      <Tile at={130} icon="chart" label="Profit factor" value={s.profitFactor === null ? (s.wins ? "no losses" : DASH) : s.profitFactor.toFixed(2)} />
      <Tile at={160} icon="down" label="Max drawdown" value={s.count ? rupees(s.maxDrawdown) : DASH} />
      <Tile at={220} icon="trophy" label="Best day" value={pnl(s.bestDay?.pnl)} className={toneClass(s.bestDay?.pnl)} note={s.bestDay?.date} />
      <Tile at={250} icon="alert" label="Worst day" value={pnl(s.worstDay?.pnl)} className={toneClass(s.worstDay?.pnl)} note={s.worstDay?.date} />
      <div className="glass rise px-4 py-3" style={delay(190)}>
        <p className="label">Exits by</p>
        <dl className="num mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 text-xs">
          {(["stop", "target", "agent", "forced"] as const).map((k) => (
            <div key={k} className="flex justify-between">
              <dt className="text-dim">{k}</dt>
              <dd>{share(s.exits[k])}</dd>
            </div>
          ))}
          {s.exits.other > 0 && (
            <div className="flex justify-between">
              <dt className="text-dim">external</dt>
              <dd>{share(s.exits.other)}</dd>
            </div>
          )}
        </dl>
      </div>
    </div>
  );
}

function Journal({ trades }: { trades: Trade[] }) {
  const [limit, setLimit] = useState(100);
  const newest = useMemo(() => [...trades].reverse(), [trades]);
  if (trades.length === 0) return <Empty icon="swap">No trades yet</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr>
            {["Opened", "Closed", "Side", "Entry", "Exit", "P&L", "R", "Exit reason", "Claude's entry reasoning"].map((h) => (
              <th key={h} className="th">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {newest.slice(0, limit).map((t) => (
            <tr key={t.id} className="row-hover">
              <td className="td num whitespace-nowrap">{dayTime(t.opened_at)}</td>
              <td className="td num whitespace-nowrap">{dayTime(t.closed_at)}</td>
              <td className="td whitespace-nowrap">
                <Chip tone={t.side === "LONG" ? "up" : "down"}>
                  {t.side} {t.lots}
                </Chip>
              </td>
              <td className="td num">{price(t.entry)}</td>
              <td className="td num">{price(t.exit)}</td>
              <td className={`td num font-semibold ${toneClass(t.pnl)}`}>{pnl(t.pnl)}</td>
              <td className={`td num ${toneClass(t.r_multiple)}`}>{rMultiple(t.r_multiple)}</td>
              <td className="td whitespace-nowrap text-dim">{t.exit_reason}</td>
              <td className="td min-w-64 break-words text-xs text-dim">{t.reasoning}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {newest.length > limit && (
        <div className="p-3 text-center">
          <button className="btn" onClick={() => setLimit(limit + 200)}>
            Show more
          </button>
        </div>
      )}
    </div>
  );
}

export default function TradesPage() {
  const { state } = useCockpit();
  const engineMode = state.session?.mode ?? state.heartbeat?.mode ?? "paper";
  const [mode, setMode] = useState<Mode>("paper");
  useEffect(() => setMode(engineMode), [engineMode]);
  const { trades, error } = useTrades(mode);
  const shown = trades?.filter((t) => t.mode === mode) ?? [];
  const stats = computeStats(shown);

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div role="tablist" aria-label="Account" className="seg">
          {(["paper", "live"] as const).map((m) => (
            <button key={m} role="tab" aria-selected={mode === m} className={`seg-item px-5 py-1.5 text-sm ${mode === m ? "seg-on" : ""}`} onClick={() => setMode(m)}>
              {m === "paper" ? "Paper" : "Live"}
            </button>
          ))}
        </div>
        <button
          className="btn"
          disabled={shown.length === 0}
          onClick={() => download(`natgas-${mode}-trades-${new Date().toISOString().slice(0, 10)}.csv`, tradesCsv(shown))}
        >
          <Icon name="download" /> Export CSV
        </button>
      </div>
      {error && <p className="text-sm text-down-ink">{error}</p>}
      {trades === null && !error ? (
        <div className="glass">
          <Skeleton lines={8} />
        </div>
      ) : (
        <>
          <StatTiles s={stats} />
          <div className="grid gap-3 xl:grid-cols-2">
            <Panel title="Equity curve (cumulative ₹, net)" icon="chart" delay={120}>
              <EquityCurve trades={shown} />
            </Panel>
            <Panel title="Daily P&L" icon="calendar" delay={160}>
              <PnlCalendar trades={shown} />
            </Panel>
          </div>
          <Panel title="Journal" icon="scroll" testId="journal" delay={200}>
            <Journal trades={shown} />
          </Panel>
        </>
      )}
    </>
  );
}
