import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Toaster } from "sonner";

import { ParserProfileTab } from "@/components/parser-profile-tab";
import { createApiMockServer } from "@/lib/__mocks__/api-server";

const projectId = "project-1";

function renderTab() {
  return render(
    <>
      <Toaster />
      <ParserProfileTab projectId={projectId} />
    </>,
  );
}

describe("ParserProfile 安全合同", () => {
  let server: ReturnType<typeof createApiMockServer>;

  beforeEach(() => {
    server = createApiMockServer();
    server.install();
    server.onGet(
      `/projects/${projectId}/parser-profiles/?page=1&page_size=100`,
      { items: [], total: 0, page: 1, page_size: 100 },
    );
    server.onGet(`/projects/${projectId}/parser-profiles/endpoints`, {
      items: [
        {
          endpoint_ref: "mineru-official",
          display_name: "MinerU 官方",
          parser_name: "mineru",
          credential_configured: true,
        },
      ],
    });
  });

  afterEach(() => {
    server.restore();
    vi.clearAllMocks();
  });

  it("创建解析器时选择服务端端点而非填写 URL，且不出现网络字段输入框", async () => {
    server.onPost(`/projects/${projectId}/parser-profiles/`, {
      id: "new-profile",
      name: "生产 MinerU",
      parser_name: "mineru",
      parser_options: { endpoint_ref: "mineru-official" },
      is_default: false,
      version: 1,
    });
    const user = userEvent.setup();
    renderTab();

    // 打开新建对话框
    await user.click(await screen.findByRole("button", { name: /新建/ }));

    // 选择 MinerU（API）
    const parserSelect = screen.getByLabelText("解析器类型");
    await user.selectOptions(parserSelect, "mineru");

    // 应出现服务端端点下拉（从服务端安全列表加载），而非 API 地址输入框。
    const endpointSelect = screen.getByLabelText("服务端端点");
    await waitFor(() => {
      expect(screen.getByRole("option", { name: /MinerU 官方/ })).toBeInTheDocument();
    });
    await user.selectOptions(endpointSelect, "mineru-official");

    // 不应出现 base_url / upload_url / vlm_base_url 等自由网络字段。
    expect(screen.queryByLabelText(/API 地址/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/VLM 服务地址/)).not.toBeInTheDocument();

    // 保存 payload 只含 endpoint_ref + 功能参数，绝不含网络 URL。
    await user.type(screen.getByLabelText("名称"), "生产 MinerU");
    // 等待 React 状态更新（select 的 onChange 已设置 endpoint_ref）
    await waitFor(() => {
      expect(screen.getByLabelText("服务端端点")).toHaveValue("mineru-official");
    });
    const saveBtn = screen.getByRole("button", { name: /保存/ });
    await user.click(saveBtn);

    await waitFor(() => {
      expect(server.getHandler("POST", `/projects/${projectId}/parser-profiles/`)?.callCount).toBe(1);
    });
    const postHandler = server.getHandler("POST", `/projects/${projectId}/parser-profiles/`);
    const sent = JSON.parse(String(postHandler?.calls[0]?.options?.body));
    expect(sent.parser_options.endpoint_ref).toBe("mineru-official");
    expect(sent.parser_options.base_url).toBeUndefined();
    expect(sent.parser_options.upload_url).toBeUndefined();
    expect(sent.parser_options.vlm_base_url).toBeUndefined();
    expect(sent.parser_options.server_url).toBeUndefined();
  });

  it("端点列表展示凭证已配置状态，且不展示真实 Token", async () => {
    const user = userEvent.setup();
    renderTab();

    await user.click(await screen.findByRole("button", { name: /新建/ }));
    const parserSelect = screen.getByLabelText("解析器类型");
    await user.selectOptions(parserSelect, "mineru");

    // 凭证已配置标记出现在端点选项上
    await waitFor(() => {
      expect(screen.getByRole("option", { name: /凭证已配置/ })).toBeInTheDocument();
    });
    // 页面不应出现真实 Token 文本
    expect(screen.queryByText(/MINERU_API_TOKEN/)).not.toBeInTheDocument();
    expect(screen.queryByText(/sk-[A-Za-z0-9]/)).not.toBeInTheDocument();
  });

  it("保存后端返回 422 时展示字段级错误", async () => {
    const user = userEvent.setup();
    server.mock("POST", `/projects/${projectId}/parser-profiles/`, {
      status: 422,
      body: {
        code: "VALIDATION_ERROR",
        message: "unsafe_parser_option: 禁用字段",
        errors: [
          { loc: ["body", "parser_options", "base_url"], msg: "unsafe_parser_option: 禁用字段", type: "value_error" },
        ],
      },
    });
    renderTab();

    await user.click(await screen.findByRole("button", { name: /新建/ }));
    await user.type(screen.getByLabelText("名称"), "unsafe");
    await user.click(screen.getByRole("button", { name: /保存/ }));

    expect(await screen.findByText(/unsafe_parser_option/)).toBeInTheDocument();
  });

  it("编辑旧 profile 时不回显旧 URL，只显示 requires_endpoint_remap", async () => {
    server.onGet(`/projects/${projectId}/parser-profiles/?page=1&page_size=100`, {
      items: [
        {
          id: "p1",
          name: "legacy",
          parser_name: "mineru",
          parser_options: null,
          is_default: false,
          version: 1,
          requires_endpoint_remap: true,
        },
      ],
      total: 1,
      page: 1,
      page_size: 100,
    });
    const user = userEvent.setup();
    renderTab();

    // 列表展示 legacy 配置
    expect(await screen.findByText("legacy")).toBeInTheDocument();
    // 点击编辑
    await user.click(screen.getByRole("button", { name: /legacy/ }));
    // 不应回显任何 URL
    expect(screen.queryByLabelText(/API 地址/)).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue(/https?:\/\//)).not.toBeInTheDocument();
  });

  it("服务型解析器未注册端点时明确说明并禁止保存", async () => {
    server.onGet(`/projects/${projectId}/parser-profiles/endpoints`, { items: [] });
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: "新建" }));
    await user.selectOptions(screen.getByLabelText("解析器类型"), "mineru_local_service");
    expect(await screen.findByText(/尚未注册此解析器的服务端端点/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
  });

  it("纯本地 MinerU 无注册端点仍可创建", async () => {
    server.onGet(`/projects/${projectId}/parser-profiles/endpoints`, { items: [] });
    const created = server.onPost(`/projects/${projectId}/parser-profiles/`, { id: "local" });
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: "新建" }));
    await user.selectOptions(screen.getByLabelText("解析器类型"), "mineru_local");
    await user.type(screen.getByLabelText("名称"), "本地解析");
    expect(screen.queryByLabelText("服务端端点")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(created.callCount).toBe(1));
    expect(JSON.parse(created.calls[0].options?.body as string)).toMatchObject({ parser_name: "mineru_local", parser_options: null });
  });

  it("既有配置指向已移除的端点时必须重新选择", async () => {
    server.onGet(`/projects/${projectId}/parser-profiles/?page=1&page_size=100`, {
      items: [{ id: "p1", name: "旧端点配置", parser_name: "mineru", parser_options: { endpoint_ref: "removed" }, is_default: false, version: 1 }], total: 1,
    });
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: "旧端点配置" }));
    expect(await screen.findByText(/原配置的服务端端点已不可用/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    await user.selectOptions(screen.getByLabelText("服务端端点"), "mineru-official");
    expect(screen.getByRole("button", { name: "保存" })).not.toBeDisabled();
  });

  it("端点请求失败有重试入口，不误报未注册", async () => {
    server.mock("GET", `/projects/${projectId}/parser-profiles/endpoints`, { networkError: true });
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("button", { name: "新建" }));
    await user.selectOptions(screen.getByLabelText("解析器类型"), "paddleocr");
    expect(await screen.findByRole("button", { name: "重试端点列表" })).toBeInTheDocument();
    expect(screen.queryByText(/尚未注册此解析器的服务端端点/)).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保存" })).toBeDisabled();
    server.onGet(`/projects/${projectId}/parser-profiles/endpoints`, { items: [] });
    await user.click(screen.getByRole("button", { name: "重试端点列表" }));
    expect(await screen.findByText(/尚未注册此解析器的服务端端点/)).toBeInTheDocument();
  });
});
