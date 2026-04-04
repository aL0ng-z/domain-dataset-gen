"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

interface TabDef {
  key: string;
  label: string;
  segment: string;
}

const TABS: TabDef[] = [
  { key: "documents", label: "文档", segment: "documents" },
  { key: "templates", label: "模板", segment: "templates" },
  { key: "candidates", label: "候选", segment: "candidates" },
  { key: "curated", label: "知识资产", segment: "curated" },
  { key: "datasets", label: "数据集", segment: "datasets" },
  { key: "benchmarks", label: "基准集", segment: "benchmarks" },
  { key: "exports", label: "导出", segment: "exports" },
  { key: "tasks", label: "任务", segment: "tasks" },
  { key: "monitoring", label: "监控", segment: "monitoring" },
  { key: "settings", label: "设置", segment: "settings" },
];

interface ProjectTabsProps {
  projectId: string;
}

export function ProjectTabs({ projectId }: ProjectTabsProps) {
  const pathname = usePathname();

  return (
    <div className="border-b">
      <nav className="flex gap-0 overflow-x-auto px-4">
        {TABS.map((tab) => {
          const href = `/projects/${projectId}/${tab.segment}`;
          const isActive = pathname.startsWith(href);
          return (
            <Link
              key={tab.key}
              href={href}
              className={cn(
                "relative inline-flex items-center px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors hover:text-foreground",
                isActive
                  ? "text-foreground"
                  : "text-muted-foreground"
              )}
            >
              {tab.label}
              {isActive && (
                <span className="absolute inset-x-0 bottom-0 h-0.5 bg-foreground rounded-full" />
              )}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
