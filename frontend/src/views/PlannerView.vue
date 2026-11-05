<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import AppIcon from "../components/AppIcon.vue";
import TripResults from "../components/TripResults.vue";
import { checkConnection, createTrip } from "../services/api";
import type {
  BudgetOptions,
  RouteMode,
  TripPlan,
  TripRequest,
} from "../types/trip";
import { localDate, tripDays } from "../utils/format";

const preferences = [
  "历史文化",
  "自然风光",
  "城市漫步",
  "亲子出游",
  "艺术展览",
  "休闲美食",
];
const modeNames: Record<RouteMode, string> = {
  auto: "自动",
  walking: "步行",
  driving: "自驾",
  transit: "公共交通",
};
const budgetRateFields = [
  { key: "hotel_per_room_night", label: "每间每晚住宿（元）" },
  { key: "meals_per_person_day", label: "每人每天餐饮（元）" },
  { key: "transport_per_person_day", label: "每人每天市内交通（元）" },
  { key: "ticket_per_person_visit", label: "每人每次景点门票均价（元）" },
] as const;
const form = reactive({
  city: "",
  start_date: localDate(1),
  end_date: localDate(2),
  route_mode: "auto" as RouteMode,
  max_leg_walking_m: "" as number | "",
  accommodation: "经济型酒店",
  dining_preference: "",
  preferences: [] as string[],
  free_text_input: "",
  optimize_order: false,
  enable_review: true,
  enable_budget: false,
  travelers: 1,
  rooms: 1,
  budget_rates: {
    hotel_per_room_night: "" as number | "",
    meals_per_person_day: "" as number | "",
    transport_per_person_day: "" as number | "",
    ticket_per_person_visit: "" as number | "",
  },
});
const days = computed(() => tripDays(form.start_date, form.end_date));
const plan = ref<TripPlan | null>(null);
const busy = ref(false);
const error = ref("");
const statusMessage = ref("");
const connection = ref<"checking" | "ready" | "offline">("checking");
const elapsed = ref(0);
let controller: AbortController | null = null;
let ticker: ReturnType<typeof setInterval> | undefined;

async function refreshConnection() {
  connection.value = "checking";
  connection.value = (await checkConnection()) ? "ready" : "offline";
}
onMounted(refreshConnection);
onBeforeUnmount(() => {
  controller?.abort();
  clearInterval(ticker);
});
function togglePreference(preference: string) {
  const index = form.preferences.indexOf(preference);
  if (index >= 0) form.preferences.splice(index, 1);
  else form.preferences.push(preference);
}
function editRequest(section: "route" | "dining" = "route") {
  const control = document.getElementById(
    section === "dining" ? "dining" : "route-mode",
  );
  const details = control?.closest("details");
  if (details) details.open = true;
  control?.scrollIntoView({ block: "center", behavior: "smooth" });
  control?.focus({ preventScroll: true });
  statusMessage.value =
    "请按你的选择调整条件；原行程仍保留，点击生成后才会重新规划。";
}
function fillExample() {
  if (busy.value) return;
  form.city = "北京";
  form.start_date = localDate(1);
  form.end_date = localDate(2);
  form.route_mode = "auto";
  form.max_leg_walking_m = "";
  form.preferences = ["历史文化", "城市漫步"];
  form.free_text_input = "每天安排 2—3 个景点，节奏轻松一些。";
  form.accommodation = "经济型酒店";
  form.dining_preference = "";
  form.optimize_order = false;
  form.enable_review = true;
  form.enable_budget = false;
  form.travelers = 1;
  form.rooms = 1;
  for (const field of budgetRateFields) form.budget_rates[field.key] = "";
  error.value = "";
  statusMessage.value = "示例需求已填写，点击“生成我的行程”开始规划。";
  document.getElementById("city")?.focus();
}
async function submit() {
  if (busy.value) return;
  error.value = "";
  statusMessage.value = "";
  if (!form.city.trim()) {
    error.value = "请填写目的地城市。";
    return;
  }
  if (!Number.isInteger(days.value) || days.value < 1 || days.value > 30) {
    error.value =
      "请选择有效的起止日期，旅行天数须为 1—30 天（包含首尾两天）。";
    return;
  }
  if (
    form.max_leg_walking_m !== "" &&
    (!Number.isInteger(form.max_leg_walking_m) ||
      form.max_leg_walking_m < 0 ||
      form.max_leg_walking_m > 50_000)
  ) {
    error.value = "每段步行上限须为 0—50000 米的整数，留空表示不限制。";
    return;
  }
  if (form.max_leg_walking_m !== "" && form.route_mode === "driving") {
    error.value = "步行上限适用于自动、步行或公共交通，请调整交通方式。";
    return;
  }
  let budgetOptions: BudgetOptions | null = null;
  if (form.enable_budget) {
    if (
      ![form.travelers, form.rooms].every(
        (value) => Number.isInteger(value) && value >= 1 && value <= 50,
      )
    ) {
      error.value = "人数和房间数须为 1—50 的整数。";
      return;
    }
    if (
      Object.values(form.budget_rates).some(
        (value) =>
          value !== "" &&
          (!Number.isInteger(value) || value < 0 || value > 1_000_000),
      )
    ) {
      error.value = "费用须为 0—1000000 的整数；留空表示未知。";
      return;
    }
    const rate = (value: number | "") => (value === "" ? null : value);
    budgetOptions = {
      travelers: form.travelers,
      rooms: form.rooms,
      hotel_per_room_night: rate(form.budget_rates.hotel_per_room_night),
      meals_per_person_day: rate(form.budget_rates.meals_per_person_day),
      transport_per_person_day: rate(
        form.budget_rates.transport_per_person_day,
      ),
      ticket_per_person_visit: rate(form.budget_rates.ticket_per_person_visit),
    };
  }
  const request: TripRequest = {
    budget_options: budgetOptions,
    city: form.city.trim(),
    start_date: form.start_date,
    end_date: form.end_date,
    travel_days: days.value,
    transportation: modeNames[form.route_mode],
    route_mode: form.route_mode,
    max_leg_walking_m:
      form.max_leg_walking_m === "" ? null : form.max_leg_walking_m,
    optimize_order: form.optimize_order,
    enable_review: form.enable_review,
    accommodation: form.accommodation,
    dining_preference: form.dining_preference.trim(),
    preferences: [...form.preferences],
    free_text_input: form.free_text_input.trim(),
  };
  busy.value = true;
  plan.value = null;
  elapsed.value = 0;
  controller = new AbortController();
  ticker = setInterval(() => {
    elapsed.value++;
  }, 1000);
  try {
    plan.value = await createTrip(request, controller.signal);
    connection.value = "ready";
  } catch (caught) {
    if (caught instanceof DOMException && caught.name === "AbortError")
      statusMessage.value = "已停止等待，你可以修改需求后重新生成。";
    else
      error.value =
        caught instanceof Error
          ? caught.message
          : "暂时无法生成行程，请稍后重试。";
  } finally {
    busy.value = false;
    clearInterval(ticker);
    controller = null;
  }
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <a class="brand" href="#/" aria-label="下一程，首页"
        ><span class="brand-mark"><AppIcon name="compass" :size="25" /></span
        ><strong>下一程<span>旅行规划助手</span></strong></a
      >
      <div class="header-right">
        <span class="nav-current">规划行程</span
        ><button
          class="connection-badge"
          :class="connection"
          type="button"
          :disabled="connection === 'checking'"
          aria-label="重新检查服务连接"
          @click="refreshConnection"
        >
          <i></i
          >{{
            connection === "ready"
              ? "服务已连接"
              : connection === "checking"
                ? "检查连接中"
                : "服务暂未连接"
          }}
        </button>
      </div>
    </header>
    <main class="main-content">
      <section class="hero">
        <div>
          <p class="eyebrow">把向往，变成下一次出发</p>
          <h1>
            想去的地方，<br class="mobile-break" />安排得刚刚好<span
              class="small-dot"
              >.</span
            >
          </h1>
          <p class="hero-description">
            从景点到吃住，给旅程一份有据可循的安排。
          </p>
        </div>
        <div class="hero-stamp">
          <AppIcon name="route" :size="26" /><span
            >少一点折返<br /><strong>多一点从容</strong></span
          >
        </div>
      </section>
      <div class="planner-layout">
        <section class="form-panel" aria-labelledby="form-title">
          <div class="panel-heading">
            <div>
              <span class="step-label">01 / 告诉我你的想法</span>
              <h2 id="form-title">这一程，想怎么走？</h2>
            </div>
            <button
              type="button"
              class="text-button"
              :disabled="busy"
              @click="fillExample"
            >
              填写示例 <span aria-hidden="true">↗</span>
            </button>
          </div>
          <form @submit.prevent="submit">
            <fieldset :disabled="busy">
              <label class="field-label" for="city">目的地城市</label>
              <div class="input-with-icon">
                <AppIcon name="pin" :size="18" /><input
                  id="city"
                  v-model="form.city"
                  name="city"
                  placeholder="例如：北京、杭州、成都"
                  maxlength="100"
                  required
                  autocomplete="off"
                />
              </div>
              <div class="date-label">
                <span class="field-label">什么时候出发</span
                ><span class="days-tag">{{
                  days >= 1 && days <= 30 ? `${days} 天旅程` : "选择日期"
                }}</span>
              </div>
              <div class="two-fields">
                <div>
                  <label class="sub-label" for="start-date">开始日期</label
                  ><input
                    id="start-date"
                    v-model="form.start_date"
                    type="date"
                    required
                  />
                </div>
                <div>
                  <label class="sub-label" for="end-date">结束日期</label
                  ><input
                    id="end-date"
                    v-model="form.end_date"
                    type="date"
                    :min="form.start_date"
                    required
                  />
                </div>
              </div>
              <div class="two-fields field-group">
                <div>
                  <label class="field-label" for="route-mode">交通方式</label
                  ><select id="route-mode" v-model="form.route_mode">
                    <option value="auto">自动推荐</option>
                    <option value="transit">公共交通</option>
                    <option value="walking">步行</option>
                    <option value="driving">自驾</option>
                  </select>
                </div>
                <div>
                  <label class="field-label" for="accommodation">住宿偏好</label
                  ><select id="accommodation" v-model="form.accommodation">
                    <option>经济型酒店</option>
                    <option>舒适型酒店</option>
                    <option>高档酒店</option>
                    <option>民宿</option>
                    <option>青旅</option>
                  </select>
                </div>
              </div>
              <p v-if="form.route_mode === 'auto'" class="field-hint">
                近距离优先步行，较远路段比较步行与公共交通耗时。
              </p>
              <div class="field-group">
                <label class="field-label" for="max-leg-walking"
                  >每段最多步行（米，可选）</label
                >
                <input
                  id="max-leg-walking"
                  v-model.number="form.max_leg_walking_m"
                  type="number"
                  min="0"
                  max="50000"
                  step="1"
                  placeholder="例如 1000，留空表示不限制"
                  aria-describedby="walking-limit-hint"
                />
                <p id="walking-limit-hint" class="field-hint">
                  限制相邻景点路线中的步行，公交包含接驳步行；不含景点内部及酒店、餐馆接驳。无法满足时会提示。
                </p>
              </div>
              <div class="field-group">
                <span class="field-label">旅行偏好 <small>可多选</small></span>
                <div class="preference-options">
                  <button
                    v-for="preference in preferences"
                    :key="preference"
                    type="button"
                    :aria-pressed="form.preferences.includes(preference)"
                    :class="{ selected: form.preferences.includes(preference) }"
                    @click="togglePreference(preference)"
                  >
                    {{ preference
                    }}<AppIcon
                      v-if="form.preferences.includes(preference)"
                      name="check"
                      :size="13"
                    />
                  </button>
                </div>
              </div>
              <label class="optimization-toggle"
                ><input v-model="form.optimize_order" type="checkbox" /><span
                  ><strong>按位置调整景点顺序</strong
                  ><small
                    >保留首站、减少折返；有固定预约时段时请关闭。</small
                  ></span
                ></label
              >
              <label class="optimization-toggle"
                ><input v-model="form.enable_review" type="checkbox" /><span
                  ><strong>审核行程并按建议调整</strong
                  ><small
                    >检查需求与行程节奏，最多修改两轮；等待和模型调用会增加。</small
                  ></span
                ></label
              >
              <details class="extra-preferences">
                <summary>
                  餐饮与其他需求<span aria-hidden="true">＋</span>
                </summary>
                <div class="extra-fields">
                  <label class="field-label" for="dining">餐饮偏好</label
                  ><input
                    id="dining"
                    v-model="form.dining_preference"
                    placeholder="例如：本地菜、面食"
                    maxlength="100"
                  /><label class="field-label" for="free-text"
                    >还有什么想告诉我？</label
                  ><textarea
                    id="free-text"
                    v-model="form.free_text_input"
                    rows="3"
                    maxlength="2000"
                    placeholder="带小朋友、希望节奏轻松、特别想去的地方……"
                  ></textarea>
                </div>
              </details>
              <details class="extra-preferences">
                <summary>
                  费用估算（可选）<span aria-hidden="true">＋</span>
                </summary>
                <label class="optimization-toggle">
                  <input v-model="form.enable_budget" type="checkbox" />
                  <span
                    ><strong>按填写的标准估算费用</strong
                    ><small
                      >留空表示未知，填 0
                      表示这项不计费用；不是实际报价或行程价格上限。</small
                    ></span
                  >
                </label>
                <div v-if="form.enable_budget" class="extra-fields">
                  <div class="two-fields">
                    <div>
                      <label class="sub-label" for="budget-travelers"
                        >出行人数</label
                      ><input
                        id="budget-travelers"
                        v-model.number="form.travelers"
                        type="number"
                        min="1"
                        max="50"
                        step="1"
                        required
                      />
                    </div>
                    <div>
                      <label class="sub-label" for="budget-rooms"
                        >每晚房间数</label
                      ><input
                        id="budget-rooms"
                        v-model.number="form.rooms"
                        type="number"
                        min="1"
                        max="50"
                        step="1"
                        required
                      />
                    </div>
                  </div>
                  <template v-for="field in budgetRateFields" :key="field.key">
                    <label class="field-label" :for="`budget-${field.key}`">{{
                      field.label
                    }}</label>
                    <input
                      :id="`budget-${field.key}`"
                      v-model.number="form.budget_rates[field.key]"
                      type="number"
                      min="0"
                      max="1000000"
                      step="1"
                      placeholder="留空表示未知"
                    />
                  </template>
                  <p class="fine-print">
                    住宿按旅行天数减 1
                    晚估算；餐饮含全天，交通仅含市内，不含往返目的地的大交通。
                  </p>
                </div>
              </details>
            </fieldset>
            <div v-if="error" class="error-box" role="alert">{{ error }}</div>
            <p v-if="statusMessage" class="inline-message" role="status">
              {{ statusMessage }}
            </p>
            <button class="primary-button" type="submit" :disabled="busy">
              <span v-if="busy" class="spinner"></span
              ><AppIcon v-else name="spark" :size="18" />{{
                busy ? "正在安排行程…" : "生成我的行程"
              }}<AppIcon v-if="!busy" name="arrow" :size="18" />
            </button>
            <button
              v-if="busy"
              class="stop-button"
              type="button"
              @click="controller?.abort()"
            >
              停止等待
            </button>
            <p class="form-footnote">
              基于地点查询与天气预报生成，开放时间及预约请出行前确认。
            </p>
          </form>
        </section>

        <div class="result-area" :aria-busy="busy">
          <section
            v-if="busy"
            class="loading-panel"
            role="status"
            aria-live="polite"
          >
            <div class="loading-compass">
              <AppIcon name="compass" :size="48" />
            </div>
            <p class="eyebrow">好旅程，值得一点等待</p>
            <h2>正在把你的想法连成行程</h2>
            <p>
              {{
                form.enable_review
                  ? "查询资料、规划与审核行程，必要时按审核意见调整并重新查询路线。"
                  : "查询旅行资料、安排游览顺序，并核实景点间路线。"
              }}
            </p>
            <span class="elapsed">已等待 {{ elapsed }} 秒</span>
            <div class="loading-lines"><i></i><i></i><i></i></div>
            <small>长行程需要更久，你也可以停止等待后调整需求。</small>
          </section>
          <TripResults
            v-else-if="plan"
            :plan="plan"
            @edit-request="editRequest"
          />
          <section v-else class="empty-panel">
            <div class="empty-heading">
              <span class="step-label">02 / 收下你的旅行灵感</span
              ><span class="little-label">下一站，随你决定</span>
            </div>
            <div class="journey-illustration" aria-hidden="true">
              <svg viewBox="0 0 580 310" fill="none">
                <path
                  d="M50 220 145 170 230 205 335 85 460 120 530 60"
                  stroke="#dce7dd"
                  stroke-width="55"
                  stroke-linecap="round"
                />
                <path
                  d="M45 235 155 155 245 190 335 75 455 110 530 45"
                  stroke="#6b9b80"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-dasharray="5 8"
                />
                <path
                  d="m93 80 20-35 20 35H93Zm10 17h20l-10-17-10 17ZM420 233l22-40 22 40h-44Zm11 19h22l-11-19-11 19Z"
                  fill="#d8e7d9"
                />
                <circle
                  cx="155"
                  cy="155"
                  r="15"
                  fill="#fffaf0"
                  stroke="#b3cbb6"
                />
                <circle cx="155" cy="155" r="5" fill="#e6b65e" />
                <circle cx="335" cy="75" r="18" fill="#176451" />
                <path d="m328 75 5 5 9-11" stroke="white" stroke-width="2" />
                <circle
                  cx="455"
                  cy="110"
                  r="10"
                  fill="#fffaf0"
                  stroke="#a2bfa8"
                />
                <rect
                  x="210"
                  y="228"
                  width="132"
                  height="43"
                  rx="9"
                  fill="white"
                  stroke="#e0e6db"
                />
                <path
                  d="M226 243h13m-13 9h20"
                  stroke="#a8b9a8"
                  stroke-width="3"
                  stroke-linecap="round"
                />
                <text
                  x="258"
                  y="254"
                  fill="#49614d"
                  font-size="13"
                  font-family="sans-serif"
                >
                  LET’S GO
                </text></svg
              ><span class="map-caption">YOUR NEXT CHAPTER</span>
            </div>
            <h2>旅程，从一个想去的地方开始</h2>
            <p class="empty-description">
              选好城市和日期，告诉我你的偏好。<br />景点、路线、吃住安排，会在这里慢慢成形。
            </p>
            <div class="feature-row">
              <div>
                <span><AppIcon name="pin" /></span><strong>真实地点</strong
                ><small>景点与吃住候选</small>
              </div>
              <div>
                <span><AppIcon name="cloud" /></span><strong>天气参考</strong
                ><small>匹配你的出行日期</small>
              </div>
              <div>
                <span><AppIcon name="route" /></span><strong>从容出发</strong
                ><small>路线与交通建议</small>
              </div>
            </div>
          </section>
        </div>
      </div>
      <footer class="page-footer">
        <span>下一程 <span class="footer-divider">/</span> 把时间留给风景</span
        ><span>旅行规划建议 · 请以目的地实际情况为准</span>
      </footer>
    </main>
  </div>
</template>
