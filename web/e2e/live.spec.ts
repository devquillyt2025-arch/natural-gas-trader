// Smoke test of the live page against a seeded local database (mock/serve.mjs).
import { expect, type Page, test } from "@playwright/test";

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Password").fill("demo1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("/");
}

test("signed-out visitors are sent to the login page and APIs refuse them", async ({ page, request }) => {
  await page.goto("/control");
  await expect(page).toHaveURL(/\/login$/);
  for (const url of ["/api/feed", "/api/trades", "/api/decisions", "/api/decisions/1", "/api/events", "/api/paper-days",
                     "/api/upstox/login", "/api/upstox/callback?code=x&state=y"]) {
    expect((await request.get(url, { maxRedirects: 0 })).status(), url).toBe(401);
  }
  expect((await request.post("/api/commands", { data: { type: "resume" } })).status()).toBe(401);
  const forged = { Cookie: `natgas_session=9999999999.${"0".repeat(64)}` };
  expect((await request.get("/api/feed", { headers: forged })).status()).toBe(401);
});

test("a wrong password is refused, and only the owner's own pages may send commands", async ({ page }, info) => {
  await page.goto("/login");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Wrong password." })).toBeVisible();
  await expect(page).toHaveURL(/\/login$/);

  await signIn(page);
  const send = (data: object, headers: Record<string, string> = {}) => page.request.post("/api/commands", { data, headers });
  expect((await send({ type: "resume" }, { "Sec-Fetch-Site": "same-site" })).status()).toBe(403);
  expect((await send({ type: "resume" }, { Origin: "http://localhost:3000" })).status()).toBe(403);
  expect((await send({ type: "go_live" })).status()).toBe(400);
  expect((await send({ type: "upstox_code", payload: { code: "x" } })).status()).toBe(400);
  expect((await page.request.get("/api/decisions?action=drop%20table")).status()).toBe(400);
  if (info.project.name === "desktop") {
    await page.getByRole("link", { name: "Control" }).first().click();
    await page.getByRole("button", { name: "Sign out" }).click();
    await expect(page).toHaveURL(/\/login$/);
    expect((await page.request.get("/api/feed")).status()).toBe(401);
  }
});

test("live page shows the engine's state and a kill goes through as a command", async ({ page }, info) => {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));
  await signIn(page);

  await expect(page.getByTestId("mode-chip")).toHaveText("PAPER");
  await expect(page.getByTestId("engine-chip")).toHaveText("Engine ok");
  await expect(page.getByTestId("upstox-banner")).toContainText("Log in to Upstox");
  await expect(page.getByTestId("position-card")).toContainText("LONG 1 lot");
  await expect(page.getByTestId("unrealized")).toHaveText("+₹275");
  await expect(page.getByTestId("trades-used")).toHaveText("2/4");
  await expect(page.getByTestId("session-card")).toContainText("Next EIA storage");
  await expect(page.getByTestId("latest-decision")).toContainText("Trend intact above 298.5");
  await expect(page.getByTestId("decision-feed").locator("li")).toHaveCount(4);
  await expect(page.getByTestId("chart-canvas").locator("canvas").first()).toBeVisible();

  await page.getByTestId("decision-feed").locator("button").first().click();
  const drawer = page.getByTestId("decision-drawer");
  await drawer.getByRole("button", { name: "Show snapshot sent to Claude" }).click();
  await expect(drawer.locator("pre")).toContainText('"hard_limits"');
  await drawer.getByRole("button", { name: "Close" }).click();

  // no page-level horizontal scroll, on either screen size
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
  await page.screenshot({ path: `test-results/live-${info.project.name}.png`, fullPage: true });

  if (info.project.name === "phone") {
    const kill = page.getByTestId("btn-kill").last();
    await expect(kill).toBeInViewport();
    await kill.click();
    await page.getByRole("dialog").getByRole("button", { name: "Kill now" }).click();
    await expect(page.getByText("kill: sent, waiting for the engine…")).toBeVisible();
  } else {
    await page.getByRole("link", { name: "Control" }).first().click();
    await page.getByTestId("controls").getByTestId("btn-kill").click();
    await page.getByRole("dialog").getByRole("button", { name: "Kill now" }).click();
    await expect(page.getByTestId("command-history")).toContainText("kill");
    await expect(page.getByTestId("command-history")).toContainText("pending");
  }
  expect(problems).toEqual([]);
});
