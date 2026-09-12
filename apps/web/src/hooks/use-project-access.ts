"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { subscribeToAuthStorage, subscribeToTokenChange } from "@/lib/auth";
import type { components } from "@/lib/api/generated";

type Role = components["schemas"]["ProjectAccessResponse"]["effective_role"];
const LEVEL: Record<Role, number> = { viewer: 0, editor: 1, reviewer: 2, admin: 3 };

/** 项目操作只依据服务器的有效项目角色；全局管理权限独立处理。 */
export function useProjectAccess(projectId: string) {
  const [access, setAccess] = useState<{ projectId: string; role: Role | null } | null>(null);
  useEffect(() => {
    let request = 0;
    let controller: AbortController | undefined;
    const load = () => {
      const current = ++request;
      controller?.abort();
      controller = new AbortController();
      void api.get("/projects/{pid}/access", {
        params: { pid: projectId }, signal: controller.signal,
      }).then((result) => {
        if (current === request) setAccess({ projectId, role: result.effective_role });
      }).catch(() => {
        if (current === request) setAccess({ projectId, role: null });
      });
    };
    const reload = () => { setAccess(null); load(); };
    load();
    const offTokens = subscribeToTokenChange(reload);
    const offStorage = subscribeToAuthStorage(reload);
    window.addEventListener("focus", reload);
    return () => {
      request += 1;
      controller?.abort();
      offTokens(); offStorage();
      window.removeEventListener("focus", reload);
    };
  }, [projectId]);
  const role = access?.projectId === projectId ? access.role : null;
  const level = role === null ? -1 : LEVEL[role];
  return { role, loading: access?.projectId !== projectId, canEdit: level >= 1, canReview: level >= 2 };
}
