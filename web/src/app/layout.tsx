import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "NatGas Trader",
  description: "Autonomous MCX natural gas trader cockpit",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", themeColor: "#06080d" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-dvh font-sans">
        <div className="ambient" aria-hidden>
          <i />
          <i />
          <i />
        </div>
        {children}
      </body>
    </html>
  );
}
