<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "./trip-map.css";
import type { DayPlan, POIInfo } from "../types/trip";
import { getPlaceDetail } from "../services/api";
import {
  dayMapPoints,
  toMapLatLng,
  validLocation,
  type MapPoint,
  type PointCategory,
} from "../utils/map";
import AppIcon from "./AppIcon.vue";

const props = defineProps<{ day: DayPlan }>();
const container = ref<HTMLDivElement | null>(null);
const enabled = ref<Record<PointCategory, boolean>>({
  attraction: true,
  meal: false,
  hotel: false,
});
const resolved = ref<MapPoint[]>([]);
const locating = ref(false);
const tileError = ref(false);
const unresolvedCount = ref(0);
const cache = new Map<string, POIInfo>();
const categoryNames = { attraction: "景点", meal: "餐饮", hotel: "住宿" };
const colors = { attraction: "#176451", meal: "#bd843c", hotel: "#647ca1" };
const categories: PointCategory[] = ["attraction", "meal", "hotel"];
const points = computed(() => dayMapPoints(props.day));
const selectedCount = computed(
  () => points.value.filter((point) => enabled.value[point.category]).length,
);
let map: L.Map | undefined;
let content: L.LayerGroup | undefined;
let controller: AbortController | undefined;
let generation = 0;

function fitPoints() {
  if (!map || !resolved.value.length) return;
  const bounds = L.latLngBounds(
    resolved.value.map((point) => toMapLatLng(point.location!)),
  );
  map.fitBounds(bounds, { padding: [35, 35], maxZoom: 15, animate: false });
}
function drawPoints() {
  if (!map || !content) return;
  content.clearLayers();
  for (const point of resolved.value) {
    const marker = L.marker(toMapLatLng(point.location!), {
      icon: L.divIcon({
        className: `trip-map-marker ${point.category}`,
        html: `<span><b>${point.category === "attraction" ? point.sequence : point.category === "meal" ? "餐" : "住"}</b></span>`,
        iconSize: [30, 36],
        iconAnchor: [15, 36],
        popupAnchor: [0, -30],
      }),
      title: point.name,
    });
    marker.on("add", () =>
      marker
        .getElement()
        ?.setAttribute(
          "aria-label",
          `${categoryNames[point.category]}：${point.name}`,
        ),
    );
    marker.addTo(content);
    // 使用 DOM 文本节点，地图弹窗不把模型或外部地点名称作为 HTML 执行。
    const popup = document.createElement("div");
    popup.className = "trip-map-popup";
    const label = document.createElement("small");
    label.textContent = point.label;
    const title = document.createElement("strong");
    title.textContent = point.name;
    const address = document.createElement("p");
    address.textContent = point.address || "地址暂未提供";
    popup.append(label, title, address);
    marker.bindPopup(popup);
  }
  // 只连接连续且都有坐标的相邻景点，不跨过缺失坐标的景点画线。
  const visible = new Map(resolved.value.map((point) => [point.key, point]));
  const attractions = points.value.filter(
    (point) => point.category === "attraction",
  );
  if (enabled.value.attraction)
    for (let index = 0; index < attractions.length - 1; index++) {
      const a = visible.get(attractions[index]!.key);
      const b = visible.get(attractions[index + 1]!.key);
      if (a && b)
        L.polyline([toMapLatLng(a.location!), toMapLatLng(b.location!)], {
          color: colors.attraction,
          weight: 2,
          dashArray: "6 7",
          opacity: 0.7,
        }).addTo(content);
    }
  fitPoints();
}
async function loadPoints() {
  if (!map) return;
  controller?.abort();
  controller = new AbortController();
  const signal = controller.signal;
  const current = ++generation;
  const selected = points.value.filter(
    (point) => enabled.value[point.category],
  );
  const known = selected.map((point) => ({ ...point }));
  for (const point of known) {
    if (!validLocation(point.location) && point.poiId && cache.has(point.poiId))
      point.location = cache.get(point.poiId)!.location;
  }
  resolved.value = known.filter((point) => validLocation(point.location));
  unresolvedCount.value = 0;
  drawPoints();
  const missing = known.filter((point) => !validLocation(point.location));
  locating.value = missing.length > 0;
  await Promise.allSettled(
    missing.map(async (point) => {
      if (!point.poiId || signal.aborted) return;
      try {
        const detail =
          cache.get(point.poiId) || (await getPlaceDetail(point.poiId, signal));
        if (signal.aborted || current !== generation) return;
        cache.set(point.poiId, detail);
        point.location = detail.location;
      } catch {
        /* 缺少坐标不影响其他地点及文字行程。 */
      }
    }),
  );
  if (signal.aborted || current !== generation) return;
  resolved.value = known.filter((point) => validLocation(point.location));
  unresolvedCount.value = known.length - resolved.value.length;
  locating.value = false;
  drawPoints();
}
function toggleCategory(category: PointCategory) {
  enabled.value = { ...enabled.value, [category]: !enabled.value[category] };
}
onMounted(() => {
  if (!container.value) return;
  map = L.map(container.value, { zoomControl: false, scrollWheelZoom: false });
  L.control
    .zoom({ zoomInTitle: "放大地图", zoomOutTitle: "缩小地图" })
    .addTo(map);
  L.tileLayer(
    import.meta.env.VITE_MAP_TILE_URL ||
      "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    {
      maxZoom: 19,
      updateWhenIdle: true,
      keepBuffer: 1,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    },
  )
    .on("loading", () => {
      tileError.value = false;
    })
    .on("tileerror", () => {
      tileError.value = true;
    })
    .addTo(map);
  content = L.layerGroup().addTo(map);
  void loadPoints();
});
watch(
  () => props.day,
  () => {
    void loadPoints();
  },
);
watch(enabled, () => {
  void loadPoints();
});
onBeforeUnmount(() => {
  generation++;
  controller?.abort();
  map?.remove();
});
</script>

<template>
  <section class="trip-map-section" aria-label="当天行程地图">
    <div class="section-title">
      <h3><AppIcon name="pin" :size="17" /> 在地图上看这一程</h3>
      <button
        type="button"
        class="text-button"
        :disabled="!resolved.length"
        @click="fitPoints"
      >
        适配视野
      </button>
    </div>
    <div class="map-filters" aria-label="地图地点类型">
      <button
        v-for="category in categories"
        :key="category"
        type="button"
        :aria-pressed="enabled[category]"
        :class="{ selected: enabled[category] }"
        @click="toggleCategory(category)"
      >
        <i :style="{ background: colors[category] }"></i
        >{{ categoryNames[category] }}
        {{
          points.filter((point) => point.category === category).length
        }}</button
      ><span class="map-location-count" role="status">{{
        locating
          ? "正在定位地点…"
          : `已定位 ${resolved.length}/${selectedCount} 个地点`
      }}</span>
    </div>
    <div class="trip-map-frame">
      <div
        ref="container"
        class="trip-map-canvas"
        role="region"
        aria-label="可缩放的行程地图"
      ></div>
      <div v-if="!resolved.length" class="map-overlay">
        <AppIcon name="pin" :size="28" />
        <p>
          {{
            locating
              ? "正在查询地点坐标…"
              : selectedCount
                ? "这些地点暂无可展示的坐标"
                : "选择上方的地点类型查看标记"
          }}
        </p>
      </div>
    </div>
    <p class="map-caption-note">
      虚线表示景点游览顺序，不代表实际道路轨迹。地点资料来自高德，底图为
      OpenStreetMap。
    </p>
    <p v-if="tileError" class="map-warning">
      地图底图暂时无法加载，地点标记和顺序连线仍可查看。
    </p>
    <p v-if="unresolvedCount" class="map-warning">
      {{ unresolvedCount }} 个地点暂未提供可用坐标，其余地点与文字行程不受影响。
    </p>
  </section>
</template>
