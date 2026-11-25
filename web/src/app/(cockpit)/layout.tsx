import { Banners, Header, MobileBar } from "@/components/shell";
import { ErrorBoundary } from "@/components/ui";
import { CockpitProvider } from "@/lib/data";

export default function CockpitLayout({ children }: { children: React.ReactNode }) {
  return (
    <CockpitProvider>
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-50 focus:rounded-xl focus:bg-accent focus:px-3 focus:py-2 focus:text-white">
        Skip to content
      </a>
      <Header />
      <main id="main" className="mx-auto max-w-[1600px] space-y-3 p-3 pb-44 md:pb-8">
        <Banners />
        <ErrorBoundary label="This page">{children}</ErrorBoundary>
      </main>
      <MobileBar />
    </CockpitProvider>
  );
}
