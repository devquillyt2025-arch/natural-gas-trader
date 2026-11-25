import type { Config } from "tailwindcss";

// Design tokens. Dark only. Profit is always teal-green and loss always red; text steps keep 4.5:1 on panels.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#06080d",
        panel: "#0d1119",
        raised: "#151b27",
        line: "rgba(255,255,255,0.08)",
        "line-strong": "rgba(255,255,255,0.16)",
        ink: "#eaeef7",
        dim: "#a3adc2",
        faint: "#7f8aa2",
        up: "#00b894",
        "up-ink": "#2ee6b6",
        down: "#f5455c",
        "down-ink": "#ff7586",
        "down-solid": "#d42843", // behind white text (4.8:1)
        accent: "#5b8cff",
        "accent-ink": "#8fb0ff",
        violet: "#9b7bff",
        cyan: "#37d6f0",
        warn: "#f7b945",
      },
      fontFamily: {
        sans: ["Inter Variable", "ui-sans-serif", "system-ui", "Segoe UI", "sans-serif"],
        mono: ["JetBrains Mono Variable", "ui-monospace", "Cascadia Mono", "Consolas", "monospace"],
      },
      fontSize: { xxs: ["0.6875rem", "0.95rem"] },
      borderRadius: { xl: "0.875rem", "2xl": "1.125rem" },
      boxShadow: {
        card: "inset 0 1px 0 rgba(255,255,255,0.05), 0 24px 48px -32px rgba(0,0,0,0.9)",
        glow: "0 0 0 1px rgba(91,140,255,0.35), 0 8px 32px -8px rgba(91,140,255,0.55)",
        "glow-up": "0 8px 36px -12px rgba(0,184,148,0.65)",
        "glow-down": "0 8px 36px -10px rgba(245,69,92,0.7)",
      },
      transitionTimingFunction: { out: "cubic-bezier(0.16, 1, 0.3, 1)" },
    },
  },
  plugins: [],
};

export default config;
