"use client";

export default function PageError({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div role="alert" className="glass border-down/50 p-5">
      <p className="text-down-ink">Something went wrong on this page.</p>
      <p className="mt-1 text-sm text-dim">{error.message}</p>
      <button className="btn mt-3" onClick={reset}>
        Try again
      </button>
    </div>
  );
}
