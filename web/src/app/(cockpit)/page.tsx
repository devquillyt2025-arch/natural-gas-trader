"use client";

import { useCallback, useMemo, useState } from "react";
import { DecisionFeed, LatestDecision, PositionCard, RiskBudget, SessionCard } from "@/components/cards";
import { DecisionDrawer } from "@/components/decision-detail";
import { Hero } from "@/components/hero";
import { PriceChart } from "@/components/price-chart";
import { useEngineStatus } from "@/components/shell";
import { ErrorBoundary, Panel } from "@/components/ui";
import { engineOffset, useCockpit, useNow } from "@/lib/data";
import { activity } from "@/lib/status";
import { dayTime } from "@/lib/time";
import type { Decision } from "@/lib/types";

export default function LivePage() {
  const data = useCockpit();
  const clockNow = useNow();
  const now = clockNow ? clockNow + engineOffset(data.state) : 0;
  const engine = useEngineStatus(clockNow);
  const [picked, setPicked] = useState<Decision[]>([]);
  const close = useCallback(() => setPicked([]), []);
  const { session, position, limits, ltp, news_brief: news, upstox_auth: auth } = data.state;
  const mode = session?.mode ?? data.state.heartbeat?.mode;
  const decisions = useMemo(() => data.decisions.filter((d) => !mode || d.mode === mode), [data.decisions, mode]);
  const { ready } = data;
  const status = activity({ engine, authenticated: !auth || auth.authenticated || !!auth.simulated, session, position, now });

  return (
    <>
      <ErrorBoundary label="Market summary">
        <Hero
          price={ltp?.price ?? null}
          session={session}
          intraday={data.intraday}
          forming={data.state.forming}
          daily={data.candlesDaily}
          now={now}
          status={status}
        />
      </ErrorBoundary>

      {/* Desktop: chart and feed on the left, status rail on the right.
          Phones: the two columns dissolve into one so the position can come first. */}
      <div className="flex flex-col gap-3 lg:grid lg:grid-cols-[minmax(0,1fr)_22rem] lg:items-start">
        <div className="contents lg:block lg:min-w-0 lg:space-y-3">
          <div className="order-2 min-w-0">
            <ErrorBoundary label="Chart">
              <PriceChart
                intraday={data.intraday}
                forming={data.state.forming}
                candlesDaily={data.candlesDaily}
                decisions={decisions}
                position={position}
                session={session}
                now={now}
                ready={ready}
                onPick={setPicked}
              />
            </ErrorBoundary>
          </div>
          <div className="order-7 min-w-0">
            <ErrorBoundary label="Decision feed">
              <DecisionFeed decisions={decisions} onSelect={(d) => setPicked([d])} ready={ready} />
            </ErrorBoundary>
          </div>
        </div>
        <aside className="contents lg:block lg:space-y-3" aria-label="Status">
          <div className="order-1">
            <ErrorBoundary label="Position">
              <PositionCard position={position} ltp={ltp?.price ?? null} now={now} ready={ready} />
            </ErrorBoundary>
          </div>
          <div className="order-3">
            <ErrorBoundary label="Risk budget">
              <RiskBudget limits={limits} ready={ready} />
            </ErrorBoundary>
          </div>
          <div className="order-4">
            <ErrorBoundary label="Session">
              <SessionCard session={session} now={now} ready={ready} />
            </ErrorBoundary>
          </div>
          <div className="order-5">
            <ErrorBoundary label="Latest decision">
              <LatestDecision decision={decisions[0]} ready={ready} />
            </ErrorBoundary>
          </div>
          {news && (
            <div className="order-6">
              <Panel title={`News brief · ${dayTime(news.ts)}`} icon="news" delay={200}>
                <p className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words px-4 py-3 text-xs leading-relaxed text-dim">{news.text}</p>
              </Panel>
            </div>
          )}
        </aside>
      </div>
      <DecisionDrawer decisions={picked} onClose={close} />
    </>
  );
}
