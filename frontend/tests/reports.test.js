import test from "node:test";
import assert from "node:assert/strict";
import { schoolToday, reportPeriod, reportRangeError, attendanceSummary } from "../src/reports.js";

test("report today follows Bishkek even when UTC is still the previous date", () => {
  assert.equal(schoolToday(new Date("2026-10-20T19:30:00Z")), "2026-10-21");
  assert.equal(schoolToday(new Date("2026-10-20T17:59:00Z")), "2026-10-20");
});

test("week starts on Monday including across year boundaries", () => {
  assert.deepEqual(reportPeriod("week", "2027-01-03"), { date_from: "2026-12-28", date_to: "2027-01-03" });
  assert.deepEqual(reportPeriod("week", "2026-10-19"), { date_from: "2026-10-19", date_to: "2026-10-19" });
});

test("month and today presets keep the selected school date", () => {
  assert.deepEqual(reportPeriod("month", "2026-10-20"), { date_from: "2026-10-01", date_to: "2026-10-20" });
  assert.deepEqual(reportPeriod("today", "2026-10-20"), { date_from: "2026-10-20", date_to: "2026-10-20" });
});

test("date range validation accepts the backend limit and rejects reversed or excessive periods", () => {
  assert.equal(reportRangeError("2024-01-01", "2025-01-01"), "");
  assert.ok(reportRangeError("2024-01-01", "2025-01-02"));
  assert.ok(reportRangeError("2026-10-20", "2026-10-19"));
  assert.ok(reportRangeError("", "2026-10-20"));
});

test("summary counts daily statuses without treating late attendance as absence", () => {
  const rows = ["present", "late", "late", "absent", "excused"].map((status) => ({ status }));
  assert.deepEqual(attendanceSummary(rows), { present: 1, late: 2, absent: 1, excused: 1 });
  assert.deepEqual(attendanceSummary([]), { present: 0, late: 0, absent: 0, excused: 0 });
});
