import React from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createApiMockServer } from "@/lib/__mocks__/api-server";
import SettingsPage from "./page";

vi.mock("next/navigation", () => ({ useParams: () => ({ id: "p1" }) }));

describe("项目设置 DTO 保存", () => {
  let server: ReturnType<typeof createApiMockServer>;
  beforeEach(() => {
    server = createApiMockServer();
    server.install();
    for (const resource of ["model-configs", "chunk-profiles", "export-profiles", "task-policies", "parser-profiles"]) {
      server.onGet(`/projects/p1/${resource}/?page=1&page_size=100`, { items: [], total: 0 });
    }
    server.onGet("/projects/p1/parser-profiles/endpoints", { items: [] });
  });
  afterEach(() => server.restore());

  async function openTab(tab: string) {
    render(<SettingsPage />);
    fireEvent.click(screen.getByRole("tab", { name: tab }));
    await waitFor(() => expect(server.wasCalled("GET", `/projects/p1/${tab === "切分" ? "chunk-profiles" : tab === "导出" ? "export-profiles" : "task-policies"}/?page=1&page_size=100`)).toBe(true));
  }

  it("切分配置仅改名称时不重置既有 overlap 和 JSON 参数", async () => {
    server.onGet("/projects/p1/chunk-profiles/?page=1&page_size=100", {
      items: [{ id: "c1", name: "旧配置", strategy: "hybrid_heading_recursive", max_tokens: 768, overlap_tokens: 123, options: { keep: true }, is_default: false }], total: 1,
    });
    const patch = server.mock("PATCH", "/projects/p1/chunk-profiles/c1", { body: {} });
    await openTab("切分");
    fireEvent.click(await screen.findByRole("button", { name: "旧配置" }));
    fireEvent.change(within(screen.getByRole("dialog")).getAllByRole("textbox")[0], { target: { value: "新名称" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(patch.callCount).toBe(1));
    expect(JSON.parse(patch.calls[0].options?.body as string)).toEqual({ name: "新名称" });
  });

  it("任务策略创建与回读保留并发 1 和重试 0，且只使用真实字段", async () => {
    const body = { id: "t1", name: "不重试", task_type: "generate", concurrency_limit: 1, max_retries: 0, timeout_seconds: 45, is_default: false };
    const post = server.mock("POST", "/projects/p1/task-policies/", () => {
      server.onGet("/projects/p1/task-policies/?page=1&page_size=100", { items: [body], total: 1 });
      return { body };
    });
    await openTab("任务策略");
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "不重试" } });
    fireEvent.change(screen.getByLabelText("任务类型"), { target: { value: "generate" } });
    fireEvent.change(screen.getByLabelText("最大并发"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("重试次数"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("超时（秒）"), { target: { value: "45" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(post.callCount).toBe(1));
    expect(JSON.parse(post.calls[0].options?.body as string)).toEqual({
      name: "不重试", task_type: "generate", concurrency_limit: 1, max_retries: 0, timeout_seconds: 45,
    });
    fireEvent.click(await screen.findByRole("button", { name: "不重试" }));
    expect(screen.getByLabelText("最大并发")).toHaveValue(1);
    expect(screen.getByLabelText("重试次数")).toHaveValue(0);
    expect(screen.getByLabelText("任务类型")).toHaveValue("generate");
  });

  it.each([
    { tab: "切分", resource: "chunk-profiles", field: "options", label: "切分参数（JSON）", values: { strategy: "hybrid_heading_recursive", max_tokens: 512, overlap_tokens: 0 } },
    { tab: "导出", resource: "export-profiles", field: "template_options", label: "模板参数（JSON）", values: { format: "qa_json" } },
  ])("$tab JSON 保存后回读并可显式清空", async ({ tab, resource, field, label, values }) => {
    let row = { id: "c1", name: "参数配置", is_default: false, ...values, [field]: { source: "初始" } };
    const list = () => server.onGet(`/projects/p1/${resource}/?page=1&page_size=100`, { items: [row], total: 1 });
    list();
    const patch = server.mock("PATCH", `/projects/p1/${resource}/c1`, ({ init }) => {
      row = { ...row, ...JSON.parse(init?.body as string) };
      list();
      return { body: row };
    });
    await openTab(tab);
    fireEvent.click(await screen.findByRole("button", { name: "参数配置" }));
    expect(JSON.parse((screen.getByLabelText(label) as HTMLTextAreaElement).value)).toEqual({ source: "初始" });
    fireEvent.change(screen.getByLabelText(label), { target: { value: '{"keep":true,"size":0}' } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(patch.callCount).toBe(1));
    expect(JSON.parse(patch.calls[0].options?.body as string)).toEqual({ [field]: { keep: true, size: 0 } });
    fireEvent.click(await screen.findByRole("button", { name: "参数配置" }));
    expect(JSON.parse((screen.getByLabelText(label) as HTMLTextAreaElement).value)).toEqual({ keep: true, size: 0 });
    fireEvent.change(screen.getByLabelText(label), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(patch.callCount).toBe(2));
    expect(JSON.parse(patch.calls[1].options?.body as string)).toEqual({ [field]: null });
  });

  it("任务策略设为默认调用独立接口并刷新状态", async () => {
    const row = { id: "t1", name: "排队策略", task_type: "generate_batch", concurrency_limit: 2, max_retries: 0, timeout_seconds: 45, is_default: false };
    server.onGet("/projects/p1/task-policies/?page=1&page_size=100", { items: [row], total: 1 });
    const activate = server.mock("POST", "/projects/p1/task-policies/t1/set-default", () => {
      server.onGet("/projects/p1/task-policies/?page=1&page_size=100", { items: [{ ...row, is_default: true }], total: 1 });
      return { body: { ...row, is_default: true } };
    });
    await openTab("任务策略");
    fireEvent.click(await screen.findByRole("button", { name: "设为默认" }));
    await waitFor(() => expect(activate.callCount).toBe(1));
    expect(await screen.findByText("默认")).toBeInTheDocument();
    expect(screen.getByText(/重试次数为 0 时仅执行一次/)).toBeInTheDocument();
  });

  it("任务策略仅改名称时保留既有零次重试和单任务并发", async () => {
    server.onGet("/projects/p1/task-policies/?page=1&page_size=100", {
      items: [{ id: "t1", name: "严格策略", task_type: "export", concurrency_limit: 1, max_retries: 0, timeout_seconds: 37, is_default: true }], total: 1,
    });
    const patch = server.mock("PATCH", "/projects/p1/task-policies/t1", { body: {} });
    await openTab("任务策略");
    fireEvent.click(await screen.findByRole("button", { name: "严格策略" }));
    expect(screen.getByLabelText("重试次数")).toHaveValue(0);
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "重命名策略" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(patch.callCount).toBe(1));
    expect(JSON.parse(patch.calls[0].options?.body as string)).toEqual({ name: "重命名策略" });
  });

  it.each(["{未完成", "[]", "123"])("无效 JSON 参数 %s 阻止请求且保留表单供修正", async (value) => {
    const post = server.mock("POST", "/projects/p1/export-profiles/", { body: {} });
    await openTab("导出");
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "导出新配置" } });
    fireEvent.change(screen.getByLabelText("模板参数（JSON）"), { target: { value } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存" })).not.toBeDisabled());
    expect(post.callCount).toBe(0);
    expect(screen.getByLabelText("名称")).toHaveValue("导出新配置");
    expect(screen.getByLabelText("模板参数（JSON）")).toHaveValue(value);
  });
});
