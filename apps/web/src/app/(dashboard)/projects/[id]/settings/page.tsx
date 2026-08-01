"use client";

import React, { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Tabs,
  TabsList,
  TabsTrigger,
  TabsContent,
} from "@/components/ui/tabs";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { api } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { PlusIcon, PencilIcon, Loader2Icon, ZapIcon } from "lucide-react";

type ModelConfig = components["schemas"]["ModelConfigResponse"];
type ParserProfile = components["schemas"]["ParserProfileResponse"];
type ChunkProfile = components["schemas"]["ChunkProfileResponse"];
type ExportProfile = components["schemas"]["ExportProfileResponse"];
type TaskPolicy = components["schemas"]["TaskPolicyResponse"];

/* ========= Shared types ========= */

/* ========= ModelConfig tab ========= */

const PROVIDER_PRESETS: Record<string, { base_url: string; models: string[] }> = {
  openai: {
    base_url: "https://api.openai.com/v1",
    models: ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
  },
  deepseek: {
    base_url: "https://api.deepseek.com/v1",
    models: ["deepseek-chat", "deepseek-reasoner"],
  },
  siliconflow: {
    base_url: "https://api.siliconflow.cn/v1",
    models: ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3", "Pro/deepseek-ai/DeepSeek-R1"],
  },
  openrouter: {
    base_url: "https://openrouter.ai/api/v1",
    models: ["openai/gpt-4o", "anthropic/claude-sonnet-4", "deepseek/deepseek-chat-v3-0324"],
  },
  vllm: {
    base_url: "http://localhost:8080/v1",
    models: [],
  },
  other: {
    base_url: "",
    models: [],
  },
};

const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  deepseek: "DeepSeek",
  siliconflow: "硅基流动 (SiliconFlow)",
  openrouter: "OpenRouter",
  vllm: "vLLM (本地)",
  other: "其他",
};

function ModelConfigTab({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<ModelConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editItem, setEditItem] = useState<ModelConfig | null>(null);
  const [form, setForm] = useState({
    name: "",
    provider: "deepseek",
    model_name: "deepseek-chat",
    base_url: "https://api.deepseek.com/v1",
    api_key: "",
    max_tokens: "" as string | number,
    temperature: "" as string | number,
  });
  const [testing, setTesting] = useState(false);
  const [useCustomModel, setUseCustomModel] = useState(false);

  const fetchItems = useCallback(() => {
    setLoading(true);
    api
      .get("/projects/{pid}/model-configs/", {
        params: { pid: projectId },
        query: { page: 1, page_size: 100 },
      })
      .then((data) => setItems(data.items))
      .catch(() => toast.error("加载模型配置失败"))
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => {
    const refreshTimer = window.setTimeout(() => {
      fetchItems();
    }, 0);
    return () => window.clearTimeout(refreshTimer);
  }, [fetchItems]);

  const handleProviderChange = (provider: string) => {
    const preset = PROVIDER_PRESETS[provider];
    setForm({
      ...form,
      provider,
      base_url: preset?.base_url || "",
      model_name: preset?.models[0] || "",
    });
    setUseCustomModel(false);
  };

  const openCreate = () => {
    setEditItem(null);
    const defaultProvider = "deepseek";
    const preset = PROVIDER_PRESETS[defaultProvider];
    setForm({
      name: "",
      provider: defaultProvider,
      model_name: preset.models[0] || "",
      base_url: preset.base_url,
      api_key: "",
      max_tokens: "",
      temperature: "",
    });
    setUseCustomModel(false);
    setDialogOpen(true);
  };

  const openEdit = (item: ModelConfig) => {
    setEditItem(item);
    const preset = PROVIDER_PRESETS[item.provider];
    const isKnownModel = preset?.models.includes(item.model_name);
    setForm({
      name: item.name,
      provider: item.provider,
      model_name: item.model_name,
      base_url: item.base_url || "",
      api_key: "",
      max_tokens: item.max_tokens ?? "",
      temperature: item.temperature ?? "",
    });
    setUseCustomModel(!isKnownModel && !!preset?.models.length);
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) {
      toast.error("请输入配置名称");
      return;
    }
    if (!form.base_url.trim()) {
      toast.error("请输入 API 地址");
      return;
    }
    if (!editItem && !form.api_key.trim()) {
      toast.error("请输入 API Key");
      return;
    }
    try {
      if (editItem) {
        const payload: components["schemas"]["ModelConfigUpdate"] = {
          name: form.name,
          provider: form.provider,
          model_name: form.model_name,
          base_url: form.base_url,
        };
        if (form.api_key.trim()) {
          payload.api_key = form.api_key;
        }
        if (form.max_tokens !== "" && form.max_tokens !== null) {
          payload.max_tokens = Number(form.max_tokens);
        }
        if (form.temperature !== "" && form.temperature !== null) {
          payload.temperature = Number(form.temperature);
        }
        await api.patch(
          "/projects/{pid}/model-configs/{config_id}",
          payload,
          { params: { pid: projectId, config_id: editItem.id } },
        );
        toast.success("保存成功");
        setDialogOpen(false);
      } else {
        const created = await api.post(
          "/projects/{pid}/model-configs/",
          {
            name: form.name,
            provider: form.provider,
            model_name: form.model_name,
            base_url: form.base_url,
            api_key: form.api_key,
            max_tokens: form.max_tokens !== "" ? Number(form.max_tokens) : undefined,
            temperature: form.temperature !== "" ? Number(form.temperature) : undefined,
          },
          { params: { pid: projectId } },
        );
        toast.success("创建成功，可点击「测试连接」验证");
        setEditItem(created); // Switch to edit mode so test button appears
      }
      fetchItems();
    } catch {
      toast.error("保存失败");
    }
  };

  const handleTestConnection = async () => {
    if (!editItem) {
      toast.error("请先保存配置后再测试连接");
      return;
    }
    setTesting(true);
    try {
      const res = await api.post(
        "/projects/{pid}/model-configs/{config_id}/test",
        undefined,
        { params: { pid: projectId, config_id: editItem.id } },
      );
      if (res.status === "success") {
        toast.success(`连接成功: ${res.response}`);
      } else {
        toast.error(`连接失败: ${res.error}`);
      }
    } catch {
      toast.error("连接测试失败");
    } finally {
      setTesting(false);
    }
  };

  const columns: ColumnDef<ModelConfig>[] = [
    {
      key: "name",
      header: "名称",
      render: (row) => (
        <button
          className="text-primary hover:underline"
          onClick={() => openEdit(row)}
        >
          {row.name}
        </button>
      ),
    },
    { key: "provider", header: "提供商", render: (row) => PROVIDER_LABELS[row.provider] || row.provider },
    { key: "model_name", header: "模型", render: (row) => row.model_name },
    { key: "base_url", header: "API 地址", render: (row) => <span className="text-xs text-muted-foreground truncate max-w-48 block">{row.base_url}</span> },
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <Button variant="ghost" size="xs" onClick={() => openEdit(row)}>
          <PencilIcon className="size-3" />
          编辑
        </Button>
      ),
    },
  ];

  const currentPreset = PROVIDER_PRESETS[form.provider];

  return (
    <div>
      <div className="flex justify-end mb-4">
        <Button onClick={openCreate}>
          <PlusIcon className="size-4" />
          新建
        </Button>
      </div>
      {loading ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={items}
          total={items.length}
          page={1}
          pageSize={100}
          onPageChange={() => {}}
          rowKey={(r) => r.id}
        />
      )}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              {editItem ? "编辑模型配置" : "新建模型配置"}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm font-medium">名称</label>
              <Input
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="例如：DeepSeek 生产"
              />
            </div>
            <div>
              <label className="text-sm font-medium">提供商</label>
              <select
                className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                value={form.provider}
                onChange={(e) => handleProviderChange(e.target.value)}
              >
                {Object.entries(PROVIDER_LABELS).map(([value, label]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-sm font-medium">API 地址</label>
              <Input
                value={form.base_url}
                onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                placeholder="https://api.example.com/v1"
              />
            </div>
            <div>
              <label className="text-sm font-medium">API Key</label>
              <Input
                type="password"
                value={form.api_key}
                onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                placeholder={editItem ? "留空则保持原 Key 不变" : "请输入 API Key"}
              />
            </div>
            <div>
              <label className="text-sm font-medium">模型名称</label>
              {currentPreset?.models.length && !useCustomModel ? (
                <select
                  className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                  value={form.model_name}
                  onChange={(e) => {
                    if (e.target.value === "__custom") {
                      setUseCustomModel(true);
                      setForm({ ...form, model_name: "" });
                    } else {
                      setForm({ ...form, model_name: e.target.value });
                    }
                  }}
                >
                  {currentPreset.models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                  <option value="__custom">自定义...</option>
                </select>
              ) : (
                <div className="flex gap-2">
                  <Input
                    value={form.model_name}
                    onChange={(e) => setForm({ ...form, model_name: e.target.value })}
                    placeholder="请输入模型名称"
                    autoFocus={useCustomModel}
                  />
                  {currentPreset?.models.length ? (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setUseCustomModel(false);
                        setForm({ ...form, model_name: currentPreset.models[0] || "" });
                      }}
                    >
                      选择
                    </Button>
                  ) : null}
                </div>
              )}
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-sm font-medium">最大 Token</label>
                <Input
                  type="number"
                  value={form.max_tokens}
                  onChange={(e) =>
                    setForm({ ...form, max_tokens: e.target.value === "" ? "" : Number(e.target.value) })
                  }
                  placeholder="默认由 API 决定"
                />
              </div>
              <div>
                <label className="text-sm font-medium">温度</label>
                <Input
                  type="number"
                  step="0.1"
                  min="0"
                  max="2"
                  value={form.temperature}
                  onChange={(e) =>
                    setForm({ ...form, temperature: e.target.value === "" ? "" : Number(e.target.value) })
                  }
                  placeholder="默认由 API 决定"
                />
              </div>
            </div>
          </div>
          <DialogFooter>
            {editItem && (
              <Button
                variant="outline"
                onClick={handleTestConnection}
                disabled={testing}
              >
                {testing ? (
                  <Loader2Icon className="size-4 animate-spin" />
                ) : (
                  <ZapIcon className="size-4" />
                )}
                测试连接
              </Button>
            )}
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ========= ParserProfile tab ========= */

const PARSER_PRESETS: Record<string, { label: string; needsApi: boolean; needsLocalModel: boolean; defaultUrl: string; defaultModelPath: string; defaultVlmUrl?: string }> = {
  pymupdf4llm: { label: "PyMuPDF4LLM（本地）", needsApi: false, needsLocalModel: false, defaultUrl: "", defaultModelPath: "" },
  mineru: { label: "MinerU（API）", needsApi: true, needsLocalModel: false, defaultUrl: "https://mineru.net/api/v4/extract/task", defaultModelPath: "" },
  mineru_local: { label: "MinerU2.5-Pro（本地模型）", needsApi: false, needsLocalModel: true, defaultUrl: "", defaultModelPath: "models/MinerU2.5-Pro-2604-1.2B" },
  mineru_local_service: { label: "MinerU（本地部署服务 / MLX）", needsApi: true, needsLocalModel: false, defaultUrl: "http://127.0.0.1:9010", defaultModelPath: "" },
  paddleocr: { label: "PaddleOCR（API）", needsApi: true, needsLocalModel: false, defaultUrl: "https://bea4c9v5r2i52ba7.aistudio-app.com/layout-parsing", defaultModelPath: "" },
  paddleocr_local_service: { label: "PaddleOCR-VL（本地部署服务 / MLX）", needsApi: true, needsLocalModel: false, defaultUrl: "http://127.0.0.1:9020/layout-parsing", defaultModelPath: "", defaultVlmUrl: "http://127.0.0.1:9021" },
};

function ParserProfileTab({ projectId }: { projectId: string }) {
  const [items, setItems] = useState<ParserProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editItem, setEditItem] = useState<ParserProfile | null>(null);
  const [form, setForm] = useState({
    name: "",
    parser_name: "pymupdf4llm",
    base_url: "",
    model_path: "",
    device_map: "auto",
    render_dpi: "160",
    image_analysis: false,
    service_backend: "vlm-auto-engine",
    server_url: "",
    vlm_base_url: "",
  });

  const fetchItems = useCallback(() => {
    setLoading(true);
    api
      .get("/projects/{pid}/parser-profiles/", {
        params: { pid: projectId },
        query: { page: 1, page_size: 100 },
      })
      .then((data) => setItems(data.items))
      .catch(() => toast.error("加载解析器配置失败"))
      .finally(() => setLoading(false));
  }, [projectId]);

  useEffect(() => {
    const refreshTimer = window.setTimeout(() => {
      fetchItems();
    }, 0);
    return () => window.clearTimeout(refreshTimer);
  }, [fetchItems]);

  const handleParserChange = (parserName: string) => {
    const preset = PARSER_PRESETS[parserName];
    setForm({
      ...form,
      parser_name: parserName,
      base_url: preset?.defaultUrl || "",
      model_path: preset?.defaultModelPath || "",
      device_map: "auto",
      render_dpi: "160",
      image_analysis: false,
      service_backend: "vlm-auto-engine",
      server_url: "",
      vlm_base_url: preset?.defaultVlmUrl || "",
    });
  };

  const openCreate = () => {
    setEditItem(null);
    setForm({ name: "", parser_name: "pymupdf4llm", base_url: "", model_path: "", device_map: "auto", render_dpi: "160", image_analysis: false, service_backend: "vlm-auto-engine", server_url: "", vlm_base_url: "" });
    setDialogOpen(true);
  };

  const openEdit = (item: ParserProfile) => {
    setEditItem(item);
    setForm({
      name: item.name,
      parser_name: item.parser_name,
      base_url: String(item.parser_options?.base_url || ""),
      model_path: String(item.parser_options?.model_path || PARSER_PRESETS[item.parser_name]?.defaultModelPath || ""),
      device_map: String(item.parser_options?.device_map || "auto"),
      render_dpi: String(item.parser_options?.render_dpi || "160"),
      image_analysis: item.parser_options?.image_analysis === true || item.parser_options?.image_analysis === "true",
      service_backend: String(item.parser_options?.backend || "vlm-auto-engine"),
      server_url: String(item.parser_options?.server_url || ""),
      vlm_base_url: String(item.parser_options?.vlm_base_url || PARSER_PRESETS[item.parser_name]?.defaultVlmUrl || ""),
    });
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.name.trim()) { toast.error("请输入配置名称"); return; }
    const preset = PARSER_PRESETS[form.parser_name];
    if (preset?.needsApi && !form.base_url.trim()) { toast.error("请输入 API 地址"); return; }
    if (preset?.needsLocalModel && !form.model_path.trim()) { toast.error("请输入本地模型目录"); return; }
    if (form.parser_name === "mineru_local_service" && form.service_backend === "vlm-http-client" && !form.server_url.trim()) {
      toast.error("请输入 VLM 服务地址");
      return;
    }
    if (form.parser_name === "paddleocr_local_service" && !form.vlm_base_url.trim()) {
      toast.error("请输入内部 MLX-VLM 服务地址");
      return;
    }

    const parser_options: Record<string, string | boolean> = {};
    if (preset?.needsApi) {
      parser_options.base_url = form.base_url;
    }
    if (preset?.needsLocalModel) {
      parser_options.model_path = form.model_path;
      parser_options.device_map = form.device_map;
      parser_options.render_dpi = form.render_dpi;
      parser_options.image_analysis = form.image_analysis;
    }
    if (form.parser_name === "mineru_local_service") {
      parser_options.backend = form.service_backend;
      parser_options.image_analysis = form.image_analysis;
      if (form.service_backend === "vlm-http-client") {
        parser_options.server_url = form.server_url;
      }
    }
    if (form.parser_name === "paddleocr_local_service") {
      parser_options.vlm_base_url = form.vlm_base_url;
      parser_options.auto_start = true;
      parser_options.use_layout_detection = true;
      parser_options.parse_timeout_seconds = "1800";
      parser_options.visualize = false;
    }

    const payload = {
      name: form.name,
      parser_name: form.parser_name,
      parser_options: Object.keys(parser_options).length > 0 ? parser_options : null,
    };

    try {
      if (editItem) {
        await api.patch("/projects/{pid}/parser-profiles/{config_id}", payload, {
          params: { pid: projectId, config_id: editItem.id },
        });
        toast.success("保存成功");
        setDialogOpen(false);
      } else {
        await api.post("/projects/{pid}/parser-profiles/", payload, {
          params: { pid: projectId },
        });
        toast.success("创建成功");
        setDialogOpen(false);
      }
      fetchItems();
    } catch { toast.error("保存失败"); }
  };

  const handleDelete = async (item: ParserProfile) => {
    if (!confirm(`确定要删除解析器配置「${item.name}」吗？`)) return;
    try {
      await api.delete("/projects/{pid}/parser-profiles/{config_id}", {
        params: { pid: projectId, config_id: item.id },
      });
      toast.success("已删除");
      fetchItems();
    } catch { toast.error("删除失败"); }
  };

  const currentPreset = PARSER_PRESETS[form.parser_name];

  const columns: ColumnDef<ParserProfile>[] = [
    {
      key: "name", header: "名称",
      render: (row) => (
        <button className="text-primary hover:underline" onClick={() => openEdit(row)}>
          {row.name}
        </button>
      ),
    },
    {
      key: "parser_name", header: "解析器",
      render: (row) => PARSER_PRESETS[row.parser_name]?.label || row.parser_name,
    },
    {
      key: "is_default", header: "默认",
      render: (row) => row.is_default ? "✓" : "",
    },
    {
      key: "actions", header: "操作",
      render: (row) => (
        <div className="flex gap-1">
          <Button variant="ghost" size="xs" onClick={() => openEdit(row)}>
            <PencilIcon className="size-3" /> 编辑
          </Button>
          <Button variant="ghost" size="xs" className="text-destructive hover:text-destructive" onClick={() => handleDelete(row)}>
            删除
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div>
      <div className="flex justify-end mb-4">
        <Button onClick={openCreate}><PlusIcon className="size-4" /> 新建</Button>
      </div>
      {loading ? (
        <div className="py-8 text-center text-sm text-muted-foreground">加载中...</div>
      ) : (
        <DataTable columns={columns} data={items} total={items.length} page={1} pageSize={100} onPageChange={() => {}} rowKey={(r) => r.id} />
      )}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{editItem ? "编辑解析器配置" : "新建解析器配置"}</DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm font-medium">名称</label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="例如：MinerU 生产" />
            </div>
            <div>
              <label className="text-sm font-medium">解析器类型</label>
              <select
                className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                value={form.parser_name}
                onChange={(e) => handleParserChange(e.target.value)}
              >
                {Object.entries(PARSER_PRESETS).map(([value, { label }]) => (
                  <option key={value} value={value}>{label}</option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground mt-1">
                {form.parser_name === "pymupdf4llm" && "本地解析，无需额外配置。基于 PyMuPDF 提取文本和结构。"}
                {form.parser_name === "mineru" && "使用 MinerU 精准解析 API；Token 由后端 MINERU_API_TOKEN 提供。"}
                {form.parser_name === "mineru_local" && "加载本机 models 目录内的 MinerU 权重，首次解析需要较长模型加载时间。"}
                {form.parser_name === "mineru_local_service" && "调用自有部署的 MinerU 服务；首次发起本机解析时后端会自动启动服务，当前 Mac 使用 MLX，未来 GPU 服务可接 vLLM；无需官方 Token。"}
                {form.parser_name === "paddleocr" && "使用 PaddleOCR 文档解析 API；Token 由后端 PADDLEOCR_API_TOKEN 提供。"}
                {form.parser_name === "paddleocr_local_service" && "调用自有部署的 PaddleX /layout-parsing 服务；首次本机解析会自动启动 PaddleX API 与内部 MLX-VLM 服务；无需官方 Token。"}
              </p>
            </div>
            {currentPreset?.needsApi && (
              <>
                <div>
                  <label className="text-sm font-medium">
                    {form.parser_name === "mineru_local_service" && "MinerU 服务地址"}
                    {form.parser_name === "paddleocr_local_service" && "PaddleX API 地址"}
                    {form.parser_name !== "mineru_local_service" && form.parser_name !== "paddleocr_local_service" && "API 地址"}
                  </label>
                  <Input
                    value={form.base_url}
                    onChange={(e) => setForm({ ...form, base_url: e.target.value })}
                    placeholder={currentPreset.defaultUrl}
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  {form.parser_name === "mineru_local_service" && "本地部署服务不使用官方 API Token；使用本机地址时，首次解析会自动启动服务。"}
                  {form.parser_name === "paddleocr_local_service" && "本地部署服务不使用官方 API Token；默认读取 models/PP-DocLayoutV3 与已转换的 MLX 权重。"}
                  {form.parser_name !== "mineru_local_service" && form.parser_name !== "paddleocr_local_service" && "API Token 仅在后端环境变量中配置，不通过网页保存或返回。"}
                </p>
              </>
            )}
            {form.parser_name === "paddleocr_local_service" && (
              <div>
                <label className="text-sm font-medium">内部 MLX-VLM 服务地址</label>
                <Input
                  value={form.vlm_base_url}
                  onChange={(e) => setForm({ ...form, vlm_base_url: e.target.value })}
                  placeholder="http://127.0.0.1:9021"
                />
                <p className="text-xs text-muted-foreground mt-1">
                  Mac 本机默认使用 MLX；未来迁移 GPU 后可将此地址改为 vLLM/FastDeploy 兼容服务，并保持外层 API 协议不变。
                </p>
              </div>
            )}
            {currentPreset?.needsLocalModel && (
              <>
                <div>
                  <label className="text-sm font-medium">本地模型目录</label>
                  <Input
                    value={form.model_path}
                    onChange={(e) => setForm({ ...form, model_path: e.target.value })}
                    placeholder={currentPreset.defaultModelPath}
                  />
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-sm font-medium">推理设备</label>
                    <select
                      className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                      value={form.device_map}
                      onChange={(e) => setForm({ ...form, device_map: e.target.value })}
                    >
                      <option value="auto">自动</option>
                      <option value="mps">Apple MPS</option>
                      <option value="cpu">CPU</option>
                    </select>
                  </div>
                  <div>
                    <label className="text-sm font-medium">页面 DPI</label>
                    <Input
                      type="number"
                      min={72}
                      value={form.render_dpi}
                      onChange={(e) => setForm({ ...form, render_dpi: e.target.value })}
                    />
                  </div>
                </div>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={form.image_analysis}
                    onChange={(e) => setForm({ ...form, image_analysis: e.target.checked })}
                  />
                  分析图片与图表
                </label>
              </>
            )}
            {form.parser_name === "mineru_local_service" && (
              <>
                <div>
                  <label className="text-sm font-medium">服务推理路径</label>
                  <select
                    className="w-full rounded border px-3 py-1.5 text-sm bg-transparent"
                    value={form.service_backend}
                    onChange={(e) => setForm({ ...form, service_backend: e.target.value })}
                  >
                    <option value="vlm-auto-engine">本机自动（Mac 使用 MLX）</option>
                    <option value="vlm-http-client">外部 VLM 服务（未来 GPU / vLLM）</option>
                  </select>
                </div>
                {form.service_backend === "vlm-http-client" && (
                  <div>
                    <label className="text-sm font-medium">VLM 服务地址</label>
                    <Input
                      value={form.server_url}
                      onChange={(e) => setForm({ ...form, server_url: e.target.value })}
                      placeholder="http://127.0.0.1:30000"
                    />
                  </div>
                )}
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={form.image_analysis}
                    onChange={(e) => setForm({ ...form, image_analysis: e.target.checked })}
                  />
                  分析图片与图表
                </label>
              </>
            )}
          </div>
          <DialogFooter>
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ========= Generic Config Tab ========= */
type ConfigRoutes = {
  "/projects/{pid}/chunk-profiles/": {
    item: ChunkProfile;
    itemPath: "/projects/{pid}/chunk-profiles/{config_id}";
    create: components["schemas"]["ChunkProfileCreate"];
    update: components["schemas"]["ChunkProfileUpdate"];
  };
  "/projects/{pid}/export-profiles/": {
    item: ExportProfile;
    itemPath: "/projects/{pid}/export-profiles/{config_id}";
    create: components["schemas"]["ExportProfileCreate"];
    update: components["schemas"]["ExportProfileUpdate"];
  };
  "/projects/{pid}/task-policies/": {
    item: TaskPolicy;
    itemPath: "/projects/{pid}/task-policies/{config_id}";
    create: components["schemas"]["TaskPolicyCreate"];
    update: components["schemas"]["TaskPolicyUpdate"];
  };
};

type ConfigEndpoint = keyof ConfigRoutes;

interface GenericConfigDescriptor<Endpoint extends ConfigEndpoint> {
  endpoint: Endpoint;
  label: string;
  extraFields: { key: string; label: string; type?: string }[];
  /** 从表单构建 create/update payload。endpoint 为联合时返回联合类型以匹配推断。 */
  buildPayload: (form: Record<string, string>) => ConfigRoutes[ConfigEndpoint]["create"];
}

// 注意：endpoint 使用 ConfigEndpoint 联合（而非泛型参数），使 api.patch/post
// 能通过 keyof paths 解析出该端点对应的请求体类型。
function GenericConfigTab({
  projectId,
  descriptor,
}: {
  projectId: string;
  descriptor: GenericConfigDescriptor<ConfigEndpoint>;
}) {
  const endpoint = descriptor.endpoint as ConfigEndpoint;
  const itemPath = (endpoint + "{config_id}") as ConfigRoutes[ConfigEndpoint]["itemPath"];
  const { label, extraFields, buildPayload } = descriptor;
  const [items, setItems] = useState<ConfigRoutes[ConfigEndpoint]["item"][]>([]);
  const [loading, setLoading] = useState(true);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editItem, setEditItem] = useState<ConfigRoutes[ConfigEndpoint]["item"] | null>(null);
  const [form, setForm] = useState<Record<string, string>>({
    name: "",
  });

  const fetchItems = useCallback(() => {
    setLoading(true);
    api
      .get(endpoint, {
        params: { pid: projectId },
        query: { page: 1, page_size: 100 },
      })
      .then((data) => setItems(data.items))
      .catch(() => toast.error(`加载${label}失败`))
      .finally(() => setLoading(false));
  }, [projectId, endpoint, label]);

  useEffect(() => {
    const refreshTimer = window.setTimeout(() => {
      fetchItems();
    }, 0);
    return () => window.clearTimeout(refreshTimer);
  }, [fetchItems]);

  const openCreate = () => {
    setEditItem(null);
    const defaults: Record<string, string> = { name: "" };
    extraFields.forEach((f) => {
      defaults[f.key] = "";
    });
    setForm(defaults);
    setDialogOpen(true);
  };

  const openEdit = (item: ConfigRoutes[ConfigEndpoint]["item"]) => {
    setEditItem(item);
    const vals: Record<string, string> = { name: item.name };
    extraFields.forEach((f) => {
      const raw = (item as Record<string, unknown>)[f.key];
      vals[f.key] = raw === undefined || raw === null ? "" : String(raw);
    });
    setForm(vals);
    setDialogOpen(true);
  };

  const handleSave = async () => {
    if (!form.name?.trim()) {
      toast.error("请输入名称");
      return;
    }
    try {
      const payload = buildPayload(form);
      if (editItem) {
        await api.patch(itemPath, payload, {
          params: { pid: projectId, config_id: editItem.id },
        });
      } else {
        await api.post(endpoint, payload, { params: { pid: projectId } });
      }
      toast.success("保存成功");
      setDialogOpen(false);
      fetchItems();
    } catch {
      toast.error("保存失败");
    }
  };

  const columns: ColumnDef<ConfigRoutes[ConfigEndpoint]["item"]>[] = [
    {
      key: "name",
      header: "名称",
      render: (row) => (
        <button
          className="text-primary hover:underline"
          onClick={() => openEdit(row)}
        >
          {row.name}
        </button>
      ),
    },
    ...(extraFields.map((f) => ({
      key: f.key,
      header: f.label,
      render: (row: ConfigRoutes[ConfigEndpoint]["item"]) =>
        String((row as Record<string, unknown>)[f.key] ?? "-"),
    })) || []),
    {
      key: "actions",
      header: "操作",
      render: (row) => (
        <Button variant="ghost" size="xs" onClick={() => openEdit(row)}>
          <PencilIcon className="size-3" />
          编辑
        </Button>
      ),
    },
  ];

  return (
    <div>
      <div className="flex justify-end mb-4">
        <Button onClick={openCreate}>
          <PlusIcon className="size-4" />
          新建
        </Button>
      </div>
      {loading ? (
        <div className="py-8 text-center text-sm text-muted-foreground">
          加载中...
        </div>
      ) : (
        <DataTable
          columns={columns}
          data={items}
          total={items.length}
          page={1}
          pageSize={100}
          onPageChange={() => {}}
          rowKey={(r) => r.id}
        />
      )}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {editItem ? `编辑${label}` : `新建${label}`}
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <label className="text-sm font-medium">名称</label>
              <Input
                value={form.name || ""}
                onChange={(e) =>
                  setForm({ ...form, name: e.target.value })
                }
              />
            </div>
            {extraFields.map((f) => (
              <div key={f.key}>
                <label className="text-sm font-medium">{f.label}</label>
                <Input
                  type={f.type || "text"}
                  value={form[f.key] || ""}
                  onChange={(e) =>
                    setForm({ ...form, [f.key]: e.target.value })
                  }
                />
              </div>
            ))}
          </div>
          <DialogFooter>
            <Button onClick={handleSave}>保存</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/* ========= Main Settings Page ========= */
export default function SettingsPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  return (
    <div className="p-6">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold">项目设置</h1>
        <p className="text-sm text-muted-foreground mt-1">
          管理各类配置文件
        </p>
      </div>

      <Tabs defaultValue="model">
        <TabsList>
          <TabsTrigger value="model">模型配置</TabsTrigger>
          <TabsTrigger value="parser">解析器</TabsTrigger>
          <TabsTrigger value="chunk">切分</TabsTrigger>
          <TabsTrigger value="export">导出</TabsTrigger>
          <TabsTrigger value="task">任务策略</TabsTrigger>
        </TabsList>

        <TabsContent value="model">
          <Card>
            <CardHeader>
              <CardTitle>模型配置 (ModelConfig)</CardTitle>
            </CardHeader>
            <CardContent>
              <ModelConfigTab projectId={projectId} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="parser">
          <Card>
            <CardHeader>
              <CardTitle>解析器配置 (ParserProfile)</CardTitle>
            </CardHeader>
            <CardContent>
              <ParserProfileTab projectId={projectId} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="chunk">
          <Card>
            <CardHeader>
              <CardTitle>切分配置 (ChunkProfile)</CardTitle>
            </CardHeader>
            <CardContent>
              <GenericConfigTab
                projectId={projectId}
                descriptor={{
                  endpoint: "/projects/{pid}/chunk-profiles/",
                  label: "切分配置",
                  extraFields: [
                    { key: "strategy", label: "切分策略" },
                    { key: "max_tokens", label: "最大Token数", type: "number" },
                    { key: "overlap_tokens", label: "重叠Token数", type: "number" },
                  ],
                  buildPayload: (form) => ({
                    name: form.name,
                    strategy: form.strategy || "hybrid_heading_recursive",
                    max_tokens: Number(form.max_tokens) || 512,
                    overlap_tokens: Number(form.overlap_tokens) || 50,
                  }),
                }}
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="export">
          <Card>
            <CardHeader>
              <CardTitle>导出配置 (ExportProfile)</CardTitle>
            </CardHeader>
            <CardContent>
              <GenericConfigTab
                projectId={projectId}
                descriptor={{
                  endpoint: "/projects/{pid}/export-profiles/",
                  label: "导出配置",
                  extraFields: [{ key: "format", label: "导出格式" }],
                  buildPayload: (form) => ({
                    name: form.name,
                    format: form.format || "sft_jsonl",
                  }),
                }}
              />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="task">
          <Card>
            <CardHeader>
              <CardTitle>任务策略 (TaskPolicy)</CardTitle>
            </CardHeader>
            <CardContent>
              <GenericConfigTab
                projectId={projectId}
                descriptor={{
                  endpoint: "/projects/{pid}/task-policies/",
                  label: "任务策略",
                  extraFields: [
                    { key: "task_type", label: "任务类型" },
                    { key: "max_retries", label: "重试次数", type: "number" },
                    { key: "timeout_seconds", label: "超时秒数", type: "number" },
                    { key: "concurrency_limit", label: "最大并发", type: "number" },
                  ],
                  buildPayload: (form) => ({
                    name: form.name,
                    task_type: form.task_type || "generate",
                    max_retries: Number(form.max_retries) || 3,
                    timeout_seconds: Number(form.timeout_seconds) || 300,
                    concurrency_limit: Number(form.concurrency_limit) || 5,
                  }),
                }}
              />
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}
