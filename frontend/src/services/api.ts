import type { POIInfo, TripPlan, TripRequest } from "../types/trip";

// 浏览器只访问同源 /api；开发时由 Vite 转发，模型和高德 Key 始终留在后端。
export async function checkConnection(): Promise<boolean> {
  try {
    const response = await fetch("/api/health", {
      signal: AbortSignal.timeout(3000),
    });
    return response.ok && (await response.json()).status === "ok";
  } catch {
    return false;
  }
}

export async function getPlaceDetail(
  poiId: string,
  signal: AbortSignal,
): Promise<POIInfo> {
  const response = await fetch(`/api/map/poi/${encodeURIComponent(poiId)}`, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
  });
  if (!response.ok) throw new Error("地点坐标暂时无法查询。");
  const data = await response.json();
  if (!data || data.id !== poiId) throw new Error("地点详情与请求不一致。");
  return data as POIInfo;
}

export async function createTrip(
  request: TripRequest,
  signal: AbortSignal,
): Promise<TripPlan> {
  const controller = new AbortController();
  let timedOut = false;
  const timer = window.setTimeout(
    () => {
      timedOut = true;
      controller.abort();
    },
    request.enable_review ? 600_000 : 300_000,
  );
  const cancel = () => controller.abort();
  signal.addEventListener("abort", cancel, { once: true });
  if (signal.aborted) cancel();
  try {
    const response = await fetch("/api/trip/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: controller.signal,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      let message = "规划服务暂时不可用，请确认后端已启动后重试。";
      if (typeof data?.detail === "string") message = data.detail;
      else if (Array.isArray(data?.detail)) {
        message = data.detail
          .slice(0, 3)
          .map((item: { msg?: string }) => item.msg ?? "请检查输入内容")
          .join("；");
      }
      throw new Error(message);
    }
    if (
      !data ||
      typeof data.city !== "string" ||
      !Array.isArray(data.days) ||
      !data.days.length ||
      !Array.isArray(data.weather_info) ||
      typeof data.overall_suggestions !== "string"
    ) {
      throw new Error("服务返回的行程不完整，请重新生成。");
    }
    return data as TripPlan;
  } catch (error) {
    if (controller.signal.aborted) {
      if (timedOut) throw new Error("等待时间较长，请稍后重试或减少旅行天数。");
      throw new DOMException("已停止等待", "AbortError");
    }
    if (error instanceof TypeError)
      throw new Error("暂时无法连接规划服务，请确认后端已启动。");
    throw error;
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", cancel);
  }
}
