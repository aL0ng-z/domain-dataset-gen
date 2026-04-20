import { refreshToken } from "./auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const DEFAULT_TIMEOUT = 15000; // 15s for normal reads
const LONG_TIMEOUT = 300000;   // 5min for uploads/writes

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

async function fetchApi<T>(
  path: string,
  options?: RequestInit,
  timeout?: number,
  signal?: AbortSignal,
): Promise<T> {
  const token =
    typeof window !== "undefined"
      ? localStorage.getItem("access_token")
      : null;

  const headers: Record<string, string> = {
    ...(options?.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (!(options?.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }

  let res = await fetchWithTimeout(
    `${API_BASE}${path}`,
    { ...options, headers },
    timeout,
    signal,
  );

  if (res.status === 401) {
    const refreshed = await refreshToken();
    if (refreshed) {
      headers["Authorization"] = `Bearer ${localStorage.getItem("access_token")}`;
      res = await fetchWithTimeout(
        `${API_BASE}${path}`,
        { ...options, headers },
        timeout,
        signal,
      );
    }
  }

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || "请求失败");
  }

  if (res.status === 204) return undefined as T;
  return res.json();
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
};
