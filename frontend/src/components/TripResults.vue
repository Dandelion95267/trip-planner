<script setup lang="ts">
import { computed, ref, watch } from "vue";
import type { TripPlan } from "../types/trip";
import { distance, duration, money, shortDate } from "../utils/format";
import AppIcon from "./AppIcon.vue";
import TripMap from "./TripMap.vue";
import BudgetSummary from "./BudgetSummary.vue";
import { tripPlainText } from "../utils/trip_document";

const props = defineProps<{ plan: TripPlan }>();
const activeIndex = ref(0);
const message = ref("");
const pdfBusy = ref(false);
const pdfProgress = ref("");
watch(
  () => props.plan,
  () => {
    activeIndex.value = 0;
    message.value = "";
  },
);
const day = computed(() => props.plan.days[activeIndex.value]!);
const weather = computed(() =>
  props.plan.weather_info.find((item) => item.date === day.value.date),
);
const attractionCount = computed(() =>
  props.plan.days.reduce((sum, item) => sum + item.attractions.length, 0),
);
const routeCount = computed(() =>
  props.plan.days.reduce((sum, item) => sum + item.routes.length, 0),
);
const mealNames = {
  breakfast: "早餐",
  lunch: "午餐",
  dinner: "晚餐",
  snack: "加餐",
};
const modeNames = { walking: "步行", driving: "驾车", transit: "公共交通" };
const reviewStatusNames = {
  passed: "行程审核通过",
  needs_revision: "行程仍需调整",
  unavailable: "行程审核未完成",
};
const segments = computed(() =>
  day.value.attractions
    .slice(0, -1)
    .map((place, index) =>
      day.value.routes.find(
        (route) =>
          route.origin.id === place.poi_id &&
          route.destination.id === day.value.attractions[index + 1]?.poi_id,
      ),
    ),
);

async function copyPlan() {
  try {
    await navigator.clipboard.writeText(tripPlainText(props.plan));
    message.value = "行程已复制";
  } catch {
    message.value = "暂时无法复制，可以使用导出功能。";
  }
}
function downloadPlan() {
  const blob = new Blob([JSON.stringify(props.plan, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  saveDownload(
    blob,
    `${props.plan.city.replace(/[\\/:*?"<>|]/g, "")}-${props.plan.start_date}-行程.json`,
  );
  message.value = "行程文件已导出";
}
function saveDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.hidden = true;
  document.body.append(anchor);
  try {
    anchor.click();
  } finally {
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
}
async function downloadPdf() {
  if (pdfBusy.value) return;
  const plan = props.plan;
  pdfBusy.value = true;
  pdfProgress.value = "正在准备 PDF…";
  message.value = "";
  try {
    // 只在用户导出时加载PDF依赖，不增加首页的初始下载量。
    const { createTripPdf, tripPdfFilename } =
      await import("../services/pdf_export");
    const blob = await createTripPdf(plan, (current, total) => {
      pdfProgress.value = `正在生成 PDF：第 ${current} / ${total} 页`;
    });
    saveDownload(blob, tripPdfFilename(plan));
    message.value = "PDF 已导出，包含全部日期、审核意见和已有预算。";
  } catch {
    message.value = "PDF 导出失败，请重试；也可以先导出 JSON 保存行程。";
  } finally {
    pdfBusy.value = false;
    pdfProgress.value = "";
  }
}
</script>

<template>
  <section class="results" aria-label="旅行规划结果">
    <header class="result-heading">
      <div>
        <span class="eyebrow">旅程已成形</span>
        <h2>
          {{ plan.city }}的 {{ plan.days.length }} 日旅程<span class="small-dot"
            >.</span
          >
        </h2>
        <p>
          {{ shortDate(plan.start_date) }} — {{ shortDate(plan.end_date)
          }}<span class="separator">/</span>{{ attractionCount }} 个景点<span
            class="separator"
            >/</span
          >已查询 {{ routeCount }} 段路线
        </p>
      </div>
      <div class="result-actions">
        <button
          type="button"
          class="icon-button"
          aria-label="复制行程"
          title="复制行程"
          @click="copyPlan"
        >
          <AppIcon name="copy" /></button
        ><button
          type="button"
          class="icon-button"
          aria-label="导出行程 JSON"
          title="导出行程 JSON"
          @click="downloadPlan"
        >
          <AppIcon name="download" />
        </button>
        <button
          type="button"
          class="icon-button"
          aria-label="导出行程 PDF"
          title="导出行程 PDF"
          :disabled="pdfBusy"
          @click="downloadPdf"
        >
          <span v-if="pdfBusy" class="spinner"></span
          ><AppIcon v-else name="file" />
        </button>
      </div>
    </header>
    <p v-if="message" class="inline-message" role="status">{{ message }}</p>
    <p v-if="pdfBusy" class="inline-message" role="status">{{ pdfProgress }}</p>
    <section v-if="plan.review" class="notice-box" aria-label="行程审核结果">
      <strong>{{ reviewStatusNames[plan.review.status] }}</strong>
      <p>{{ plan.review.message }}</p>
      <p v-if="plan.review.result">{{ plan.review.result.summary }}</p>
      <small
        >审核尝试 {{ plan.review.review_count }} 次 · 已调整
        {{ plan.review.revisions }} 轮</small
      >
      <ul v-if="plan.review.result?.issues.length">
        <li v-for="(issue, index) in plan.review.result.issues" :key="index">
          <strong
            >{{
              issue.day_index === null
                ? "整体行程"
                : `第 ${issue.day_index + 1} 天`
            }}：{{ issue.problem }}</strong
          >
          <p>依据：{{ issue.evidence }}</p>
          <p>建议：{{ issue.suggestion }}</p>
        </li>
      </ul>
    </section>
    <nav class="day-tabs" role="tablist" aria-label="选择行程日期">
      <button
        v-for="(item, index) in plan.days"
        :id="`day-tab-${index}`"
        :key="item.date"
        type="button"
        role="tab"
        :aria-selected="activeIndex === index"
        :aria-controls="`day-panel-${index}`"
        :class="{ active: activeIndex === index }"
        @click="activeIndex = index"
      >
        <span>第 {{ item.day_index + 1 }} 天</span
        ><small>{{ shortDate(item.date) }}</small>
      </button>
    </nav>

    <div
      :id="`day-panel-${activeIndex}`"
      role="tabpanel"
      :aria-labelledby="`day-tab-${activeIndex}`"
      class="day-content"
    >
      <div class="day-summary">
        <div>
          <span class="eyebrow"
            >DAY {{ String(day.day_index + 1).padStart(2, "0") }}</span
          >
          <h3>{{ shortDate(day.date) }}，出发吧</h3>
          <p>{{ day.transportation }}</p>
        </div>
        <div class="weather-card">
          <AppIcon name="cloud" :size="28" /><template v-if="weather"
            ><div>
              <strong>{{ weather.day_weather }}</strong
              ><span
                >白天 {{ weather.day_temp }}° / 夜间
                {{ weather.night_temp }}°</span
              >
            </div></template
          ><span v-else>该日期暂无天气预报</span>
        </div>
      </div>
      <p class="day-description">{{ day.description }}</p>

      <details v-if="day.order_optimization" class="optimization-note">
        <summary>
          <AppIcon name="route" :size="17" />{{
            day.order_optimization.status === "optimized"
              ? "已调整景点顺序，减少折返"
              : "景点顺序说明"
          }}
        </summary>
        <p>{{ day.order_optimization.reason }}</p>
        <p v-if="day.order_optimization.status === 'optimized'">
          原顺序：{{ day.order_optimization.original_order.join(" → ") }}
        </p>
        <p
          v-if="
            day.order_optimization.before_distance_m !== null &&
            day.order_optimization.after_distance_m !== null
          "
        >
          估算直线距离：{{ distance(day.order_optimization.before_distance_m) }}
          →
          {{
            distance(day.order_optimization.after_distance_m)
          }}。这不是实际道路里程。
        </p>
      </details>

      <TripMap :day="day" />

      <div class="section-title">
        <h3>沿途去看看</h3>
        <span>{{ day.attractions.length }} 个目的地</span>
      </div>
      <div class="itinerary">
        <div
          v-for="(place, index) in day.attractions"
          :key="place.poi_id || `${place.name}-${index}`"
          class="itinerary-stop"
        >
          <div class="stop-marker">
            {{ String(index + 1).padStart(2, "0") }}
          </div>
          <article class="attraction-card">
            <div class="attraction-title">
              <h4>{{ place.name }}</h4>
              <span class="duration-pill"
                ><AppIcon name="clock" :size="14" />建议
                {{ place.visit_duration }} 分钟</span
              >
            </div>
            <p>{{ place.description }}</p>
            <div class="place-meta">
              <span
                ><AppIcon name="pin" :size="14" />{{
                  place.address || "地址暂未提供"
                }}</span
              ><span>门票：{{ money(place.ticket_price) }}</span>
            </div>
          </article>
          <div
            v-if="index < day.attractions.length - 1"
            class="route-connector"
          >
            <template v-if="segments[index]">
              <div class="route-overview">
                <AppIcon name="route" :size="16" /><strong>{{
                  modeNames[segments[index]!.mode]
                }}</strong
                ><span>{{ duration(segments[index]!.duration_s) }}</span
                ><span>{{ distance(segments[index]!.distance_m) }}</span>
              </div>
              <p v-if="segments[index]!.selection_reason">
                {{ segments[index]!.selection_reason }}
              </p>
              <p v-if="segments[index]!.walking_distance_m !== null">
                其中步行 {{ distance(segments[index]!.walking_distance_m) }}
              </p>
              <details v-if="segments[index]!.steps.length">
                <summary>路线详情</summary>
                <ol>
                  <li
                    v-for="(step, stepIndex) in segments[index]!.steps"
                    :key="stepIndex"
                  >
                    {{ step }}
                  </li>
                </ol>
              </details>
            </template>
            <span v-else>这段路线暂未查询成功，请出行前确认。</span>
          </div>
        </div>
      </div>
      <div v-if="day.route_warnings.length" class="notice-box">
        <strong>路线提示</strong>
        <ul>
          <li v-for="warning in day.route_warnings" :key="warning">
            {{ warning }}
          </li>
        </ul>
      </div>

      <div class="section-title">
        <h3>好好吃饭，也好好休息</h3>
        <span>候选地点</span>
      </div>
      <div class="stay-meal-grid">
        <article class="support-card">
          <div class="support-title">
            <AppIcon name="food" />
            <h4>用餐安排</h4>
          </div>
          <div
            v-for="meal in day.meals"
            :key="`${meal.type}-${meal.poi_id}`"
            class="meal-item"
          >
            <span class="meal-label">{{ mealNames[meal.type] }}</span>
            <div>
              <strong>{{ meal.name }}</strong>
              <p>{{ meal.address || "地址暂未提供" }}</p>
              <small>费用：{{ money(meal.estimated_cost) }}</small>
            </div>
          </div>
          <p v-if="!day.meals.length" class="muted">
            暂无候选餐馆，可调整餐饮偏好后重试。
          </p>
        </article>
        <article class="support-card">
          <div class="support-title">
            <AppIcon name="bed" />
            <h4>住在哪里</h4>
          </div>
          <template v-if="day.hotel"
            ><strong class="hotel-name">{{ day.hotel.name }}</strong>
            <p>{{ day.hotel.address || "地址暂未提供" }}</p>
            <div class="hotel-facts">
              <span
                >房价：{{
                  day.hotel.price_range || money(day.hotel.estimated_cost)
                }}</span
              ><span>评分：{{ day.hotel.rating ?? "未查询" }}</span>
            </div>
            <p class="fine-print">房态与预订条件请另行确认。</p></template
          >
          <p v-else class="muted">暂无住宿候选，可调整偏好后重试。</p>
        </article>
      </div>
    </div>
    <BudgetSummary v-if="plan.budget" :budget="plan.budget" />
    <details class="travel-notes" open>
      <summary>出行前，记得这些</summary>
      <p>{{ plan.overall_suggestions }}</p>
    </details>
    <p class="result-footnote">
      路线耗时为查询时的估计；实际报价未查询的仍显示为“未查询”。{{
        plan.budget
          ? "费用估算按填写标准计算，请出行前核实。"
          : "未开启费用估算。"
      }}
    </p>
  </section>
</template>
