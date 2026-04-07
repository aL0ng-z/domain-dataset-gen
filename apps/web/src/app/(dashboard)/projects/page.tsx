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
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api } from "@/lib/api";
import { FolderOpenIcon, PlusIcon } from "lucide-react";
import { toast } from "sonner";

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
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [creating, setCreating] = useState(false);

  const fetchProjects = useCallback(() => {
    setLoading(true);
    setError(null);
    api
      .get<ProjectListResponse>("/projects/?page=1&page_size=50")
      .then((data) => setProjects(data.items))
      .catch((err) => {
        console.error("Failed to fetch projects:", err);
        setError(err.message);
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  async function handleCreate() {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await api.post("/projects/", { name: newName.trim(), description: newDesc.trim() || null });
      toast.success("项目创建成功");
      setDialogOpen(false);
      setNewName("");
      setNewDesc("");
      fetchProjects();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "创建失败");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold">项目列表</h1>
          <p className="text-sm text-muted-foreground mt-1">
            选择一个项目开始工作
          </p>
        </div>
        <Button onClick={() => setDialogOpen(true)}>
          <PlusIcon className="size-4" />
          新建项目
        </Button>
      </div>

      {loading ? (
        <div className="py-12 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : error ? (
        <div className="py-12 text-center text-sm text-destructive">
          加载失败: {error}
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

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>新建项目</DialogTitle>
          </DialogHeader>
          <div className="flex flex-col gap-4 py-2">
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium">项目名称</label>
              <Input
                placeholder="请输入项目名称"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <label className="text-sm font-medium">项目描述（可选）</label>
              <Textarea
                placeholder="请输入项目描述"
                value={newDesc}
                onChange={(e) => setNewDesc(e.target.value)}
                rows={3}
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>
              取消
            </Button>
            <Button onClick={handleCreate} disabled={creating || !newName.trim()}>
              {creating ? "创建中..." : "创建"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
