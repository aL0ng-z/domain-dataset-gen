"use client";

import React, { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useAuth } from "@/hooks/use-auth";
import { Loader2Icon } from "lucide-react";

export default function LoginPage() {
  const { login } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      await login(username, password);
      const next = new URLSearchParams(window.location.search).get("next");
      // 只允许同站路径，禁止协议相对 URL 或反斜杠重定向。
      let destination = "/projects";
      if (next?.startsWith("/") && !next.startsWith("//") && !next.includes("\\")) {
        const target = new URL(next, window.location.origin);
        if (target.origin === window.location.origin && !target.pathname.startsWith("/login")) {
          destination = target.pathname + target.search + target.hash;
        }
      }
      router.push(destination);
    } catch (err) {
      setError(err instanceof Error ? err.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-muted/30 p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="text-center">
          <CardTitle className="text-xl">压气机知识提取平台</CardTitle>
          <CardDescription>请登录以继续</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div className="flex flex-col gap-1.5">
              <label htmlFor="username" className="text-sm font-medium">
                用户名
              </label>
              <Input
                id="username"
                type="text"
                placeholder="请输入用户名"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                required
                autoFocus
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="password" className="text-sm font-medium">
                密码
              </label>
              <Input
                id="password"
                type="password"
                placeholder="请输入密码"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </div>
            {error && (
              <div className="text-sm text-destructive">{error}</div>
            )}
            <Button type="submit" disabled={loading} className="w-full">
              {loading && <Loader2Icon className="size-4 animate-spin" />}
              登录
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
