import type { TripPlan } from "../types/trip";
import { distance, duration, money } from "./format";

export type DocumentBlockKind =
  "title" | "heading" | "subheading" | "body" | "muted" | "warning";
export interface DocumentBlock {
  kind: DocumentBlockKind;
  text: string;
  pageBreakBefore?: boolean;
}
const modeNames = { walking: "步行", driving: "驾车", transit: "公共交通" };
const mealNames = {
  breakfast: "早餐",
  lunch: "午餐",
  dinner: "晚餐",
  snack: "加餐",
};
const preferenceNames = {
  supported: "有地图类别依据",
  hint: "仅有名称线索",
  unknown: "待确认",
  conflict: "类别冲突",
};
const reviewNames = {
  passed: "审核通过",
  needs_revision: "行程仍需调整",
  unavailable: "审核未完成",
};

export function buildTripDocument(plan: TripPlan): DocumentBlock[] {
  const blocks: DocumentBlock[] = [];
  const add = (
    kind: DocumentBlockKind,
    text: string,
    pageBreakBefore = false,
  ) => blocks.push({ kind, text, pageBreakBefore });
  add("title", `${plan.city}旅行计划`);
  add("body", `${plan.start_date} — ${plan.end_date} · ${plan.days.length} 天`);
  add(
    "muted",
    "行程、天气与路线仅供出行参考，请在出发前确认开放时间、预约和交通条件。此文件保存导出时的最终版本，不会自动更新。",
  );

  if (plan.route_resolution) {
    const resolution = plan.route_resolution;
    add(
      "heading",
      resolution.status === "needs_choice"
        ? "部分路线待确认 · 请先选择下一步"
        : "相邻景点路线核验范围",
    );
    add(
      resolution.status === "needs_choice" ? "warning" : "body",
      resolution.summary,
    );
    add(
      "body",
      `原交通要求：${resolution.requested_transportation}；每段步行上限：${resolution.max_leg_walking_m === null ? "未设置" : `${resolution.max_leg_walking_m}米`}。`,
    );
    for (const item of resolution.unresolved) {
      add(
        "subheading",
        `第 ${item.day_index + 1} 天：${item.origin_name} → ${item.destination_name}`,
      );
      add("warning", item.explanation);
    }
    for (const option of resolution.alternatives) {
      add("subheading", `可选做法（待你确认，尚未应用）：${option.title}`);
      add("body", option.description);
      for (const change of option.condition_changes) add("muted", change);
      add(
        "muted",
        "此替代选择尚未重新查询，可用性待核验；原行程和原条件保持不变。",
      );
    }
    for (const limitation of resolution.limitations) add("muted", limitation);
  }

  if (plan.review) {
    const review = plan.review;
    add("heading", `行程审核 · ${reviewNames[review.status]}`);
    add(review.status === "passed" ? "body" : "warning", review.message);
    add(
      "muted",
      `审核尝试 ${review.review_count} 次，已调整 ${review.revisions} 轮。`,
    );
    if (review.result) {
      add("body", review.result.summary);
      for (const item of review.result.time_evidence ?? []) {
        add("body", item.display_text);
      }
      if (review.result.semantic_status === "unavailable") {
        add("warning", "语义审核未完成，当前保留的是程序核对结果。");
      } else if (review.result.semantic_status === "not_run") {
        add("muted", "语义审核尚未执行，其余安排仍需确认。");
      }
      for (const item of review.result.meal_consistency ?? []) {
        add(item.status === "conflict" ? "warning" : "muted", `第 ${item.day_index + 1} 天 · ${item.explanation}`);
      }
      for (const issue of review.result.issues) {
        add(
          "subheading",
          `${issue.day_index === null ? "整体行程" : `第 ${issue.day_index + 1} 天`}：${issue.problem}`,
        );
        add("body", `依据：${issue.evidence}`);
        if (issue.source) {
          add("muted", `来源：${issue.source === "program" ? "程序核对" : "模型意见"}`);
        }
        add("body", `修改建议：${issue.suggestion}`);
        if (issue.alternative) {
          add("warning", "其他调整方向 · 待你确认，尚未采用");
          add("body", issue.alternative.proposal);
          add("muted", "这可能改变原要求。只有你同意并修改需求后才重新规划，本次不会自动执行。");
        }
      }
    }
  } else {
    add("muted", "本次行程未开启自动审核。");
  }

  if (plan.budget) {
    const budget = plan.budget;
    const estimateMoney = (value: number | null) =>
      value === null ? "未填写" : money(value);
    add("heading", "旅行费用估算");
    add(
      "body",
      `${budget.travelers} 人 · ${budget.rooms} 间房 · ${budget.nights} 晚；按填写标准估算，不是实际报价。`,
    );
    for (const item of budget.items) {
      add("subheading", `${item.label}：${estimateMoney(item.subtotal)}`);
      add(
        "muted",
        `${item.basis}；填写单价：${estimateMoney(item.unit_price)}`,
      );
    }
    add(
      "body",
      budget.is_complete
        ? `四项估算合计：${money(budget.total)}`
        : `可计算项目小计：${money(budget.known_subtotal)}；另有未填写项目，不能视为完整旅行费用。`,
    );
    for (const note of budget.notes) add("muted", note);
  } else {
    add("muted", "本次行程未开启费用估算，未知价格不代表免费。");
  }

  for (const day of plan.days) {
    add("heading", `第 ${day.day_index + 1} 天 · ${day.date}`, true);
    add("body", day.description);
    add("muted", `交通：${day.transportation}；住宿偏好：${day.accommodation}`);
    const weather = plan.weather_info.find((item) => item.date === day.date);
    add(
      "body",
      weather
        ? `天气：白天 ${weather.day_weather} ${weather.day_temp}℃；夜间 ${weather.night_weather} ${weather.night_temp}℃；${weather.wind_direction} ${weather.wind_power}。`
        : "该日期暂无天气预报，请临近出行时再查询。",
    );
    if (day.order_optimization)
      add(
        "muted",
        `景点顺序说明：${day.order_optimization.reason}（距离依据为坐标间直线距离）。`,
      );

    day.attractions.forEach((place, index) => {
      add("subheading", `${index + 1}. ${place.name}`);
      add(
        "muted",
        `地址：${place.address || "暂未提供"}；建议游览：${place.visit_duration} 分钟；门票：${money(place.ticket_price)}`,
      );
      add("body", place.description);
      const next = day.attractions[index + 1];
      if (!next) return;
      const route = day.routes.find(
        (item) =>
          item.origin.id === place.poi_id &&
          item.destination.id === next.poi_id,
      );
      if (!route) {
        add(
          "warning",
          `${place.name} → ${next.name}：路线暂未查询成功，请另行确认。`,
        );
        return;
      }
      add(
        "body",
        `${route.origin.name} → ${route.destination.name}：${modeNames[route.mode]}，${distance(route.distance_m)}，预计 ${duration(route.duration_s)}。`,
      );
      if (route.walking_distance_m !== null)
        add("muted", `其中步行距离约 ${distance(route.walking_distance_m)}。`);
      if (route.selection_reason)
        add("muted", `选择依据：${route.selection_reason}`);
      for (const step of route.steps) add("muted", `路线说明：${step}`);
      for (const warning of route.warnings) add("warning", warning);
    });
    for (const warning of day.route_warnings)
      add("warning", `路线提示：${warning}`);
    add("subheading", "用餐安排");
    if (!day.meals.length) add("body", "暂无候选餐馆，请自行确认用餐安排。");
    for (const meal of day.meals) {
      add(
        "body",
        `${mealNames[meal.type]}：${meal.name}；费用：${money(meal.estimated_cost)}`,
      );
      add("muted", `地址：${meal.address || "暂未提供"}`);
      if (meal.description) add("body", meal.description);
      for (const item of meal.preference_evidence || []) {
        add("body", `${item.preference}：${preferenceNames[item.status]}`);
        if (item.value)
          add(
            "muted",
            `依据（${item.source === "poi_type" ? "地图类别" : "名称线索"}）：${item.value}`,
          );
        add("muted", item.explanation);
      }
    }
    for (const warning of day.dining_warnings || []) add("warning", warning);
    if (day.dining_resolution) {
      const dining = day.dining_resolution;
      add("subheading", "餐饮候选交付说明");
      add("body", dining.summary);
      for (const note of dining.preference_notes || []) add("muted", note);
      if (dining.requested_preference)
        add(
          "muted",
          `原餐饮偏好：${dining.requested_preference}；适配尚未核实。`,
        );
      for (const item of dining.unresolved)
        add(
          "warning",
          `${mealNames[item.meal_type]} · ${item.anchor_name}：${item.explanation}`,
        );
      for (const option of dining.alternatives) {
        add("subheading", `餐饮可选做法（待确认，尚未应用）：${option.title}`);
        add("body", option.description);
        add("muted", "可用性待核验，需你选择后再处理。");
      }
    }
    add("subheading", "住宿候选");
    if (day.hotel) {
      add("body", day.hotel.name);
      add(
        "muted",
        `地址：${day.hotel.address || "暂未提供"}；房价：${day.hotel.price_range || money(day.hotel.estimated_cost)}；评分：${day.hotel.rating ?? "未查询"}`,
      );
      add("muted", "候选住宿不代表房态或预订已确认。");
    } else {
      add("body", "暂无候选住宿，请自行确认。");
    }
  }
  add("heading", "出行前，记得这些");
  add("body", plan.overall_suggestions);
  return blocks;
}

export function tripPlainText(plan: TripPlan): string {
  return buildTripDocument(plan)
    .map((block) => block.text)
    .join("\n\n");
}
