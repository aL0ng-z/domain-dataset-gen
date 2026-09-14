import type { components } from "./api/generated";

/**
 * 统一认证状态。
 *
 * access、refresh 和会话标识必须作为一个值读写。会话标识把旧请求与新登录
 * 隔离开：旧会话的刷新失败、重试或迟到响应都不能影响新会话。
 */

export interface TokenData {
  access_token: string;
  refresh_token: string;
  token_type?: string;
}

export interface AuthData {
  access_token: string;
  refresh_token: string;
  user: components["schemas"]["UserResponse"];
}

export interface AuthSession {
  session_id: string;
  access_token: string;
  refresh_token: string;
  token_type?: string;
}

/** 单条 localStorage 记录；旧的分散 token 键不再兼容，部署后需重新登录。 */
export const AUTH_SESSION_STORAGE_KEY = "domain_dataset_auth";
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
export const REFRESH_TIMEOUT = 10_000;

let tokenGeneration = 0;

type TokenChangeListener = () => void;
const tokenChangeListeners = new Set<TokenChangeListener>();

function createSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `session-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function parseSession(value: string | null): AuthSession | null {
  if (!value) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== "object") return null;
    const record = parsed as Record<string, unknown>;
    if (
      typeof record.session_id !== "string" ||
      typeof record.access_token !== "string" ||
      typeof record.refresh_token !== "string" ||
      !record.session_id ||
      !record.access_token ||
      !record.refresh_token
    ) {
      return null;
    }
    return {
      session_id: record.session_id,
      access_token: record.access_token,
      refresh_token: record.refresh_token,
      ...(typeof record.token_type === "string" ? { token_type: record.token_type } : {}),
    };
  } catch {
    return null;
  }
}

function notifyTokenChange(): void {
  tokenChangeListeners.forEach((listener) => listener());
}

/** 在切换/退出会话时中断同会话的共享刷新；实现在文件后半段。 */
function cancelPendingRefreshForSession(sessionId: string | null): void {
  if (sessionId && pendingRefresh?.sessionId === sessionId) {
    pendingRefresh.controller.abort();
  }
}

export class TokenStore {
  static isBrowser(): boolean {
    return typeof window !== "undefined";
  }

  static getSession(): AuthSession | null {
    if (!TokenStore.isBrowser()) return null;
    return parseSession(window.localStorage.getItem(AUTH_SESSION_STORAGE_KEY));
  }

  static getSessionId(): string | null {
    return TokenStore.getSession()?.session_id ?? null;
  }

  static getAccessToken(): string | null {
    return TokenStore.getSession()?.access_token ?? null;
  }

  static getRefreshToken(): string | null {
    return TokenStore.getSession()?.refresh_token ?? null;
  }

  static currentGeneration(): number {
    return tokenGeneration;
  }

  /**
   * 更新当前会话的 token。expectedSessionId 用于刷新结果写回前的会话校验。
   * 直接调用时保留已有 session_id；首次调用则建立一个会话，供测试和受控初始化使用。
   */
  static setTokens(
    access: string,
    refresh: string,
    options?: {
      expectedSessionId?: string;
      expectedRefreshToken?: string;
      sessionId?: string;
      tokenType?: string;
    },
  ): boolean {
    if (!TokenStore.isBrowser()) return false;
    const current = TokenStore.getSession();
    if (
      options?.expectedSessionId !== undefined &&
      current?.session_id !== options.expectedSessionId
    ) {
      return false;
    }
    if (
      options?.expectedRefreshToken !== undefined &&
      current?.refresh_token !== options.expectedRefreshToken
    ) {
      return false;
    }

    const sessionId = options?.sessionId ?? current?.session_id ?? createSessionId();
    if (current?.session_id && current.session_id !== sessionId) {
      cancelPendingRefreshForSession(current.session_id);
    }
    const session: AuthSession = {
      session_id: sessionId,
      access_token: access,
      refresh_token: refresh,
      ...(options?.tokenType ? { token_type: options.tokenType } : current?.token_type ? { token_type: current.token_type } : {}),
    };
    tokenGeneration += 1;
    window.localStorage.setItem(AUTH_SESSION_STORAGE_KEY, JSON.stringify(session));
    notifyTokenChange();
    return true;
  }

  /** 登录成功时创建全新的会话 ID，绝不复用先前用户的会话。 */
  static startSession(access: string, refresh: string, tokenType?: string): string | null {
    const sessionId = createSessionId();
    resetAuthFailureGuard();
    return TokenStore.setTokens(access, refresh, { sessionId, tokenType }) ? sessionId : null;
  }

  /**
   * 清除整个会话。expectedSessionId 不匹配时不做任何事，防止旧请求退出新用户。
   */
  static clearTokens(options?: { expectedSessionId?: string }): boolean {
    if (!TokenStore.isBrowser()) return false;
    const current = TokenStore.getSession();
    if (!current) return false;
    if (
      options?.expectedSessionId !== undefined &&
      current.session_id !== options.expectedSessionId
    ) {
      return false;
    }
    cancelPendingRefreshForSession(current.session_id);
    tokenGeneration += 1;
    window.localStorage.removeItem(AUTH_SESSION_STORAGE_KEY);
    notifyTokenChange();
    return true;
  }
}

/** 订阅本标签页 token 写入；跨标签页变更另见 subscribeToAuthStorage。 */
export function subscribeToTokenChange(listener: TokenChangeListener): () => void {
  tokenChangeListeners.add(listener);
  return () => tokenChangeListeners.delete(listener);
}

function abortError(message = "Request cancelled"): DOMException {
  return new DOMException(message, "AbortError");
}

function waitForAbortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  if (signal.aborted) return Promise.reject(abortError());
  return new Promise<T>((resolve, reject) => {
    const onAbort = () => {
      cleanup();
      reject(abortError());
    };
    const cleanup = () => signal.removeEventListener("abort", onAbort);
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        cleanup();
        resolve(value);
      },
      (error: unknown) => {
        cleanup();
        reject(error);
      },
    );
  });
}

export type RefreshResult = "refreshed" | "failed" | "cancelled";

interface PendingRefresh {
  sessionId: string;
  controller: AbortController;
  promise: Promise<RefreshResult>;
}

let pendingRefresh: PendingRefresh | null = null;

async function requestRefresh(session: AuthSession, controller: AbortController): Promise<RefreshResult> {
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, REFRESH_TIMEOUT);

  try {
    const response = await waitForAbortable(
      fetch(`${API_URL}/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: session.refresh_token }),
        signal: controller.signal,
      }),
      controller.signal,
    );
    if (!response.ok) return "failed";
    const data = await waitForAbortable(
      Promise.resolve().then(() => response.json() as Promise<TokenData>),
      controller.signal,
    );
    if (!data.access_token || !data.refresh_token) return "failed";
    const wrote = TokenStore.setTokens(data.access_token, data.refresh_token, {
      expectedSessionId: session.session_id,
      expectedRefreshToken: session.refresh_token,
      sessionId: session.session_id,
      tokenType: data.token_type,
    });
    return wrote ? "refreshed" : "cancelled";
  } catch {
    return timedOut ? "failed" : "cancelled";
  } finally {
    clearTimeout(timer);
  }
}

function waitForRefreshResult(
  promise: Promise<RefreshResult>,
  signal?: AbortSignal,
): Promise<RefreshResult> {
  if (!signal) return promise;
  return waitForAbortable(promise, signal).catch((error: unknown) => {
    if (error instanceof DOMException && error.name === "AbortError") return "cancelled";
    throw error;
  });
}

/**
 * 同一 session 共享一个刷新请求。调用方取消只退出自身等待；会话切换/退出才会取消共享请求。
 */
export function refreshToken(options?: {
  sessionId?: string | null;
  signal?: AbortSignal;
}): Promise<RefreshResult> {
  const session = TokenStore.getSession();
  const sessionId = options?.sessionId !== undefined
    ? options.sessionId
    : session?.session_id ?? null;
  if (!session || !sessionId || session.session_id !== sessionId) {
    return Promise.resolve("cancelled");
  }
  if (options?.signal?.aborted) return Promise.resolve("cancelled");

  if (!pendingRefresh || pendingRefresh.sessionId !== sessionId) {
    if (pendingRefresh) pendingRefresh.controller.abort();
    const controller = new AbortController();
    const pending: PendingRefresh = {
      sessionId,
      controller,
      promise: Promise.resolve("cancelled"),
    };
    pending.promise = requestRefresh(session, controller).finally(() => {
      if (pendingRefresh === pending) pendingRefresh = null;
    });
    pendingRefresh = pending;
  }

  return waitForRefreshResult(pendingRefresh.promise, options?.signal);
}

type AuthFailureListener = () => void;
const authFailureListeners = new Set<AuthFailureListener>();
let failedSessionId: string | null = null;

/** 只允许当前会话触发退出。旧请求携带旧 sessionId 调用时会被忽略。 */
export function handleAuthFailure(expectedSessionId?: string | null): boolean {
  const currentSessionId = TokenStore.getSessionId();
  const sessionId = expectedSessionId ?? currentSessionId;
  if (!sessionId || currentSessionId !== sessionId || failedSessionId === sessionId) {
    return false;
  }
  failedSessionId = sessionId;
  if (!TokenStore.clearTokens({ expectedSessionId: sessionId })) return false;
  authFailureListeners.forEach((listener) => listener());
  return true;
}

export function onAuthFailure(listener: AuthFailureListener): () => void {
  authFailureListeners.add(listener);
  return () => authFailureListeners.delete(listener);
}

export function resetAuthFailureGuard(): void {
  failedSessionId = null;
}

export async function login(
  username: string,
  password: string,
  options?: { signal?: AbortSignal },
): Promise<AuthData> {
  const generation = TokenStore.currentGeneration();
  const response = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
    signal: options?.signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: "登录失败" }));
    throw new Error(error.message || error.detail || "登录失败");
  }
  const tokenData: TokenData = await response.json();
  if (!tokenData.access_token || !tokenData.refresh_token) {
    throw new Error("登录响应无效");
  }

  const meResponse = await fetch(`${API_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${tokenData.access_token}` },
    signal: options?.signal,
  });
  if (!meResponse.ok) throw new Error("获取用户信息失败");
  const user = await meResponse.json();
  if (options?.signal?.aborted || generation !== TokenStore.currentGeneration()) {
    throw abortError("登录已取消");
  }

  resetAuthFailureGuard();
  if (!TokenStore.startSession(tokenData.access_token, tokenData.refresh_token, tokenData.token_type)) {
    throw abortError("登录已取消");
  }
  return {
    access_token: tokenData.access_token,
    refresh_token: tokenData.refresh_token,
    user,
  };
}

export function logout(): void {
  TokenStore.clearTokens();
}

export function getToken(): string | null {
  return TokenStore.getAccessToken();
}

export type AuthStorageEventType = "tokens-set" | "tokens-cleared";

export interface AuthStorageEvent {
  type: AuthStorageEventType;
}

type StorageListener = (event: AuthStorageEvent) => void;
const storageListeners = new Set<StorageListener>();

export function subscribeToAuthStorage(listener: StorageListener): () => void {
  storageListeners.add(listener);
  return () => storageListeners.delete(listener);
}

function notifyStorageEvent(type: AuthStorageEventType): void {
  storageListeners.forEach((listener) => listener({ type }));
}

if (TokenStore.isBrowser()) {
  window.addEventListener("storage", (event) => {
    if (event.key !== null && event.key !== AUTH_SESSION_STORAGE_KEY) return;
    if (event.newValue === event.oldValue) return;

    const previous = event.key === null ? null : parseSession(event.oldValue);
    const next = event.key === null ? null : parseSession(event.newValue);
    if (event.key === null && pendingRefresh) {
      pendingRefresh.controller.abort();
    } else if (previous?.session_id && previous.session_id !== next?.session_id) {
      cancelPendingRefreshForSession(previous.session_id);
    }
    tokenGeneration += 1;
    if (next) resetAuthFailureGuard();
    notifyTokenChange();
    notifyStorageEvent(next ? "tokens-set" : "tokens-cleared");
  });
}
