// Playwright CLI `run-code --filename` script.
// Prerequisite: run-review-workflow.ps1 started API :18000, runner, stub and Web :3100;
// sign in at http://localhost:3100 as admin/admin123 before invoking this script.
async (page) => {
  const apiBase = "http://127.0.0.1:18000/api";
  const result = await page.evaluate(async ({ apiBase }) => {
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const token = localStorage.getItem("access_token");
    if (!token) throw new Error("浏览器没有登录会话；请先通过登录页登录 admin/admin123");
    const uid = crypto.randomUUID().slice(0, 8);
    const cleanText = "# Compressor\nA compressor increases pressure for the working fluid.\n";

    async function api(path, options = {}) {
      const headers = { Authorization: `Bearer ${token}`, ...(options.headers || {}) };
      let body = options.body;
      if (options.json !== undefined) {
        headers["Content-Type"] = "application/json";
        body = JSON.stringify(options.json);
      }
      const response = await fetch(`${apiBase}${path}`, { method: options.method || "GET", headers, body });
      const raw = await response.text();
      let data = null;
      try { data = raw ? JSON.parse(raw) : null; } catch { data = raw; }
      if (!response.ok) {
        const detail = typeof data === "object" && data ? (data.message || data.detail || JSON.stringify(data)) : raw;
        throw new Error(`${options.method || "GET"} ${path} -> ${response.status}: ${detail}`);
      }
      return data;
    }

    async function waitTask(pid, taskId, label) {
      for (let attempt = 0; attempt < 180; attempt += 1) {
        const task = await api(`/projects/${pid}/tasks/${taskId}`);
        if (task.status === "completed") return task;
        if (task.status === "failed" || task.status === "cancelled") {
          throw new Error(`${label} 任务终态为 ${task.status}: ${task.error_code || ""} ${task.error_message || ""}`);
        }
        await sleep(500);
      }
      throw new Error(`${label} 超时等待任务 ${taskId}`);
    }

    function makePdf(text) {
      const encoder = new TextEncoder();
      const escaped = text.replace(/[\\()]/g, "\\$&");
      const stream = `BT /F1 16 Tf 72 720 Td (${escaped}) Tj ET`;
      const objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        `<< /Length ${encoder.encode(stream).length} >>\nstream\n${stream}\nendstream`,
      ];
      let pdf = "%PDF-1.4\n";
      const offsets = [0];
      for (let i = 0; i < objects.length; i += 1) {
        offsets.push(encoder.encode(pdf).length);
        pdf += `${i + 1} 0 obj\n${objects[i]}\nendobj\n`;
      }
      const start = encoder.encode(pdf).length;
      pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
      for (const offset of offsets.slice(1)) pdf += `${String(offset).padStart(10, "0")} 00000 n \n`;
      pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${start}\n%%EOF\n`;
      return new Blob([encoder.encode(pdf)], { type: "application/pdf" });
    }

    async function upload(pid, name, text) {
      const form = new FormData();
      form.set("file", new File([makePdf(text)], name, { type: "application/pdf" }));
      return api(`/projects/${pid}/documents/upload`, { method: "POST", body: form });
    }

    async function parse(pid, documentId, profileId, key) {
      const accepted = await api(`/projects/${pid}/documents/${documentId}/parse`, {
        method: "POST", json: { parser_profile_id: profileId }, headers: { "Idempotency-Key": key },
      });
      await waitTask(pid, accepted.task_id, `解析 ${documentId}`);
      const jobs = await api(`/projects/${pid}/documents/${documentId}/parse-jobs`);
      const completed = jobs.filter((job) => job.status === "completed");
      if (!completed.length) throw new Error("解析完成后没有 completed ParseJob");
      return completed[0];
    }

    async function cleanAndAccept(pid, documentId, parseJobId, text, key) {
      const started = await api(`/projects/${pid}/documents/${documentId}/cleaning/start`, {
        method: "POST", json: { parse_job_id: parseJobId }, headers: { "Idempotency-Key": key },
      });
      await waitTask(pid, started.task_id, `清洗 ${documentId}`);
      const sections = await api(`/projects/${pid}/documents/${documentId}/sections?cleaning_job_id=${started.cleaning_job_id}&page=1&page_size=100`);
      if (!sections.items.length) throw new Error("清洗完成后没有 Section");
      for (const [index, section] of sections.items.entries()) {
        const lease = await api(`/sections/${section.id}/lease/acquire`, { method: "POST" });
        const updated = await api(`/sections/${section.id}`, {
          method: "PATCH",
          json: { cleaned_markdown: index === 0 ? text : "", expected_revision: section.content_revision, lease_id: lease.id },
        });
        const submitted = await api(`/sections/${section.id}/submit`, {
          method: "POST", json: { expected_revision: updated.content_revision, lease_id: lease.id },
        });
        await api(`/sections/${section.id}/review`, { method: "POST", json: { action: "accept", note: "e2e accept" } });
        if (submitted.status !== "review_pending") throw new Error("Section 未进入审核状态");
      }
      const version = await api(`/projects/${pid}/documents/${documentId}/cleaning/merge?cleaning_job_id=${started.cleaning_job_id}`, {
        method: "POST", headers: { "Idempotency-Key": `${key}-merge` },
      });
      return api(`/projects/${pid}/documents/${documentId}/cleaning/final-review?cleaning_job_id=${started.cleaning_job_id}`, {
        method: "POST", json: { version_id: version.id, action: "accept", comment: "e2e accept" },
      });
    }

    async function chunk(pid, documentId, profileId, key) {
      const accepted = await api(`/projects/${pid}/documents/${documentId}/chunk`, {
        method: "POST", json: { chunk_profile_id: profileId }, headers: { "Idempotency-Key": key },
      });
      await waitTask(pid, accepted.task_id, `切分 ${documentId}`);
      const chunks = await api(`/projects/${pid}/documents/${documentId}/chunks?page=1&page_size=100&status=ready`);
      if (!chunks.items.length) throw new Error("切分完成后没有 ready Chunk");
      return { chunkSetId: accepted.chunk_set_id, chunk: chunks.items[0] };
    }

    const projects = await api("/projects/?page=1&page_size=100");
    const project = projects.items[0];
    if (!project) throw new Error("seed 后没有默认项目");
    const pid = project.id;
    const parsers = await api(`/projects/${pid}/parser-profiles/?page=1&page_size=100`);
    const parser = parsers.items.find((item) => item.parser_name === "pymupdf4llm");
    if (!parser) throw new Error("没有可用 PyMuPDF parser profile");
    const profiles = await api(`/projects/${pid}/chunk-profiles/?page=1&page_size=100`);
    const chunkProfile = profiles.items.find((item) => item.is_default) || profiles.items[0];
    if (!chunkProfile) throw new Error("没有 ChunkProfile");

    const model = await api(`/projects/${pid}/model-configs/`, {
      method: "POST",
      json: { name: `e2e-model-${uid}`, provider: "local", base_url: "http://127.0.0.1:18765/v1", api_key: "e2e-local", model_name: "review-stub", temperature: 0, max_tokens: 128 },
    });
    const templates = await api(`/projects/${pid}/prompt-templates/?page=1&page_size=100`);
    const template = templates.items.find((item) => item.task_type === "qa_generation");
    if (!template) throw new Error("没有 QA PromptTemplate");

    const docA = await upload(pid, `e2e-A-${uid}.pdf`, "Compressor evidence version one");
    const parseA1 = await parse(pid, docA.id, parser.id, `e2e-a1-${uid}`);
    const parseA2 = await parse(pid, docA.id, parser.id, `e2e-a2-${uid}`);
    const acceptedA1 = await cleanAndAccept(pid, docA.id, parseA1.id, cleanText, `e2e-clean-a1-${uid}`);
    const sourceA1 = await chunk(pid, docA.id, chunkProfile.id, `e2e-chunk-a1-${uid}`);

    const generated = await api(`/projects/${pid}/documents/${docA.id}/generate-batch`, {
      method: "POST",
      headers: { "Idempotency-Key": `e2e-generate-${uid}` },
      json: { prompt_template_id: template.id, model_config_id: model.id, selected_chunk_ids: [sourceA1.chunk.id] },
    });
    await waitTask(pid, generated.task_id, "生成");
    const candidates = await api(`/chunks/${sourceA1.chunk.id}/candidates?page=1&page_size=20`);
    const candidate = candidates.items[0];
    if (!candidate) throw new Error("生成完成后没有 Candidate");
    const span = (chunk) => {
      const quote = chunk.content.slice(0, Math.min(chunk.content.length, 24));
      return { chunk_id: chunk.id, start_char: 0, end_char: quote.length, quote_text: quote };
    };
    await api(`/candidates/${candidate.id}/review`, {
      method: "POST", json: {
        verdict: "supported",
        expected_revision: candidate.content_revision,
        evidence_spans: [span(sourceA1.chunk)],
      },
    });
    const curated = await api(`/candidates/${candidate.id}/promote-to-curated`, {
      method: "POST", json: { expected_revision: candidate.content_revision },
    });
    const approved = await api(`/projects/${pid}/curated-items/${curated.id}/review`, {
      method: "POST", json: { action: "approve", expected_revision: curated.current_revision },
    });

    const dataset = await api(`/projects/${pid}/datasets/`, { method: "POST", json: { name: `e2e-dataset-${uid}` } });
    await api(`/projects/${pid}/datasets/${dataset.id}/items`, { method: "POST", json: { curated_item_id: approved.id } });
    const beforeFinalize = await api(`/projects/${pid}/datasets/${dataset.id}`);
    const finalized = await api(`/projects/${pid}/datasets/${dataset.id}/finalize`, {
      method: "POST", json: { expected_revision: beforeFinalize.composition_revision, expected_sha256: beforeFinalize.composition_sha256 },
    });

    // Switch A to a new active clean/chunk version after the evidence has been fixed.
    await cleanAndAccept(pid, docA.id, parseA2.id, "# Compressor V2\nA newer clean version.\n", `e2e-clean-a2-${uid}`);
    await chunk(pid, docA.id, chunkProfile.id, `e2e-chunk-a2-${uid}`);

    const exportProfile = await api(`/projects/${pid}/export-profiles/`, { method: "POST", json: { name: `e2e-qa-${uid}`, format: "qa_json" } });
    const created = await api(`/projects/${pid}/datasets/${dataset.id}/export`, {
      method: "POST", headers: { "Idempotency-Key": `e2e-export-${uid}` },
      json: { export_profile_id: exportProfile.id, expected_source_revision: finalized.composition_revision, expected_source_sha256: finalized.composition_sha256 },
    });
    await waitTask(pid, created.task_id, "导出");
    const exported = await api(`/projects/${pid}/exports/${created.export_id}`);
    if (exported.status !== "completed") throw new Error(`导出未完成: ${exported.status}`);
    const manifest = await api(`/projects/${pid}/exports/${created.export_id}/manifest`);
    const entries = manifest.manifest.members[0].evidence;
    const aEvidence = entries.find((entry) => entry.provenance.document.document_id === docA.id);
    if (!aEvidence) throw new Error("导出 manifest 缺少候选的冻结来源证据");
    if (aEvidence.provenance.parse.parse_job_id !== parseA1.id || aEvidence.provenance.chunk_set.chunk_set_id !== sourceA1.chunkSetId) {
      throw new Error("导出错误地使用了 A 的最新解析或活动分块版本");
    }
    const link = await api(`/projects/${pid}/exports/${created.export_id}/download-link`, { method: "POST" });
    const verified = await api(`/projects/${pid}/exports/${created.export_id}/verify`, { method: "POST", json: { deep: true } });
    if (verified.status !== "verified") throw new Error(`导出深度验证失败: ${verified.status}`);
    return {
      project_id: pid, document_a: docA.id,
      parse_a1: parseA1.id, parse_a2: parseA2.id, chunk_set_a1: sourceA1.chunkSetId,
      export_id: created.export_id, manifest_schema: manifest.schema_version,
      download_url: link.url, evidence_count: entries.length,
      accepted_version_a1: acceptedA1.version,
    };
  }, { apiBase });
  // APIRequestContext follows the version-fixed MinIO link without a page CORS
  // limitation, while the link itself was still issued by the authenticated UI
  // browser session above.
  const payloadResponse = await page.context().request.get(result.download_url);
  if (!payloadResponse.ok()) throw new Error(`下载导出文件失败: ${payloadResponse.status()}`);
  const payload = await payloadResponse.json();
  if (!payload[0]?.question?.trim() || !payload[0]?.answer?.trim()) throw new Error("导出包含空 QA 样本");
  const summary = { ...result, payload_question: payload[0].question };
  delete summary.download_url;
  console.log(JSON.stringify({ e2e_review_workflow: summary }, null, 2));
  return summary;
}
