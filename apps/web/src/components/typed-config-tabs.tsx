"use client";

import React, { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Loader2Icon, PencilIcon, PlusIcon } from "lucide-react";
import { api } from "@/lib/api";
import type { components } from "@/lib/api/generated";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { DataTable, type ColumnDef } from "@/components/data-table";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter, DialogDescription } from "@/components/ui/dialog";

type Schema = components["schemas"];
type ChunkProfile = Schema["ChunkProfileResponse"];
type ExportProfile = Schema["ExportProfileResponse"];
type TaskPolicy = Schema["TaskPolicyResponse"];
type NamedProfile = { id: string; name: string; is_default: boolean };
const query = { page: 1, page_size: 100 };
const selectClass = "w-full rounded border px-3 py-1.5 text-sm bg-transparent";

/** 只共享列表和保存状态；字段、DTO 及 API 路径由各类表单显式定义。 */
function useConfigEditor<T extends NamedProfile>(load: () => Promise<T[]>) {
  const [items, setItems] = useState<T[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [editItem, setEditItem] = useState<T | null>(null);
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const refresh = useCallback(async () => {
    setLoading(true);
    setLoadFailed(false);
    try {
      setItems(await load());
    } catch {
      setLoadFailed(true);
      toast.error("加载配置失败");
    } finally {
      setLoading(false);
    }
  }, [load]);
  useEffect(() => {
    const timer = window.setTimeout(() => void refresh(), 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);
  const persist = async (operation: () => Promise<unknown>, close = true) => {
    if (saving) return;
    setSaving(true);
    try {
      await operation();
      toast.success("保存成功");
      if (close) setOpen(false);
      await refresh();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };
  return { items, loading, loadFailed, refresh, editItem, setEditItem, open, setOpen, saving, persist };
}

function ProfileShell<T extends NamedProfile>({ label, editor, columns, onOpen, onSave, onDefault, children, note }: {
  label: string;
  editor: ReturnType<typeof useConfigEditor<T>>;
  columns: ColumnDef<T>[];
  onOpen: (item: T | null) => void;
  onSave: () => void;
  onDefault: (item: T) => void;
  children: React.ReactNode;
  note?: string;
}) {
  const tableColumns: ColumnDef<T>[] = [
    { key: "name", header: "名称", render: (row) => <button className="text-primary hover:underline" onClick={() => onOpen(row)}>{row.name}</button> },
    ...columns,
    { key: "default", header: "状态", render: (row) => row.is_default ? "默认" : "—" },
    { key: "actions", header: "操作", render: (row) => <div className="flex gap-2">
      <Button variant="ghost" size="xs" onClick={() => onOpen(row)}><PencilIcon className="size-3" />编辑</Button>
      {!row.is_default && <Button variant="outline" size="xs" disabled={editor.saving} onClick={() => onDefault(row)}>设为默认</Button>}
    </div> },
  ];
  return <div>
    {note && <p className="mb-3 text-sm text-muted-foreground">{note}</p>}
    <div className="mb-4 flex justify-end"><Button onClick={() => onOpen(null)}><PlusIcon className="size-4" />新建</Button></div>
    {editor.loading ? <div className="py-8 text-center text-sm text-muted-foreground">加载中...</div>
      : editor.loadFailed ? <div role="alert">加载配置失败。<Button variant="outline" onClick={() => void editor.refresh()}>重试</Button></div>
        : <DataTable columns={tableColumns} data={editor.items} total={editor.items.length} page={1} pageSize={100} onPageChange={() => {}} rowKey={(item) => item.id} />}
    <Dialog open={editor.open} onOpenChange={(open) => { if (!editor.saving) editor.setOpen(open); }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{editor.editItem ? "编辑" : "新建"}{label}</DialogTitle>
          <DialogDescription>保存当前配置；编辑时只更新修改过的字段。</DialogDescription>
        </DialogHeader>
        <form onSubmit={(event) => { event.preventDefault(); onSave(); }}>
          <fieldset disabled={editor.saving} className="space-y-3">{children}</fieldset>
          <DialogFooter className="mt-4"><Button type="submit" disabled={editor.saving}>
            {editor.saving && <Loader2Icon className="size-4 animate-spin" />}保存
          </Button></DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  </div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block space-y-1 text-sm font-medium"><span>{label}</span>{children}</label>;
}

function validName(name: string): string {
  if (!name.trim()) throw new Error("请输入名称");
  return name.trim();
}

function integer(value: string, label: string, minimum: number): number {
  const parsed = Number(value);
  if (!value.trim() || !Number.isSafeInteger(parsed) || parsed < minimum) throw new Error(`${label}必须是大于或等于 ${minimum} 的整数`);
  return parsed;
}

function jsonObject(value: string): Record<string, unknown> | null {
  if (!value.trim()) return null;
  let parsed: unknown;
  try { parsed = JSON.parse(value); } catch { throw new Error("JSON 格式无效，请输入对象或清空"); }
  if (parsed === null) return null;
  if (typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("JSON 参数必须是对象或 null");
  return parsed as Record<string, unknown>;
}

function differences<T extends object>(next: T, previous: T): Partial<T> {
  const result: Partial<T> = {};
  for (const key of Object.keys(next) as (keyof T)[]) {
    if (JSON.stringify(next[key]) !== JSON.stringify(previous[key])) result[key] = next[key];
  }
  return result;
}

type ChunkForm = { name: string; strategy: string; max_tokens: string; overlap_tokens: string; options: string };
function chunkForm(item: ChunkProfile | null): ChunkForm {
  return {
    name: item?.name ?? "", strategy: item?.strategy ?? "hybrid_heading_recursive",
    max_tokens: String(item?.max_tokens ?? 512), overlap_tokens: String(item?.overlap_tokens ?? 50),
    options: item?.options == null ? "" : JSON.stringify(item.options, null, 2),
  };
}
function chunkBody(form: ChunkForm): Schema["ChunkProfileCreate"] {
  const max_tokens = integer(form.max_tokens, "最大 Token 数", 1);
  const overlap_tokens = integer(form.overlap_tokens, "重叠 Token 数", 0);
  if (overlap_tokens >= max_tokens) throw new Error("重叠 Token 数必须小于最大 Token 数");
  return { name: validName(form.name), strategy: form.strategy, max_tokens, overlap_tokens, options: jsonObject(form.options) };
}

export function ChunkProfileTab({ projectId }: { projectId: string }) {
  const load = useCallback(async () => (await api.get("/projects/{pid}/chunk-profiles/", { params: { pid: projectId }, query })).items, [projectId]);
  const editor = useConfigEditor(load);
  const [form, setForm] = useState(() => chunkForm(null));
  const onOpen = (item: ChunkProfile | null) => { editor.setEditItem(item); setForm(chunkForm(item)); editor.setOpen(true); };
  const onSave = () => void editor.persist(async () => {
    const body = chunkBody(form);
    if (editor.editItem) {
      const patch: Schema["ChunkProfileUpdate"] = differences(body, chunkBody(chunkForm(editor.editItem)));
      if (Object.keys(patch).length) await api.patch("/projects/{pid}/chunk-profiles/{config_id}", patch, { params: { pid: projectId, config_id: editor.editItem.id } });
    } else await api.post("/projects/{pid}/chunk-profiles/", body, { params: { pid: projectId } });
  });
  return <ProfileShell label="切分配置" editor={editor} onOpen={onOpen} onSave={onSave}
    onDefault={(item) => void editor.persist(() => api.post("/projects/{pid}/chunk-profiles/{config_id}/set-default", undefined, { params: { pid: projectId, config_id: item.id } }), false)}
    columns={[
      { key: "strategy", header: "切分策略", render: (row) => row.strategy },
      { key: "max_tokens", header: "最大 Token 数", render: (row) => row.max_tokens },
      { key: "overlap_tokens", header: "重叠 Token 数", render: (row) => row.overlap_tokens },
    ]}>
    <Field label="名称"><Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field>
    <Field label="切分策略"><select className={selectClass} value={form.strategy} onChange={(event) => setForm({ ...form, strategy: event.target.value })}>
      <option value="hybrid_heading_recursive">标题与递归切分</option>
      {form.strategy !== "hybrid_heading_recursive" && <option value={form.strategy}>{form.strategy}（当前值）</option>}
    </select></Field>
    <Field label="最大 Token 数"><Input type="number" min={1} value={form.max_tokens} onChange={(event) => setForm({ ...form, max_tokens: event.target.value })} /></Field>
    <Field label="重叠 Token 数"><Input type="number" min={0} value={form.overlap_tokens} onChange={(event) => setForm({ ...form, overlap_tokens: event.target.value })} /></Field>
    <Field label="切分参数（JSON）"><Textarea className="min-h-24 font-mono text-xs" value={form.options} onChange={(event) => setForm({ ...form, options: event.target.value })} /></Field>
    <p className="text-xs text-muted-foreground">输入 JSON 对象；留空或 null 可清除参数。</p>
  </ProfileShell>;
}

const EXPORT_FORMATS = ["sft_jsonl", "qa_json", "messages", "alpaca", "sharegpt", "benchmark_json"];
type ExportForm = { name: string; format: string; template_options: string };
function exportForm(item: ExportProfile | null): ExportForm {
  return { name: item?.name ?? "", format: item?.format ?? "sft_jsonl", template_options: item?.template_options == null ? "" : JSON.stringify(item.template_options, null, 2) };
}
function exportBody(form: ExportForm): Schema["ExportProfileCreate"] {
  return { name: validName(form.name), format: form.format, template_options: jsonObject(form.template_options) };
}

export function ExportProfileTab({ projectId }: { projectId: string }) {
  const load = useCallback(async () => (await api.get("/projects/{pid}/export-profiles/", { params: { pid: projectId }, query })).items, [projectId]);
  const editor = useConfigEditor(load);
  const [form, setForm] = useState(() => exportForm(null));
  const onOpen = (item: ExportProfile | null) => { editor.setEditItem(item); setForm(exportForm(item)); editor.setOpen(true); };
  const onSave = () => void editor.persist(async () => {
    const body = exportBody(form);
    if (editor.editItem) {
      const patch: Schema["ExportProfileUpdate"] = differences(body, exportBody(exportForm(editor.editItem)));
      if (Object.keys(patch).length) await api.patch("/projects/{pid}/export-profiles/{config_id}", patch, { params: { pid: projectId, config_id: editor.editItem.id } });
    } else await api.post("/projects/{pid}/export-profiles/", body, { params: { pid: projectId } });
  });
  return <ProfileShell label="导出配置" editor={editor} onOpen={onOpen} onSave={onSave}
    onDefault={(item) => void editor.persist(() => api.post("/projects/{pid}/export-profiles/{config_id}/set-default", undefined, { params: { pid: projectId, config_id: item.id } }), false)}
    columns={[{ key: "format", header: "导出格式", render: (row) => row.format }]}>
    <Field label="名称"><Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field>
    <Field label="导出格式"><select className={selectClass} value={form.format} onChange={(event) => setForm({ ...form, format: event.target.value })}>
      {EXPORT_FORMATS.map((format) => <option key={format} value={format}>{format}</option>)}
      {!EXPORT_FORMATS.includes(form.format) && <option value={form.format}>{form.format}（当前值）</option>}
    </select></Field>
    <Field label="模板参数（JSON）"><Textarea className="min-h-24 font-mono text-xs" value={form.template_options} onChange={(event) => setForm({ ...form, template_options: event.target.value })} /></Field>
    <p className="text-xs text-muted-foreground">输入 JSON 对象；留空或 null 可清除参数。</p>
  </ProfileShell>;
}

const TASK_TYPES = { parse: "解析", clean: "清洗", chunk: "切分", generate: "单块生成", generate_batch: "批量生成", export: "导出" };
type TaskType = keyof typeof TASK_TYPES;
type TaskForm = { name: string; task_type: string; concurrency_limit: string; max_retries: string; timeout_seconds: string };
function taskForm(item: TaskPolicy | null): TaskForm {
  return { name: item?.name ?? "", task_type: item?.task_type ?? "parse", concurrency_limit: String(item?.concurrency_limit ?? 5), max_retries: String(item?.max_retries ?? 3), timeout_seconds: String(item?.timeout_seconds ?? 300) };
}
function taskBody(form: TaskForm): Schema["TaskPolicyCreate"] {
  if (!(form.task_type in TASK_TYPES)) throw new Error("请选择有效任务类型");
  return { name: validName(form.name), task_type: form.task_type as TaskType,
    concurrency_limit: integer(form.concurrency_limit, "最大并发", 1),
    max_retries: integer(form.max_retries, "重试次数", 0),
    timeout_seconds: integer(form.timeout_seconds, "超时", 1) };
}

export function TaskPolicyTab({ projectId }: { projectId: string }) {
  const load = useCallback(async () => (await api.get("/projects/{pid}/task-policies/", { params: { pid: projectId }, query })).items, [projectId]);
  const editor = useConfigEditor(load);
  const [form, setForm] = useState(() => taskForm(null));
  const onOpen = (item: TaskPolicy | null) => { editor.setEditItem(item); setForm(taskForm(item)); editor.setOpen(true); };
  const onSave = () => void editor.persist(async () => {
    const body = taskBody(form);
    if (editor.editItem) {
      const patch: Schema["TaskPolicyUpdate"] = differences(body, taskBody(taskForm(editor.editItem)));
      if (Object.keys(patch).length) await api.patch("/projects/{pid}/task-policies/{config_id}", patch, { params: { pid: projectId, config_id: editor.editItem.id } });
    } else await api.post("/projects/{pid}/task-policies/", body, { params: { pid: projectId } });
  });
  return <ProfileShell label="任务策略" editor={editor} onOpen={onOpen} onSave={onSave}
    note="每类任务只使用该类型的默认策略。重试次数和超时在创建新任务时生效；并发上限影响后续领取，不中断执行中的任务。重试次数为 0 时仅执行一次。"
    onDefault={(item) => void editor.persist(() => api.post("/projects/{pid}/task-policies/{config_id}/set-default", undefined, { params: { pid: projectId, config_id: item.id } }), false)}
    columns={[
      { key: "task_type", header: "任务类型", render: (row) => TASK_TYPES[row.task_type as TaskType] ?? row.task_type },
      { key: "concurrency_limit", header: "最大并发", render: (row) => row.concurrency_limit },
      { key: "max_retries", header: "重试次数", render: (row) => row.max_retries },
      { key: "timeout_seconds", header: "超时（秒）", render: (row) => row.timeout_seconds },
    ]}>
    <Field label="名称"><Input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></Field>
    <Field label="任务类型"><select className={selectClass} value={form.task_type} onChange={(event) => setForm({ ...form, task_type: event.target.value })}>
      {Object.entries(TASK_TYPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select></Field>
    <Field label="最大并发"><Input type="number" min={1} value={form.concurrency_limit} onChange={(event) => setForm({ ...form, concurrency_limit: event.target.value })} /></Field>
    <Field label="重试次数"><Input type="number" min={0} value={form.max_retries} onChange={(event) => setForm({ ...form, max_retries: event.target.value })} /></Field>
    <Field label="超时（秒）"><Input type="number" min={1} value={form.timeout_seconds} onChange={(event) => setForm({ ...form, timeout_seconds: event.target.value })} /></Field>
  </ProfileShell>;
}
