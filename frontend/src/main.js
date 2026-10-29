import "./styles.css";
import { connectCameraFeeds } from "./cameraFeeds.js";
import { API_BASE_URL, ApiError, api, openProtectedPhoto } from "./api.js";
import { connectNotificationsSocket, disconnectNotificationsSocket } from "./notificationsSocket.js";
import { renderReportsPage } from "./reportsPage.js";

const app = document.querySelector("#app");
const state = { session: null, students: [], activeView: "dashboard", unreadCount: 0, cameraTimer: null };
let cameraFeeds = null;
let cameraPollAbort = null;

function stopCameraView() {
  window.clearInterval(state.cameraPreviewTimer);
  state.cameraPreviewTimer = null;
  if (state.cameraTimer) window.clearInterval(state.cameraTimer);
  state.cameraTimer = null;
  cameraPollAbort?.abort();
  cameraPollAbort = null;
  cameraFeeds?.dispose();
  cameraFeeds = null;
}
const PAGE_SIZE = 15;
const WEEKDAYS = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"];

const escapeHtml = (value) => String(value ?? "")
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");

const roleLabel = (role) => ({ admin: "Администратор", teacher: "Учитель", parent: "Родитель" }[role] || role);
const statusLabel = (status) => status === "keldi" ? "Пришел" : "Ушел";
const eventTypeLabel = (eventType) => eventType === "enter" ? "Пришёл / Кирди" : "Ушёл / Чыкты";

function paginated(items, query, fields, page) {
  const normalized = String(query || "").trim().toLocaleLowerCase("ru");
  const filtered = normalized ? items.filter((item) => fields.some((field) =>
    String(field(item) ?? "").toLocaleLowerCase("ru").includes(normalized))) : items;
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(Math.max(Number(page) || 1, 1), pages);
  return { items: filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE), total: filtered.length, pages, page: current };
}

function listControls(query, result, placeholder = "Поиск") {
  return `<form class="list-controls" data-list-search><input name="q" value="${escapeHtml(query)}" placeholder="${escapeHtml(placeholder)}">
    <button class="button" type="submit">Найти</button><span>${result.total} записей</span>
    <div class="pagination"><button class="button small" data-page="${result.page - 1}" type="button" ${result.page <= 1 ? "disabled" : ""}>←</button>
      <strong>${result.page} / ${result.pages}</strong><button class="button small" data-page="${result.page + 1}" type="button" ${result.page >= result.pages ? "disabled" : ""}>→</button></div></form>`;
}

async function refreshUnreadCount() {
  if (!state.session) return;
  const result = await api.unreadNotificationCount();
  state.unreadCount = result.count;
  const badge = document.querySelector("#notification-badge");
  if (badge) badge.textContent = state.unreadCount ? ` ${state.unreadCount}` : "";
}

async function catchUpNotifications() {
  await Promise.all([api.notifications(true), refreshUnreadCount()]);
  if (state.activeView === "notifications") await renderNotifications();
}

function startNotificationsSocket() {
  connectNotificationsSocket({
    onCatchUp: catchUpNotifications,
    onNotification: async (notification) => {
      state.unreadCount += 1;
      const badge = document.querySelector("#notification-badge");
      if (badge) badge.textContent = ` ${state.unreadCount}`;
      notify(notification.message);
      if (state.activeView === "notifications") await renderNotifications();
    },
  });
}

function notify(message, type = "success") {
  const target = document.querySelector("#notification");
  if (!target) return;
  target.textContent = message;
  target.className = `notification ${type}`;
  window.setTimeout(() => { target.className = "notification hidden"; }, 3500);
}

function showError(error) {
  if (error instanceof ApiError && error.status === 401) {
    disconnectNotificationsSocket();
    state.session = null;
    renderLogin("Сессия завершена. Войдите снова.");
    return;
  }
  notify(error.message || "Произошла ошибка", "error");
}

function renderLogin(error = "") {
  stopCameraView();
  app.innerHTML = `
    <main class="login-layout">
      <section class="login-brand">
        <div class="login-ambient" aria-hidden="true">
          <span></span><span></span><span></span>
        </div>
        <div class="brand-lockup">
          <div class="brand-mark">A</div>
          <div><strong>AMANAT</strong><small>School intelligence</small></div>
        </div>
        <div class="login-copy">
          <p class="eyebrow light">Безопасная школа</p>
          <h1>Каждый приход<br><em>под контролем.</em></h1>
          <p>Умная система посещаемости и распознавания учеников в реальном времени.</p>
          <div class="feature-pills" aria-label="Возможности системы">
            <span><i></i> Face ID</span>
            <span><i></i> Live-статус</span>
            <span><i></i> Уведомления</span>
          </div>
        </div>
        <div class="login-signal" aria-hidden="true">
          <div class="signal-icon">✓</div>
          <div><small>Система активна</small><strong>Распознавание работает</strong></div>
          <span>LIVE</span>
        </div>
      </section>
      <section class="login-card">
        <div class="login-card-mark">A</div>
        <p class="eyebrow">Личный кабинет</p>
        <h2>Вход</h2>
        <p class="login-subtitle">Введите данные вашей учётной записи</p>
        ${error ? `<p class="form-error">${escapeHtml(error)}</p>` : ""}
        <form id="login-form" class="form-stack">
          <label>Логин<input name="login" autocomplete="username" required autofocus></label>
          <label>Пароль<input name="password" type="password" autocomplete="current-password" required></label>
          <button class="button primary" type="submit">Войти</button>
        </form>
        <p class="api-hint">API: ${escapeHtml(API_BASE_URL)}</p>
      </section>
    </main>`;

  document.querySelector("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const button = event.currentTarget.querySelector("button");
    button.disabled = true;
    try {
      state.session = await api.login(form.get("login"), form.get("password"));
      state.activeView = state.session.role === "parent" ? "parent" : "dashboard";
      await refreshUnreadCount();
      renderShell();
      startNotificationsSocket();
      await renderActiveView();
    } catch (loginError) {
      renderLogin(loginError.message);
    }
  });
}

function navigation() {
  if (state.session.role === "parent") {
    return `<button data-view="parent">Отчёт ребёнка</button>
      <button data-view="notifications">Уведомления<span id="notification-badge">${state.unreadCount ? ` ${state.unreadCount}` : ""}</span></button>`;
  }
  const admin = state.session.role === "admin";
  return `
    <button data-view="dashboard">Обзор</button>
    <button data-view="attendance">Посещаемость</button>
    <button data-view="notifications">Уведомления<span id="notification-badge">${state.unreadCount ? ` ${state.unreadCount}` : ""}</span></button>
    ${admin ? '<button data-view="students">Ученики</button>' : ""}
    ${admin ? '<button data-view="teachers">Учителя</button>' : ""}
    ${admin ? '<button data-view="classes-admin">Классы</button>' : ""}
    ${admin ? '<button data-view="parents-admin">Родители</button>' : ""}
    ${admin ? '<button data-view="reports">Отчёты</button>' : ""}
    ${admin ? '<button data-view="audit">Аудит</button>' : ""}
    ${admin ? '<button data-view="camera">Камера</button><button data-view="classroom">Аудитория</button>' : ""}`;
}

function renderShell() {
  app.innerHTML = `
    <div class="app-shell">
      <aside class="sidebar">
        <div class="sidebar-brand"><span>ID</span><strong>School Face ID</strong></div>
        <nav id="navigation">${navigation()}</nav>
        <div class="account">
          <small>${escapeHtml(roleLabel(state.session.role))}</small>
          <strong>${escapeHtml(state.session.login)}</strong>
          <button id="logout" class="button ghost">Выйти</button>
        </div>
      </aside>
      <main class="content">
        <div id="notification" class="notification hidden"></div>
        <section id="view"><div class="loading">Загрузка…</div></section>
      </main>
    </div>`;

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.addEventListener("click", async () => {
      state.activeView = button.dataset.view;
      await renderActiveView();
    });
  });
  document.querySelector("#logout").addEventListener("click", async () => {
    try { await api.logout(); } finally {
      disconnectNotificationsSocket();
      state.session = null;
      state.unreadCount = 0;
      renderLogin();
    }
  });
}

function setView(html) {
  const view = document.querySelector("#view");
  view.innerHTML = html;
  view.classList.remove("view-enter");
  void view.offsetWidth;
  view.classList.add("view-enter");
  document.querySelectorAll("#navigation [data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.activeView);
    button.setAttribute("aria-current", button.dataset.view === state.activeView ? "page" : "false");
  });
}

async function renderDashboard() {
  const classes = await api.classes();
  const totals = classes.reduce((sum, row) => ({
    students: sum.students + row.students,
    arrived: sum.arrived + row.arrived,
    left: sum.left + row.left,
  }), { students: 0, arrived: 0, left: 0 });
  setView(`
    <header class="page-header"><div><p class="eyebrow">Сегодня</p><h1>Обзор школы</h1></div></header>
    <div class="stats">
      <article><span>Ученики</span><strong>${totals.students}</strong></article>
      <article><span>Пришли</span><strong>${totals.arrived}</strong></article>
      <article><span>Ушли</span><strong>${totals.left}</strong></article>
    </div>
    <section class="panel">
      <h2>Классы</h2>
      <div class="class-grid">${classes.map((row) => `
        <button class="class-card" data-open-class="${escapeHtml(row.class_name)}">
          <strong>${escapeHtml(row.class_name || "Без класса")}</strong>
          <span>${row.students} учеников · ${row.arrived} пришли</span>
        </button>`).join("") || '<p class="empty">Классы ещё не добавлены</p>'}</div>
    </section>`);
  document.querySelectorAll("[data-open-class]").forEach((button) => {
    button.addEventListener("click", async () => renderClassReport(button.dataset.openClass));
  });
}

async function renderAttendance() {
  const classes = await api.classes();
  const admin = state.session.role === "admin";
  const events = admin ? await api.attendanceEvents() : [];
  setView(`
    <header class="page-header"><div><p class="eyebrow">Журнал</p><h1>Посещаемость</h1></div>
      ${admin ? '<button id="clear-attendance" class="button danger">Очистить журнал</button>' : ""}</header>
    <div class="class-grid">${classes.map((row) => `
      <button class="class-card" data-open-class="${escapeHtml(row.class_name)}">
        <strong>${escapeHtml(row.class_name)}</strong>
        <span>Пришли: ${row.arrived} · Ушли: ${row.left}</span>
      </button>`).join("")}</div>
    ${admin ? `<section class="panel table-wrap"><h2>История проходов</h2><table>
      <thead><tr><th>Время</th><th>Номер</th><th>Имя</th><th>Класс</th><th>Событие</th><th>Фото</th></tr></thead>
      <tbody>${events.map((event) => `<tr><td>${escapeHtml(event.timestamp)}</td>
        <td>${escapeHtml(event.student_number)}</td><td>${escapeHtml(event.name)}</td>
        <td>${escapeHtml(event.class_name)}</td><td>${escapeHtml(eventTypeLabel(event.event_type))}</td>
        <td>${event.photo_url ? `<button class="button ghost" data-event-photo="${escapeHtml(event.photo_url)}">Открыть</button>` : "Фото удалено"}</td></tr>`).join("") ||
        '<tr><td colspan="6" class="empty">История пока пуста</td></tr>'}</tbody>
    </table></section>` : ""}`);
  document.querySelectorAll("[data-open-class]").forEach((button) => {
    button.addEventListener("click", () => renderClassReport(button.dataset.openClass));
  });
  document.querySelectorAll("[data-event-photo]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await openProtectedPhoto(button.dataset.eventPhoto);
      } catch (error) {
        button.textContent = "Фото удалено";
        button.disabled = true;
        showError(error);
      }
    });
  });
  document.querySelector("#clear-attendance")?.addEventListener("click", async () => {
    if (!window.confirm("Очистить доступный журнал посещаемости?")) return;
    try { await api.clearAttendance(); notify("Журнал очищен"); await renderAttendance(); } catch (error) { showError(error); }
  });
}

async function renderClassReport(className) {
  const rows = await api.classReport(className);
  setView(`
    <header class="page-header"><div><p class="eyebrow">Класс</p><h1>${escapeHtml(className)}</h1></div>
      <button id="back-attendance" class="button ghost">Назад</button></header>
    <section class="panel table-wrap"><table>
      <thead><tr><th>Номер</th><th>Ученик</th><th>Дата</th><th>Пришел</th><th>Ушел</th></tr></thead>
      <tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.student_number)}</td><td>${escapeHtml(row.name)}</td><td>${escapeHtml(row.date)}</td>
        <td>${escapeHtml(row.arrived_at || "—")}</td><td>${escapeHtml(row.left_at || "—")}</td></tr>`).join("") ||
        '<tr><td colspan="5" class="empty">Нет данных</td></tr>'}</tbody>
    </table></section>`);
  document.querySelector("#back-attendance").addEventListener("click", renderAttendance);
}

function studentForm(student = null) {
  const primaryParent = student?.parents?.[0] || null;
  return `<form id="student-form" class="panel form-grid" data-old-name="${escapeHtml(student?.name || "")}">
    <h2>${student ? "Изменить ученика" : "Добавить ученика"}</h2>
    <label>Имя<input name="name" value="${escapeHtml(student?.name || "")}" required></label>
    <label>Класс<input name="class_name" value="${escapeHtml(student?.class_name || "")}" required></label>
    <label>Имя родителя<input name="parent_name" value="${escapeHtml(primaryParent?.display_name || "")}"></label>
    <label>Логин родителя<input name="parent_login" value="${escapeHtml(primaryParent?.login || "")}"></label>
    <label>Телефон родителя<input name="parent_phone" value="${escapeHtml(primaryParent?.phone || "")}" placeholder="+996700123456"></label>
    <label>Связь<select name="parent_relationship">
      ${["mother", "father", "guardian", "other"].map((value) => `<option value="${value}" ${primaryParent?.relationship === value ? "selected" : ""}>${value}</option>`).join("")}
    </select></label>
    <label>Пароль родителя<input name="parent_password" type="password" value="" placeholder="Обязателен только для нового аккаунта"></label>
    <label class="wide">${student ? "Основное фото" : "1. Фото спереди"}<input name="photo" type="file" accept="image/*" ${student ? "" : "required"}><small>Одно лицо, хорошее освещение, без фильтров.</small></label>
    ${student ? "" : `
      <p class="form-description wide">Добавьте три разных фото одного ученика. Поворачивайте голову слегка, примерно на 20–30°, чтобы оба глаза оставались видны. Фото с расстояния можно добавить четвёртым, если лицо на нём чёткое.</p>
      <label>2. Поворот влево<input name="photo_left" type="file" accept="image/*" required></label>
      <label>3. Поворот вправо<input name="photo_right" type="file" accept="image/*" required></label>
      <label class="wide">4. С расстояния — необязательно<input name="photo_distance" type="file" accept="image/*"><small>Снимите с обычного расстояния у входа. Лицо должно оставаться хорошо различимым.</small></label>
    `}
    <div class="form-actions wide"><button class="button primary">${student ? "Сохранить" : "Добавить"}</button>
      ${student ? '<button id="cancel-edit" class="button ghost" type="button">Отмена</button>' : ""}</div>
  </form>`;
}

async function renderStudents(editIndex = null, query = "", page = 1) {
  state.students = await api.students();
  const editing = editIndex === null ? null : state.students[editIndex];
  const result = paginated(state.students, query, [
    (item) => item.name, (item) => item.student_number, (item) => item.class_name,
    (item) => item.parents.map((parent) => `${parent.display_name} ${parent.login}`).join(" "),
  ], page);
  setView(`
    <header class="page-header"><div><p class="eyebrow">Администрирование</p><h1>Ученики</h1></div></header>
    ${studentForm(editing)}
    <section class="panel table-wrap">${listControls(query, result, "Имя, номер, класс или родитель")}<table>
      <thead><tr><th>Фото</th><th>Номер</th><th>Имя</th><th>Класс</th><th>Родитель</th><th></th></tr></thead>
      <tbody>${result.items.map((student) => { const index = state.students.indexOf(student); return `<tr>
        <td><img class="student-photo" src="${API_BASE_URL}${student.photo_url}" alt=""></td>
        <td>${escapeHtml(student.student_number)}</td><td><button class="text-button" data-view-student="${index}"><strong>${escapeHtml(student.name)}</strong></button></td><td>${escapeHtml(student.class_name)}</td>
        <td>${student.parents.map((parent) => `${escapeHtml(parent.display_name)} - ${escapeHtml(parent.relationship)}`).join("<br>") || "—"}</td><td class="actions">
          <button class="button small" data-edit-student="${index}">Изменить</button>
          <button class="button small danger" data-delete-student="${index}">Удалить</button></td></tr>`; }).join("") ||
          '<tr><td colspan="6" class="empty">Ученики ещё не добавлены</td></tr>'}</tbody>
    </table></section>`);

  document.querySelector("#student-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const submitButton = event.currentTarget.querySelector('button[type="submit"], button:not([type])');
    const originalLabel = submitButton.textContent;
    submitButton.disabled = true;
    submitButton.textContent = "Проверяем фотографии и сохраняем…";
    try {
      if (editing) await api.updateStudent(editing.id, form);
      else await api.createStudent(form);
      notify(editing ? "Ученик обновлён" : "Ученик добавлен");
      await renderStudents();
    } catch (error) { showError(error); }
    finally { submitButton.disabled = false; submitButton.textContent = originalLabel; }
  });
  document.querySelector("#cancel-edit")?.addEventListener("click", () => renderStudents());
  document.querySelector("[data-list-search]").addEventListener("submit", (event) => {
    event.preventDefault(); renderStudents(null, new FormData(event.currentTarget).get("q"), 1);
  });
  document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => {
    renderStudents(null, query, Number(button.dataset.page));
  }));
  document.querySelectorAll("[data-edit-student]").forEach((button) => {
    button.addEventListener("click", () => renderStudents(Number(button.dataset.editStudent)));
  });
  document.querySelectorAll("[data-view-student]").forEach((button) => {
    button.addEventListener("click", () => renderStudentCard(state.students[Number(button.dataset.viewStudent)]));
  });
  document.querySelectorAll("[data-delete-student]").forEach((button) => {
    button.addEventListener("click", async () => {
      const student = state.students[Number(button.dataset.deleteStudent)];
      if (!window.confirm(`Удалить ${student.name}?`)) return;
      try { await api.deleteStudent(student.id); notify("Ученик удалён"); await renderStudents(); } catch (error) { showError(error); }
    });
  });
}

async function renderStudentCard(student) {
  const events = await api.studentEvents(student.id);
  setView(`<header class="page-header"><div><p class="eyebrow">Карточка ученика</p><h1>${escapeHtml(student.name)}</h1>
      <p class="page-description">${escapeHtml(student.student_number)} · ${escapeHtml(student.class_name)}</p></div>
      <button id="back-students" class="button ghost">← К ученикам</button></header>
    <div class="stats"><article><span>Фотографии лица</span><strong>${escapeHtml(student.face_samples ?? 0)}</strong></article>
      <article><span>Готовность распознавания</span><strong>${student.recognition_ready ? "Готов" : "Нужно 3–5 фото"}</strong></article>
      <article><span>События</span><strong>${events.length}</strong></article></div>
    <form id="face-samples-form" class="panel form-grid"><h2>Повторная регистрация лица</h2>
      <p class="form-description wide">Добавьте разные чёткие фотографии. Система проверит освещение, размытие и наличие ровно одного лица. Всего должно быть 3–5 образцов.</p>
      <label class="wide">Новые фотографии<input name="photos" type="file" accept="image/*,.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff,.gif" multiple required><small>Размер и поворот исправятся автоматически. На каждом фото должно быть одно лицо.</small></label>
      <div class="form-actions wide"><button class="button primary">Проверить и добавить</button></div></form>
    <section class="panel table-wrap"><h2>История посещений</h2><table><thead><tr><th>Время</th><th>Событие</th><th>Источник</th><th>Точность</th></tr></thead>
      <tbody>${events.map((event) => `<tr><td>${escapeHtml(event.timestamp)}</td><td>${escapeHtml(eventTypeLabel(event.event_type))}</td><td>${escapeHtml(event.source)}</td><td>${event.confidence == null ? "—" : escapeHtml(Number(event.confidence).toFixed(3))}</td></tr>`).join("") || '<tr><td colspan="4" class="empty">История пуста</td></tr>'}</tbody></table></section>`);
  document.querySelector("#back-students").addEventListener("click", () => renderStudents());
  document.querySelector("#face-samples-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const files = event.currentTarget.elements.photos.files;
    if (!files.length || files.length > 5) { notify("Выберите от 1 до 5 фотографий", "error"); return; }
    try {
      const result = await api.addStudentFaces(student.id, files);
      notify(`Добавлено. Образцов: ${result.face_samples}`);
      const refreshed = (await api.students()).find((item) => item.id === student.id);
      await renderStudentCard(refreshed || student);
    } catch (error) { showError(error); }
  });
}

async function renderTeachers(query = "", page = 1) {
  const [teachers, classes] = await Promise.all([api.teachers(), api.classCatalog()]);
  const result = paginated(teachers, query, [
    (item) => item.full_name, (item) => item.login, (item) => item.phone,
    (item) => item.class_names.join(" "),
  ], page);
  setView(`
    <header class="page-header"><div><p class="eyebrow">Администрирование</p><h1>Учителя</h1></div></header>
    <form id="teacher-form" class="panel form-grid">
      <h2>Добавить учителя</h2>
      <label>ФИО<input name="full_name" placeholder="Фамилия Имя Отчество"></label>
      <label>Телефон<input name="phone" placeholder="+996..."></label>
      <label>Логин<input name="login" required></label>
      <label>Пароль<input name="password" type="password" minlength="12" placeholder="Минимум 12 символов" required></label>
      <fieldset class="wide class-selector">
        <legend>Классы учителя</legend>
        <div class="class-selector-header">
          <span>Выберите один или несколько классов</span>
          <div><button id="select-all-classes" class="text-button" type="button">Выбрать все</button>
            <button id="clear-all-classes" class="text-button" type="button">Снять выбор</button></div>
        </div>
        <div class="class-options">${classes.map((schoolClass) => `
          <label class="class-option"><input type="checkbox" name="class_ids" value="${escapeHtml(schoolClass.id)}">
            <span>${escapeHtml(schoolClass.name)}</span></label>`).join("") ||
          '<p class="empty class-empty">Сначала добавьте классы</p>'}</div>
      </fieldset>
      <div class="form-actions wide"><button class="button primary">Добавить учителя</button></div>
    </form>
    <section class="panel table-wrap">
      <div class="panel-heading"><div><p class="eyebrow">Аккаунты</p><h2>Список учителей</h2></div>
        <span class="count-badge">${result.total}</span></div>
      ${listControls(query, result, "ФИО, логин, телефон или класс")}
      <table><thead><tr><th>Учитель</th><th>Телефон</th><th>Последний вход</th><th>Классы</th><th></th></tr></thead>
      <tbody>${result.items.map((teacher) => { const index = teachers.indexOf(teacher); return `<tr><td><strong>${escapeHtml(teacher.full_name || teacher.login)}</strong><br><small>${escapeHtml(teacher.login)}</small>${teacher.is_active ? "" : '<br><small>Заблокирован</small>'}</td>
        <td>${escapeHtml(teacher.phone || "—")}</td><td>${escapeHtml(teacher.last_login_at || "Ещё не входил")}</td>
        <td><div class="class-tags">${teacher.class_names.map((className) => `<span>${escapeHtml(className)}</span>`).join("") || "Не назначены"}</div></td>
        <td class="actions"><button class="button small" data-edit-teacher="${index}">Изменить</button>
          <button class="button small ${teacher.is_active ? "danger" : ""}" data-status-teacher="${escapeHtml(teacher.id)}" data-next-active="${teacher.is_active ? "false" : "true"}">${teacher.is_active ? "Заблокировать" : "Разблокировать"}</button></td></tr>`; }).join("") ||
        '<tr><td colspan="5" class="empty">Учителя ещё не добавлены</td></tr>'}</tbody>
      </table>
    </section>`);

  const form = document.querySelector("#teacher-form");
  const classCheckboxes = [...form.querySelectorAll('input[name="class_ids"]')];
  document.querySelector("#select-all-classes").addEventListener("click", () => {
    classCheckboxes.forEach((item) => { item.checked = true; });
  });
  document.querySelector("#clear-all-classes").addEventListener("click", () => {
    classCheckboxes.forEach((item) => { item.checked = false; });
  });
  document.querySelector("[data-list-search]").addEventListener("submit", (event) => {
    event.preventDefault(); renderTeachers(new FormData(event.currentTarget).get("q"), 1);
  });
  document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => {
    renderTeachers(query, Number(button.dataset.page));
  }));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const selectedClasses = classCheckboxes.filter((item) => item.checked).map((item) => item.value);
    if (!selectedClasses.length) { notify("Выберите хотя бы один класс", "error"); return; }
    try {
      await api.createTeacher({
        login: formData.get("login"), password: formData.get("password"),
        full_name: formData.get("full_name"), phone: formData.get("phone"),
        class_ids: selectedClasses,
      });
      notify("Учитель добавлен");
      await renderTeachers();
    } catch (error) { showError(error); }
  });
  document.querySelectorAll("[data-edit-teacher]").forEach((button) => button.addEventListener("click", async () => {
    try {
      await renderTeacherEditor(teachers[Number(button.dataset.editTeacher)]);
    } catch (error) { showError(error); }
  }));
  document.querySelectorAll("[data-status-teacher]").forEach((button) => button.addEventListener("click", async () => {
    const nextActive = button.dataset.nextActive === "true";
    if (!window.confirm(nextActive ? "Разблокировать аккаунт учителя?" : "Заблокировать аккаунт учителя?")) return;
    try {
      await api.setTeacherStatus(button.dataset.statusTeacher, nextActive);
      notify(nextActive ? "Учитель разблокирован" : "Учитель заблокирован");
      await renderTeachers();
    } catch (error) { showError(error); }
  }));
}

async function renderTeacherEditor(teacher) {
  const classes = await api.classCatalog();
  setView(`
    <header class="page-header teacher-edit-header"><div><p class="eyebrow">Учителя · редактирование</p>
      <h1>${escapeHtml(teacher.login)}</h1><p class="page-description">Измените логин, классы или пароль учителя</p></div>
      <button id="back-to-teachers" class="button ghost" type="button">← К списку учителей</button></header>
    <div class="teacher-editor-grid">
      <form id="teacher-edit-form" class="panel form-grid teacher-edit-card">
        <div class="teacher-profile wide"><span>${escapeHtml(teacher.login.slice(0, 1).toUpperCase())}</span>
          <div><strong>${escapeHtml(teacher.login)}</strong><small>${teacher.is_active ? "Активный аккаунт" : "Аккаунт заблокирован"}</small></div></div>
        <h2 class="wide">Основные данные</h2>
        <label>ФИО<input name="full_name" value="${escapeHtml(teacher.full_name || "")}"></label>
        <label>Телефон<input name="phone" value="${escapeHtml(teacher.phone || "")}"></label>
        <label class="wide">Логин<input name="login" value="${escapeHtml(teacher.login)}" required></label>
        <fieldset class="wide class-selector">
          <legend>Классы учителя</legend>
          <div class="class-selector-header"><span>Выберите один или несколько классов</span>
            <div><button id="edit-select-all-classes" class="text-button" type="button">Выбрать все</button>
              <button id="edit-clear-all-classes" class="text-button" type="button">Снять выбор</button></div></div>
          <div class="class-options">${classes.map((schoolClass) => `
            <label class="class-option"><input type="checkbox" name="class_ids" value="${escapeHtml(schoolClass.id)}" ${teacher.class_ids.includes(schoolClass.id) ? "checked" : ""}>
              <span>${escapeHtml(schoolClass.name)}</span></label>`).join("") || '<p class="empty class-empty">Классы ещё не добавлены</p>'}</div>
        </fieldset>
        <div class="form-actions wide"><button class="button primary">Сохранить изменения</button>
          <button class="button ghost" data-back-teachers type="button">Отмена</button></div>
      </form>
      <form id="teacher-password-form" class="panel form-grid teacher-password-card">
        <div class="wide"><p class="eyebrow">Безопасность</p><h2>Сменить пароль</h2>
          <p class="form-description">После смены пароля все активные сессии учителя завершатся.</p></div>
        <label class="wide">Новый пароль<input name="new_password" type="password" minlength="12" autocomplete="new-password" placeholder="Минимум 12 символов" required></label>
        <label class="wide">Повторите пароль<input name="confirm_password" type="password" minlength="12" autocomplete="new-password" required></label>
        <div class="wide"><button class="button">Обновить пароль</button></div>
      </form>
    </div>`);

  const editForm = document.querySelector("#teacher-edit-form");
  const checkboxes = [...editForm.querySelectorAll('input[name="class_ids"]')];
  const back = () => renderTeachers();
  document.querySelector("#back-to-teachers").addEventListener("click", back);
  document.querySelector("[data-back-teachers]").addEventListener("click", back);
  document.querySelector("#edit-select-all-classes").addEventListener("click", () => {
    checkboxes.forEach((item) => { item.checked = true; });
  });
  document.querySelector("#edit-clear-all-classes").addEventListener("click", () => {
    checkboxes.forEach((item) => { item.checked = false; });
  });
  editForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const classIds = checkboxes.filter((item) => item.checked).map((item) => item.value);
    if (!classIds.length) { notify("Выберите хотя бы один класс", "error"); return; }
    try {
      await api.updateTeacher(teacher.id, {
        login: formData.get("login"), full_name: formData.get("full_name"),
        phone: formData.get("phone"), class_ids: classIds,
      });
      notify("Данные учителя обновлены");
      await renderTeachers();
    } catch (error) { showError(error); }
  });
  document.querySelector("#teacher-password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(event.currentTarget);
    const password = String(formData.get("new_password") || "");
    if (password !== formData.get("confirm_password")) {
      notify("Пароли не совпадают", "error");
      return;
    }
    try {
      await api.changeTeacherPassword(teacher.id, password);
      event.currentTarget.reset();
      notify("Пароль учителя обновлён");
    } catch (error) { showError(error); }
  });
}

async function renderClassesAdmin() {
  const classes = await api.classCatalog(true);
  setView(`
    <header class="page-header"><div><p class="eyebrow">Справочники</p><h1>Управление классами</h1></div></header>
    <form id="class-form" class="panel form-grid"><h2>Новый класс</h2>
      <label class="wide">Название<input name="name" required placeholder="Например, 7_A"></label>
      <div class="form-actions wide"><button class="button primary">Добавить</button></div></form>
    <section class="panel table-wrap"><table><thead><tr><th>Название</th><th>Статус</th><th></th></tr></thead>
      <tbody>${classes.map((item) => `<tr><td><strong>${escapeHtml(item.name)}</strong></td>
        <td>${item.is_active ? "Активен" : "Заблокирован"}</td><td class="actions">
        <button class="button small" data-rename-class="${escapeHtml(item.id)}" data-name="${escapeHtml(item.name)}">Переименовать</button>
        <button class="button small ${item.is_active ? "danger" : ""}" data-class-status="${escapeHtml(item.id)}" data-active="${item.is_active ? "false" : "true"}">${item.is_active ? "Заблокировать" : "Включить"}</button></td></tr>`).join("")}</tbody></table></section>`);
  document.querySelector("#class-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    try { await api.createClass(new FormData(event.currentTarget).get("name")); await renderClassesAdmin(); }
    catch (error) { showError(error); }
  });
  document.querySelectorAll("[data-rename-class]").forEach((button) => button.addEventListener("click", async () => {
    const name = window.prompt("Новое название класса", button.dataset.name);
    if (!name) return;
    try { await api.renameClass(button.dataset.renameClass, name); await renderClassesAdmin(); } catch (error) { showError(error); }
  }));
  document.querySelectorAll("[data-class-status]").forEach((button) => button.addEventListener("click", async () => {
    try { await api.setClassStatus(button.dataset.classStatus, button.dataset.active === "true"); await renderClassesAdmin(); }
    catch (error) { showError(error); }
  }));
}

async function renderParentsAdmin(query = "", page = 1) {
  const parents = await api.parents();
  const result = paginated(parents, query, [
    (item) => item.display_name, (item) => item.login, (item) => item.phone,
    (item) => (item.students || []).map((student) => student.name).join(" "),
  ], page);
  setView(`
    <header class="page-header"><div><p class="eyebrow">Аккаунты</p><h1>Родители</h1></div></header>
    <form id="parent-form" class="panel form-grid"><h2>Новый родитель</h2>
      <label>ФИО<input name="display_name" required></label><label>Телефон<input name="phone"></label>
      <label>Логин<input name="login" required></label><label>Пароль<input name="password" type="password" minlength="12" required></label>
      <div class="form-actions wide"><button class="button primary">Создать аккаунт</button></div></form>
    <section class="panel table-wrap">${listControls(query, result, "ФИО, логин, телефон или ученик")}<table><thead><tr><th>ФИО</th><th>Логин</th><th>Телефон</th><th>Ученики</th><th>Статус</th><th></th></tr></thead>
      <tbody>${result.items.map((parent) => `<tr><td><strong>${escapeHtml(parent.display_name)}</strong></td><td>${escapeHtml(parent.login)}</td>
        <td>${escapeHtml(parent.phone || "—")}</td><td>${(parent.students || []).map((student) => escapeHtml(student.name)).join("<br>") || "—"}</td>
        <td>${parent.is_active ? "Активен" : "Заблокирован"}</td><td class="actions"><button class="button small" data-edit-parent="${escapeHtml(parent.id)}">Изменить</button><button class="button small ${parent.is_active ? "danger" : ""}" data-parent-status="${escapeHtml(parent.id)}" data-active="${parent.is_active ? "false" : "true"}">${parent.is_active ? "Заблокировать" : "Включить"}</button></td></tr>`).join("") || '<tr><td colspan="6" class="empty">Нет родителей</td></tr>'}</tbody></table></section>`);
  document.querySelector("#parent-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    try { await api.createParent(Object.fromEntries(form.entries())); notify("Аккаунт создан"); await renderParentsAdmin(); }
    catch (error) { showError(error); }
  });
  document.querySelectorAll("[data-parent-status]").forEach((button) => button.addEventListener("click", async () => {
    try { await api.setParentStatus(button.dataset.parentStatus, button.dataset.active === "true"); await renderParentsAdmin(); }
    catch (error) { showError(error); }
  }));
  document.querySelectorAll("[data-edit-parent]").forEach((button) => button.addEventListener("click", () => {
    renderParentEditor(parents.find((parent) => parent.id === button.dataset.editParent));
  }));
  document.querySelector("[data-list-search]").addEventListener("submit", (event) => {
    event.preventDefault(); renderParentsAdmin(new FormData(event.currentTarget).get("q"), 1);
  });
  document.querySelectorAll("[data-page]").forEach((button) => button.addEventListener("click", () => {
    renderParentsAdmin(query, Number(button.dataset.page));
  }));
}

async function renderParentEditor(parent) {
  const students = await api.students();
  const linkedIds = new Set((parent.students || []).map((student) => student.id));
  setView(`<header class="page-header"><div><p class="eyebrow">Родители · редактирование</p><h1>${escapeHtml(parent.display_name)}</h1></div>
      <button id="back-parents" class="button ghost">← К родителям</button></header>
    <div class="teacher-editor-grid"><form id="parent-edit-form" class="panel form-grid"><h2>Профиль</h2>
      <label>ФИО<input name="display_name" value="${escapeHtml(parent.display_name)}" required></label>
      <label>Телефон<input name="phone" value="${escapeHtml(parent.phone || "")}"></label>
      <label class="wide">Логин<input name="login" value="${escapeHtml(parent.login)}" required></label>
      <div class="form-actions wide"><button class="button primary">Сохранить</button></div></form>
      <form id="parent-password-form" class="panel form-grid"><h2>Сменить пароль</h2>
        <label class="wide">Новый пароль<input name="password" type="password" minlength="12" required></label>
        <label class="wide">Повторите пароль<input name="confirm" type="password" minlength="12" required></label>
        <div class="wide"><button class="button">Обновить пароль</button></div></form></div>
    <section class="panel"><h2>Связанные ученики</h2>
      <form id="parent-link-form" class="form-grid"><label>Ученик<select name="student_id">${students.filter((student) => !linkedIds.has(student.id)).map((student) => `<option value="${escapeHtml(student.id)}">${escapeHtml(student.name)} · ${escapeHtml(student.class_name)}</option>`).join("")}</select></label>
        <label>Связь<select name="relationship"><option value="mother">Мать</option><option value="father">Отец</option><option value="guardian">Опекун</option><option value="other">Другое</option></select></label>
        <div class="form-actions wide"><button class="button primary" ${students.length === linkedIds.size ? "disabled" : ""}>Связать</button></div></form>
      <div class="class-grid">${(parent.students || []).map((student) => `<article class="class-card"><strong>${escapeHtml(student.name)}</strong><span>${escapeHtml(student.class_name || "")} · ${escapeHtml(student.relationship || "guardian")}</span><button class="button small danger" data-unlink-student="${escapeHtml(student.id)}">Убрать связь</button></article>`).join("") || '<p class="empty">Нет связанных учеников</p>'}</div></section>`);
  document.querySelector("#back-parents").addEventListener("click", () => renderParentsAdmin());
  document.querySelector("#parent-edit-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const data = Object.fromEntries(new FormData(event.currentTarget).entries());
    try { await api.updateParent(parent.id, data); notify("Профиль обновлён"); await renderParentsAdmin(); } catch (error) { showError(error); }
  });
  document.querySelector("#parent-password-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    if (data.get("password") !== data.get("confirm")) { notify("Пароли не совпадают", "error"); return; }
    try { await api.changeParentPassword(parent.id, data.get("password")); event.currentTarget.reset(); notify("Пароль обновлён"); } catch (error) { showError(error); }
  });
  document.querySelector("#parent-link-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const data = new FormData(event.currentTarget); if (!data.get("student_id")) return;
    try { await api.linkParentStudent(parent.id, data.get("student_id"), data.get("relationship")); notify("Ученик связан"); await renderParentEditor((await api.parents()).find((item) => item.id === parent.id)); } catch (error) { showError(error); }
  });
  document.querySelectorAll("[data-unlink-student]").forEach((button) => button.addEventListener("click", async () => {
    try { await api.unlinkParentStudent(parent.id, button.dataset.unlinkStudent); notify("Связь удалена"); await renderParentEditor((await api.parents()).find((item) => item.id === parent.id)); } catch (error) { showError(error); }
  }));
}

async function renderAudit(actor = "", entity = "", query = "", page = 1) {
  const rows = await api.audit(actor, entity);
  const result = paginated(rows, query, [
    (item) => item.action, (item) => item.entity_type, (item) => item.entity_id,
    (item) => item.actor_login, (item) => JSON.stringify(item.details || {}),
  ], page);
  setView(`<header class="page-header"><div><p class="eyebrow">Безопасность</p><h1>Журнал аудита</h1></div></header>
    <form id="audit-filter" class="panel form-grid"><label>Администратор<input name="actor" value="${escapeHtml(actor)}"></label>
      <label>Тип объекта<input name="entity" value="${escapeHtml(entity)}" placeholder="student, teacher, parent..."></label>
      <label class="wide">Поиск<input name="query" value="${escapeHtml(query)}" placeholder="Действие, ID или путь"></label>
      <div class="form-actions wide"><button class="button primary">Применить фильтры</button></div></form>
    <section class="panel table-wrap"><table><thead><tr><th>Время</th><th>Администратор</th><th>Действие</th><th>Объект</th><th>ID</th></tr></thead>
      <tbody>${result.items.map((row) => `<tr><td>${escapeHtml(row.created_at)}</td><td>${escapeHtml(row.actor_login)}</td><td>${escapeHtml(row.action)}</td><td>${escapeHtml(row.entity_type)}</td><td>${escapeHtml(row.entity_id || "—")}</td></tr>`).join("") || '<tr><td colspan="5" class="empty">Журнал пуст</td></tr>'}</tbody></table>
      <div class="pagination footer-pagination"><button class="button small" data-audit-page="${result.page - 1}" ${result.page <= 1 ? "disabled" : ""}>←</button><strong>${result.page} / ${result.pages}</strong><button class="button small" data-audit-page="${result.page + 1}" ${result.page >= result.pages ? "disabled" : ""}>→</button></div></section>`);
  document.querySelector("#audit-filter").addEventListener("submit", (event) => {
    event.preventDefault(); const data = new FormData(event.currentTarget);
    renderAudit(data.get("actor"), data.get("entity"), data.get("query"), 1);
  });
  document.querySelectorAll("[data-audit-page]").forEach((button) => button.addEventListener("click", () => {
    renderAudit(actor, entity, query, Number(button.dataset.auditPage));
  }));
}

async function renderReports() {
  return renderReportsPage({ setView, notify, showError, escapeHtml, isActive: () => state.activeView === "reports" });
}
function cameraConfigForm(config) {
  return `<form class="panel form-grid" data-camera-config="${config.direction}">
    <h2 id="camera-config-title-${config.direction}">${config.direction === "new" ? "Добавить камеру аудитории" : `Настройка камеры: ${escapeHtml(config.name || { keldi: "вход", ketti: "выход" }[config.direction] || "аудитория")}`}</h2>
    ${!["keldi", "ketti"].includes(config.direction) ? `<label class="wide">Название камеры<input name="name" value="${escapeHtml(config.name || "")}" maxlength="100" placeholder="Например: Кабинет 201" required></label>` : ""}
    <label>IP-адрес<input name="host" value="${escapeHtml(config.host)}" placeholder="192.168.0.100" required></label>
    <label>RTSP-порт<input name="port" type="number" min="1" max="65535" value="${config.port}" required></label>
    <label>Логин<input name="username" value="${escapeHtml(config.username)}" autocomplete="off"></label>
    <label>Пароль<input name="password" type="password" autocomplete="new-password" placeholder="${config.has_password ? "Сохранён. Оставьте пустым, чтобы не менять" : "Пароль камеры"}"></label>
    <label class="wide">Путь видеопотока<input name="path" value="${escapeHtml(config.path)}" placeholder="/V_ENC_000" required><small>Для вашей Cootli: /V_ENC_000. Для другой модели путь может отличаться.</small></label>
    <div class="form-actions wide"><button type="button" class="button ghost" data-close-config>Закрыть</button><button type="button" class="button ghost" data-test-config>Проверить видео</button><button type="submit" class="button primary">Сохранить и подключить</button></div>
    <p class="wide" data-config-result role="status" aria-live="polite"></p>
  </form>`;
}

async function renderCamera(classroom = false) {
  const view = classroom ? "classroom" : "camera";
  const statuses = await api.cameraStatuses();
  const configs = (await api.cameraConfigs()).filter((config) => classroom ? config.direction.startsWith("classroom") : ["keldi", "ketti"].includes(config.direction));
  const dialogConfigs = classroom ? [...configs, { direction: "new", host: "", port: 554, username: "admin", path: "/V_ENC_000", has_password: false }] : configs;
  if (state.activeView !== view || !state.session) return;
  setView(`
    <header class="page-header camera-page-header">
      <div>
        <p class="eyebrow">Face ID · наблюдение</p>
        <h1>${classroom ? "Аудитория" : "Камеры школы"}</h1>
        <p class="page-description">${classroom ? "Предпросмотр обновляется раз в секунду. Нажмите «Открыть видео» для непрерывного потока." : "Оба пропускных пункта отображаются одновременно"}</p>
      </div>
      ${classroom ? '<button type="button" class="button primary" data-edit-camera="new" aria-haspopup="dialog" aria-controls="camera-config-new">Добавить камеру</button>' : '<div class="live-indicator"><span></span> Прямой эфир</div>'}
    </header>
    <section class="camera-grid${classroom ? " camera-grid-vertical" : ""}" aria-label="${classroom ? "Камера аудитории" : "Камеры входа и выхода"}">
      ${classroom ? configs.map((config) => cameraCard(config.direction, config.name || "Аудитория", "Видеонаблюдение в аудитории", "enter")).join("") || '<p class="panel empty">Камер пока нет. Нажмите «Добавить камеру» и укажите параметры подключения.</p>' : `
        ${cameraCard("keldi", "Вход", "Фиксирует приход учеников", "enter")}
        ${cameraCard("ketti", "Выход", "Фиксирует уход учеников", "exit")}
      `}
    </section>
    ${dialogConfigs.map((config) => `<dialog class="camera-config-dialog" id="camera-config-${config.direction}" aria-labelledby="camera-config-title-${config.direction}">${cameraConfigForm(config)}</dialog>`).join("")}
    ${classroom ? '<dialog class="camera-config-dialog" id="classroom-live-dialog" aria-label="Видео камеры аудитории"><div class="panel"><button type="button" class="button ghost" data-close-live>Закрыть видео</button><img data-live-image style="display:block;width:100%;margin-top:16px" alt="Видео камеры аудитории"></div></dialog>' : ""}`);

  const liveDialog = document.querySelector("#classroom-live-dialog");
  if (liveDialog) {
    liveDialog.querySelector("[data-close-live]").addEventListener("click", () => liveDialog.close());
    liveDialog.addEventListener("close", () => liveDialog.querySelector("img").removeAttribute("src"));
    document.querySelectorAll("[data-live-camera]").forEach((button) => button.addEventListener("click", () => {
      liveDialog.querySelector("img").src = `${API_BASE_URL}/api/v1/camera/feed?status=${encodeURIComponent(button.dataset.liveCamera)}`;
      liveDialog.showModal();
    }));
  }

  document.querySelectorAll("[data-delete-camera]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Удалить эту камеру из аудитории?")) return;
      button.disabled = true;
      try {
        await api.deleteClassroomCamera(button.dataset.deleteCamera);
        if (state.activeView === view) { stopCameraView(); await renderCamera(true); }
      } catch (error) { showError(error); button.disabled = false; }
    });
  });

  document.querySelectorAll("[data-edit-camera]").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelector(`#camera-config-${button.dataset.editCamera}`).showModal();
    });
  });

  document.querySelectorAll("[data-camera-config]").forEach((form) => {
    const dialog = form.closest("dialog");
    form.querySelector("[data-close-config]").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => { form.elements.password.value = ""; });
    const perform = async (save) => {
      const direction = form.dataset.cameraConfig;
      if (!form.reportValidity()) return;
      const values = Object.fromEntries(new FormData(form));
      values.port = Number(values.port);
      const message = form.querySelector("[data-config-result]");
      const buttons = form.querySelectorAll("button");
      buttons.forEach((button) => { button.disabled = true; });
      message.textContent = save ? "Сохраняем и подключаем…" : "Проверяем получение видео…";
      try {
        if (save) {
          const result = direction === "new" ? await api.createClassroomCamera(values) : await api.saveCameraConfig(direction, values);
          form.elements.password.value = "";
          form.elements.password.placeholder = result.has_password ? "Сохранён. Оставьте пустым, чтобы не менять" : "Пароль камеры";
          if (classroom) {
            dialog.close();
            if (state.activeView === view) { stopCameraView(); await renderCamera(true); notify("Камера сохранена"); }
            return;
          }
          cameraFeeds?.retry(direction);
          document.querySelector(`[data-edit-camera="${direction}"]`).textContent = "Изменить камеру";
          message.textContent = "Настройки сохранены. Камера подключается автоматически.";
        } else {
          const result = await api.testCameraConfig(direction, values);
          message.textContent = result.connected ? "Видео получено. Можно сохранить настройки." : "Видео не получено. Проверьте IP, порт, пароль, путь потока и доступ к сети камеры.";
        }
      } catch (error) {
        message.textContent = typeof error.message === "string" ? error.message : "Не удалось выполнить действие";
      } finally { buttons.forEach((button) => { button.disabled = false; }); }
    };
    form.addEventListener("submit", (event) => { event.preventDefault(); perform(true); });
    form.querySelector("[data-test-config]").addEventListener("click", () => perform(false));
  });

  const applyStatuses = (items) => items.forEach((status) => {
      const card = document.querySelector(`[data-camera-feed="${status.camera}"]`)?.closest(".camera-card");
      if (!card) return;
      card.classList.toggle("camera-error", status.state !== "working");
      card.querySelector("[data-camera-state]").textContent = status.reconnect_in_seconds
        ? `${status.label} · повтор через ${status.reconnect_in_seconds} с` : status.label;
      card.querySelector("[data-camera-error-detail]").textContent = status.error || "Проверьте подключение камеры";
    });
  applyStatuses(statuses);
  cameraFeeds = connectCameraFeeds(document.querySelectorAll("[data-camera-feed]"), API_BASE_URL, window, classroom ? "snapshot" : "feed");
  const currentFeeds = cameraFeeds;
  if (classroom) state.cameraPreviewTimer = window.setInterval(() => {
    if (cameraFeeds !== currentFeeds || document.hidden) return;
    configs.forEach((config) => currentFeeds.retry(config.direction));
  }, 1000);
  let polling = false;
  state.cameraTimer = window.setInterval(async () => {
    if (state.activeView !== view || polling || cameraFeeds !== currentFeeds) return;
    polling = true;
    const controller = new AbortController();
    cameraPollAbort = controller;
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    try {
      const items = await api.cameraStatuses({ signal: controller.signal });
      if (cameraFeeds === currentFeeds) applyStatuses(items);
    } catch (error) {
      if (cameraFeeds === currentFeeds && error instanceof ApiError && [401, 403].includes(error.status)) {
        stopCameraView();
        showError(error);
      }
    } finally {
      window.clearTimeout(timeout);
      if (cameraPollAbort === controller) cameraPollAbort = null;
      polling = false;
    }
  }, 5000);

  document.querySelectorAll("[data-camera-feed]").forEach((image) => {
    const card = image.closest(".camera-card");
    image.addEventListener("error", () => {
      card.classList.add("camera-error");
      card.querySelector("[data-camera-state]").textContent = "Нет соединения";
    });
  });
  document.querySelectorAll("[data-retry-camera]").forEach((button) => {
    button.addEventListener("click", () => {
      const status = button.dataset.retryCamera;
      const image = document.querySelector(`[data-camera-feed="${status}"]`);
      const card = image.closest(".camera-card");
      card.classList.remove("camera-error");
      card.querySelector("[data-camera-state]").textContent = "Подключение…";
      cameraFeeds?.retry(status);
    });
  });
}

function cameraCard(status, title, description, variant, configured = true) {
  return `<article class="camera-card camera-card-${variant}">
    <header class="camera-card-header">
      <div class="camera-title-group">
        <span class="camera-direction" aria-hidden="true">${variant === "enter" ? "↘" : "↗"}</span>
        <div><h2>${escapeHtml(title)}</h2><p>${description}</p></div>
      </div>
      <span class="camera-state"><i></i><span data-camera-state>Подключение…</span></span>
    </header>
    <div class="camera-viewport">
      <img data-camera-feed="${status}" src="${API_BASE_URL}/api/v1/camera/${status.startsWith("classroom") ? "snapshot" : "feed"}?status=${status}" alt="Камера: ${escapeHtml(title.toLowerCase())}">
      <div class="camera-error-message">
        <strong>Поток недоступен</strong>
        <span data-camera-error-detail>Проверьте подключение камеры</span>
        <button class="button camera-retry" data-retry-camera="${status}" type="button">Подключить снова</button>
      </div>
    </div>
    <footer class="camera-card-actions">${status.startsWith("classroom") ? `<button type="button" class="button ghost" data-live-camera="${status}" aria-haspopup="dialog">Открыть видео</button><button type="button" class="button danger" data-delete-camera="${status}">Удалить</button>` : ""}<button type="button" class="button ghost" data-edit-camera="${status}" aria-haspopup="dialog" aria-controls="camera-config-${status}">${configured ? "Изменить камеру" : "Добавить камеру"}</button></footer>
  </article>`;
}

async function renderParent() {
  const rows = await api.parentReport();
  setView(`
    <header class="page-header"><div><p class="eyebrow">Кабинет родителя</p><h1>Отчёт ребёнка</h1></div></header>
    <section class="panel table-wrap"><table><thead><tr><th>Ученик</th><th>Класс</th><th>Дата</th><th>Пришел</th><th>Ушел</th></tr></thead>
      <tbody>${rows.map((row) => `<tr><td>${escapeHtml(row.student_name)}</td><td>${escapeHtml(row.class_name)}</td>
        <td>${escapeHtml(row.date)}</td><td>${escapeHtml(row.arrived_at || "—")}</td><td>${escapeHtml(row.left_at || "—")}</td></tr>`).join("") ||
        '<tr><td colspan="5" class="empty">Отчёт пока пуст</td></tr>'}</tbody></table></section>`);
}

async function renderNotifications() {
  const notifications = await api.notifications();
  const canMarkRead = state.session.role !== "admin";
  setView(`
    <header class="page-header"><div><p class="eyebrow">События</p><h1>Уведомления</h1></div>
      ${canMarkRead ? '<button id="read-all-notifications" class="button ghost">Прочитать все</button>' : ""}</header>
    <section class="panel table-wrap"><table>
      <thead><tr><th>Время</th><th>Ученик</th><th>Сообщение</th><th>Статус</th><th></th></tr></thead>
      <tbody>${notifications.map((notification) => `<tr class="${notification.is_read ? "" : "notification-unread"}">
        <td>${escapeHtml(notification.event.occurred_at)}</td>
        <td>${escapeHtml(notification.student.student_number)} · ${escapeHtml(notification.student.name)}</td>
        <td>${escapeHtml(notification.message)}</td>
        <td>${notification.is_read ? "Прочитано" : "Новое"}</td>
        <td>${canMarkRead && !notification.is_read ? `<button class="button small" data-read-notification="${notification.id}">Прочитать</button>` : ""}</td>
      </tr>`).join("") || '<tr><td colspan="5" class="empty">Уведомлений пока нет</td></tr>'}</tbody>
    </table></section>`);
  document.querySelectorAll("[data-read-notification]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await api.markNotificationRead(button.dataset.readNotification);
        await refreshUnreadCount();
        await renderNotifications();
      } catch (error) { showError(error); }
    });
  });
  document.querySelector("#read-all-notifications")?.addEventListener("click", async () => {
    try {
      await api.markAllNotificationsRead();
      await refreshUnreadCount();
      await renderNotifications();
    } catch (error) { showError(error); }
  });
}

async function renderActiveView() {
  try {
    stopCameraView();
    const renderers = {
      dashboard: renderDashboard,
      attendance: renderAttendance,
      students: renderStudents,
      teachers: renderTeachers,
      "classes-admin": renderClassesAdmin,
      "parents-admin": renderParentsAdmin,
      reports: renderReports,
      audit: renderAudit,
      camera: renderCamera,
      classroom: () => renderCamera(true),
      parent: renderParent,
      notifications: renderNotifications,
    };
    await (renderers[state.activeView] || renderDashboard)();
  } catch (error) { showError(error); }
}

async function bootstrap() {
  try {
    const session = await api.me();
    if (!session.authenticated) { renderLogin(); return; }
    state.session = session;
    state.activeView = session.role === "parent" ? "parent" : "dashboard";
    await refreshUnreadCount();
    renderShell();
    startNotificationsSocket();
    await renderActiveView();
  } catch (error) {
    renderLogin(`Backend недоступен: ${error.message}`);
  }
}

bootstrap();
