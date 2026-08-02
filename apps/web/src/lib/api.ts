import { TokenStore, handleAuthFailure, refreshToken } from "./auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const DEFAULT_TIMEOUT = 15000; // 15s for normal reads
const LONG_TIMEOUT = 300000;   // 5min for uploads/writes

/** 刷新逻辑排除的路径：登录/刷新本身不做 401 重试（任务卡 §6）。 */
const NO_RETRY_PATHS = ["/auth/login", "/auth/refresh"];

export interface ApiRequestOptions {
  signal?: AbortSignal;
  timeout?: number;
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

function createTimeoutError(timeout: number): Error {
  const error = new Error(`请求超时（${Math.ceil(timeout / 1000)}s）`);
  error.name = "TimeoutError";
  return error;
}

function fetchWithTimeout(
  url: string,
  options?: RequestInit,
  timeout?: number,
  externalSignal?: AbortSignal,
): Promise<Response> {
  if (!timeout && !externalSignal) return fetch(url, options);

  const timeoutController = timeout ? new AbortController() : null;
  const combinedController =
    timeoutController && externalSignal ? new AbortController() : null;
  let didTimeout = false;

  const timer = timeoutController
    ? setTimeout(() => {
        didTimeout = true;
        timeoutController.abort();
      }, timeout)
    : null;

  let cleanup = () => {};
  let signal = externalSignal ?? timeoutController?.signal;

  if (timeoutController && externalSignal && combinedController) {
    const forwardAbort = () => {
      if (!combinedController.signal.aborted) {
        combinedController.abort();
      }
    };

    if (externalSignal.aborted || timeoutController.signal.aborted) {
      forwardAbort();
    } else {
      externalSignal.addEventListener("abort", forwardAbort, { once: true });
      timeoutController.signal.addEventListener("abort", forwardAbort, { once: true });
      cleanup = () => {
        externalSignal.removeEventListener("abort", forwardAbort);
        timeoutController.signal.removeEventListener("abort", forwardAbort);
      };
    }

    signal = combinedController.signal;
  }

  return fetch(url, { ...options, signal })
    .catch((error) => {
      if (didTimeout && timeout) {
        throw createTimeoutError(timeout);
      }
      throw error;
    })
    .finally(() => {
      if (timer) clearTimeout(timer);
      cleanup();
    });
}

function buildHeaders(options?: RequestInit): Record<string, string> {
  const token = TokenStore.getAccessToken();
  const headers: Record<string, string> = {
    ...(options?.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (!(options?.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  return headers;
}

function isNoRetryPath(path: string): boolean {
  return NO_RETRY_PATHS.some((p) => path === p || path.startsWith(`${p}/`));
}

async function fetchApi<T>(
  path: string,
  options?: RequestInit,
  timeout?: number,
  signal?: AbortSignal,
): Promise<T> {
  // 首个请求：立即带上当前 access token
  let headers = buildHeaders(options);
  let res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    { ...options, headers },
    timeout,
    signal,
  );

  // 401 处理：仅对非登录/刷新请求触发一次 single-flight 刷新，并各重试一次。
  if (res.status === 401 && !isNoRetryPath(path)) {
    const refreshed = await refreshToken();
    if (refreshed) {
      // 刷新成功后使用最新 access token 重试（不复用首次请求的旧 Authorization 头）
      headers = buildHeaders(options);
      res = await fetchWithTimeout(
        `${API_BASE}${path}`,
        { ...options, headers },
        timeout,
        signal,
      );
    }
  }

  // 重试后仍然 401（或刷新失败）：原子清理并触发一次统一认证失败回调
  if (res.status === 401) {
    handleAuthFailure();
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || "请求失败");
  }

  if (res.status === 204) return undefined as T;
  return res.json();
}

/**
 * 携带 Authorization 的 Blob 下载（任务卡 §5.2、§6）。
 *
 * PDF 等二进制资源只允许经 Authorization 头获取；禁止回退为带 token 的 query
 * URL。401 走统一刷新重试；任何失败抛错，由调用方负责清理已创建的 object URL。
 */
async function fetchBlob(
  path: string,
  options?: RequestInit,
  timeout?: number,
  signal?: AbortSignal,
): Promise<Blob> {
  let headers = buildHeaders(options);
  let res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    { ...options, headers },
    timeout,
    signal,
  );

  if (res.status === 401 && !isNoRetryPath(path)) {
    const refreshed = await refreshToken();
    if (refreshed) {
      headers = buildHeaders(options);
      res = await fetchWithTimeout(
        `${API_BASE}${path}`,
        { ...options, headers },
        timeout,
        signal,
      );
    }
  }

  if (res.status === 401) {
    handleAuthFailure();
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || "下载失败");
  }

  return res.blob();
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export const api = {
  get: <T>(path: string, requestOptions?: ApiRequestOptions) =>
    fetchApi<T>(
      path,
      undefined,
      requestOptions?.timeout ?? DEFAULT_TIMEOUT,
      requestOptions?.signal,
    ),
  post: <T>(path: string, body?: unknown) =>
    fetchApi<T>(path, {
      method: "POST",
      body: body ? JSON.stringify(body) : undefined,
    }, LONG_TIMEOUT),
  put: <T>(path: string, body: unknown) =>
    fetchApi<T>(path, {
      method: "PUT",
      body: JSON.stringify(body),
    }, LONG_TIMEOUT),
  patch: <T>(path: string, body: unknown) =>
    fetchApi<T>(path, {
      method: "PATCH",
      body: JSON.stringify(body),
    }, LONG_TIMEOUT),
  delete: (path: string) => fetchApi<void>(path, { method: "DELETE" }, LONG_TIMEOUT),
  upload: <T>(path: string, formData: FormData) =>
    fetchApi<T>(path, { method: "POST", body: formData }),  // no timeout for file uploads
  getBlob: (path: string, requestOptions?: ApiRequestOptions) =>
    fetchBlob(
      path,
      undefined,
      requestOptions?.timeout ?? DEFAULT_TIMEOUT,
      requestOptions?.signal,
    ),
};
