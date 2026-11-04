import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import type { TripPlan } from "../src/types/trip";
import { mockMapDetails, mockMapTiles } from "./helpers/map";

const fixture: TripPlan = JSON.parse(
  readFileSync(new URL("./fixtures/trip.json", import.meta.url), "utf8"),
);

async function openPlanner(page: Page) {
  await mockMapTiles(page);
  await mockMapDetails(page);
  await page.route("**/api/health", (route) =>
    route.fulfill({ json: { status: "ok" } }),
  );
  await page.goto("/");
}
async function fillTrip(page: Page) {
  await page.getByLabel("目的地城市").fill(" 北京 ");
  await page.getByLabel("开始日期", { exact: true }).fill("2026-11-01");
  await page.getByLabel("结束日期", { exact: true }).fill("2026-11-02");
}

test("初始页面、连接状态和桌面布局", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openPlanner(page);
  await expect(page.getByText("服务已连接", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "旅程，从一个想去的地方开始" }),
  ).toBeVisible();
  await expect(page.getByLabel("目的地城市")).toHaveValue("");
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "artifacts/desktop-empty.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("提交真实格式的需求、展示结果、切换日期并导出", async ({ page }) => {
  let submitted: Record<string, unknown> = {};
  await page.route("**/api/trip/plan", (route) => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: fixture });
  });
  await openPlanner(page);
  await fillTrip(page);
  await page.getByRole("button", { name: "历史文化", exact: true }).click();
  await page.getByLabel("每段最多步行（米，可选）").fill("1000");
  await page.getByRole("checkbox", { name: /按位置调整景点顺序/ }).check();
  await page.locator("summary").filter({ hasText: "餐饮与其他需求" }).click();
  await page.getByLabel("餐饮偏好", { exact: true }).fill("面食");
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(
    page.getByRole("heading", { name: /北京的 2 日旅程/ }),
  ).toBeVisible();
  expect(submitted.city).toBe("北京");
  expect(submitted.travel_days).toBe(2);
  expect(submitted.route_mode).toBe("auto");
  expect(submitted.max_leg_walking_m).toBe(1000);
  expect(submitted.optimize_order).toBe(true);
  expect(submitted.enable_review).toBe(true);
  expect(submitted.dining_preference).toBe("面食");
  await expect(page.getByText("门票：未查询", { exact: true })).toBeVisible();
  await expect(page.getByText("门票：¥0", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "故宫博物院", exact: true }),
  ).toBeVisible();
  await page.getByRole("tab", { name: /第 2 天/ }).click();
  await expect(
    page.getByRole("heading", { name: "天坛公园", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("该日期暂无天气预报", { exact: true }),
  ).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出行程 JSON" }).click();
  const download = await downloadPromise;
  const downloadPath = "artifacts/exported-trip.json";
  await download.saveAs(downloadPath);
  expect(JSON.parse(await readFile(downloadPath, "utf8")).days).toHaveLength(2);
});

test("驾车与步行上限冲突时提示修改，不发送规划请求", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/trip/plan", (route) => {
    calls++;
    return route.fulfill({ json: fixture });
  });
  await openPlanner(page);
  await fillTrip(page);
  await page.getByLabel("交通方式", { exact: true }).selectOption("driving");
  await page.getByLabel("每段最多步行（米，可选）").fill("1000");
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(page.getByRole("alert")).toContainText(
    "步行上限适用于自动、步行或公共交通",
  );
  expect(calls).toBe(0);
});

test("后端失败时显示错误并允许重试", async ({ page }) => {
  await page.route("**/api/trip/plan", (route) =>
    route.fulfill({
      status: 502,
      json: { detail: "高德服务暂时不可用，请稍后重试。" },
    }),
  );
  await openPlanner(page);
  await fillTrip(page);
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(page.getByRole("alert")).toContainText("高德服务暂时不可用");
  await expect(
    page.getByRole("button", { name: "生成我的行程" }),
  ).toBeEnabled();
});

test("超过30天在浏览器内拦截，不发出规划请求", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/trip/plan", (route) => {
    calls++;
    return route.fulfill({ json: fixture });
  });
  await openPlanner(page);
  await fillTrip(page);
  await page.getByLabel("结束日期", { exact: true }).fill("2026-12-02");
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(page.getByRole("alert")).toContainText("1—30 天");
  expect(calls).toBe(0);
});

test("等待期间可停止并重新编辑", async ({ page }) => {
  await page.route("**/api/trip/plan", () => {
    /* 保持请求等待，测试浏览器主动停止。 */
  });
  await openPlanner(page);
  await fillTrip(page);
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(
    page.getByRole("heading", { name: "正在把你的想法连成行程" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "停止等待" }).click();
  await expect(
    page.getByText("已停止等待，你可以修改需求后重新生成。"),
  ).toBeVisible();
  await expect(page.getByLabel("目的地城市")).toBeEnabled();
});

test("手机布局、表单与结果无横向溢出", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/trip/plan", (route) =>
    route.fulfill({ json: fixture }),
  );
  await openPlanner(page);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await fillTrip(page);
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(
    page.getByRole("heading", { name: /北京的 2 日旅程/ }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "artifacts/mobile-results.png",
    fullPage: true,
  });
});

test("填写示例只填表，不自动调用模型", async ({ page }) => {
  let calls = 0;
  await page.route("**/api/trip/plan", (route) => {
    calls++;
    return route.fulfill({ json: fixture });
  });
  await openPlanner(page);
  await page.getByRole("button", { name: /填写示例/ }).click();
  await expect(page.getByLabel("目的地城市")).toHaveValue("北京");
  expect(calls).toBe(0);
});

for (const status of ["passed", "needs_revision", "unavailable"] as const) {
  test(`展示审核状态 ${status} 并将其包含在导出中`, async ({ page }) => {
    const data: TripPlan = structuredClone(fixture);
    data.review = {
      status,
      review_count: 3,
      revisions: 2,
      message:
        status === "unavailable"
          ? "审核未能完成，保留当前有效行程。"
          : "已完成本次审核流程。",
      result:
        status === "unavailable"
          ? null
          : {
              passed: status === "passed",
              summary:
                status === "passed"
                  ? "未发现待修改问题。"
                  : "第一天行程仍然偏满。",
              issues:
                status === "passed"
                  ? []
                  : [
                      {
                        category: "pace",
                        day_index: 0,
                        problem: "当天游览时间偏长。",
                        evidence: "三个景点各需两小时。",
                        suggestion: "减少一个景点，增加休息时间。",
                      },
                    ],
            },
    };
    await page.route("**/api/trip/plan", (route) =>
      route.fulfill({ json: data }),
    );
    await openPlanner(page);
    await fillTrip(page);
    await page.getByRole("button", { name: "生成我的行程" }).click();
    const review = page.getByRole("region", { name: "行程审核结果" });
    await expect(review).toBeVisible();
    await expect(review).toContainText(
      {
        passed: "行程审核通过",
        needs_revision: "行程仍需调整",
        unavailable: "行程审核未完成",
      }[status],
    );
    await expect(review).toContainText("已调整 2 轮");
    if (status === "needs_revision") {
      await expect(review).toContainText("第 1 天");
      await expect(review).toContainText("减少一个景点，增加休息时间。");
      await page.screenshot({
        path: "artifacts/review-needs-revision.png",
        fullPage: true,
      });
    }
    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "导出行程 JSON" }).click();
    const download = await downloadPromise;
    const path = `artifacts/review-${status}.json`;
    await download.saveAs(path);
    expect(JSON.parse(await readFile(path, "utf8")).review).toEqual(
      data.review,
    );
  });
}

test("关闭审核时提交原规划选项且不显示审核结论", async ({ page }) => {
  let submitted: Record<string, unknown> = {};
  await page.route("**/api/trip/plan", (route) => {
    submitted = route.request().postDataJSON();
    return route.fulfill({ json: fixture });
  });
  await openPlanner(page);
  await fillTrip(page);
  await page.getByRole("checkbox", { name: /审核行程并按建议调整/ }).uncheck();
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(
    page.getByRole("heading", { name: /北京的 2 日旅程/ }),
  ).toBeVisible();
  expect(submitted.enable_review).toBe(false);
  await expect(page.getByRole("region", { name: "行程审核结果" })).toHaveCount(
    0,
  );
});

test("真实后端：从网页提交到模型与高德并展示结果", async ({ page }) => {
  test.skip(process.env.RUN_LIVE_TRIP !== "1", "显式开启才调用真实外部服务");
  test.setTimeout(600_000);
  await mockMapTiles(page);
  await page.goto("/");
  await expect(page.getByText("服务已连接", { exact: true })).toBeVisible();
  await page.getByLabel("目的地城市").fill("北京");
  await page.getByLabel("开始日期", { exact: true }).fill("2026-11-01");
  await page.getByLabel("结束日期", { exact: true }).fill("2026-11-01");
  await page.locator("summary").filter({ hasText: "餐饮与其他需求" }).click();
  await page
    .getByLabel("还有什么想告诉我？")
    .fill("只安排故宫附近两个候选景点，节奏轻松。");
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/trip/plan") &&
      response.request().method() === "POST",
    { timeout: 580_000 },
  );
  await page.getByRole("button", { name: "生成我的行程" }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  await expect(
    page.getByRole("heading", { name: /北京的 1 日旅程/ }),
  ).toBeVisible();
  const result: TripPlan = await response.json();
  expect(result.review).toBeTruthy();
  expect(["passed", "needs_revision"]).toContain(result.review?.status);
  await expect(
    page.getByRole("region", { name: "行程审核结果" }),
  ).toBeVisible();
  expect(result.days[0].attractions.length).toBeGreaterThan(0);
  await expect(page.locator(".trip-map-marker").first()).toBeVisible();
  const firstDay = result.days[0]!;
  if (firstDay.meals.length)
    await page
      .locator(".map-filters")
      .getByRole("button", { name: /餐饮/ })
      .click();
  if (firstDay.hotel)
    await page
      .locator(".map-filters")
      .getByRole("button", { name: /住宿/ })
      .click();
  const expectedPoints =
    firstDay.attractions.length +
    new Set(firstDay.meals.map((meal) => meal.poi_id || meal.name)).size +
    (firstDay.hotel ? 1 : 0);
  await expect(page.locator(".trip-map-marker")).toHaveCount(expectedPoints, {
    timeout: 20_000,
  });
  await page.screenshot({ path: "artifacts/live-result.png", fullPage: true });
});
