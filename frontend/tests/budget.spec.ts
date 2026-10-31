import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { readFile } from "node:fs/promises";
import type { Budget, TripPlan } from "../src/types/trip";
import { mockMapDetails, mockMapTiles } from "./helpers/map";

const fixture: TripPlan = JSON.parse(
  readFileSync(new URL("./fixtures/trip.json", import.meta.url), "utf8"),
);

function completeBudget(): Budget {
  return {
    source: "user_estimate",
    currency: "CNY",
    travelers: 2,
    rooms: 1,
    nights: 1,
    total_attractions: 300,
    total_hotels: 300,
    total_meals: 400,
    total_transportation: 80,
    known_subtotal: 1080,
    total: 1080,
    is_complete: true,
    items: [
      {
        category: "attractions",
        label: "景点门票",
        quantity: 6,
        unit_price: 50,
        subtotal: 300,
        basis: "3次景点游览 × 2人 × 每人每次平均门票",
      },
      {
        category: "hotels",
        label: "住宿",
        quantity: 1,
        unit_price: 300,
        subtotal: 300,
        basis: "1晚 × 1间 × 每间每晚费用",
      },
      {
        category: "meals",
        label: "餐饮",
        quantity: 4,
        unit_price: 100,
        subtotal: 400,
        basis: "2天 × 2人 × 每人每天全部餐饮费用",
      },
      {
        category: "transportation",
        label: "市内交通",
        quantity: 4,
        unit_price: 20,
        subtotal: 80,
        basis: "2天 × 2人 × 每人每天市内交通费用",
      },
    ],
    notes: ["用户填写标准，不是实时价格。", "不含往返目的地的大交通。"],
  };
}

async function openBudgetForm(page: Page) {
  await mockMapTiles(page);
  await mockMapDetails(page);
  await page.route("**/api/health", (route) =>
    route.fulfill({ json: { status: "ok" } }),
  );
  await page.goto("/");
  await page.getByLabel("目的地城市").fill("北京");
  await page.getByLabel("开始日期", { exact: true }).fill("2026-11-01");
  await page.getByLabel("结束日期", { exact: true }).fill("2026-11-02");
  await page.locator("summary").filter({ hasText: "费用估算（可选）" }).click();
  await page.getByRole("checkbox", { name: /按填写的标准估算费用/ }).check();
}

test("提交费用标准、展示团体明细并导出和复制", async ({ page }) => {
  const budget = completeBudget();
  let submitted: Record<string, unknown> = {};
  await page.route("**/api/trip/plan", (route) => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: { ...fixture, budget } });
  });
  await openBudgetForm(page);
  await page.getByLabel("出行人数", { exact: true }).fill("2");
  await page.getByLabel("每间每晚住宿（元）", { exact: true }).fill("300");
  await page.getByLabel("每人每天餐饮（元）", { exact: true }).fill("100");
  await page.getByLabel("每人每天市内交通（元）", { exact: true }).fill("20");
  await page
    .getByLabel("每人每次景点门票均价（元）", { exact: true })
    .fill("50");
  await page.getByRole("button", { name: "生成我的行程" }).click();
  const panel = page.getByRole("region", { name: "旅行费用估算" });
  await expect(panel).toContainText("四项估算合计");
  await expect(panel).toContainText("¥1080");
  await expect(panel).toContainText("2 人 · 1 间房 · 1 晚");
  expect(submitted.budget_options).toEqual({
    travelers: 2,
    rooms: 1,
    hotel_per_room_night: 300,
    meals_per_person_day: 100,
    transport_per_person_day: 20,
    ticket_per_person_visit: 50,
  });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出行程 JSON" }).click();
  const download = await downloadPromise;
  await download.saveAs("artifacts/budget-trip.json");
  const exported = JSON.parse(
    await readFile("artifacts/budget-trip.json", "utf8"),
  );
  expect(exported.budget).toEqual(budget);
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "复制行程" }).click();
  await expect(page.getByText("行程已复制", { exact: true })).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("四项估算合计：¥1080");
  expect(copied).toContain("不含往返目的地的大交通");
  await page.screenshot({
    path: "artifacts/budget-desktop.png",
    fullPage: true,
  });
});

test("手机端区分未填写和零费用，部分小计不显示为完整合计", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const budget = completeBudget();
  budget.total_attractions = 0;
  budget.total_meals = null;
  budget.total_transportation = null;
  budget.known_subtotal = 300;
  budget.total = null;
  budget.is_complete = false;
  budget.items[0]!.unit_price = 0;
  budget.items[0]!.subtotal = 0;
  for (const item of budget.items.slice(2)) {
    item.unit_price = null;
    item.subtotal = null;
  }
  let submitted: Record<string, unknown> = {};
  await page.route("**/api/trip/plan", (route) => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: { ...fixture, budget } });
  });
  await openBudgetForm(page);
  await page.getByLabel("出行人数", { exact: true }).fill("2");
  await page.getByLabel("每间每晚住宿（元）", { exact: true }).fill("300");
  await page
    .getByLabel("每人每次景点门票均价（元）", { exact: true })
    .fill("0");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.getByRole("button", { name: "生成我的行程" }).click();
  const panel = page.getByRole("region", { name: "旅行费用估算" });
  await expect(panel).toContainText("可计算项目小计");
  await expect(panel).toContainText("¥300");
  await expect(panel).toContainText("未知费用不代表免费");
  await expect(
    panel
      .getByRole("row")
      .filter({
        has: page.getByRole("rowheader", { name: "景点门票", exact: true }),
      }),
  ).toContainText("¥0");
  await expect(
    panel
      .getByRole("row")
      .filter({
        has: page.getByRole("rowheader", { name: "餐饮", exact: true }),
      }),
  ).toContainText("未填写");
  await expect(panel.getByText("四项估算合计", { exact: true })).toHaveCount(0);
  expect(submitted.budget_options).toEqual({
    travelers: 2,
    rooms: 1,
    hotel_per_room_night: 300,
    meals_per_person_day: null,
    transport_per_person_day: null,
    ticket_per_person_visit: 0,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "artifacts/budget-mobile.png",
    fullPage: true,
  });
});

test("关闭费用估算后不提交残留标准，也不展示预算", async ({ page }) => {
  let submitted: Record<string, unknown> = {};
  await page.route("**/api/trip/plan", (route) => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: fixture });
  });
  await openBudgetForm(page);
  await page.getByLabel("每间每晚住宿（元）", { exact: true }).fill("300");
  await page.getByRole("checkbox", { name: /按填写的标准估算费用/ }).uncheck();
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(
    page.getByRole("heading", { name: /北京的 2 日旅程/ }),
  ).toBeVisible();
  expect(submitted.budget_options).toBeNull();
  await expect(page.getByRole("region", { name: "旅行费用估算" })).toHaveCount(
    0,
  );
});
