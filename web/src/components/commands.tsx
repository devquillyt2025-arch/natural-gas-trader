"use client";

import { useEffect, useRef, useState } from "react";
import { useCockpit } from "@/lib/data";
import type { Command, CommandType } from "@/lib/types";
import { Icon, type IconName } from "./icons";

export function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel,
  danger,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        onCancel();
      }}
      className={`glass w-[min(92vw,26rem)] p-0 text-ink ${danger ? "border-down/50 shadow-glow-down" : "border-line-strong"}`}
      aria-labelledby="confirm-title"
    >
      <div className="p-5">
        <div className="flex items-center gap-3">
          <span className={`inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${danger ? "bg-down/15 text-down-ink" : "bg-accent/15 text-accent-ink"}`}>
            <Icon name={danger ? "alert" : "info"} size={20} />
          </span>
          <h2 id="confirm-title" className="text-base font-semibold">
            {title}
          </h2>
        </div>
        <p className="mt-3 text-sm leading-relaxed text-dim">{body}</p>
        <div className="mt-5 grid grid-cols-2 gap-2">
          <button className="btn min-h-12" onClick={onCancel} autoFocus>
            Cancel
          </button>
          <button className={`btn min-h-12 ${danger ? "btn-danger" : "btn-primary"}`} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </dialog>
  );
}

/** Send a command and follow it until the engine acknowledges it. */
export function useCommand() {
  const { sendCommand, commands } = useCockpit();
  const [sent, setSent] = useState<Command | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const latest = sent ? (commands.find((c) => c.id === sent.id) ?? sent) : null;

  const send = async (type: CommandType, payload?: Record<string, unknown>) => {
    setBusy(true);
    setError(null);
    setSent(null);
    try {
      setSent(await sendCommand(type, payload));
    } catch (e) {
      setError(e instanceof Error ? e.message : "could not send");
    } finally {
      setBusy(false);
    }
  };
  return { send, command: latest, error, pending: busy || latest?.status === "pending" };
}

export function CommandStatus({ command, error }: { command: Command | null; error: string | null }) {
  if (error) {
    return (
      <p role="alert" className="flex items-center gap-2 text-sm text-down-ink">
        <Icon name="alert" size={14} /> Not sent: {error}
      </p>
    );
  }
  if (!command) return null;
  if (command.status === "pending") {
    return (
      <p role="status" className="flex items-center gap-2 text-sm text-warn">
        <span className="live-dot pulse" aria-hidden />
        <span>{command.type}: sent, waiting for the engine…</span>
      </p>
    );
  }
  return (
    <p role="status" className={`flex items-center gap-2 text-sm ${command.status === "acked" ? "text-up-ink" : "text-down-ink"}`}>
      <Icon name={command.status === "acked" ? "check" : "cross"} size={14} />
      <span>
        {command.type} {command.status === "acked" ? "done" : "failed"}: {command.result}
      </span>
    </p>
  );
}

const COPY: Record<"kill" | "resume" | "flatten", { title: string; body: string; confirm: string; danger: boolean; icon: IconName }> = {
  kill: {
    title: "Kill switch",
    body: "Block all new entries and close any open position now.",
    confirm: "Kill now",
    danger: true,
    icon: "power",
  },
  resume: {
    title: "Resume trading",
    body: "Release the kill switch and clear any safety halt. The daily loss cap still applies.",
    confirm: "Resume",
    danger: false,
    icon: "play",
  },
  flatten: {
    title: "Flatten now",
    body: "Close the open position at market. New entries stay allowed.",
    confirm: "Flatten",
    danger: true,
    icon: "flatten",
  },
};

export function ActionButton({
  type,
  label,
  className,
  send,
  disabled,
}: {
  type: "kill" | "resume" | "flatten";
  label: string;
  className: string;
  send: (type: CommandType) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const copy = COPY[type];
  return (
    <>
      <button className={className} onClick={() => setOpen(true)} disabled={disabled} data-testid={`btn-${type}`}>
        <Icon name={copy.icon} size={18} />
        {label}
      </button>
      <ConfirmDialog
        open={open}
        title={copy.title}
        body={copy.body}
        confirmLabel={copy.confirm}
        danger={copy.danger}
        onCancel={() => setOpen(false)}
        onConfirm={() => {
          setOpen(false);
          send(type);
        }}
      />
    </>
  );
}
