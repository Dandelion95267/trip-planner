import { test, expect } from "@playwright/test";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import type { TripPlan, TripRequest } from "../src/types/trip";
import { localDate } from "../src/utils/format";
import { mockMapTiles } from "./helpers/map";

// 必须显式开启，避免普通回归测试调用付费模型；测试依次执行。
const scenarios = [
  {
    id: "beijing-2d",
    city: "北京",
    days: 2,
    review: true,
    mode: "auto",
    ticket: 50,
    preferences: ["历史文化"],
    extra: "两人出游，每天安排两个候选景点，节奏轻松，优先相邻区域。",
  },
  {
    id: "hangzhou-3d",
    city: "杭州",
    days: 3,
    review: false,
    mode: "auto",
    ticket: 30,
    preferences: ["自然风光", "休闲美食"],
    extra: "每天安排两个候选景点，兼顾自然风光和休息，不要跨区域来回折返。",
  },
  {
    id: "elderly-2d",
    city: "北京",
    days: 2,
    review: true,
    mode: "transit",
    ticket: null,
    preferences: ["历史文化"],
    extra:
      "两人出行，其中一位老人。每天最多两个景点，轻松游玩，减少步行，留出午休，景点间使用公共交通；不能确认的接驳请明确提示。",
  },
] as const;

for (const scenario of scenarios) {
  test(`真实验收 ${scenario.id}：规划、页面及三种导出一致`, async ({
    page,
  }) => {
    test.skip(
      process.env.RUN_LIVE_ACCEPTANCE !== "1",
      "显式开启且启动后端后才调用真实模型和高德",
    );
    test.setTimeout(660_000);
    await mkdir("artifacts/acceptance", { recursive: true });
    await page.addInitScript(() => {
      const audit = window as typeof window & { acceptancePdfText: string[] };
      audit.acceptancePdfText = [];
      const original = CanvasRenderingContext2D.prototype.fillText;
      CanvasRenderingContext2D.prototype.fillText = function (
        text,
        x,
        y,
        maxWidth,
      ) {
        audit.acceptancePdfText.push(text);
        return maxWidth === undefined
          ? original.call(this, text, x, y)
          : original.call(this, text, x, y, maxWidth);
      };
    });
    await mockMapTiles(page);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    let planningCalls = 0;
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        request.url().endsWith("/api/trip/plan")
      )
        planningCalls++;
    });
    await page.goto("/");
    await expect(page.getByText("服务已连接", { exact: true })).toBeVisible();
    await page.getByLabel("目的地城市").fill(scenario.city);
    await page.getByLabel("开始日期", { exact: true }).fill(localDate(1));
    await page
      .getByLabel("结束日期", { exact: true })
      .fill(localDate(scenario.days));
    await page
      .getByLabel("交通方式", { exact: true })
      .selectOption(scenario.mode);
    await page
      .getByRole("checkbox", { name: /审核行程并按建议调整/ })
      .setChecked(scenario.review);
    for (const preference of scenario.preferences)
      await page.getByRole("button", { name: preference, exact: true }).click();
    await page.locator("summary").filter({ hasText: "餐饮与其他需求" }).click();
    await page.getByLabel("还有什么想告诉我？").fill(scenario.extra);
    await page
      .locator("summary")
      .filter({ hasText: "费用估算（可选）" })
      .click();
    await page.getByRole("checkbox", { name: /按填写的标准估算费用/ }).check();
    await page.getByLabel("出行人数", { exact: true }).fill("2");
    await page.getByLabel("每间每晚住宿（元）", { exact: true }).fill("300");
    await page.getByLabel("每人每天餐饮（元）", { exact: true }).fill("100");
    await page.getByLabel("每人每天市内交通（元）", { exact: true }).fill("30");
    if (scenario.ticket !== null)
      await page
        .getByLabel("每人每次景点门票均价（元）", { exact: true })
        .fill(String(scenario.ticket));
    const start = Date.now();
    const responsePromise = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/trip/plan") &&
        response.request().method() === "POST",
      { timeout: 610_000 },
    );
    await page.getByRole("button", { name: "生成我的行程" }).click();
    const response = await responsePromise;
    expect(response.status()).toBe(200);
    const result = (await response.json()) as TripPlan;
    const submitted = response.request().postDataJSON() as TripRequest;
    await writeFile(
      `artifacts/acceptance/${scenario.id}.json`,
      JSON.stringify(
        { elapsed_ms: Date.now() - start, request: submitted, result },
        null,
        2,
      ),
    );
    expect(result.city).toBe(scenario.city);
    expect(result.days).toHaveLength(scenario.days);
    expect(result.start_date).toBe(submitted.start_date);
    expect(result.end_date).toBe(submitted.end_date);
    await expect(
      page.getByRole("heading", {
        name: `${scenario.city}的 ${scenario.days} 日旅程.`,
      }),
    ).toBeVisible();
    for (const [index, day] of result.days.entries()) {
      expect(day.day_index).toBe(index);
      expect(day.date).toBe(localDate(index + 1));
      expect(day.attractions.length).toBeGreaterThan(0);
      expect(new Set(day.attractions.map((place) => place.poi_id)).size).toBe(
        day.attractions.length,
      );
      expect(day.attractions.every((place) => Boolean(place.poi_id))).toBe(
        true,
      );
      await page
        .getByRole("tab", { name: new RegExp(`第 ${index + 1} 天`) })
        .click();
      for (const place of day.attractions)
        await expect(
          page.getByRole("heading", { name: place.name, exact: true }),
        ).toBeVisible();
    }
    const visits = result.days.reduce(
      (count, day) => count + day.attractions.length,
      0,
    );
    expect(result.budget?.total_hotels).toBe(300 * (scenario.days - 1));
    expect(result.budget?.total_meals).toBe(100 * 2 * scenario.days);
    expect(result.budget?.total_transportation).toBe(30 * 2 * scenario.days);
    expect(result.budget?.total_attractions).toBe(
      scenario.ticket === null ? null : scenario.ticket * 2 * visits,
    );
    expect(result.budget?.is_complete).toBe(scenario.ticket !== null);
    if (scenario.ticket === null) expect(result.budget?.total).toBeNull();
    if (scenario.review) {
      expect(result.review).toBeTruthy();
      expect(["passed", "needs_revision", "unavailable"]).toContain(
        result.review?.status,
      );
      expect(result.review!.revisions).toBeLessThanOrEqual(2);
      await expect(
        page.getByRole("region", { name: "行程审核结果" }),
      ).toBeVisible();
    } else expect(result.review).toBeNull();

    const jsonPromise = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出行程 JSON", exact: true })
      .click();
    await (
      await jsonPromise
    ).saveAs(`artifacts/acceptance/${scenario.id}-export.json`);
    expect(
      JSON.parse(
        await readFile(
          `artifacts/acceptance/${scenario.id}-export.json`,
          "utf8",
        ),
      ),
    ).toEqual(result);
    await page
      .context()
      .grantPermissions(["clipboard-read", "clipboard-write"]);
    await page.getByRole("button", { name: "复制行程", exact: true }).click();
    await expect(page.getByText("行程已复制", { exact: true })).toBeVisible();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    await writeFile(`artifacts/acceptance/${scenario.id}.txt`, copied);
    for (const day of result.days) {
      expect(copied).toContain(day.date);
      for (const place of day.attractions) expect(copied).toContain(place.name);
      for (const meal of day.meals) expect(copied).toContain(meal.name);
      if (day.hotel) expect(copied).toContain(day.hotel.name);
    }
    expect(copied).toContain(String(result.budget!.known_subtotal));
    const pdfPromise = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "导出行程 PDF", exact: true })
      .click();
    await (await pdfPromise).saveAs(`artifacts/acceptance/${scenario.id}.pdf`);
    const pdf = await readFile(`artifacts/acceptance/${scenario.id}.pdf`);
    expect(pdf.subarray(0, 5).toString()).toBe("%PDF-");
    const drawn = await page.evaluate(() =>
      (
        window as typeof window & { acceptancePdfText: string[] }
      ).acceptancePdfText.join(""),
    );
    for (const day of result.days)
      for (const place of day.attractions)
        expect(drawn.replace(/\s/g, "")).toContain(
          place.name.replace(/\s/g, ""),
        );
    expect(drawn).toContain(String(result.budget!.known_subtotal));
    await page.screenshot({
      path: `artifacts/acceptance/${scenario.id}.png`,
      fullPage: true,
    });
    expect(planningCalls).toBe(1);
    expect(errors).toEqual([]);
  });
}
