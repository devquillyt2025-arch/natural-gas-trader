"use client";

import { Component, type CSSProperties, type ReactNode } from "react";
import { Icon, type IconName } from "./icons";

export function Panel({
  title,
  icon,
  actions,
  children,
  className = "",
  testId,
  delay = 0,
}: {
  title?: ReactNode;
  icon?: IconName;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  testId?: string;
  /** stagger of the entrance, in ms */
  delay?: number;
}) {
  return (
    <section className={`glass rise min-w-0 overflow-hidden ${className}`} style={{ animationDelay: `${delay}ms` }} data-testid={testId}>
      {title !== undefined && (
        <header className="flex min-h-12 flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2">
          <h2 className="label flex items-center gap-2">
            {icon && (
              <span className="inline-flex h-6 w-6 items-center justify-center rounded-lg bg-accent/15 text-accent-ink">
                <Icon name={icon} size={14} />
              </span>
            )}
            {title}
          </h2>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export type Tone = "neutral" | "up" | "down" | "accent" | "warn" | "solid-down";

const TONES: Record<Tone, string> = {
  neutral: "border-line bg-white/[0.04] text-dim",
  up: "border-up/40 bg-up/10 text-up-ink",
  down: "border-down/45 bg-down/10 text-down-ink",
  accent: "border-accent/45 bg-accent/10 text-accent-ink",
  warn: "border-warn/45 bg-warn/10 text-warn",
  "solid-down": "border-transparent bg-down-solid text-white shadow-glow-down",
};

export function Chip({
  tone = "neutral",
  children,
  title,
  testId,
  dot = false,
  pulse = false,
}: {
  tone?: Tone;
  children: ReactNode;
  title?: string;
  testId?: string;
  dot?: boolean;
  pulse?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 py-0.5 text-xxs font-semibold uppercase tracking-wide ${TONES[tone]}`}
      title={title}
    >
      {dot && <span className={`live-dot ${pulse ? "pulse" : ""}`} aria-hidden />}
      <span data-testid={testId}>{children}</span>
    </span>
  );
}

/** Label / value row used in the side rail. */
export function Row({ label, children, className = "" }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5">
      <span className="text-xs text-dim">{label}</span>
      <span className={`num text-right text-sm ${className}`}>{children}</span>
    </div>
  );
}

export function Skeleton({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2.5 p-4 ${className}`} aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <div key={i} className="shimmer h-3 rounded-full" style={{ width: `${92 - ((i * 17) % 45)}%` }} />
      ))}
    </div>
  );
}

export function Empty({ children, icon = "radar" }: { children: ReactNode; icon?: IconName }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-7 text-center">
      <span className="inline-flex h-10 w-10 items-center justify-center rounded-full border border-line bg-white/[0.03] text-faint">
        <Icon name={icon} size={18} />
      </span>
      <p className="text-sm text-dim">{children}</p>
    </div>
  );
}

/** A circular gauge: share from 0 to 1. Always accompanied by the number as text. */
export function Ring({
  share,
  size = 52,
  color = "#5b8cff",
  label,
  children,
}: {
  label?: string;
  share: number;
  size?: number;
  color?: string;
  children?: ReactNode;
}) {
  const radius = 18;
  const length = 2 * Math.PI * radius;
  const filled = Math.max(0, Math.min(1, share));
  return (
    <span className="relative inline-flex shrink-0 items-center justify-center" style={{ width: size, height: size }} title={label}>
      {label && <span className="sr-only">{label}: </span>}
      <svg viewBox="0 0 44 44" width={size} height={size} className="-rotate-90 overflow-visible" aria-hidden>
        <circle cx="22" cy="22" r={radius} fill="none" stroke="rgba(255,255,255,0.09)" strokeWidth="4" />
        <circle
          cx="22"
          cy="22"
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth="4"
          strokeLinecap="round"
          strokeDasharray={length}
          strokeDashoffset={length * (1 - filled)}
          style={{ transition: "stroke-dashoffset 600ms cubic-bezier(0.16,1,0.3,1)", filter: `drop-shadow(0 0 4px ${color}88)` }}
        />
      </svg>
      <span className="num absolute text-xxs font-semibold">{children}</span>
    </span>
  );
}

export const delay = (ms: number): CSSProperties => ({ animationDelay: `${ms}ms` });

export class ErrorBoundary extends Component<{ children: ReactNode; label: string }, { error: string | null }> {
  override state = { error: null as string | null };

  static getDerivedStateFromError(error: Error) {
    return { error: error.message };
  }

  override render() {
    if (this.state.error === null) return this.props.children;
    return (
      <div role="alert" className="glass border-down/50 p-4 text-sm">
        <p className="flex items-center gap-2 text-down-ink">
          <Icon name="alert" /> {this.props.label} failed to render.
        </p>
        <p className="mt-1 text-xs text-dim">{this.state.error}</p>
        <button className="btn mt-3" onClick={() => this.setState({ error: null })}>
          Retry
        </button>
      </div>
    );
  }
}
