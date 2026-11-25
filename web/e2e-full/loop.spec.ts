// Real engine <-> local Postgres (real migration) <-> web app. Nothing here is stubbed in the UI:
// every value on screen was written by the engine, and every control goes back through `commands`.
import { expect, type Page, test } from "@playwright/test";

const shot = (page: Page, name: string) => page.screenshot({ path: `test-results/full-${name}.png`, fullPage: true });

async function confirm(page: Page, button: string) {
  await page.getByRole("dialog").getByRole("button", { name: button }).click();
}

test("paper trading end to end with controls from the UI", async ({ page }) => {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(e.message));

  await page.goto("/login");
  await page.getByLabel("Password").fill("demo1234");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("/");

  // the engine is alive and its data arrives by polling
  await expect(page.getByTestId("mode-chip")).toHaveText("PAPER");
  await expect(page.getByTestId("engine-chip")).toHaveText("Engine ok");
  await expect(page.getByTestId("chart-canvas").locator("canvas").first()).toBeVisible();
  await expect(page.getByTestId("decision-feed").locator("li").nth(4)).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("risk-budget")).toContainText("Daily loss used");
  await shot(page, "live");

  // trades appear in the journal
  await page.getByRole("link", { name: "Trades" }).first().click();
  await expect(page.getByTestId("journal").locator("tbody tr").first()).toBeVisible({ timeout: 120_000 });
  await expect(page.getByTestId("stats")).toContainText("Expectancy");
  await shot(page, "trades");

  // kill switch: queued by the browser, acted on and acknowledged by the engine
  await page.getByRole("link", { name: "Control" }).first().click();
  const history = page.getByTestId("command-history");
  await page.getByTestId("controls").getByTestId("btn-kill").click();
  await confirm(page, "Kill now");
  await expect(history.locator("tbody tr").first()).toContainText("acked");
  await expect(history.locator("tbody tr").first()).toContainText("kill switch ON");
  await expect(page.getByTestId("controls")).toContainText("ON: entries blocked");
  await expect(page.getByTestId("controls")).toContainText("flat");

  await page.getByTestId("controls").getByTestId("btn-resume").click();
  await confirm(page, "Resume");
  await expect(history.locator("tbody tr").first()).toContainText("resumed");

  // tightening is accepted; loosening past the engine's ceiling is refused
  await page.getByLabel("Max risk per trade (₹)").fill("800");
  await page.getByRole("button", { name: /Apply 1 change/ }).click();
  await confirm(page, "Apply");
  await expect(history.locator("tbody tr").first()).toContainText("max_risk_inr: 1500.0 -> 800.0");
  await page.getByLabel("Daily loss cap (₹)").fill("9000");
  await page.getByRole("button", { name: /Apply 1 change/ }).click();
  await confirm(page, "Apply");
  await expect(history.locator("tbody tr").first()).toContainText("failed");
  await expect(history.locator("tbody tr").first()).toContainText("looser than the engine ceiling");
  await expect(page.getByTestId("limits").locator("tr", { hasText: "Max risk per trade" })).toContainText("800");
  await shot(page, "control");

  await page.getByRole("link", { name: "Events" }).first().click();
  await expect(page.getByTestId("events")).toContainText("limit changed from UI: max_risk_inr");
  await expect(page.getByTestId("events")).toContainText("KILL SWITCH ON");
  await shot(page, "events");

  await page.getByRole("link", { name: "Decisions" }).first().click();
  await expect(page.getByTestId("rejection-chart")).toBeVisible();
  const table = page.getByTestId("decisions-table");
  await table.locator("tbody tr").first().click();
  await table.getByRole("button", { name: "Show snapshot sent to Claude" }).first().click();
  await expect(table.locator("pre").or(table.getByText("No snapshot")).first()).toBeVisible();
  await page.getByLabel("Risk layer").selectOption("no");
  await expect(table.locator("tbody tr").first()).toContainText("✗");
  await shot(page, "decisions");

  expect(problems).toEqual([]);
});
