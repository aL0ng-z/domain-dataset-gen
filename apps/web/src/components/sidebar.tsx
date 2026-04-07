"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  FolderOpenIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  LogOutIcon,
  UserIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useAuth } from "@/hooks/use-auth";

interface Project {
  id: string;
  name: string;
}

interface ProjectListResponse {
  items: Project[];
  total: number;
}

export function Sidebar() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);

  useEffect(() => {
    api
      .get<ProjectListResponse>("/projects?page=1&page_size=50")
      .then((data) => setProjects(data.items))
      .catch(() => {
        // silently fail - user may not be logged in
      });
  }, []);

  // Extract active project id from path
  const projectMatch = pathname.match(/\/projects\/([^/]+)/);
  const activeProjectId = projectMatch?.[1];

  const roleLabels: Record<string, string> = {
    admin: "管理员",
    leader: "组长",
    annotator: "标注员",
    viewer: "访客",
  };

  return (
    <aside
      className={cn(
        "flex h-full flex-col border-r bg-sidebar text-sidebar-foreground transition-all duration-200",
        collapsed ? "w-14" : "w-[250px]"
      )}
    >
      {/* Header */}
      <div className="flex h-12 items-center justify-between px-3">
        {!collapsed && (
          <Link href="/projects" className="text-sm font-semibold truncate hover:text-primary transition-colors">
            DTRC-KE
          </Link>
        )}
        <Button
          variant="ghost"
          size="icon-xs"
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed ? <ChevronRightIcon /> : <ChevronLeftIcon />}
        </Button>
      </div>

      <Separator />

      {/* Project list */}
      <ScrollArea className="flex-1">
        <div className="p-2">
          {!collapsed && (
            <div className="mb-1 px-2 text-xs font-medium text-muted-foreground">
              项目列表
            </div>
          )}
          {projects.map((project) => (
            <Link
              key={project.id}
              href={`/projects/${project.id}/documents`}
            >
              <div
                className={cn(
                  "flex items-center gap-2 rounded-md px-2 py-1.5 text-sm transition-colors hover:bg-sidebar-accent",
                  activeProjectId === project.id &&
                    "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                )}
              >
                <FolderOpenIcon className="size-4 shrink-0" />
                {!collapsed && (
                  <span className="truncate">{project.name}</span>
                )}
              </div>
            </Link>
          ))}
          {projects.length === 0 && !collapsed && (
            <div className="px-2 py-4 text-center text-xs text-muted-foreground">
              暂无项目
            </div>
          )}
        </div>
      </ScrollArea>

      <Separator />

      {/* User menu */}
      <div className="p-2">
        {user ? (
          <div
            className={cn(
              "flex items-center gap-2",
              collapsed ? "justify-center" : "px-2"
            )}
          >
            <UserIcon className="size-4 shrink-0" />
            {!collapsed && (
              <div className="flex flex-1 items-center justify-between min-w-0">
                <div className="min-w-0">
                  <div className="text-sm font-medium truncate">
                    {user.username}
                  </div>
                  <Badge variant="secondary" className="text-[10px]">
                    {roleLabels[user.role] || user.role}
                  </Badge>
                </div>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  onClick={logout}
                  className="shrink-0"
                >
                  <LogOutIcon />
                </Button>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </aside>
  );
}
