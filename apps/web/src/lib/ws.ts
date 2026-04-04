export type WsMessageHandler = (data: unknown) => void;

export interface WsClient {
  connect: () => void;
  disconnect: () => void;
  subscribe: (event: string, handler: WsMessageHandler) => () => void;
  send: (data: unknown) => void;
}

export function createWsClient(url: string): WsClient {
  let ws: WebSocket | null = null;
  let reconnectAttempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let intentionalClose = false;
  const listeners = new Map<string, Set<WsMessageHandler>>();

  const MAX_BACKOFF = 30000;

  function getBackoff(): number {
    const delay = Math.min(1000 * Math.pow(2, reconnectAttempt), MAX_BACKOFF);
    return delay;
  }

  function scheduleReconnect() {
    if (intentionalClose) return;
    const delay = getBackoff();
    reconnectAttempt++;
    reconnectTimer = setTimeout(() => {
      connect();
    }, delay);
  }

  function connect() {
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    intentionalClose = false;

    const token =
      typeof window !== "undefined"
        ? localStorage.getItem("access_token")
        : null;

    const wsUrl = token ? `${url}?token=${encodeURIComponent(token)}` : url;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      reconnectAttempt = 0;
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        const eventType = msg.event || msg.type || "message";
        const handlers = listeners.get(eventType);
        if (handlers) {
          handlers.forEach((h) => h(msg));
        }
        // Also notify wildcard listeners
        const allHandlers = listeners.get("*");
        if (allHandlers) {
          allHandlers.forEach((h) => h(msg));
        }
      } catch {
        // non-JSON message, ignore
      }
    };

    ws.onclose = () => {
      ws = null;
      scheduleReconnect();
    };

    ws.onerror = () => {
      ws?.close();
    };
  }

  function disconnect() {
    intentionalClose = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (ws) {
      ws.close();
      ws = null;
    }
  }

  function subscribe(event: string, handler: WsMessageHandler): () => void {
    if (!listeners.has(event)) {
      listeners.set(event, new Set());
    }
    listeners.get(event)!.add(handler);
    return () => {
      const set = listeners.get(event);
      if (set) {
        set.delete(handler);
        if (set.size === 0) listeners.delete(event);
      }
    };
  }

  function send(data: unknown) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(data));
    }
  }

  return { connect, disconnect, subscribe, send };
}
