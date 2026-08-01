import { TokenStore, subscribeToTokenChange } from "./auth";

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
  let unsubscribeTokenChange: (() => void) | null = null;
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

    // 统一经 TokenStore 读取令牌（任务卡 §6：WebSocket 获取令牌必须经过统一认证模块）。
    const token = TokenStore.getAccessToken();
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

  /**
   * 令牌变化处理（任务卡 §6、§11 验收标准 9）：
   * - 令牌轮换（新 access token）-> 关闭旧连接触发退避重连，重连时读取新令牌；
   * - 令牌被清除（logout）-> 完全断开且不再自动重连。
   */
  function handleTokenChange() {
    if (intentionalClose) return; // 已主动断开（logout）
    if (TokenStore.getAccessToken()) {
      if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
        // 关闭会触发 onclose -> scheduleReconnect -> connect() 读取新令牌
        ws.close();
      }
    } else {
      disconnect();
    }
  }

  function disconnect() {
    intentionalClose = true;
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    if (unsubscribeTokenChange) {
      unsubscribeTokenChange();
      unsubscribeTokenChange = null;
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

  // 客户端被创建即订阅令牌变化（首个 connect 前若令牌已存在也不会误连）
  if (!unsubscribeTokenChange) {
    unsubscribeTokenChange = subscribeToTokenChange(handleTokenChange);
  }

  return { connect, disconnect, subscribe, send };
}
