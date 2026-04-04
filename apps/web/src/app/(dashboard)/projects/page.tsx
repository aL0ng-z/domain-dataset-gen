"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api } from "@/lib/api";
import { FolderOpenIcon, PlusIcon } from "lucide-react";

interface Project {
  id: string;
  name: string;
  description?: string;
  created_at: string;
}

interface ProjectListResponse {
  items: Project[];
  total: number;
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchProjects = useCallback(() => {
    setLoading(true);
    api
      .get<ProjectListResponse>("/projects?page=1&page_size=50")
      .then((data) => setProjects(data.items))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">项目列表</h1>
          <p className="text-sm text-muted-foreground mt-1">
            选择一个项目开始工作
          </p>
        </div>
        <Button>
          <PlusIcon className="size-4" />
          新建项目
        </Button>
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : projects.length === 0 ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          暂无项目，请点击「新建项目」创建
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((project) => (
            <Link
              key={project.id}
              href={`/projects/${project.id}/documents`}
            >
              <Card className="transition-shadow hover:shadow-md cursor-pointer">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <FolderOpenIcon className="size-4" />
                    {project.name}
                  </CardTitle>
                  {project.description && (
                    <CardDescription>{project.description}</CardDescription>
                  )}
                </CardHeader>
                <CardContent>
                  <div className="text-xs text-muted-foreground">
                    创建于{" "}
                    {new Date(project.created_at).toLocaleDateString("zh-CN")}
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
