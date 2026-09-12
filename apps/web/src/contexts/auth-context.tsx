"use client";

import React, {
  createContext,
  useCallback,
  useEffect,
  useMemo,
  useRef,
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
  const [state, setState] = useState<{ user: User | null; token: string | null; status: AuthStatus }>({
    user: null, token: null, status: "bootstrapping",
  });
  const requestGeneration = useRef(0);
  const pending = useRef<AbortController | null>(null);

  const invalidate = useCallback(() => {
    requestGeneration.current += 1;
    pending.current?.abort();
    pending.current = null;
    return requestGeneration.current;
  }, []);

  const becomeAnonymous = useCallback(() => {
    invalidate();
    setState({ user: null, token: null, status: "anonymous" });
  }, [invalidate]);

  const logout = useCallback(() => {
    becomeAnonymous();
    authLogout();
  }, [becomeAnonymous]);

  const restoreSession = useCallback(async () => {
    const generation = invalidate();
    const access = TokenStore.getAccessToken();
    if (!access) {
      setState({ user: null, token: null, status: "anonymous" });
      return;
    }
    const controller = new AbortController();
    pending.current = controller;
    setState({ user: null, token: access, status: "bootstrapping" });
    try {
      const user = await api.get("/auth/me", { signal: controller.signal, timeout: ME_REQUEST_TIMEOUT });
      if (generation !== requestGeneration.current || controller.signal.aborted) return;
      setState({ user, token: TokenStore.getAccessToken(), status: "authenticated" });
    } catch {
      if (generation !== requestGeneration.current || controller.signal.aborted) return;
      logout();
    } finally {
      if (pending.current === controller) pending.current = null;
    }
  }, [invalidate, logout]);

  useEffect(() => {
    // React StrictMode replay and unmount must invalidate scheduled/ongoing work.
    let active = true;
    const initialGeneration = requestGeneration.current;
    queueMicrotask(() => {
      if (active && requestGeneration.current === initialGeneration) void restoreSession();
    });
    const offTokens = subscribeToTokenChange(() => {
      const access = TokenStore.getAccessToken();
      if (!access) becomeAnonymous();
      else setState((previous) => ({ ...previous, token: access }));
    });
    const offFailure = onAuthFailure(becomeAnonymous);
    const offStorage = subscribeToAuthStorage((event) => {
      if (event.type === "tokens-cleared") becomeAnonymous();
      else void restoreSession();
    });
    return () => {
      active = false;
      invalidate();
      offTokens();
      offFailure();
      offStorage();
    };
  }, [becomeAnonymous, invalidate, restoreSession]);

  const login = useCallback(async (username: string, password: string) => {
    authLogout();
    const generation = invalidate();
    const controller = new AbortController();
    pending.current = controller;
    setState({ user: null, token: null, status: "bootstrapping" });
    try {
      const data: AuthData = await authLogin(username, password, { signal: controller.signal });
      if (generation !== requestGeneration.current || controller.signal.aborted) {
        throw new DOMException("Login cancelled", "AbortError");
      }
      setState({ user: data.user, token: data.access_token, status: "authenticated" });
    } catch (error) {
      if (generation === requestGeneration.current) becomeAnonymous();
      throw error;
    } finally {
      if (pending.current === controller) pending.current = null;
    }
  }, [becomeAnonymous, invalidate]);

  const value = useMemo(
    () => ({ ...state, loading: state.status === "bootstrapping", login, logout }),
    [state, login, logout],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
