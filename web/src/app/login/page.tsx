"use client";

import { useActionState, useState } from "react";
import { Icon, Logo } from "@/components/icons";
import { type LoginState, signIn } from "./actions";

export default function LoginPage() {
  const [state, submit, checking] = useActionState<LoginState, FormData>(signIn, {});
  const [visible, setVisible] = useState(false);

  return (
    <main className="flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="rise w-full max-w-sm">
        <div className="glass relative overflow-hidden p-7">
          <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent-ink to-transparent" />
          <div className="flex flex-col items-center text-center">
            <Logo size={56} />
            <h1 className="gradient-text mt-4 text-2xl font-bold tracking-tight">NatGas Trader</h1>
            <p className="mt-1 text-xs text-dim">Private. Sign in with your dashboard password.</p>
          </div>
          <form action={submit} className="mt-6 space-y-3">
            <label className="block">
              <span className="label">Password</span>
              <span className="relative mt-1.5 block">
                <Icon name="lock" className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
                <input name="password" type={visible ? "text" : "password"} required autoComplete="current-password" className="field min-h-12 w-full pl-10 pr-12" />
              </span>
            </label>
            <div className="flex justify-end">
              <button type="button" className="flex cursor-pointer items-center gap-1.5 text-xs text-dim hover:text-ink" aria-pressed={visible} onClick={() => setVisible(!visible)}>
                <Icon name="eye" size={14} /> {visible ? "Hide characters" : "Reveal characters"}
              </button>
            </div>
            <button className="btn btn-primary min-h-12 w-full text-base" disabled={checking}>
              {checking ? "Checking…" : "Sign in"}
            </button>
            {state.message && (
              <p role="alert" className="flex items-center gap-2 rounded-xl border border-down/40 bg-down/10 px-3 py-2 text-sm text-down-ink">
                <Icon name="alert" size={14} /> {state.message}
              </p>
            )}
          </form>
        </div>
        <p className="mt-4 text-center text-xxs text-faint">Paper trading by default. This page cannot switch to live.</p>
      </div>
    </main>
  );
}
