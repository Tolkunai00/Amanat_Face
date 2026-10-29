export const ATTENDANCE_LABELS = {
  present: "Присутствовал",
  late: "Опоздал",
  absent: "Отсутствовал",
  excused: "Уважительная причина",
};

export function schoolToday(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en", {
    timeZone: "Asia/Bishkek", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const get = (type) => parts.find((part) => part.type === type).value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

export function reportPeriod(period, today) {
  let start = today;
  if (period === "month") start = `${today.slice(0, 8)}01`;
  if (period === "week") {
    const date = new Date(`${today}T12:00:00Z`);
    date.setUTCDate(date.getUTCDate() - (date.getUTCDay() + 6) % 7);
    start = date.toISOString().slice(0, 10);
  }
  return { date_from: start, date_to: today };
}

export function reportRangeError(from, to) {
  if (!from || !to) return "Укажите начало и конец периода.";
  if (to < from) return "Конец периода должен быть не раньше начала.";
  if ((Date.parse(to) - Date.parse(from)) / 86400000 > 366) return "Выберите период не больше 367 дней.";
  return "";
}

export function attendanceSummary(rows) {
  const counts = { present: 0, late: 0, absent: 0, excused: 0 };
  for (const row of rows) if (Object.hasOwn(counts, row.status)) counts[row.status]++;
  return counts;
}

export function reportDate(value) {
  return value ? value.slice(0, 10).split("-").reverse().join(".") : "—";
}
