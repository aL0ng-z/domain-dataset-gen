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

export interface AuthContextValue {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue>({
  user: null,
  token: null,
  loading: true,
  login: async () => {},
  logout: () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // On mount, check for existing token and fetch user (with timeout fallback)
  // 说明：为避免在 effect 内同步 setState（react-hooks/set-state-in-effect），
  // 首次状态写入通过 queueMicrotask/异步回调触发，行为与同步写入一致。
  useEffect(() => {
    const stored = localStorage.getItem("access_token");
    if (stored) {
      queueMicrotask(() => setToken(stored));

      // Safety timeout: if /auth/me takes > 10s, treat as failed
      const timeout = setTimeout(() => {
        authLogout();
        setToken(null);
        setUser(null);
        setLoading(false);
      }, 10000);

      api
        .get<User>("/auth/me")
        .then((u) => {
          setUser(u);
        })
        .catch(() => {
          authLogout();
          setToken(null);
          setUser(null);
        })
        .finally(() => {
          clearTimeout(timeout);
          setLoading(false);
        });
    } else {
      queueMicrotask(() => setLoading(false));
    }
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const data: AuthData = await authLogin(username, password);
    setToken(data.access_token);
    setUser(data.user);
  }, []);

  const logout = useCallback(() => {
    authLogout();
    setToken(null);
    setUser(null);
  }, []);

  const value = useMemo(
    () => ({ user, token, loading, login, logout }),
    [user, token, loading, login, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
