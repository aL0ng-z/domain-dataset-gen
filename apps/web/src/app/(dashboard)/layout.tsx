"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { Sidebar } from "@/components/sidebar";
import { TaskFloatingPanel } from "@/components/task-floating-panel";
import { WsProvider } from "@/contexts/ws-context";
import { useAuth } from "@/hooks/use-auth";
import { usePathname } from "next/navigation";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, loading, status } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  // Extract project ID for WebSocket context
  const projectMatch = pathname.match(/\/projects\/([^/]+)/);
  const projectId = projectMatch?.[1];

  // Redirect to login if not authenticated
  React.useEffect(() => {
    if (status === "anonymous") {
      const next = window.location.pathname + window.location.search;
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [status, router]);

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-sm text-muted-foreground">加载中...</div>
      </div>
    );
  }

  if (!user) {
    // Redirecting to login — show loading instead of blank
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div className="text-sm text-muted-foreground">正在跳转...</div>
      </div>
    );
  }

  return (
    <WsProvider projectId={projectId}>
      <div className="flex h-screen overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">{children}</main>
        {projectId && <TaskFloatingPanel projectId={projectId} />}
      </div>
    </WsProvider>
  );
}
