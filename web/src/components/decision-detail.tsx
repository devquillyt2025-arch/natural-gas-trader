"use client";

import { useEffect, useRef, useState } from "react";
import { getJson } from "@/lib/api";
import { actionLabel, DASH, price, reasonLabel } from "@/lib/format";
import { dayTime } from "@/lib/time";
import type { Decision } from "@/lib/types";
import { ActionBadge, actionTone, Verdict } from "./cards";
import { Icon } from "./icons";
import { Chip, Row, Skeleton } from "./ui";

/** The exact JSON sent to the model, fetched only when asked for. */
export function Snapshot({ id }: { id: number }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open || text !== null) return;
    getJson<{ snapshot: unknown }>(`/api/decisions/${id}`).then(
      ({ snapshot }) =>
        setText(snapshot ? JSON.stringify(snapshot, null, 2) : "No snapshot: this was a code-level action, not a model decision."),
      (e: Error) => setError(e.message),
    );
  }, [open, text, id]);

  return (
    <div className="mt-3">
      <div className="flex flex-wrap gap-2">
        <button className="btn" aria-expanded={open} onClick={() => setOpen(!open)}>
          <Icon name="scroll" /> {open ? "Hide" : "Show"} snapshot sent to Claude
        </button>
        {open && text && (
          <button
            className="btn"
            onClick={() => {
              void navigator.clipboard.writeText(text).then(() => setCopied(true));
            }}
          >
            {copied ? "Copied" : "Copy JSON"}
          </button>
        )}
      </div>
      {open &&
        (error ? (
          <p className="mt-2 text-sm text-down-ink">{error}</p>
        ) : text === null ? (
          <Skeleton lines={4} />
        ) : (
          <pre className="num mt-2 max-h-96 overflow-auto rounded-xl border border-line bg-black/40 p-3 text-xxs leading-4 text-dim">{text}</pre>
        ))}
    </div>
  );
}

export function DecisionBody({ decision }: { decision: Decision }) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2.5">
        <ActionBadge action={decision.action} size={34} />
        <Chip tone={actionTone(decision.action)}>{actionLabel(decision.action)}</Chip>
        <span className="num text-xs text-dim">{dayTime(decision.ts)} IST</span>
        {!decision.approved && <Chip tone="warn">{reasonLabel(decision.verdict_code)}</Chip>}
      </div>
      <div className="tile mt-3 max-w-sm">
        <Row label="Price at decision">{price(decision.price)}</Row>
        <Row label="Confidence">{decision.confidence === null ? DASH : decision.confidence.toFixed(2)}</Row>
        {decision.lots !== null && <Row label="Lots">{decision.lots}</Row>}
        {decision.stop_loss !== null && <Row label="Stop">{price(decision.stop_loss)}</Row>}
        {decision.target !== null && <Row label="Target">{price(decision.target)}</Row>}
      </div>
      <h3 className="label mt-4">Reasoning</h3>
      {/* model text is rendered as text, never as HTML */}
      <p className="mt-1 whitespace-pre-wrap break-words border-l-2 border-accent/60 pl-3 text-sm leading-relaxed">{decision.reasoning || DASH}</p>
      <h3 className="label mt-4">Risk layer</h3>
      <p className="mt-1 text-sm">
        <Verdict decision={decision} />
      </p>
      <Snapshot id={decision.id} />
    </div>
  );
}

export function DecisionDrawer({ decisions, onClose }: { decisions: Decision[]; onClose: () => void }) {
  const open = decisions.length > 0;
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <aside
      role="dialog"
      aria-label="Decision detail"
      data-testid="decision-drawer"
      className="drawer-in fixed inset-x-0 bottom-0 z-40 max-h-[80vh] overflow-auto rounded-t-2xl border-t border-line-strong bg-panel/95 shadow-card backdrop-blur-xl md:inset-y-0 md:left-auto md:right-0 md:max-h-none md:w-[30rem] md:rounded-none md:rounded-l-2xl md:border-l md:border-t-0"
    >
      <div className="sticky top-0 flex items-center justify-between border-b border-line bg-panel/95 px-4 py-3 backdrop-blur">
        <h2 className="label">{decisions.length > 1 ? `${decisions.length} decisions on this candle` : "Decision"}</h2>
        <button ref={closeRef} className="btn" onClick={onClose}>
          <Icon name="cross" size={14} /> Close
        </button>
      </div>
      <div className="divide-y divide-line">
        {decisions.map((d) => (
          <div key={d.id} className="p-4">
            <DecisionBody decision={d} />
          </div>
        ))}
      </div>
    </aside>
  );
}
