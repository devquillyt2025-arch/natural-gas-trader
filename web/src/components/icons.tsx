// One line-icon family (24px grid, 1.8 stroke). Decorative: the text next to an icon carries the meaning.
import type { SVGProps } from "react";

const PATHS = {
  flame: "M12 3c1 3.5 5 5.5 5 10.5a5 5 0 0 1-10 0c0-2 1-3.5 2-4.5.3 1.5 1 2.3 2 2.5C10 8.5 10.5 5.5 12 3Z",
  pulse: "M3 12h4l2.5-7 5 14 2.5-7h4",
  list: "M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01",
  chart: "M4 4v16h16M8 15l3.5-4 3 2.5L20 7",
  shield: "M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6l-7-3Zm-3 9 2 2 4-4",
  scroll: "M7 4h10a2 2 0 0 1 2 2v14l-3-2-2 2-2-2-2 2-2-2-3 2V6a2 2 0 0 1 2-2Zm2 5h6m-6 4h6",
  power: "M12 3v9M6.5 7a8 8 0 1 0 11 0",
  play: "M8 5v14l11-7L8 5Z",
  flatten: "M5 12h14M12 5v3m0 8v3M8 8l4 4 4-4M8 16l4-4 4 4",
  up: "M7 17 17 7M9 7h8v8",
  down: "M7 7l10 10M17 9v8H9",
  pause: "M9 5v14M15 5v14",
  clock: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v5l3 2",
  radar: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-4.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9Zm0-4.5 6-6",
  news: "M5 5h11v14H6a2 2 0 0 1-2-2V6a1 1 0 0 1 1-1Zm11 4h4v8a2 2 0 0 1-4 0V9ZM7.5 9h5m-5 3.5h5M7.5 16h3",
  gauge: "M4 18a8.5 8.5 0 1 1 16 0M12 14l4-4",
  target: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-5a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0-4h.01",
  calendar: "M5 6h14v14H5V6Zm3-3v5m8-5v5M5 11h14",
  download: "M12 4v11m-4-4 4 4 4-4M5 20h14",
  check: "m5 12.5 4.5 4.5L19 7.5",
  cross: "M6 6l12 12M18 6 6 18",
  alert: "M12 4 2.8 19.5h18.4L12 4Zm0 6v4m0 3h.01",
  info: "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-9.5V16m0-8h.01",
  swap: "M7 7h13l-3-3M17 17H4l3 3",
  logout: "M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M10 16l4-4-4-4m4 4H4",
  eye: "M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12Zm9.5 3a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
  lock: "M6 11h12v9H6v-9Zm2.5 0V8a3.5 3.5 0 0 1 7 0v3",
  chevron: "m9 6 6 6-6 6",
  trophy: "M8 4h8v5a4 4 0 0 1-8 0V4Zm0 2H5a3 3 0 0 0 3 4m8-4h3a3 3 0 0 1-3 4m-4 3v4m-4 3h8",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, className = "", ...rest }: { name: IconName; size?: number } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      focusable="false"
      className={`shrink-0 ${className}`}
      {...rest}
    >
      <path d={PATHS[name]} />
    </svg>
  );
}

/** The app mark: a flame on a gradient tile. */
export function Logo({ size = 32 }: { size?: number }) {
  return (
    <span
      className="inline-flex shrink-0 items-center justify-center rounded-xl shadow-glow"
      style={{ width: size, height: size, background: "linear-gradient(135deg,#37d6f0,#5b8cff 45%,#9b7bff)" }}
    >
      <Icon name="flame" size={Math.round(size * 0.62)} className="text-white" />
    </span>
  );
}
