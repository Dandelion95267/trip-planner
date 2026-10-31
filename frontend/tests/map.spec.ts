import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { mockMapDetails, mockMapTiles, detailFor } from "./helpers/map";
import type { TripPlan } from "../src/types/trip";

const fixture: TripPlan = JSON.parse(
  readFileSync(new URL("./fixtures/trip.json", import.meta.url), "utf8"),
);
async function openResult(page: Page, plan = fixture) {
  await mockMapTiles(page);
  await mockMapDetails(page);
  await page.route("**/api/health", (route) =>
    route.fulfill({ json: { status: "ok" } }),
  );
  await page.route("**/api/trip/plan", (route) =>
    route.fulfill({ json: plan }),
  );
  await page.goto("/");
  await page.getByLabel("目的地城市").fill("北京");
  await page.getByLabel("开始日期", { exact: true }).fill("2026-11-01");
  await page.getByLabel("结束日期", { exact: true }).fill("2026-11-02");
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await expect(
    page.getByRole("region", { name: "可缩放的行程地图" }),
  ).toBeVisible();
}

test("景点标记、弹窗、缩放、餐馆住宿筛选与日期切换", async ({ page }) => {
  await openResult(page);
  await expect(page.locator(".trip-map-marker")).toHaveCount(2);
  await page
    .getByRole("button", { name: "景点：故宫博物院", exact: true })
    .click();
  await expect(page.locator(".leaflet-popup-content")).toContainText(
    "故宫博物院",
  );
  await page.locator(".leaflet-control-zoom-in").click();
  await page.getByRole("button", { name: /餐饮 1/ }).click();
  await expect(page.locator(".trip-map-marker")).toHaveCount(3);
  await page.getByRole("button", { name: /住宿 1/ }).click();
  await expect(page.locator(".trip-map-marker")).toHaveCount(4);
  await page.getByRole("button", { name: "适配视野" }).click();
  await page.getByRole("tab", { name: /第 2 天/ }).click();
  await expect(page.locator(".trip-map-marker")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "景点：天坛公园", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "景点：故宫博物院", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("虚线表示景点游览顺序", { exact: false }),
  ).toBeVisible();
  await page.screenshot({ path: "artifacts/map-desktop.png", fullPage: true });
});

test("高德坐标转换到地图坐标且不改写原始值", async ({ page }) => {
  await page.route("**/api/health", (route) =>
    route.fulfill({ json: { status: "ok" } }),
  );
  await page.goto("/");
  const transformed = await page.evaluate(async () => {
    const modulePath = "/src/utils/map.ts";
    const { toMapLatLng } = await import(/* @vite-ignore */ modulePath);
    const input = { longitude: 123, latitude: 45 };
    return { result: toMapLatLng(input), input };
  });
  // 来自 gcoord 官方示例的 GCJ-02 → WGS-84 对照值。
  expect(transformed.result[0]).toBeCloseTo(44.99804071, 5);
  expect(transformed.result[1]).toBeCloseTo(122.99395597, 5);
  expect(transformed.input).toEqual({ longitude: 123, latitude: 45 });
});

test("底图请求失败时保留地点标记和文字行程", async ({ page }) => {
  await openResult(page);
  await expect(page.locator(".trip-map-marker")).toHaveCount(2);
  await page.route("https://tile.openstreetmap.org/**", (route) =>
    route.abort(),
  );
  await page.locator(".leaflet-control-zoom-in").click();
  await expect(
    page.getByText("地图底图暂时无法加载", { exact: false }),
  ).toBeVisible();
  await expect(page.locator(".trip-map-marker")).toHaveCount(2);
  await expect(
    page.getByRole("heading", { name: "故宫博物院", exact: true }),
  ).toBeVisible();
});

test("地点缺少坐标时不画虚构标记", async ({ page }) => {
  await openResult(page);
  await expect(page.locator(".trip-map-marker")).toHaveCount(2);
  await page.unroute("**/api/map/poi/*");
  await page.route("**/api/map/poi/*", (route) =>
    route.fulfill({ status: 502, json: { detail: "坐标不可用" } }),
  );
  await page.getByRole("tab", { name: /第 2 天/ }).click();
  await expect(
    page.getByText("这些地点暂无可展示的坐标", { exact: true }),
  ).toBeVisible();
  await expect(page.locator(".trip-map-marker")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "天坛公园", exact: true }),
  ).toBeVisible();
});

test("地图弹窗把外部名称作为文本显示", async ({ page }) => {
  const plan = structuredClone(fixture);
  plan.days[0]!.attractions[0]!.name = '<img src=x onerror="alert(1)">';
  await openResult(page, plan);
  await expect(page.locator(".trip-map-marker")).toHaveCount(2);
  await page.locator(".trip-map-marker").first().click();
  await expect(page.locator(".leaflet-popup-content")).toContainText("<img");
  await expect(page.locator(".leaflet-popup-content img")).toHaveCount(0);
});

test("快速切换日期不会显示上一天迟到的坐标", async ({ page }) => {
  await openResult(page);
  await page.getByRole("tab", { name: /第 2 天/ }).click();
  await expect(page.locator(".trip-map-marker")).toHaveCount(1);
  // 使用一个全新页面，延迟第一天的坐标，再切换到第二天。
  await page.reload();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/map/poi/*", async (route) => {
    const id = new URL(route.request().url()).pathname.split("/").pop()!;
    if (id !== "test-temple") await gate;
    await route.fulfill({ json: detailFor(id) }).catch(() => {});
  });
  await page.getByLabel("目的地城市").fill("北京");
  await page.getByRole("button", { name: "生成我的行程" }).click();
  await page.getByRole("tab", { name: /第 2 天/ }).click();
  await expect(
    page.getByRole("button", { name: "景点：天坛公园", exact: true }),
  ).toBeVisible();
  release();
  await expect(page.locator(".trip-map-marker")).toHaveCount(1);
});
