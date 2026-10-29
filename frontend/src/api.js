// In local development requests stay on the Vite origin and are proxied to
// FastAPI.  Keeping the browser on one origin is important because auth uses
// an HttpOnly session cookie (localhost and 127.0.0.1 are different sites).
const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL || "";
export const API_BASE_URL = configuredBaseUrl.replace(/\/$/, "");
let csrfToken = "";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

export async function apiRequest(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const unsafe = ["POST", "PUT", "PATCH", "DELETE"].includes(method);
  const headers = {
    Accept: "application/json",
    ...(unsafe && csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
    ...(options.headers || {}),
  };
  const bodyIsForm = options.body instanceof FormData;
  if (options.body && !bodyIsForm && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }

  const response = await fetch(`${API_BASE_URL}/api/v1${path}`, {
    credentials: "include",
    ...options,
    headers,
  });

  if (response.status === 204) return null;
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json") ? await response.json() : null;
  if (payload?.csrf_token) csrfToken = payload.csrf_token;
  if (!response.ok) {
    throw new ApiError(payload?.detail || `Ошибка API: ${response.status}`, response.status);
  }
  return payload;
}

export async function openProtectedPhoto(path) {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    credentials: "include",
    headers: { Accept: "image/jpeg" },
  });
  if (!response.ok) {
    const payload = (response.headers.get("content-type") || "").includes("application/json")
      ? await response.json() : null;
    throw new ApiError(payload?.detail || "Фото удалено или недоступно", response.status);
  }
  const objectUrl = URL.createObjectURL(await response.blob());
  window.open(objectUrl, "_blank", "noopener");
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

export const api = {
  createClassroomCamera: (config) => apiRequest("/camera/classrooms", { method: "POST", body: JSON.stringify(config) }),
  deleteClassroomCamera: (direction) => apiRequest(`/camera/classrooms/${encodeURIComponent(direction)}`, { method: "DELETE" }),
  cameraConfigs: () => apiRequest("/camera/config"),
  saveCameraConfig: (direction, config) => apiRequest(`/camera/config/${direction}`, { method: "PUT", body: JSON.stringify(config) }),
  testCameraConfig: (direction, config) => apiRequest(`/camera/config/${direction}/test`, { method: "POST", body: JSON.stringify(config) }),
  me: () => apiRequest("/auth/me"),
  login: (login, password) => apiRequest("/auth/login", {
    method: "POST",
    body: JSON.stringify({ login, password }),
  }),
  logout: async () => { const result = await apiRequest("/auth/logout", { method: "POST" }); csrfToken = ""; return result; },
  classes: () => apiRequest("/attendance/classes"),
  classCatalog: (includeInactive = false) => apiRequest(`/classes?include_inactive=${includeInactive}`),
  createClass: (name) => apiRequest("/classes", { method: "POST", body: JSON.stringify({ name }) }),
  renameClass: (classId, name) => apiRequest(`/classes/${encodeURIComponent(classId)}`, { method: "PUT", body: JSON.stringify({ name }) }),
  setClassStatus: (classId, isActive) => apiRequest(`/classes/${encodeURIComponent(classId)}/status`, { method: "PUT", body: JSON.stringify({ is_active: isActive }) }),
  classReport: (name) => apiRequest(`/attendance/classes/${encodeURIComponent(name)}`),
  recentAttendance: () => apiRequest("/attendance/recent"),
  attendanceEvents: () => apiRequest("/attendance/events?limit=100"),
  clearAttendance: (className = "") => apiRequest(`/attendance?class_name=${encodeURIComponent(className)}`, { method: "DELETE" }),
  parentReport: () => apiRequest("/attendance/parent"),
  notifications: (unreadOnly = false) => apiRequest(`/notifications?limit=100&unread_only=${unreadOnly}`),
  unreadNotificationCount: () => apiRequest("/notifications/unread-count"),
  markNotificationRead: (notificationId) => apiRequest(`/notifications/${encodeURIComponent(notificationId)}/read`, { method: "POST" }),
  markAllNotificationsRead: () => apiRequest("/notifications/read-all", { method: "POST" }),
  students: () => apiRequest("/students"),
  studentEvents: (studentId) => apiRequest(`/attendance/students/${encodeURIComponent(studentId)}/events`),
  addStudentFaces: (studentId, files) => {
    const form = new FormData();
    [...files].forEach((file) => form.append("photos", file));
    return apiRequest(`/students/${encodeURIComponent(studentId)}/faces`, { method: "POST", body: form });
  },
  createStudent: (form) => apiRequest("/students", { method: "POST", body: form }),
  updateStudent: (studentId, form) => apiRequest(`/students/${encodeURIComponent(studentId)}`, { method: "PUT", body: form }),
  deleteStudent: (studentId) => apiRequest(`/students/${encodeURIComponent(studentId)}`, { method: "DELETE" }),
  teachers: () => apiRequest("/teachers"),
  createTeacher: (payload) => apiRequest("/teachers", { method: "POST", body: JSON.stringify(payload) }),
  updateTeacher: (teacherId, payload) => apiRequest(`/teachers/${encodeURIComponent(teacherId)}`, { method: "PUT", body: JSON.stringify(payload) }),
  changeTeacherPassword: (teacherId, newPassword) => apiRequest(`/teachers/${encodeURIComponent(teacherId)}/password`, { method: "PUT", body: JSON.stringify({ new_password: newPassword }) }),
  setTeacherStatus: (teacherId, isActive) => apiRequest(`/teachers/${encodeURIComponent(teacherId)}/status`, { method: "PUT", body: JSON.stringify({ is_active: isActive }) }),
  deleteTeacher: (teacherId) => apiRequest(`/teachers/${encodeURIComponent(teacherId)}`, { method: "DELETE" }),
  parents: () => apiRequest("/parents"),
  createParent: (payload) => apiRequest("/parents", { method: "POST", body: JSON.stringify(payload) }),
  updateParent: (parentId, payload) => apiRequest(`/parents/${encodeURIComponent(parentId)}`, { method: "PUT", body: JSON.stringify(payload) }),
  changeParentPassword: (parentId, newPassword) => apiRequest(`/parents/${encodeURIComponent(parentId)}/password`, { method: "PUT", body: JSON.stringify({ new_password: newPassword }) }),
  setParentStatus: (parentId, isActive) => apiRequest(`/parents/${encodeURIComponent(parentId)}/status`, { method: "PUT", body: JSON.stringify({ is_active: isActive }) }),
  linkParentStudent: (parentId, studentId, relationship) => apiRequest(`/parents/${encodeURIComponent(parentId)}/students/${encodeURIComponent(studentId)}`, { method: "POST", body: JSON.stringify({ relationship }) }),
  unlinkParentStudent: (parentId, studentId) => apiRequest(`/parents/${encodeURIComponent(parentId)}/students/${encodeURIComponent(studentId)}`, { method: "DELETE" }),
  audit: (actor = "", entity = "") => apiRequest(`/audit?limit=300&actor_login=${encodeURIComponent(actor)}&entity_type=${encodeURIComponent(entity)}`),
  cameraStatuses: (options = {}) => apiRequest("/camera/status", options),
  shifts: () => apiRequest("/school-attendance/shifts"),
  createShift: (payload) => apiRequest("/school-attendance/shifts", { method: "POST", body: JSON.stringify(payload) }),
  schedules: () => apiRequest("/school-attendance/schedules"),
  setSchedule: (payload) => apiRequest("/school-attendance/schedules", { method: "PUT", body: JSON.stringify(payload) }),
  setCalendarDay: (payload) => apiRequest("/school-attendance/calendar", { method: "PUT", body: JSON.stringify(payload) }),
  calendar: (dateFrom, dateTo) => apiRequest(`/school-attendance/calendar?date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`),
  attendanceReport: (dateFrom, dateTo, classId = "", studentId = "") => apiRequest(`/school-attendance/reports?date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}&class_id=${encodeURIComponent(classId)}&student_id=${encodeURIComponent(studentId)}`),
  correctAttendance: (payload) => apiRequest("/school-attendance/corrections", { method: "PUT", body: JSON.stringify(payload) }),
};
