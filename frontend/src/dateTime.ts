export const DISPLAY_TIME_ZONE = "Asia/Shanghai";

export function formatCreatedAt(value: string): string {
  // Older timestamp values without an offset were also recorded in UTC.
  const qualified = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(value) ? value + "Z" : value;
  const date = new Date(qualified);
  if (Number.isNaN(date.getTime())) return value;
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: DISPLAY_TIME_ZONE,
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const part = (name: string) => parts.find(item => item.type === name)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}:${part("second")}（北京时间）`;
}
