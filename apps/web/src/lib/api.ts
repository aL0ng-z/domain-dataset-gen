import { refreshToken } from "./auth";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const DEFAULT_TIMEOUT = 15000; // 15s for normal reads
const LONG_TIMEOUT = 300000;   // 5min for uploads/writes

function fetchWithTimeout(url: string, options?: RequestInit, timeout?: number): Promise<Response> {
  if (!timeout) return fetch(url, options);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  return fetch(url, { ...options, signal: controller.signal }).finally(() =>
    clearTimeout(timer)
  );
}

async function fetchApi<T>(
  path: string,
  options?: RequestInit,
  timeout?: number
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

  let res = await fetchWithTimeout(`${API_BASE}${path}`, { ...options, headers }, timeout);

  if (res.status === 401) {
    const refreshed = await refreshToken();
    if (refreshed) {
      headers["Authorization"] = `Bearer ${localStorage.getItem("access_token")}`;
      res = await fetchWithTimeout(`${API_BASE}${path}`, { ...options, headers }, timeout);
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
  get: <T>(path: string) => fetchApi<T>(path, undefined, DEFAULT_TIMEOUT),
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
