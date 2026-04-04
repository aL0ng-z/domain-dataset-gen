"use client";

import React from "react";
import { useParams } from "next/navigation";
import { ProjectTabs } from "@/components/project-tabs";

export default function ProjectLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  return (
    <div className="flex h-full flex-col">
      <ProjectTabs projectId={projectId} />
      <div className="flex-1 overflow-y-auto">{children}</div>
    </div>
  );
}
