"use client";

import React, {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import {
  login as authLogin,
  logout as authLogout,
  onAuthFailure,
  subscribeToAuthStorage,
  subscribeToTokenChange,
  TokenStore,
  type AuthData,
} from "@/lib/auth";
import { api } from "@/lib/api";

export interface User {
  id: string;
  username: string;
  email: string;
  role: string;
  is_active?: boolean;
  created_at?: string;
}

export type AuthStatus = "bootstrapping" | "authenticated" | "refreshing" | "anonymous";

export interface AuthContextValue {
  user: User | null;
  token: string | null;
  status: AuthStatus;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue>({
  user: null,
  token: null,
  status: "bootstrapping",
  loading: true,
  login: async () => {},
  logout: () => {},
});

const ME_REQUEST_TIMEOUT = 10000;

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [status, setStatus] = useState<AuthStatus>("bootstrapping");

  // 退出登录：清除令牌、用户态，进入 anonymous（不跳转，由路由守卫处理）
  const logout = useCallback(() => {
    authLogout();
    setUser(null);
    setToken(null);
    setStatus("anonymous");
  }, []);

  // 同步本地 React 状态与统一令牌存储（登录/刷新/退出/跨标签页都会写存储）
  useEffect(() => {
    const syncFromStorage = () => {
      // token 可能为 null（已登出）——此时不读取，由 storage 事件/失败回调处理状态
      setToken(TokenStoreSnapshot());
    };
    const unsubscribe = subscribeToTokenChange(syncFromStorage);
    return unsubscribe;
  }, []);

  // 认证失败回调：一次清理 + 一次 anonymous 状态（跳转由路由守卫按状态触发）
  useEffect(() => {
    const unsubscribe = onAuthFailure(() => {
      setUser(null);
      setToken(null);
      setStatus("anonymous");
    });
    return unsubscribe;
  }, []);

  // 跨标签页同步：其它标签页登录/退出时收敛到同一状态
  useEffect(() => {
    const unsubscribe = subscribeToAuthStorage((event) => {
      if (event.type === "tokens-cleared") {
        setUser(null);
        setToken(null);
        setStatus("anonymous");
      } else if (event.type === "tokens-set") {
        setStatus("authenticated");
      }
    });
    return unsubscribe;
  }, []);

  // 初始化：bootstrapping -> 有 token 则拉取 /auth/me，无 token 则 anonymous
  useEffect(() => {
    const hasToken = TokenStoreSnapshot() !== null;
    if (!hasToken) {
      queueMicrotask(() => setStatus("anonymous"));
      return;
    }

    // 与仓库既有约定一致：避免 effect 内同步 setState，延迟到下一事件循环
    queueMicrotask(() => setStatus("authenticated"));

    // Safety timeout: if /auth/me takes > 10s, treat as failed
    const timeout = setTimeout(() => {
      authLogout();
      setUser(null);
      setToken(null);
      setStatus("anonymous");
    }, ME_REQUEST_TIMEOUT);

    api
      .get("/auth/me")
      .then((u) => {
        setUser(u);
      })
      .catch(() => {
        authLogout();
        setUser(null);
        setToken(null);
        setStatus("anonymous");
      })
      .finally(() => {
        clearTimeout(timeout);
      });
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const data: AuthData = await authLogin(username, password);
    setToken(data.access_token);
    setUser(data.user);
    setStatus("authenticated");
  }, []);

  const value = useMemo(
    () => ({ user, token, status, loading: status === "bootstrapping", login, logout }),
    [user, token, status, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** 统一令牌读取：仅经 TokenStore，其它模块不得直接读 localStorage（任务卡 §6）。 */
function TokenStoreSnapshot(): string | null {
  return TokenStore.getAccessToken();
}
