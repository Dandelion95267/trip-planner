import { test, expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import { readFileSync } from "node:fs";
import type { TripPlan } from "../src/types/trip";
import { mockMapDetails, mockMapTiles } from "./helpers/map";

const fixture: TripPlan = JSON.parse(
  readFileSync(new URL("./fixtures/trip.json", import.meta.url), "utf8"),
);

async function openResults(page: Page, plan: TripPlan = fixture) {
  await page.addInitScript(() => {
    const audit = window as typeof window & { pdfTexts: string[] };
    audit.pdfTexts = [];
    const original = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (
      text,
      x,
      y,
      maxWidth,
    ) {
      audit.pdfTexts.push(text);
      if (maxWidth === undefined) return original.call(this, text, x, y);
      return original.call(this, text, x, y, maxWidth);
    };
  });
  await mockMapTiles(page);
  await mockMapDetails(page);
  await page.route("**/api/health", (route) =>
    route.fulfill({ json: { status: "ok" } }),
  );
  await page.route("**/api/trip/plan", (route) =>
    route.fulfill({ json: plan }),
  );
  await page.goto("/");
  await page.getByLabel("目的地城市").fill(plan.city);
  await page.getByLabel("开始日期", { exact: true }).fill(plan.start_date);
  await page.getByLabel("结束日期", { exact: true }).fill(plan.end_date);
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(
    page.getByRole("heading", {
      name: new RegExp(`${plan.days.length} 日旅程`),
    }),
  ).toBeVisible();
}

async function downloadPdf(page: Page, path: string) {
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出行程 PDF", exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.pdf$/);
  await download.saveAs(path);
  const buffer = await readFile(path);
  const content = buffer.toString("latin1");
  expect(content.startsWith("%PDF-")).toBe(true);
  expect(content.trimEnd().endsWith("%%EOF")).toBe(true);
  const pages = [...content.matchAll(/\/Type\s*\/Page\b/g)].length;
  expect(pages).toBeGreaterThan(0);
  expect([...content.matchAll(/\/Subtype\s*\/Image\b/g)].length).toBe(pages);
  await expect(
    page.getByRole("button", { name: "导出行程 PDF", exact: true }),
  ).toBeEnabled();
  return { pages, filename: download.suggestedFilename() };
}

async function capturedText(page: Page) {
  return page.evaluate(() =>
    (window as typeof window & { pdfTexts: string[] }).pdfTexts.join("\n"),
  );
}

test("PDF包含全部日期、中文和路线，日期标签切换不影响导出", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await openResults(page);
  await page.getByRole("tab", { name: /第 2 天/ }).click();
  await page.context().grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.getByRole("button", { name: "复制行程", exact: true }).click();
  await expect(page.getByText("行程已复制", { exact: true })).toBeVisible();
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  for (const text of [
    "故宫博物院",
    "天坛公园",
    "午餐：测试餐馆",
    "住宿候选",
    "测试酒店",
    "天气：白天",
    "该日期暂无天气预报",
  ])
    expect(copied).toContain(text);
  const result = await downloadPdf(page, "artifacts/trip-example.pdf");
  expect(result.filename).toBe("北京-2026-11-01-旅行计划.pdf");
  expect(result.pages).toBeGreaterThanOrEqual(3);
  const text = await capturedText(page);
  expect(text).toContain("第 1 天 · 2026-11-01");
  expect(text).toContain("第 2 天 · 2026-11-02");
  expect(text).toContain("故宫博物院");
  expect(text).toContain("天坛公园");
  expect(text).toContain("门票：未查询");
  expect(text).toContain("门票：¥0");
  expect(text).toContain("路线说明：到达景山公园");
  expect(text).toContain("该日期暂无天气预报");
  expect(text).toContain(`第 ${result.pages} / ${result.pages} 页`);
  await expect(page.getByRole("tab", { name: /第 2 天/ })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(errors).toEqual([]);
});

test("PDF保留未通过审核和部分预算，不把未知金额当作零", async ({ page }) => {
  const plan = structuredClone(fixture);
  plan.review = {
    status: "needs_revision",
    review_count: 3,
    revisions: 2,
    message: "已达到两轮修改上限，仍需确认。",
    result: {
      passed: false,
      summary: "第一天偏满。",
      issues: [
        {
          category: "pace",
          day_index: 0,
          problem: "安排过多。",
          evidence: "三个景点各两小时。",
          suggestion: "减少一个景点并增加休息。",
        },
      ],
    },
  };
  plan.budget = {
    source: "user_estimate",
    currency: "CNY",
    travelers: 1,
    rooms: 1,
    nights: 1,
    total_attractions: 0,
    total_hotels: 300,
    total_meals: null,
    total_transportation: null,
    known_subtotal: 300,
    total: null,
    is_complete: false,
    items: [
      {
        category: "hotels",
        label: "住宿",
        quantity: 1,
        unit_price: 300,
        subtotal: 300,
        basis: "1晚 × 1间",
      },
      {
        category: "attractions",
        label: "景点门票",
        quantity: 3,
        unit_price: 0,
        subtotal: 0,
        basis: "3次游览 × 1人",
      },
      {
        category: "meals",
        label: "餐饮",
        quantity: 2,
        unit_price: null,
        subtotal: null,
        basis: "2天 × 1人",
      },
    ],
    notes: ["不含往返目的地的大交通。"],
  };
  await openResults(page, plan);
  await downloadPdf(page, "artifacts/trip-review-budget.pdf");
  const text = await capturedText(page);
  expect(text).toContain("行程审核 · 行程仍需调整");
  expect(text).toContain("修改建议：减少一个景点并增加休息。");
  expect(text).toContain("可计算项目小计：¥300");
  expect(text).toContain("景点门票：¥0");
  expect(text).toContain("餐饮：未填写");
  expect(text).not.toContain("四项估算合计");
});

test("长行程自动分页，长中文段落和连续英文不丢失尾部", async ({ page }) => {
  test.setTimeout(60_000);
  const plan = structuredClone(fixture);
  const source = structuredClone(plan.days[0]!);
  const description =
    "这里是一段需要跨行分页的中文旅行建议。".repeat(80) +
    "A".repeat(300) +
    "长段落结束标记";
  plan.start_date = "2026-11-01";
  plan.end_date = "2026-11-30";
  plan.days = Array.from({ length: 30 }, (_, index) => ({
    ...structuredClone(source),
    date: `2026-11-${String(index + 1).padStart(2, "0")}`,
    day_index: index,
    description: index === 0 ? description : `第${index + 1}天的行程建议。`,
  }));
  await openResults(page, plan);
  const { pages } = await downloadPdf(page, "artifacts/trip-30-days.pdf");
  expect(pages).toBeGreaterThan(30);
  const text = await capturedText(page);
  expect(text.replace(/\n/g, "")).toContain("长段落结束标记");
  expect(text).toContain("第 30 天 · 2026-11-30");
  expect(text).toContain(`第 ${pages} / ${pages} 页`);
});

test("绘制失败后恢复按钮，重试可以下载", async ({ page }) => {
  await openResults(page);
  await page.evaluate(() => {
    const audit = window as typeof window & {
      originalPdfCanvas: typeof HTMLCanvasElement.prototype.toDataURL;
    };
    audit.originalPdfCanvas = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (type, quality) {
      if (type === "image/jpeg") throw new Error("simulated canvas failure");
      return audit.originalPdfCanvas.call(this, type, quality);
    };
  });
  await page.getByRole("button", { name: "导出行程 PDF", exact: true }).click();
  await expect(page.getByText(/PDF 导出失败，请重试/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "导出行程 PDF", exact: true }),
  ).toBeEnabled();
  await page.evaluate(() => {
    HTMLCanvasElement.prototype.toDataURL = (
      window as typeof window & {
        originalPdfCanvas: typeof HTMLCanvasElement.prototype.toDataURL;
      }
    ).originalPdfCanvas;
  });
  await downloadPdf(page, "artifacts/trip-retry.pdf");
});

test("导出期间禁止重复点击，外部文字按文本处理", async ({ page }) => {
  const plan = structuredClone(fixture);
  plan.days[0]!.description = '<img src=x onerror="window.pdfInjected=true">';
  await openResults(page, plan);
  let unblock!: () => void;
  const gate = new Promise<void>((resolve) => {
    unblock = resolve;
  });
  await page.route("**/src/services/pdf_export.ts*", async (route) => {
    await gate;
    await route.continue();
  });
  const button = page.getByRole("button", {
    name: "导出行程 PDF",
    exact: true,
  });
  const downloadPromise = page.waitForEvent("download");
  await button.click();
  await expect(button).toBeDisabled();
  await button.evaluate((element) => (element as HTMLButtonElement).click());
  unblock();
  const download = await downloadPromise;
  await download.saveAs("artifacts/trip-text-safety.pdf");
  await expect(button).toBeEnabled();
  expect((await capturedText(page)).replace(/\n/g, "")).toContain(
    plan.days[0]!.description,
  );
  expect(
    await page.evaluate(
      () => (window as typeof window & { pdfInjected?: boolean }).pdfInjected,
    ),
  ).toBeUndefined();
});
