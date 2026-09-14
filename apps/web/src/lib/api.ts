import { TokenStore, handleAuthFailure, refreshToken } from "./auth";
import type { components, paths } from "./api/generated";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const DEFAULT_TIMEOUT = 15_000;
const LONG_TIMEOUT = 300_000;

/** 登录/刷新本身不进入 401 刷新循环。 */
const NO_RETRY_PATHS = ["/auth/login", "/auth/refresh"];

export interface ApiRequestOptions {
  signal?: AbortSignal;
  timeout?: number;
}

export function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && (error as Error).name === "AbortError";
}

function createAbortError(message = "Request cancelled"): DOMException {
  return new DOMException(message, "AbortError");
}

function createTimeoutError(timeout: number): Error {
  const error = new Error(`请求超时（${Math.ceil(timeout / 1000)}s）`);
  error.name = "TimeoutError";
  return error;
}

/**
 * 一个请求只有一个截止时间。它从发起首个 fetch 开始，覆盖 401 刷新等待、重试
 * 以及 JSON/Blob body 的完整读取；请求结束后才清理计时器和事件监听。
 */
class RequestLifecycle {
  private readonly controller = new AbortController();
  private readonly externalSignal?: AbortSignal;
  private readonly timeout?: number;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private timedOut = false;
  private readonly onExternalAbort: () => void;

  constructor(timeout?: number, externalSignal?: AbortSignal) {
    this.timeout = timeout;
    this.externalSignal = externalSignal;
    this.onExternalAbort = () => this.controller.abort();

    if (externalSignal?.aborted) {
      this.controller.abort();
    } else {
      externalSignal?.addEventListener("abort", this.onExternalAbort, { once: true });
    }
    if (timeout !== undefined) {
      this.timer = setTimeout(() => {
        this.timedOut = true;
        this.controller.abort();
      }, timeout);
    }
  }

  get signal(): AbortSignal {
    return this.controller.signal;
  }

  assertActive(): void {
    if (this.timedOut) throw createTimeoutError(this.timeout ?? 0);
    if (this.controller.signal.aborted || this.externalSignal?.aborted) {
      throw createAbortError();
    }
  }

  wait<T>(promise: Promise<T>): Promise<T> {
    try {
      this.assertActive();
    } catch (error) {
      return Promise.reject(error);
    }

    return new Promise<T>((resolve, reject) => {
      let settled = false;
      const cleanup = () => this.controller.signal.removeEventListener("abort", onAbort);
      const finish = (callback: () => void) => {
        if (settled) return;
        settled = true;
        cleanup();
        callback();
      };
      const onAbort = () => finish(() => {
        try {
          this.assertActive();
        } catch (error) {
          reject(error);
        }
      });

      this.controller.signal.addEventListener("abort", onAbort, { once: true });
      promise.then(
        (value) => finish(() => {
          try {
            this.assertActive();
            resolve(value);
          } catch (error) {
            reject(error);
          }
        }),
        (error: unknown) => finish(() => {
          if (this.controller.signal.aborted) {
            try {
              this.assertActive();
            } catch (abort) {
              reject(abort);
              return;
            }
          }
          reject(error);
        }),
      );
    });
  }

  dispose(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.externalSignal?.removeEventListener("abort", this.onExternalAbort);
  }
}

// ---------------------------------------------------------------------------
// 错误判别联合
// ---------------------------------------------------------------------------

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

function buildHeaders(options?: RequestInit, accessToken?: string | null): Record<string, string> {
  const headers: Record<string, string> = {
    ...(options?.headers as Record<string, string>),
  };
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (!(options?.body instanceof FormData)) headers["Content-Type"] = "application/json";
  return headers;
}

function isNoRetryPath(path: string): boolean {
  return NO_RETRY_PATHS.some((excluded) => path === excluded || path.startsWith(`${excluded}/`));
}

function assertSessionStillCurrent(sessionId: string | null): void {
  if (sessionId && TokenStore.getSessionId() !== sessionId) {
    throw createAbortError("Session changed");
  }
}

async function readJson<T>(response: Response, lifecycle: RequestLifecycle): Promise<T> {
  return lifecycle.wait(Promise.resolve().then(() => response.json() as Promise<T>));
}

async function readJsonOrFallback(
  response: Response,
  lifecycle: RequestLifecycle,
  fallback: unknown,
): Promise<unknown> {
  try {
    return await readJson(response, lifecycle);
  } catch {
    lifecycle.assertActive();
    return fallback;
  }
}

type ResponseReader<T> = (response: Response, lifecycle: RequestLifecycle) => Promise<T>;
type ErrorFactory = (response: Response, lifecycle: RequestLifecycle) => Promise<Error>;

/** 统一请求执行链，确保所有阶段使用同一个 RequestLifecycle。 */
async function executeRequest<T>(
  path: string,
  options: RequestInit | undefined,
  timeout: number | undefined,
  externalSignal: AbortSignal | undefined,
  readSuccess: ResponseReader<T>,
  createError: ErrorFactory,
): Promise<T> {
  const lifecycle = new RequestLifecycle(timeout, externalSignal);
  const initialSession = TokenStore.getSession();
  const sessionId = initialSession?.session_id ?? null;
  const fetchResponse = async (accessToken: string | null): Promise<Response> => {
    lifecycle.assertActive();
    const headers = buildHeaders(options, accessToken);
    const response = await lifecycle.wait(
      fetch(`${API_BASE}${path}`, { ...options, headers, signal: lifecycle.signal }),
    );
    lifecycle.assertActive();
    assertSessionStillCurrent(sessionId);
    return response;
  };

  try {
    let response = await fetchResponse(initialSession?.access_token ?? null);

    if (response.status === 401 && sessionId && !isNoRetryPath(path)) {
      const currentSession = TokenStore.getSession();
      if (
        currentSession?.session_id === sessionId &&
        currentSession.access_token !== initialSession?.access_token
      ) {
        // 同一会话已经在别处完成 token 轮换，直接用当前 token 重试一次，
        // 不能把旧 401 当成新 token 的认证失败。
        response = await fetchResponse(currentSession.access_token);
      } else {
        const refreshResult = await lifecycle.wait(refreshToken({ sessionId }));
        lifecycle.assertActive();
        assertSessionStillCurrent(sessionId);
        if (refreshResult === "refreshed") {
          response = await fetchResponse(TokenStore.getSession()?.access_token ?? null);
        } else if (refreshResult === "failed") {
          handleAuthFailure(sessionId);
        } else {
          throw createAbortError("Session changed");
        }
      }
    }

    lifecycle.assertActive();
    assertSessionStillCurrent(sessionId);
    if (response.status === 401) handleAuthFailure(sessionId);
    if (!response.ok) throw await createError(response, lifecycle);
    lifecycle.assertActive();
    assertSessionStillCurrent(sessionId);
    return await readSuccess(response, lifecycle);
  } finally {
    lifecycle.dispose();
  }
}

async function fetchApi(
  path: string,
  options?: RequestInit,
  timeout?: number,
  signal?: AbortSignal,
): Promise<unknown> {
  return executeRequest(
    path,
    options,
    timeout,
    signal,
    async (response, lifecycle) => {
      if (response.status === 204) return undefined;
      return readJson(response, lifecycle);
    },
    async (response, lifecycle) => {
      const body = await readJsonOrFallback(response, lifecycle, { message: response.statusText });
      return new ApiErrorException(parseError(response.status, body));
    },
  );
}

/** Blob 下载与 JSON 请求共用认证、重试、截止时间和取消生命周期。 */
async function fetchBlob(
  path: string,
  options?: RequestInit,
  timeout?: number,
  signal?: AbortSignal,
): Promise<Blob> {
  return executeRequest(
    path,
    options,
    timeout,
    signal,
    (response, lifecycle) => lifecycle.wait(Promise.resolve().then(() => response.blob())),
    async (response, lifecycle) => {
      const body = await readJsonOrFallback(response, lifecycle, { detail: response.statusText });
      const detail = (body as { detail?: unknown }).detail;
      return new Error(typeof detail === "string" ? detail : "下载失败");
    },
  );
}

// ---------------------------------------------------------------------------
// 类型化 client：以 HTTP method + path（OpenAPI 路由模板）为索引推导类型。
// ---------------------------------------------------------------------------

type RelativePaths = {
  [P in keyof paths as P extends `/api${infer R}` ? R : never]: paths[P];
};

type MethodOf<Path extends keyof RelativePaths, M extends "get" | "post" | "put" | "patch" | "delete"> =
  NonNullable<RelativePaths[Path][M]>;

type BodyParams<Op> = Op extends unknown
  ? Op extends { requestBody?: { content: { "application/json": infer B } } }
    ? B
    : never
  : never;

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
  signal?: AbortSignal;
  timeout?: number;
  headers?: Record<string, string>;
}

function buildUrl(path: string, init?: RequestInitTyped): string {
  let resolved = path;
  for (const [key, value] of Object.entries(init?.params ?? {})) {
    resolved = resolved.replace(`{${key}}`, String(value));
  }
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(init?.query ?? {})) {
    if (value !== undefined && value !== null) search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `${resolved}?${query}` : resolved;
}

export const api = {
  get: async <Path extends keyof RelativePaths>(
    path: Path,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "get">>> => (
    await fetchApi(buildUrl(path, init), undefined, init?.timeout ?? DEFAULT_TIMEOUT, init?.signal)
  ) as SuccessResponse<MethodOf<Path, "get">>,

  post: async <Path extends keyof RelativePaths>(
    path: Path,
    body?: BodyParams<MethodOf<Path, "post">>,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "post">>> => (
    await fetchApi(
      buildUrl(path, init),
      {
        method: "POST",
        body: body === undefined ? undefined : JSON.stringify(body),
        ...(init?.headers ? { headers: init.headers } : {}),
      },
      init?.timeout ?? LONG_TIMEOUT,
      init?.signal,
    )
  ) as SuccessResponse<MethodOf<Path, "post">>,

  put: async <Path extends keyof RelativePaths>(
    path: Path,
    body: BodyParams<MethodOf<Path, "put">>,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "put">>> => (
    await fetchApi(
      buildUrl(path, init),
      { method: "PUT", body: JSON.stringify(body), headers: init?.headers },
      init?.timeout ?? LONG_TIMEOUT,
      init?.signal,
    )
  ) as SuccessResponse<MethodOf<Path, "put">>,

  patch: async <Path extends keyof RelativePaths>(
    path: Path,
    body?: BodyParams<MethodOf<Path, "patch">>,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "patch">>> => (
    await fetchApi(
      buildUrl(path, init),
      {
        method: "PATCH",
        body: body === undefined ? undefined : JSON.stringify(body),
        headers: init?.headers,
      },
      init?.timeout ?? LONG_TIMEOUT,
      init?.signal,
    )
  ) as SuccessResponse<MethodOf<Path, "patch">>,

  delete: async <Path extends keyof RelativePaths>(
    path: Path,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "delete">>> => (
    await fetchApi(
      buildUrl(path, init),
      { method: "DELETE", headers: init?.headers },
      init?.timeout ?? LONG_TIMEOUT,
      init?.signal,
    )
  ) as SuccessResponse<MethodOf<Path, "delete">>,

  upload: async <Path extends keyof RelativePaths>(
    path: Path,
    formData: FormData,
    init?: RequestInitTyped,
  ): Promise<SuccessResponse<MethodOf<Path, "post">>> => (
    await fetchApi(
      buildUrl(path, init),
      { method: "POST", body: formData, headers: init?.headers },
      init?.timeout ?? LONG_TIMEOUT,
      init?.signal,
    )
  ) as SuccessResponse<MethodOf<Path, "post">>,

  getBlob: async (path: string, init?: RequestInitTyped): Promise<Blob> => (
    fetchBlob(buildUrl(path, init), undefined, init?.timeout ?? DEFAULT_TIMEOUT, init?.signal)
  ),
};

export type { components, paths, RelativePaths };
