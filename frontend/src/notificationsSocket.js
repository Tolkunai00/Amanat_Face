const reconnectDelays = [1000, 2000, 5000, 10000, 30000];

let socket = null;
let reconnectTimer = null;
let reconnectAttempt = 0;
let stopped = true;
let handlers = {};

function websocketUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/v1/ws/notifications`;
}

function scheduleReconnect() {
  if (stopped || reconnectTimer) return;
  const delay = reconnectDelays[Math.min(reconnectAttempt, reconnectDelays.length - 1)];
  reconnectAttempt += 1;
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    openSocket();
  }, delay);
}

function openSocket() {
  if (stopped || socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return;
  socket = new WebSocket(websocketUrl());
  socket.addEventListener("open", () => {
    reconnectAttempt = 0;
  });
  socket.addEventListener("message", async (event) => {
    let payload;
    try { payload = JSON.parse(event.data); } catch { return; }
    if (payload.type === "connected") {
      await handlers.onCatchUp?.();
      handlers.onConnected?.();
    } else if (payload.type === "notification.created") {
      handlers.onNotification?.(payload.data);
    }
  });
  socket.addEventListener("close", () => {
    socket = null;
    handlers.onDisconnected?.();
    scheduleReconnect();
  });
  socket.addEventListener("error", () => socket?.close());
}

export function connectNotificationsSocket(nextHandlers = {}) {
  handlers = nextHandlers;
  stopped = false;
  openSocket();
}

export function disconnectNotificationsSocket() {
  stopped = true;
  if (reconnectTimer) window.clearTimeout(reconnectTimer);
  reconnectTimer = null;
  reconnectAttempt = 0;
  if (socket) socket.close(1000, "logout");
  socket = null;
}

window.addEventListener("beforeunload", disconnectNotificationsSocket);
