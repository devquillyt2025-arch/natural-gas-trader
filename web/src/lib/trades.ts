"use client";

import { useEffect, useState } from "react";
import { getJson } from "./api";
import { useCockpit } from "./data";
import type { Mode, Trade } from "./types";

/** All trades of one account, oldest first. Refetched when the engine records a new one. */
export function useTrades(mode: Mode): { trades: Trade[] | null; error: string | null } {
  const { tradesVersion } = useCockpit();
  const [trades, setTrades] = useState<Trade[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stale = false;
    getJson<Trade[]>(`/api/trades?mode=${mode}`).then(
      (all) => {
        if (stale) return;
        setError(null);
        setTrades(all);
      },
      (e: Error) => {
        if (!stale) setError(e.message);
      },
    );
    return () => {
      stale = true;
    };
  }, [mode, tradesVersion]);

  return { trades, error };
}
