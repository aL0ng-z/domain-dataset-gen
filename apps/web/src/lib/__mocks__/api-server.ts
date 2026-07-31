import type { ApiRequestOptions } from "../api";

/**
 * 统一 API mock 层。
 *
 * 设计目标（对应任务卡“前端合同”）：
 * - 测试按真实 HTTP 状态码与 JSON 响应运行，不允许只 mock 页面内部函数；
 * - 支持 fake timers、延迟响应、AbortSignal、401→refresh 与 WebSocket mock。
 *
 * 用法：
 *   const server = createApiMockServer();
 *   server.mock("GET", "/projects", { items: [], total: 0, page: 1, page_size: 20 });
 *   // 触发被测代码
 *   server.restore();
 */

export interface MockResponseOptions {
  /** HTTP 状态码，默认 200。 */
  status?: number;
  /** 模拟网络延迟（毫秒）。传 0 表示立即返回。 */
  delay?: number;
  /** 响应体。缺省表示空 body。 */
  body?: unknown;
  /** 额外响应头。 */
  headers?: Record<string, string>;
  /** 模拟网络级失败（TypeError: Failed to fetch），用于错误态测试。 */
  networkError?: boolean;
}

/** 动态响应回调：每次调用时决定返回内容，可用于序列/条件响应（如 401→refresh）。 */
export type MockResponder = (call: {
  url: string;
  method: string;
  init?: RequestInit;
  callCount: number;
}) => MockResponseOptions;

export interface MockCall {
  url: string;
  options?: RequestInit;
}

export interface MockHandler {
  /** 已匹配被调用次数。 */
  callCount: number;
  /** 每次调用收到的请求。 */
  calls: MockCall[];
}

type RegisteredRoute = MockHandler & {
  options?: MockResponseOptions;
  responder: MockResponder;
};

function routeKey(method: string, path: string): string {
  return `${method.toUpperCase()} ${path}`;
}

export function createApiMockServer() {
  const routes: Map<string, RegisteredRoute> = new Map();
  const originalFetch = globalThis.fetch;

  const fetchHandler = async (
    input: RequestInfo | URL,
    init?: RequestInit,
  ): Promise<Response> => {
    const rawUrl = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = (init?.method ?? "GET").toUpperCase();

    const urlObj = new URL(rawUrl, "http://localhost:8000/api");
    // 去掉 base 前缀与 query，得到用于路由匹配的相对路径。
    let path = urlObj.pathname;
    if (urlObj.search) path += urlObj.search;
    path = path.replace(/^\/api/, "") || "/";

    const key = routeKey(method, path);
    const route = routes.get(key);

    if (!route) {
      // 未注册路由 -> 404，保证测试不会误连真实服务。
      return new Response(JSON.stringify({ detail: "mock not found" }), {
        status: 404,
        headers: { "Content-Type": "application/json" },
      });
    }

    route.callCount += 1;
    route.calls.push({ url: rawUrl, options: init });

    // 支持动态响应（序列/条件）。callCount 为 1 起的调用次数，便于判断“第几次”。
    const routeOptions =
      typeof route.responder === "function"
        ? route.responder({ url: rawUrl, method, init, callCount: route.callCount })
        : (route.options ?? {});
    if (!routeOptions) throw new Error("mock route has no responder or options");

    if (routeOptions.networkError) {
      throw new TypeError("Failed to fetch");
    }

    // 尊重 AbortSignal：已中止立即抛 AbortError；延迟期间中止也抛 AbortError。
    const signal = init?.signal;
    if (signal?.aborted) {
      throw new DOMException("The operation was aborted.", "AbortError");
    }

    const delay = routeOptions.delay ?? 0;
    if (delay > 0) {
      if (signal) {
        await new Promise<void>((resolve, reject) => {
          const onAbort = () => {
            signal.removeEventListener("abort", onAbort);
            reject(new DOMException("The operation was aborted.", "AbortError"));
          };
          signal.addEventListener("abort", onAbort, { once: true });
          setTimeout(() => {
            signal.removeEventListener("abort", onAbort);
            resolve();
          }, delay);
        });
      } else {
        await new Promise((resolve) => setTimeout(resolve, delay));
      }
    }

    const status = routeOptions.status ?? 200;
    const body = routeOptions.body === undefined ? null : JSON.stringify(routeOptions.body);
    return new Response(body, {
      status,
      headers: { "Content-Type": "application/json", ...routeOptions.headers },
    });
  };

  return {
    /**
     * 注册路由。path 为相对 API 前缀的路径（如 "/projects"）。
     * 参数可以是静态 MockResponseOptions，或一个 MockResponder 动态回调
     * （用于 401→refresh 之类的序列/条件响应）。
     * 返回带 callCount/calls 的 handler 用于断言。
     */
    mock(method: string, path: string, options?: MockResponseOptions | MockResponder): MockHandler {
      const route: RegisteredRoute & { options?: MockResponseOptions } = {
        callCount: 0,
        calls: [],
        responder: typeof options === "function" ? options : () => (options ?? {}),
      };
      if (typeof options !== "function") {
        route.options = options ?? {};
      }
      routes.set(routeKey(method, path), route);
      return route;
    },

    /** 便捷：注册 200 成功响应。 */
    onGet(path: string, body: unknown, options?: Omit<MockResponseOptions, "body" | "status">) {
      return this.mock("GET", path, { ...options, status: 200, body });
    },

    /** 便捷：注册 200 成功响应。 */
    onPost(path: string, body: unknown, options?: Omit<MockResponseOptions, "body" | "status">) {
      return this.mock("POST", path, { ...options, status: 200, body });
    },

    /** 安装 mock（替换 globalThis.fetch）。 */
    install(): void {
      globalThis.fetch = fetchHandler as typeof fetch;
    },

    /** 恢复真实 fetch。 */
    restore(): void {
      globalThis.fetch = originalFetch;
    },

    /** 清理所有已注册路由与调用记录。 */
    reset(): void {
      routes.clear();
    },

    wasCalled(method: string, path: string): boolean {
      return (routes.get(routeKey(method, path))?.callCount ?? 0) > 0;
    },

    getHandler(method: string, path: string): MockHandler | undefined {
      return routes.get(routeKey(method, path));
    },
  };
}

export type ApiMockServer = ReturnType<typeof createApiMockServer>;
export type { ApiRequestOptions };
