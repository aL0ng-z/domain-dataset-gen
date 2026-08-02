import { TokenStore, handleAuthFailure, refreshToken } from "./auth";
import type { components, paths } from "./api/generated";

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

// ---------------------------------------------------------------------------
// 错误判别联合
// ---------------------------------------------------------------------------
// 前端按稳定 `code` 分支，不匹配中文 message/detail。两类错误：
//   - 应用业务错误：ErrorResponse
//   - 字段校验错误：ValidationErrorResponse
// 非 2xx body 一律不当作成功响应强转。

export interface ApiClientErrorBase {
  status: number;
  requestId?: string;
}

export interface ApiBusinessError extends ApiClientErrorBase {
  kind: "business";
  code: components["schemas"]["ErrorResponse"]["code"];
  message: string;
  context: components["schemas"]["ErrorResponse"]["context"];
}

export interface ApiValidationError extends ApiClientErrorBase {
  kind: "validation";
  code: "VALIDATION_ERROR";
  message: string;
  errors: components["schemas"]["ValidationErrorResponse"]["errors"];
}

export type ApiError = ApiBusinessError | ApiValidationError;

function parseError(status: number, body: unknown): ApiError {
  const obj = (body ?? {}) as Record<string, unknown>;
  const code = typeof obj.code === "string" ? obj.code : "INTERNAL_ERROR";
  // 兼容后端 HTTPException detail 字符串（如 parser-profile 422 返回 detail）。
  const message =
    typeof obj.message === "string"
      ? obj.message
      : typeof obj.detail === "string"
        ? obj.detail
        : "请求失败";
  const requestId = typeof obj.request_id === "string" ? obj.request_id : undefined;

  if (status === 422 && Array.isArray(obj.errors)) {
    return {
      kind: "validation",
      status,
      code: "VALIDATION_ERROR",
      message,
      requestId,
      errors: obj.errors as components["schemas"]["ValidationErrorResponse"]["errors"],
    };
  }

  return {
    kind: "business",
    status,
    code: code as components["schemas"]["ErrorResponse"]["code"],
    message,
    requestId,
    context: (obj.context ?? null) as components["schemas"]["ErrorResponse"]["context"],
  };
}

export class ApiErrorException extends Error {
  readonly apiError: ApiError;
  constructor(apiError: ApiError) {
    super(apiError.message);
    this.name = "ApiError";
    this.apiError = apiError;
  }
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

async function fetchApi(
  url: string,
  options?: RequestInit,
  timeout?: number,
  signal?: AbortSignal,
): Promise<unknown> {
  // 统一经 TokenStore 读取 access token（任务卡 §6：单一令牌读写入口）。
  let headers = buildHeaders(options);
  let res = await fetchWithTimeout(
    `${API_BASE}${url}`,
    { ...options, headers },
    timeout,
    signal,
  );

  // 401 处理：仅对非登录/刷新请求触发一次 single-flight 刷新，并各重试一次。
  if (res.status === 401 && !isNoRetryPath(url)) {
    const refreshed = await refreshToken();
    if (refreshed) {
      headers = buildHeaders(options);
      res = await fetchWithTimeout(
        `${API_BASE}${url}`,
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
    const errorBody = await res.json().catch(() => ({ message: res.statusText }));
    throw new ApiErrorException(parseError(res.status, errorBody));
  }

  if (res.status === 204) return undefined;
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

// ---------------------------------------------------------------------------
// 类型化 client：以 HTTP method + path（OpenAPI 路由模板）为索引推导类型。
// ---------------------------------------------------------------------------
// path 必须是 generated.ts 中的路由模板（如 "/projects/{pid}/datasets"，
// 不带 /api 前缀）；实际取值通过 params.path 提供，运行时替换 {pid} 等占位符。
// 返回类型从 operations 的 responses 推导；页面不允许再用 api.get<T>() 手写覆盖。

type RelativePaths = {
  [P in keyof paths as P extends `/api${infer R}` ? R : never]: paths[P];
};

type MethodOf<Path extends keyof RelativePaths, M extends "get" | "post" | "put" | "patch" | "delete"> =
  NonNullable<RelativePaths[Path][M]>;

// 请求体推导。`Op extends unknown` 使条件类型在联合上分配（distributive），
// 因此 endpoint 为联合时仍能推出每个分支的请求体。
type BodyParams<Op> = Op extends unknown
  ? Op extends { requestBody?: { content: { "application/json": infer B } } }
    ? B
    : never
  : never;

// 响应类型推导：取所有 responses 的 application/json 内容，再排除错误 envelope。
// 204 等无内容响应推导为 void。
type OperationResponseData<Operation> = Operation extends { responses: Record<string, unknown> }
  ? {
      [code in keyof Operation["responses"]]: Operation["responses"][code] extends {
        content: { "application/json": infer T };
      }
        ? T
        : Operation["responses"][code] extends { content?: never }
          ? void
          : never;
    }[keyof Operation["responses"]]
  : never;

type ErrorSchema =
  | components["schemas"]["ErrorResponse"]
  | components["schemas"]["ValidationErrorResponse"];

type SuccessResponse<Operation> = Exclude<OperationResponseData<Operation>, ErrorSchema>;

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// ---------------------------------------------------------------------------
// 共享 JSON 展示/解析 helper（页面展示 JSONB 字段时使用）。
// ---------------------------------------------------------------------------

export function formatJsonPreview(value: unknown): string {
  if (value === null || value === undefined) return "(空)";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function parseJsonObject(value: string): Record<string, unknown> {
  const parsed: unknown = JSON.parse(value);
  if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
    throw new Error("内容必须是 JSON 对象");
  }
  return parsed as Record<string, unknown>;
}

export interface RequestInitTyped {
  params?: Record<string, string | number>;
  query?: Record<string, string | number | boolean | undefined | null>;
  headers?: Record<string, string>;
  signal?: AbortSignal;
  timeout?: number;
  /** 附加请求头（如 Idempotency-Key）。与 Authorization 合并，不覆盖既有头。 */
  headers?: Record<string, string>;
}

function buildUrl(path: string, init?: RequestInitTyped): string {
  let resolved = path;
  for (const [k, v] of Object.entries(init?.params ?? {})) {
    resolved = resolved.replace(`{${k}}`, String(v));
  }
  const search = new URLSearchParams();
  for (const [k, v] of Object.entries(init?.query ?? {})) {
    if (v !== undefined && v !== null) search.set(k, String(v));
  }
  const qs = search.toString();
  return qs ? `${resolved}?${qs}` : resolved;
}

export const api = {
  get: async <Path extends keyof RelativePaths>(
    path: Path,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "get">>> => {
    return (await fetchApi(
      buildUrl(path, init),
      undefined,
      init?.timeout ?? DEFAULT_TIMEOUT,
      init?.signal,
    )) as SuccessResponse<MethodOf<Path, "get">>;
  },
  post: async <Path extends keyof RelativePaths>(
    path: Path,
    body?: BodyParams<MethodOf<Path, "post">>,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "post">>> => {
    return (await fetchApi(
      buildUrl(path, init),
      {
        method: "POST",
        body: body === undefined ? undefined : JSON.stringify(body),
        ...(init?.headers ? { headers: init.headers } : {}),
      },
      init?.timeout ?? LONG_TIMEOUT,
      init?.signal,
    )) as SuccessResponse<MethodOf<Path, "post">>;
  },

  put: async <Path extends keyof RelativePaths>(
    path: Path,
    body: BodyParams<MethodOf<Path, "put">>,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "put">>> => {
    return (await fetchApi(
      buildUrl(path, init),
      { method: "PUT", body: JSON.stringify(body), headers: init?.headers },
      init?.timeout ?? LONG_TIMEOUT,
      init?.signal,
    )) as SuccessResponse<MethodOf<Path, "put">>;
  },

  patch: async <Path extends keyof RelativePaths>(
    path: Path,
    body?: BodyParams<MethodOf<Path, "patch">>,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "patch">>> => {
    return (await fetchApi(
      buildUrl(path, init),
      {
        method: "PATCH",
        body: body === undefined ? undefined : JSON.stringify(body),
        headers: init?.headers,
      },
      init?.timeout ?? LONG_TIMEOUT,
      init?.signal,
    )) as SuccessResponse<MethodOf<Path, "patch">>;
  },

  delete: async <Path extends keyof RelativePaths>(
    path: Path,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "delete">>> => {
    return (await fetchApi(
      buildUrl(path, init),
      { method: "DELETE", headers: init?.headers },
      init?.timeout ?? LONG_TIMEOUT,
      init?.signal,
    )) as SuccessResponse<MethodOf<Path, "delete">>;
  },

  upload: async <Path extends keyof RelativePaths>(
    path: Path,
    formData: FormData,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "post">>> => {
    return (await fetchApi(
      buildUrl(path, init),
      { method: "POST", body: formData, headers: init?.headers },
      init?.timeout,
      init?.signal,
    )) as SuccessResponse<MethodOf<Path, "post">>;
  },

  getBlob: async (path: string, init?: RequestInitTyped): Promise<Blob> => {
    return fetchBlob(
      buildUrl(path, init),
      undefined,
      init?.timeout ?? DEFAULT_TIMEOUT,
      init?.signal,
    );
  },
};

export { components, type paths, type RelativePaths };
