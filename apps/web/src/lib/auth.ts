export interface AuthData {
  access_token: string;
  refresh_token: string;
  user: {
    id: string;
    username: string;
    display_name: string;
    role: string;
  };
}

export async function login(
  username: string,
  password: string
): Promise<AuthData> {
  const res = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "登录失败" }));
    throw new Error(err.detail || "登录失败");
  }
  const data: AuthData = await res.json();
  localStorage.setItem("access_token", data.access_token);
  localStorage.setItem("refresh_token", data.refresh_token);
  return data;
}

export async function refreshToken(): Promise<boolean> {
  const rt = localStorage.getItem("refresh_token");
  if (!rt) return false;
  try {
    const res = await fetch("/api/auth/refresh", {
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
