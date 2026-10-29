import { api, API_BASE_URL } from "./api.js";
import { ATTENDANCE_LABELS, schoolToday, reportPeriod, reportRangeError, attendanceSummary, reportDate } from "./reports.js";

const DAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];

export async function renderReportsPage(ui, selection = {}, openSettings = false) {
  const { escapeHtml: e, setView, notify, showError } = ui;
  const today = schoolToday();
  const [classes, students, shifts, schedules, calendar] = await Promise.all([
    api.classCatalog(), api.students(), api.shifts(), api.schedules(),
    api.calendar(`${today.slice(0, 4)}-01-01`, `${today.slice(0, 4)}-12-31`),
  ]);
  if (!ui.isActive()) return;
  const options = (items, selected = "") => items.map((item) => `<option value="${e(item.id)}" ${item.id === selected ? "selected" : ""}>${e(item.name)}</option>`).join("");
  const studentOptions = (items) => items.map((item) => `<option value="${e(item.id)}">${e(item.name)} · ${e(item.class_name)}</option>`).join("");
  setView(`<header class="page-header"><div><p class="eyebrow">Посещаемость школы</p><h1>Отчёты</h1><p class="page-description">Выберите период и посмотрите, кто пришёл, опоздал или отсутствовал.</p></div></header>
    <form id="report-form" class="panel form-grid">
      <h2>За какой период показать отчёт?</h2>
      <div class="report-periods wide" aria-label="Быстрый выбор периода"><button type="button" class="button small" data-period="today">Сегодня</button><button type="button" class="button small" data-period="week">Эта неделя</button><button type="button" class="button small" data-period="month">Этот месяц</button></div>
      <label>Начало периода<input name="date_from" type="date" value="${e(selection.date_from || today)}" required></label>
      <label>Конец периода<input name="date_to" type="date" value="${e(selection.date_to || today)}" required></label>
      <label>Класс<select name="class_id"><option value="">Все классы</option>${options(classes, selection.class_id)}</select></label>
      <label>Ученик<select name="student_id"><option value="">Все ученики выбранного класса</option>${studentOptions(students)}</select></label>
      <div class="form-actions wide"><button class="button primary" type="submit">Показать отчёт</button></div>
      <p id="report-feedback" class="form-description wide" role="status" aria-live="polite"></p>
    </form>
    <div id="report-summary" class="report-summary" aria-live="polite"></div>
    <section class="panel"><div class="panel-heading report-result-heading"><div><h2>Посещаемость по дням</h2><p id="report-caption" class="form-description">Загружаем отчёт…</p></div><div class="form-actions"><button id="export-xlsx" class="button" type="button" disabled>Скачать Excel</button><button id="export-csv" class="button ghost" type="button" disabled>Скачать CSV</button></div></div>
      <p class="report-explanation">Одна строка — один ученик за один учебный день. «Отсутствовал» означает, что отметки прихода нет; в течение дня данные могут измениться.</p>
      <div id="report-result" class="table-wrap" aria-live="polite"><p class="empty">Загрузка…</p></div>
    </section>
    <details id="report-settings" class="panel report-details" ${openSettings ? "open" : ""}><summary><span>Учебное расписание и выходные</span><small>Настройте один раз: это определяет учебные дни и опоздания</small></summary>
      <div class="report-settings-stack">
        <form id="shift-form" class="form-grid"><h2>1. Добавьте учебную смену</h2><p class="form-description wide">Смена задаёт время начала и окончания занятий, а также время, после которого ученик считается опоздавшим.</p>
          <label>Название смены<input name="name" placeholder="Например: Первая смена" required></label><label>Начало занятий<input name="starts_at" type="time" required></label><label>Считать опозданием после<input name="late_after" type="time" required></label><label>Окончание занятий<input name="ends_at" type="time" required></label>
          <div class="form-actions wide"><button class="button primary">Добавить смену</button></div>
          <div class="calendar-list wide">${shifts.map((shift) => `<span><strong>${e(shift.name)}</strong> · ${e(shift.starts_at.slice(0, 5))}–${e(shift.ends_at.slice(0, 5))} · опоздание после ${e(shift.late_after.slice(0, 5))}</span>`).join("") || '<span>Смен пока нет.</span>'}</div>
        </form>
        <form id="schedule-form" class="form-grid"><h2>2. Назначьте смену классу</h2><p class="form-description wide">Выберите день недели и смену. Повторите для каждого учебного дня класса. Сохранение заменит расписание этого класса на выбранный день.</p>
          <label>Класс<select name="class_id" required><option value="">Выберите класс</option>${options(classes)}</select></label><label>День недели<select name="weekday">${DAYS.map((day, i) => `<option value="${i}">${day}</option>`).join("")}</select></label>
          <label class="wide">Учебная смена<select name="shift_id" required><option value="">Выберите смену</option>${options(shifts)}</select></label>
          <div class="form-actions wide"><button class="button primary" ${!shifts.length || !classes.length ? "disabled" : ""}>Сохранить расписание</button></div>
        </form>
        <div class="table-wrap"><h2>Назначенное расписание</h2><table><thead><tr><th>Класс</th><th>День недели</th><th>Смена</th><th>Занятия</th><th>Опоздание после</th></tr></thead><tbody>${schedules.map((item) => `<tr><td>${e(item.class_name)}</td><td>${DAYS[item.weekday]}</td><td>${e(item.shift_name)}</td><td>${e(item.starts_at.slice(0, 5))}–${e(item.ends_at.slice(0, 5))}</td><td>${e(item.late_after.slice(0, 5))}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">Расписание ещё не задано. Добавьте смену и назначьте её классу.</td></tr>'}</tbody></table></div>
        <form id="calendar-form" class="form-grid"><h2>3. Укажите праздники и переносы, если нужны</h2><p class="form-description wide">Эта настройка заменяет обычное расписание на выбранную дату для всей школы.</p>
          <label>Дата<input name="date" type="date" value="${today}" required></label><label>Занятия в этот день<select name="is_school_day"><option value="false">Нет занятий — выходной или каникулы</option><option value="true">Учебный день — занятия есть</option></select></label><label class="wide">Название или пояснение<input name="title" placeholder="Например: Осенние каникулы"></label>
          <div class="form-actions wide"><button class="button primary">Сохранить день</button></div>
          <div class="calendar-list wide">${calendar.map((item) => `<span><strong>${reportDate(item.date)}</strong> · ${item.is_school_day ? "Учебный день" : "Нет занятий"}${item.title ? ` · ${e(item.title)}` : ""}</span>`).join("") || '<span>Праздники и переносы на этот год не заданы.</span>'}</div>
        </form>
      </div>
    </details>
    <details id="report-correction" class="panel report-details"><summary><span>Исправить отметку ученика</span><small>Если камера пропустила приход или есть уважительная причина</small></summary>
      <form id="correction-form" class="form-grid"><p class="form-description wide">Выберите ученика и дату, укажите верный статус и причину. Исправление будет отмечено в отчёте как ручное.</p>
        <label>Ученик<select name="student_id" required><option value="">Выберите ученика</option>${studentOptions(students)}</select></label><label>Дата<input name="date" type="date" value="${today}" required></label>
        <label>Верный статус<select name="status">${Object.entries(ATTENDANCE_LABELS).map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select></label><label>Время прихода, если известно<input name="arrived_at" type="time" step="1"></label><label>Время ухода, если известно<input name="left_at" type="time" step="1"></label>
        <label class="wide">Причина исправления<textarea name="reason" minlength="3" required placeholder="Например: Камера не зафиксировала приход, ученик пришёл в 08:10"></textarea></label>
        <div class="form-actions wide"><button class="button primary" ${!students.length ? "disabled" : ""}>Сохранить исправление</button></div>
      </form>
    </details>`);

  const form = document.querySelector("#report-form");
  const feedback = document.querySelector("#report-feedback");
  const result = document.querySelector("#report-result");
  const summary = document.querySelector("#report-summary");
  const caption = document.querySelector("#report-caption");
  const correction = document.querySelector("#correction-form");
  const exports = [document.querySelector("#export-csv"), document.querySelector("#export-xlsx")];
  const values = () => Object.fromEntries(new FormData(form));
  const updateStudents = () => {
    const selectedClass = classes.find((item) => item.id === form.elements.class_id.value);
    form.elements.student_id.innerHTML = '<option value="">Все ученики выбранного класса</option>' + studentOptions(students.filter((item) => !selectedClass || item.class_name === selectedClass.name));
  };
  updateStudents();
  if (selection.student_id && [...form.elements.student_id.options].some((option) => option.value === selection.student_id)) form.elements.student_id.value = selection.student_id;
  let rows = [], loadedFilters = null, requestVersion = 0;
  const markChanged = () => {
    requestVersion++;
    result.removeAttribute("aria-busy");
    exports.forEach((button) => { button.disabled = true; });
    feedback.textContent = "Фильтры изменены. Нажмите «Показать отчёт», чтобы обновить таблицу.";
  };
  form.addEventListener("input", markChanged);
  form.elements.class_id.addEventListener("change", updateStudents);

  const renderTable = (page = 1) => {
    const pages = Math.max(1, Math.ceil(rows.length / 50));
    const offset = (page - 1) * 50;
    result.innerHTML = rows.length ? `<table><thead><tr><th>Дата</th><th>Ученик</th><th>Класс</th><th>Статус</th><th>Приход</th><th>Уход</th><th>Пояснение</th><th></th></tr></thead><tbody>${rows.slice(offset, offset + 50).map((row, i) => `<tr><td>${reportDate(row.date)}</td><td><strong>${e(row.student_name)}</strong><small class="report-student-number">${e(row.student_number)}</small></td><td>${e(row.class_name)}</td><td><span class="report-status report-status-${Object.hasOwn(ATTENDANCE_LABELS, row.status) ? row.status : "unknown"}">${e(ATTENDANCE_LABELS[row.status] || row.status)}</span></td><td>${e(row.arrived_at?.slice(0, 5) || "—")}</td><td>${e(row.left_at?.slice(0, 5) || "—")}</td><td class="report-reason">${row.manual ? '<small class="report-manual">Исправлено вручную</small>' : ""}${e(row.reason || (row.manual ? "—" : "По данным камер"))}</td><td><button type="button" class="text-button" data-correct-row="${offset + i}">Исправить</button></td></tr>`).join("")}</tbody></table><div class="report-pagination"><span>Строки ${offset + 1}–${Math.min(offset + 50, rows.length)} из ${rows.length}</span><div class="pagination"><button class="button small" data-report-page="${page - 1}" ${page === 1 ? "disabled" : ""}>Назад</button><strong>${page} / ${pages}</strong><button class="button small" data-report-page="${page + 1}" ${page === pages ? "disabled" : ""}>Далее</button></div></div>` : `<div class="empty"><strong>За выбранный период нет строк отчёта</strong><p>Проверьте класс, ученика и даты. В отчёт входят учебные дни по расписанию, специальные учебные дни и ручные исправления.</p>${!schedules.length ? '<button class="button" type="button" data-open-report-settings>Настроить учебное расписание</button>' : ""}</div>`;
    result.querySelectorAll("[data-report-page]").forEach((button) => button.addEventListener("click", () => renderTable(Number(button.dataset.reportPage))));
    result.querySelector("[data-open-report-settings]")?.addEventListener("click", () => {
      const details = document.querySelector("#report-settings"); details.open = true; details.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    result.querySelectorAll("[data-correct-row]").forEach((button) => button.addEventListener("click", () => {
      const row = rows[Number(button.dataset.correctRow)];
      for (const [key, value] of Object.entries({ student_id: row.student_id, date: row.date, status: row.status, arrived_at: row.arrived_at || "", left_at: row.left_at || "", reason: row.reason || "" })) correction.elements[key].value = value;
      const details = document.querySelector("#report-correction"); details.open = true; details.scrollIntoView({ behavior: "smooth", block: "start" }); correction.elements.reason.focus({ preventScroll: true });
    }));
  };

  const loadReport = async () => {
    if (!form.reportValidity()) return;
    const filters = values();
    const error = reportRangeError(filters.date_from, filters.date_to);
    if (error) { feedback.textContent = error; return; }
    const version = ++requestVersion;
    feedback.textContent = "Формируем отчёт…";
    exports.forEach((button) => { button.disabled = true; });
    result.setAttribute("aria-busy", "true");
    try {
      const data = await api.attendanceReport(filters.date_from, filters.date_to, filters.class_id, filters.student_id);
      if (!result.isConnected || version !== requestVersion) return;
      rows = data;
      loadedFilters = filters;
      caption.textContent = `${reportDate(filters.date_from)} — ${reportDate(filters.date_to)} · ${classes.find((item) => item.id === filters.class_id)?.name || "Все классы"} · ${students.find((item) => item.id === filters.student_id)?.name || "Все ученики"} · ${rows.length} строк`;
      const counts = attendanceSummary(rows);
      summary.innerHTML = Object.entries(ATTENDANCE_LABELS).map(([status, label]) => `<article class="report-total report-total-${status}"><span>${label}</span><strong>${counts[status]}</strong><small>отметок за выбранный период</small></article>`).join("");
      feedback.textContent = "Отчёт обновлён. Даты и время — по Бишкеку.";
      renderTable();
      exports.forEach((button) => { button.disabled = !rows.length; });
    } catch (error) {
      if (!result.isConnected || version !== requestVersion) return;
      feedback.textContent = `Не удалось загрузить отчёт: ${error.message}`;
      if (!loadedFilters) result.innerHTML = '<p class="empty">Не удалось загрузить данные. Нажмите «Показать отчёт», чтобы повторить.</p>';
    } finally { if (version === requestVersion) result.removeAttribute("aria-busy"); }
  };
  form.addEventListener("submit", (event) => { event.preventDefault(); loadReport(); });
  form.querySelectorAll("[data-period]").forEach((button) => button.addEventListener("click", () => {
    const period = reportPeriod(button.dataset.period, today);
    form.elements.date_from.value = period.date_from; form.elements.date_to.value = period.date_to;
    markChanged(); loadReport();
  }));
  const exportReport = (extension) => {
    if (!loadedFilters || reportRangeError(...["date_from", "date_to"].map((key) => loadedFilters[key]))) return;
    window.open(`${API_BASE_URL}/api/v1/school-attendance/reports.${extension}?${new URLSearchParams(loadedFilters)}`, "_blank", "noopener");
  };
  exports[0].addEventListener("click", () => exportReport("csv"));
  exports[1].addEventListener("click", () => exportReport("xlsx"));

  const saveForm = (id, save, message, settings = false) => {
    document.querySelector(id).addEventListener("submit", async (event) => {
      event.preventDefault();
      const submitted = event.currentTarget;
      const button = submitted.querySelector('button[type="submit"], button:not([type])');
      button.disabled = true;
      try {
        await save(Object.fromEntries(new FormData(submitted)));
        if (!ui.isActive() || !submitted.isConnected) return;
        notify(message);
        if (settings) await renderReportsPage(ui, values(), true);
        else await loadReport();
      } catch (error) { showError(error); }
      finally { button.disabled = false; }
    });
  };
  saveForm("#shift-form", (data) => api.createShift(data), "Смена добавлена. Теперь назначьте её классу.", true);
  saveForm("#schedule-form", (data) => api.setSchedule({ ...data, weekday: Number(data.weekday) }), "Расписание сохранено.", true);
  saveForm("#calendar-form", (data) => api.setCalendarDay({ ...data, is_school_day: data.is_school_day === "true" }), "Учебный календарь обновлён.", true);
  saveForm("#correction-form", (data) => api.correctAttendance({ ...data, arrived_at: data.arrived_at || null, left_at: data.left_at || null }), "Исправление сохранено.");
  await loadReport();
}
