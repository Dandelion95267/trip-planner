import type { Page } from "@playwright/test";

// 自动浏览器测试只用模拟瓦片，不向公共地图服务发起自动缩放/平移请求。
export async function mockMapTiles(page: Page) {
  await page.route("https://tile.openstreetmap.org/**", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#edf2e7"/><path d="M0 128h256M128 0v256" stroke="#dce5d4"/><text x="18" y="24" fill="#a6b29c" font-size="10">TEST TILE</text></svg>',
    }),
  );
}
export const testPlaces: Record<
  string,
  { name: string; longitude: number; latitude: number }
> = {
  "test-palace": {
    name: "故宫博物院",
    longitude: 116.397029,
    latitude: 39.917839,
  },
  "test-park": { name: "景山公园", longitude: 116.396551, latitude: 39.925875 },
  "test-temple": { name: "天坛公园", longitude: 116.41, latitude: 39.881 },
  "test-restaurant": { name: "测试餐馆", longitude: 116.398, latitude: 39.92 },
  "test-hotel": { name: "测试酒店", longitude: 116.4, latitude: 39.923 },
};
export function detailFor(id: string) {
  const point = testPlaces[id];
  return point
    ? {
        id,
        name: point.name,
        address: "测试地址",
        type: "",
        location: { longitude: point.longitude, latitude: point.latitude },
      }
    : null;
}
export async function mockMapDetails(page: Page) {
  await page.route("**/api/map/poi/*", (route) => {
    const id = decodeURIComponent(
      new URL(route.request().url()).pathname.split("/").pop()!,
    );
    const data = detailFor(id);
    return route.fulfill({
      status: data ? 200 : 404,
      json: data ?? { detail: "没有测试坐标" },
    });
  });
}
