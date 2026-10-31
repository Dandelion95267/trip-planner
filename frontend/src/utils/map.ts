import gcoord from "gcoord";
import type { DayPlan, Location } from "../types/trip";

export type PointCategory = "attraction" | "meal" | "hotel";
export interface MapPoint {
  key: string;
  poiId: string | null;
  name: string;
  address: string;
  label: string;
  category: PointCategory;
  location: Location | null;
  sequence: number;
}

export function validLocation(value: Location | null): value is Location {
  return (
    !!value &&
    Number.isFinite(value.longitude) &&
    Number.isFinite(value.latitude) &&
    Math.abs(value.longitude) <= 180 &&
    Math.abs(value.latitude) <= 90
  );
}

export function toMapLatLng(location: Location): [number, number] {
  // 高德为 GCJ-02，OpenStreetMap 为 WGS-84；Leaflet 参数顺序为 [纬度, 经度]。
  const coordinate = gcoord.transform(
    [location.longitude, location.latitude],
    gcoord.GCJ02,
    gcoord.WGS84,
  );
  return [coordinate[1], coordinate[0]];
}

export function dayMapPoints(day: DayPlan): MapPoint[] {
  const raw: MapPoint[] = day.attractions.map((place, index) => ({
    key: `attraction:${place.poi_id || index}`,
    poiId: place.poi_id,
    name: place.name,
    address: place.address,
    label: `第 ${index + 1} 站`,
    category: "attraction",
    location: place.location,
    sequence: index + 1,
  }));
  const labels = {
    breakfast: "早餐",
    lunch: "午餐",
    dinner: "晚餐",
    snack: "加餐",
  };
  for (const meal of day.meals)
    raw.push({
      key: `meal:${meal.poi_id || meal.name}`,
      poiId: meal.poi_id,
      name: meal.name,
      address: meal.address || "",
      label: labels[meal.type],
      category: "meal",
      location: meal.location,
      sequence: 0,
    });
  if (day.hotel)
    raw.push({
      key: `hotel:${day.hotel.poi_id || day.hotel.name}`,
      poiId: day.hotel.poi_id,
      name: day.hotel.name,
      address: day.hotel.address,
      label: "候选住宿",
      category: "hotel",
      location: day.hotel.location,
      sequence: 0,
    });
  // 午餐和晚餐推荐同一地点时合并标记，保留两个用餐标签。
  const unique = new Map<string, MapPoint>();
  for (const point of raw) {
    const previous = unique.get(point.key);
    if (previous) previous.label += ` / ${point.label}`;
    else unique.set(point.key, { ...point });
  }
  return [...unique.values()];
}
