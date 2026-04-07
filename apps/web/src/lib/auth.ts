export interface TokenData {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export interface AuthData {
  access_token: string;
  refresh_token: string;
  user: {
    id: string;
    username: string;
    email: string;
    role: string;
  };
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

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
  localStorage.setItem("access_token", tokenData.access_token);
  localStorage.setItem("refresh_token", tokenData.refresh_token);

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

export async function refreshToken(): Promise<boolean> {
  const rt = localStorage.getItem("refresh_token");
  if (!rt) return false;
  try {
    const res = await fetch(`${API_URL}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    localStorage.setItem("access_token", data.access_token);
    localStorage.setItem("refresh_token", data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

export function logout() {
  localStorage.removeItem("access_token");
  localStorage.removeItem("refresh_token");
}

export function getToken(): string | null {
  return localStorage.getItem("access_token");
}
