"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { engineOffset, useCockpit, useNow } from "@/lib/data";
import { clock, duration, engineStatus } from "@/lib/time";
import { ActionButton, CommandStatus, useCommand } from "./commands";
import { Icon, type IconName, Logo } from "./icons";
import { Chip } from "./ui";

const NAV: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Live", icon: "pulse" },
  { href: "/decisions", label: "Decisions", icon: "list" },
  { href: "/trades", label: "Trades", icon: "chart" },
  { href: "/control", label: "Control", icon: "shield" },
  { href: "/events", label: "Events", icon: "scroll" },
];

function Nav({ className = "", stacked = false }: { className?: string; stacked?: boolean }) {
  const path = usePathname();
  return (
    <nav aria-label="Main" className={className}>
      {NAV.map((n) => {
        const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
        return (
          <Link
            key={n.href}
            href={n.href}
            aria-current={active ? "page" : undefined}
            className={`flex cursor-pointer items-center justify-center rounded-xl font-medium transition-colors duration-200 ${
              stacked ? "min-h-12 flex-col gap-0.5 text-xxs" : "gap-2 px-3.5 py-2 text-sm"
            } ${active ? "bg-white/10 text-white shadow-[inset_0_1px_0_rgba(255,255,255,0.1)]" : "text-dim hover:bg-white/5 hover:text-ink"}`}
          >
            <Icon name={n.icon} size={stacked ? 18 : 16} className={active ? "text-accent-ink" : ""} />
            {n.label}
          </Link>
        );
      })}
    </nav>
  );
}

/** null until there is something to go on. */
export function useEngineStatus(now: number) {
  const { state, heartbeatSeenAt, heardAt, connection, ready } = useCockpit();
  if (!ready || now === 0) return null;
  return engineStatus(state.heartbeat?.ts, heartbeatSeenAt, heardAt(), connection !== "offline", now);
}

export function Header() {
  const { state, connection } = useCockpit();
  const now = useNow();
  const { heartbeat, session, upstox_auth: auth } = state;
  const mode = session?.mode ?? heartbeat?.mode;
  const status = useEngineStatus(now);
  const offline = status === "offline";
  const engineNow = now + engineOffset(state);

  return (
    <header className="sticky top-0 z-20 border-b border-line bg-bg/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5">
        <div className="flex items-center gap-2.5">
          <Logo />
          <div className="leading-tight">
            <p className="gradient-text text-sm font-bold tracking-tight">NatGas Trader</p>
            <p className="text-xxs text-faint" title={session?.expiry ? `expires ${session.expiry}` : undefined}>
              {session?.trading_symbol ?? session?.symbol ?? "MCX natural gas"}
            </p>
          </div>
        </div>
        {mode && (
          <Chip tone={mode === "live" ? "solid-down" : "accent"} testId="mode-chip">
            {mode.toUpperCase()}
          </Chip>
        )}
        <Nav className="mx-auto hidden gap-1 rounded-2xl border border-line bg-black/30 p-1 md:flex" />
        <p className="num ml-auto text-right text-sm font-semibold md:ml-0" aria-label="Time in India">
          {now ? clock(engineNow) : ""} <span className="text-xxs font-normal text-faint">IST</span>
        </p>
      </div>
      <div className="border-t border-line">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-1.5 px-3 py-1.5">
          {status && (
            <Chip tone={status === "ok" ? "up" : offline ? "solid-down" : "neutral"} testId="engine-chip" dot pulse={status === "ok"}>
              {status === "ok" ? "Engine ok" : offline ? "Engine offline" : "Checking…"}
            </Chip>
          )}
          {session && (
            <Chip tone={session.market_open && !offline ? "up" : "neutral"} dot>
              {session.market_open ? "Market open" : "Market closed"}
            </Chip>
          )}
          {auth &&
            (auth.simulated ? (
              <Chip dot>Simulated feed</Chip>
            ) : (
              <Chip tone={auth.authenticated ? "up" : "warn"} dot>
                {auth.authenticated ? "Upstox connected" : "Upstox login needed"}
              </Chip>
            ))}
          {session && (
            <Chip tone={session.kill ? "solid-down" : "neutral"} dot>
              {session.kill ? "Kill on" : "Kill off"}
            </Chip>
          )}
          {session?.candle_min && <Chip>{session.candle_min}-minute candles</Chip>}
          {connection === "offline" && <Chip tone="warn">Reconnecting</Chip>}
        </div>
      </div>
    </header>
  );
}

function Banner({ tone, children, testId }: { tone: "warn" | "down"; children: React.ReactNode; testId?: string }) {
  const style = tone === "warn" ? "border-warn/45 bg-warn/10 text-warn" : "border-down/55 bg-down/10 text-down-ink";
  return (
    <div role={tone === "down" ? "alert" : "status"} className={`rise flex flex-wrap items-center gap-3 rounded-2xl border px-4 py-3 text-sm backdrop-blur ${style}`} data-testid={testId}>
      <Icon name="alert" size={18} />
      {children}
    </div>
  );
}

function UpstoxResult() {
  const params = useSearchParams();
  const result = params.get("upstox");
  const { state } = useCockpit();
  if (result === "sent" && !state.upstox_auth?.authenticated) {
    return <Banner tone="warn">Login code sent to the engine. Waiting for it to confirm…</Banner>;
  }
  if (result === "error") {
    return <Banner tone="down">Upstox login failed: {params.get("reason") ?? "unknown error"}</Banner>;
  }
  return null;
}

export function Banners() {
  const { state, loadError } = useCockpit();
  const now = useNow();
  const { heartbeat, session, upstox_auth: auth, engine_error: error } = state;
  const offline = useEngineStatus(now) === "offline";
  return (
    <div className="space-y-2 empty:hidden">
      {loadError && <Banner tone="down">Could not load data: {loadError}</Banner>}
      {auth && !auth.authenticated && !auth.simulated && (
        <Banner tone="warn" testId="upstox-banner">
          <span className="min-w-0 flex-1 basis-60">
            Upstox login needed: tokens expire around 03:30 IST every day. {auth.error ?? "No market data until you log in."}
          </span>
          {/* a plain link: this must be a full navigation to Upstox */}
          <a className="btn btn-primary" href="/api/upstox/login">
            Log in to Upstox
          </a>
        </Banner>
      )}
      <Suspense fallback={null}>
        <UpstoxResult />
      </Suspense>
      {offline && (
        <Banner tone="down">
          <span className="min-w-0 flex-1">
            Engine offline{heartbeat ? ` (last heartbeat ${duration(now - Date.parse(heartbeat.ts))} ago)` : " (no heartbeat yet)"}.
            Nothing is being monitored; in live mode only the exchange stop protects an open position.
          </span>
        </Banner>
      )}
      {session?.orphan && <Banner tone="down">The broker holds a position this app does not know about. Entries are blocked; check Upstox.</Banner>}
      {session?.safety_halt && <Banner tone="down">Safety halt: {session.safety_halt}. Check Events, then press Resume on Control.</Banner>}
      {error && (
        <Banner tone="warn">
          Engine error ({error.source}): {error.msg}
        </Banner>
      )}
    </div>
  );
}

/** Phone: nav and a thumb-sized kill button pinned to the bottom. */
export function MobileBar() {
  const { state } = useCockpit();
  const { send, command, error, pending } = useCommand();
  const killed = state.session?.kill ?? false;
  return (
    <div className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-bg/90 pb-[env(safe-area-inset-bottom)] backdrop-blur-xl md:hidden">
      {(command || error) && (
        <div className="border-b border-line px-3 py-1">
          <CommandStatus command={command} error={error} />
        </div>
      )}
      <div className="px-3 pt-2">
        {killed ? (
          <ActionButton type="resume" label="Resume trading" className="btn btn-primary h-14 w-full text-base" send={send} disabled={pending} />
        ) : (
          <ActionButton type="kill" label="KILL SWITCH" className="btn btn-danger h-14 w-full text-base tracking-widest" send={send} disabled={pending} />
        )}
      </div>
      <Nav className="grid grid-cols-5 gap-1 p-1.5" stacked />
    </div>
  );
}
