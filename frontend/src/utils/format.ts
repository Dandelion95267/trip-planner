export function localDate(offset = 0): string {
  const date = new Date();
  date.setDate(date.getDate() + offset);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}
export function tripDays(start: string, end: string): number {
  const a = new Date(`${start}T00:00:00Z`);
  const b = new Date(`${end}T00:00:00Z`);
  if (!Number.isFinite(a.getTime()) || !Number.isFinite(b.getTime())) return 0;
  if (
    a.toISOString().slice(0, 10) !== start ||
    b.toISOString().slice(0, 10) !== end
  )
    return 0;
  return Math.round((b.getTime() - a.getTime()) / 86_400_000) + 1;
}
export function shortDate(date: string): string {
  const [, month, day] = date.split("-");
  return `${Number(month)}月${Number(day)}日`;
}
export function distance(meters: number | null): string {
  if (meters === null) return "总距离未提供";
  return meters < 1000 ? `${meters} 米` : `${(meters / 1000).toFixed(2)} 公里`;
}
export function duration(seconds: number): string {
  const minutes = Math.ceil(seconds / 60);
  return minutes < 60
    ? `${minutes} 分钟`
    : `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}
export function money(value: number | null | undefined): string {
  return value == null ? "未查询" : `¥${value}`;
}
