import type { components } from "./api/generated";

/**
 * 统一前端认证令牌模块（T01）。
 *
 * 本模块是令牌持久化的唯一读写入口：任何其它模块（api、ws、context、页面）
 * 都不得直接读写 localStorage，一律经 TokenStore / getToken / refreshToken。
 *
 * 覆盖任务卡 §6 前端合同：
 * - TokenStore 收口 localStorage 访问；
 * - refreshToken 为 single-flight（并发调用共享同一刷新操作）；
 * - 旧刷新响应晚到时通过令牌代数（generation）丢弃，防止覆盖新登录会话；
 * - 刷新失败/响应格式错误时原子清除令牌并触发一次认证失败回调；
 * - storage 事件同步跨标签页登录/刷新/退出。
 */

export interface TokenData {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface AuthData {
  access_token: string;
  refresh_token: string;
  user: components["schemas"]["UserResponse"];
}

const ACCESS_KEY = "access_token";
const REFRESH_KEY = "refresh_token";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

// ---------------------------------------------------------------------------
// TokenStore：localStorage 唯一读写入口
// ---------------------------------------------------------------------------

/** 令牌代数：每次写入令牌（登录/刷新）自增；晚到的旧刷新结果按代数丢弃。 */
let tokenGeneration = 0;

type TokenChangeListener = () => void;
const tokenChangeListeners = new Set<TokenChangeListener>();

export class TokenStore {
  private static accessKey = ACCESS_KEY;
  private static refreshKey = REFRESH_KEY;

  static isBrowser(): boolean {
    return typeof window !== "undefined";
  }

  static getAccessToken(): string | null {
    if (!TokenStore.isBrowser()) return null;
    return window.localStorage.getItem(TokenStore.accessKey);
  }

  static getRefreshToken(): string | null {
    if (!TokenStore.isBrowser()) return null;
    return window.localStorage.getItem(TokenStore.refreshKey);
  }

  /** 当前令牌代数；发起刷新前捕获，成功后校验未变才写回（防止旧响应覆盖新会话）。 */
  static currentGeneration(): number {
    return tokenGeneration;
  }

  /** 写入两类令牌并通知订阅方。expectedGeneration 非空且与当前代数不一致时丢弃写入。 */
  static setTokens(
    access: string,
    refresh: string,
    options?: { expectedGeneration?: number }
  ): void {
    if (
      options?.expectedGeneration !== undefined &&
      options.expectedGeneration !== tokenGeneration
    ) {
      // 晚到的旧刷新结果：忽略，不得覆盖较新的登录会话（任务卡 §11 验收标准 8）。
      return;
    }
    if (!TokenStore.isBrowser()) return;
    tokenGeneration += 1;
    window.localStorage.setItem(TokenStore.accessKey, access);
    window.localStorage.setItem(TokenStore.refreshKey, refresh);
    notifyTokenChange();
  }

  /** 原子清除两类令牌并通知订阅方。 */
  static clearTokens(): void {
    if (!TokenStore.isBrowser()) return;
    tokenGeneration += 1;
    window.localStorage.removeItem(TokenStore.accessKey);
    window.localStorage.removeItem(TokenStore.refreshKey);
    notifyTokenChange();
  }
}

function notifyTokenChange(): void {
  tokenChangeListeners.forEach((l) => l());
}

/** 订阅令牌变化（AuthContext 同步用户态、ws-context 重连）。 */
export function subscribeToTokenChange(listener: TokenChangeListener): () => void {
  tokenChangeListeners.add(listener);
  return () => tokenChangeListeners.delete(listener);
}

// ---------------------------------------------------------------------------
// Single-flight 刷新
// ---------------------------------------------------------------------------

async function requestRefresh(): Promise<boolean> {
  const rt = TokenStore.getRefreshToken();
  if (!rt) return false;
  const generation = TokenStore.currentGeneration();
  try {
    const res = await fetch(`${API_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!res.ok) return false;
    const data: TokenData = await res.json();
    if (!data.access_token || !data.refresh_token) return false;
    // 只写回代数未变的刷新结果；期间若有新登录则丢弃本次写入。
    TokenStore.setTokens(data.access_token, data.refresh_token, { expectedGeneration: generation });
    return true;
  } catch {
    return false;
  }
}

let pendingRefresh: Promise<boolean> | null = null;

/** Single-flight 刷新：并发调用共享同一个刷新 Promise（任务卡 §6、§11 验收标准 6）。 */
export function refreshToken(): Promise<boolean> {
  if (!pendingRefresh) {
    pendingRefresh = requestRefresh()
      .catch(() => false)
      .finally(() => {
        pendingRefresh = null;
      });
  }
  return pendingRefresh;
}

// ---------------------------------------------------------------------------
// 认证失败统一处理（一次清理 + 一次跳转）
// ---------------------------------------------------------------------------

type AuthFailureListener = () => void;
const authFailureListeners = new Set<AuthFailureListener>();
let authFailed = false;

/** 刷新失败、响应格式错误或重试后再次 401 时调用：原子清除令牌并通知订阅方。 */
export function handleAuthFailure(): void {
  if (authFailed) return;
  authFailed = true;
  TokenStore.clearTokens();
  authFailureListeners.forEach((l) => l());
}

export function onAuthFailure(listener: AuthFailureListener): () => void {
  authFailureListeners.add(listener);
  return () => authFailureListeners.delete(listener);
}

/** 复位认证失败守卫（登录时自动调用；测试在 beforeEach 显式复位以防跨用例污染）。 */
export function resetAuthFailureGuard(): void {
  authFailed = false;
}

// ---------------------------------------------------------------------------
// 登录 / 退出
// ---------------------------------------------------------------------------

export async function login(
  username: string,
  password: string
): Promise<AuthData> {
  // Step 1: Get tokens
  const res = await fetch(`${API_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "登录失败" }));
    throw new Error(err.detail || "登录失败");
  }
  const tokenData: TokenData = await res.json();
  resetAuthFailureGuard();
  TokenStore.setTokens(tokenData.access_token, tokenData.refresh_token);

  // Step 2: Fetch user profile
  const meRes = await fetch(`${API_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${tokenData.access_token}` },
  });
  if (!meRes.ok) {
    throw new Error("获取用户信息失败");
  }
  const user = await meRes.json();

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

// ---------------------------------------------------------------------------
// 跨标签页同步（storage 事件）
// ---------------------------------------------------------------------------

export type AuthStorageEventType = "tokens-set" | "tokens-cleared";

export interface AuthStorageEvent {
  type: AuthStorageEventType;
}

type StorageListener = (event: AuthStorageEvent) => void;
const storageListeners = new Set<StorageListener>();

/** 订阅同源其它标签页的令牌存储变更（登录/刷新/退出）。 */
export function subscribeToAuthStorage(listener: StorageListener): () => void {
  storageListeners.add(listener);
  return () => storageListeners.delete(listener);
}

function notifyStorageEvent(type: AuthStorageEventType): void {
  storageListeners.forEach((l) => l({ type }));
}

if (TokenStore.isBrowser()) {
  window.addEventListener("storage", (e) => {
    if (e.key === null) return; // clear() 全清，忽略
    if (e.key !== ACCESS_KEY && e.key !== REFRESH_KEY) return;
    // 只处理 access 变化避免重复广播；刷新/登录总会更新 access。
    if (e.key === REFRESH_KEY) return;
    if (e.newValue === e.oldValue) return;
    if (e.newValue === null) {
      notifyStorageEvent("tokens-cleared");
    } else {
      notifyStorageEvent("tokens-set");
    }
  });
}
