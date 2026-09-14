import { ApiErrorException, api, isAbortError } from "./api";
import { TokenStore, handleAuthFailure, subscribeToTokenChange } from "./auth";

export type WsMessageHandler = (data: unknown) => void;

export interface WsClient {
  connect: () => void;
  disconnect: () => void;
  subscribe: (event: string, handler: WsMessageHandler) => () => void;
  send: (data: unknown) => void;
}

export interface WsClientOptions {
  /** 提供时，建连前以受保护 HTTP access 接口确认当前用户仍可访问项目。 */
  projectId?: string;
}

/** 已建立连接后的服务端关闭码；初次握手失败不依赖这些私有码。 */
export const WS_UNAUTHORIZED_CLOSE = 4401;
export const WS_FORBIDDEN_CLOSE = 4403;

export function createWsClient(url: string, options: WsClientOptions = {}): WsClient {
  let ws: WebSocket | null = null;
  let reconnectAttempt = 0;
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let preflightController: AbortController | null = null;
  let desiredConnection = false;
  let permissionDenied = false;
  let connectionVersion = 0;
  let unsubscribeTokenChange: (() => void) | null = null;
  const listeners = new Map<string, Set<WsMessageHandler>>();

  const MAX_BACKOFF = 30_000;

  const emit = (message: unknown) => {
    const event = (message as { event?: string }).event;
    if (event) listeners.get(event)?.forEach((handler) => handler(message));
    listeners.get("*")?.forEach((handler) => handler(message));
  };

  const stopReconnectTimer = () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
  };

  const cancelPreflight = () => {
    preflightController?.abort();
    preflightController = null;
  };

  const closeSocket = () => {
    if (!ws) return;
    const socket = ws;
    ws = null;
    socket.onclose = null;
    socket.onerror = null;
    socket.close();
  };

  const getBackoff = () => Math.min(1_000 * 2 ** reconnectAttempt, MAX_BACKOFF);

  const scheduleReconnect = () => {
    if (!desiredConnection || permissionDenied || reconnectTimer) return;
    const delay = getBackoff();
    reconnectAttempt += 1;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, delay);
  };

  const rejectPermission = () => {
    permissionDenied = true;
    stopReconnectTimer();
    emit({ event: "ws_forbidden", code: WS_FORBIDDEN_CLOSE });
  };

  const openSocket = (token: string, sessionId: string, version: number) => {
    if (
      !desiredConnection ||
      permissionDenied ||
      version !== connectionVersion ||
      TokenStore.getSessionId() !== sessionId
    ) {
      return;
    }
    const wsUrl = `${url}?token=${encodeURIComponent(token)}`;
    const socket = new WebSocket(wsUrl);
    ws = socket;

    socket.onopen = () => {
      if (ws !== socket || version !== connectionVersion) return;
      reconnectAttempt = 0;
    };

    socket.onmessage = (event) => {
      if (ws !== socket || version !== connectionVersion) return;
      try {
        const message = JSON.parse(event.data);
        emit(message);
      } catch {
        // 非 JSON 消息不属于任务事件协议。
      }
    };

    socket.onclose = (event: CloseEvent) => {
      if (ws !== socket || version !== connectionVersion) return;
      ws = null;
      if (!desiredConnection) return;
      if (event.code === WS_FORBIDDEN_CLOSE) {
        rejectPermission();
        return;
      }
      if (event.code === WS_UNAUTHORIZED_CLOSE) {
        handleAuthFailure(sessionId);
        return;
      }
      scheduleReconnect();
    };

    socket.onerror = () => {
      if (ws === socket) socket.close();
    };
  };

  const connect = () => {
    desiredConnection = true;
    if (!unsubscribeTokenChange) {
      unsubscribeTokenChange = subscribeToTokenChange(handleTokenChange);
    }
    if (ws || preflightController || reconnectTimer || permissionDenied) return;

    const session = TokenStore.getSession();
    if (!session) return;
    const version = ++connectionVersion;

    if (!options.projectId) {
      openSocket(session.access_token, session.session_id, version);
      return;
    }

    const controller = new AbortController();
    preflightController = controller;
    void api.get("/projects/{pid}/access", {
      params: { pid: options.projectId },
      signal: controller.signal,
    }).then(() => {
      if (preflightController === controller) preflightController = null;
      if (controller.signal.aborted || version !== connectionVersion) return;
      const current = TokenStore.getSession();
      if (!current || current.session_id !== session.session_id) return;
      openSocket(current.access_token, current.session_id, version);
    }).catch((error: unknown) => {
      if (preflightController === controller) preflightController = null;
      if (controller.signal.aborted || version !== connectionVersion || isAbortError(error)) return;
      if (error instanceof ApiErrorException && (error.apiError.status === 401 || error.apiError.status === 403)) {
        rejectPermission();
        return;
      }
      scheduleReconnect();
    });
  };

  function handleTokenChange() {
    connectionVersion += 1;
    cancelPreflight();
    stopReconnectTimer();
    closeSocket();

    const session = TokenStore.getSession();
    if (!session) {
      desiredConnection = false;
      return;
    }
    permissionDenied = false;
    reconnectAttempt = 0;
    if (desiredConnection) connect();
  }

  const disconnect = () => {
    desiredConnection = false;
    connectionVersion += 1;
    cancelPreflight();
    stopReconnectTimer();
    closeSocket();
    unsubscribeTokenChange?.();
    unsubscribeTokenChange = null;
  };

  const subscribe = (event: string, handler: WsMessageHandler): (() => void) => {
    if (!listeners.has(event)) listeners.set(event, new Set());
    listeners.get(event)!.add(handler);
    return () => {
      const handlers = listeners.get(event);
      if (!handlers) return;
      handlers.delete(handler);
      if (handlers.size === 0) listeners.delete(event);
    };
  };

  const send = (data: unknown) => {
    if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(data));
  };

  return { connect, disconnect, subscribe, send };
}
