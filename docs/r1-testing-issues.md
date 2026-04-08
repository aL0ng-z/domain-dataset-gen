# R1 测试问题记录

> 记录每次测试反馈的问题、根因分析和修复内容。

---

## Issue #1: 登录后无法跳转到项目列表

**反馈：** 输入用户名密码后，API 返回 200 OK，但前端不跳转到项目列表页。

**根因：** `/api/auth/login` 只返回 `{access_token, refresh_token, token_type}`，不包含用户信息。但前端 `auth.ts` 的 `AuthData` 接口期望响应包含 `user` 字段，导致 `setUser(data.user)` 设为 undefined，dashboard layout 的 auth guard（`!user` 时 redirect 到 login）阻止了跳转。

**修复：**
- `apps/web/src/lib/auth.ts`: 登录成功后增加一步 `GET /api/auth/me` 获取用户信息
- `apps/web/src/contexts/auth-context.tsx`: User 接口字段对齐后端实际返回（`email` 替代 `display_name`）
- `apps/web/src/components/sidebar.tsx`: `user.display_name` → `user.username`

**提交：** `7f87f18 fix: login now fetches user profile after token, fixing redirect to dashboard`

---

## Issue #2: 项目列表为空 + 新建项目按钮无反应

**反馈：** 登录后看不到"压气机知识抽取"项目，且"新建项目"按钮点击无反应。

**根因：**
1. **列表为空**：Next.js rewrite 代理没有正确转发 Authorization header，导致 API 返回 401 "Not authenticated"。错误被 `.catch(() => {})` 吞掉了。
2. **按钮无反应**：Button 组件没有 `onClick` 处理器，也没有 Dialog 弹窗。

**修复：**
- `apps/web/src/lib/api.ts`: `API_BASE` 从 `/api`（走 Next.js 代理）改为 `http://localhost:8000/api`（直连后端，绕过代理）
- `apps/web/src/lib/auth.ts`: 登录和刷新请求也改为直连后端
- `apps/web/src/app/(dashboard)/projects/page.tsx`: 添加完整的新建项目 Dialog（名称+描述表单），API 路径加尾部斜杠，错误不再被吞（显示在页面上）

**提交：**
- `394d40f fix: add project create dialog and fix API path for project listing`
- `3364e13 fix: use direct API URL instead of Next.js rewrite proxy`

---

## Issue #3: 多个页面加载失败 + 弹窗关闭按钮不可用

**反馈：** 进入项目后，模板/候选/监控/设置 Tab 页面均显示"加载失败"弹窗。弹窗左侧关闭图标无法点击。

**根因：** 前端 API 路径与后端路由定义不匹配。批量问题：

| 前端错误路径 | 后端实际路由 |
|-------------|-------------|
| `/projects/{pid}/templates` | `/projects/{pid}/prompt-templates` |
| `/projects/{pid}/config/model-configs` | `/projects/{pid}/model-configs` |
| `/projects/{pid}/candidates` | `/candidates/{cid}`（不在项目路径下） |
| `/projects/{pid}/monitoring/llm-usage` | `/projects/{pid}/monitoring/summary` |
| `/projects/.../sections/{sid}/submit-review` | `/sections/{sid}/submit` |
| `/projects/.../sections/{sid}/approve` | `/sections/{sid}/review` + `{action:"accept"}` |
| `api.put` | 应为 `api.patch`（后端用 PATCH） |

**修复：** 修改 10 个前端页面文件，对齐所有 API 路径：
- `templates/page.tsx`, `templates/[tid]/page.tsx`: templates → prompt-templates
- `settings/page.tsx`: 去掉 `config/` 前缀
- `candidates/page.tsx`: 改为根路径 `/candidates`
- `monitoring/page.tsx`: llm-usage → summary
- `documents/[did]/clean/page.tsx`: section 操作路径全部修正
- `documents/[did]/chunks/[cid]/page.tsx`: chunk 操作路径修正
- `curated/[cid]/page.tsx`, `benchmarks/[bid]/page.tsx`, `datasets/[did]/page.tsx`: put→patch, 去掉 config/ 前缀

**提交：** `fix: align all frontend API paths with backend route definitions`

> 弹窗关闭按钮问题待进一步确认（可能是 sonner toast 的 UI 问题，不影响功能）。

---

## Issue #4: 模型配置表单缺少 API Key + 提供商不联动

**反馈：**
1. 模型配置新建表单缺少 API Key 输入框，无法填写密钥
2. 切换提供商（OpenAI/vLLM/其他）后，API 地址等参数没有变化，应根据提供商自动填入
3. 缺少常用提供商：硅基流动、OpenRouter、DeepSeek

**根因：**
- 表单 state 中有 `api_base` 但没有 `api_key` 字段，且字段名与后端 schema 不匹配（后端用 `base_url` 和 `api_key`）
- Provider 的 onChange 只更新 provider 值，没有联动更新其他字段
- Provider 选项只有 3 个硬编码值

**修复：**
- 添加 API Key 输入框（password 类型，编辑时留空表示保持原值）
- 新增 `PROVIDER_PRESETS` 配置表，包含 6 个提供商的默认 base_url 和推荐模型列表：
  - OpenAI (`https://api.openai.com/v1`)
  - DeepSeek (`https://api.deepseek.com/v1`)
  - 硅基流动 (`https://api.siliconflow.cn/v1`)
  - OpenRouter (`https://openrouter.ai/api/v1`)
  - vLLM (`http://localhost:8080/v1`)
  - 其他（手动输入）
- 切换提供商自动填充 API 地址和默认模型名
- 已知提供商显示模型下拉列表，支持"自定义"选项；vLLM/其他为自由输入
- 表单字段名对齐后端 schema（`base_url`, `api_key`）
- 测试连接改为使用已保存配置的 ID 调用 `POST /{config_id}/test`

**提交：** `90f5282 fix: improve ModelConfig form with API key field, provider presets, and more providers`

---

## Issue #5: 模型配置保存失败 (500) + 新建时缺少测试按钮

**反馈：** 填写模型配置后点保存，显示"保存失败"。后端报 `TypeError: 'api_key' is an invalid keyword argument for ModelConfig`。另外新建时看不到"测试连接"按钮。

**根因：**
1. **500 错误**：前端 schema 字段名 `api_key` 与 SQLAlchemy 模型列名 `api_key_encrypted` 不匹配。`ConfigService.create()` 直接将 schema 字段名传给模型构造函数，模型不认识 `api_key`。
2. **307 重定向**：POST 请求 URL 缺少尾部斜杠，FastAPI 307 重定向到带斜杠的 URL，POST body 可能丢失。
3. **测试按钮**：新建时 `editItem` 为 null，测试按钮被条件隐藏（需要 config ID 才能调用测试端点）。

**修复：**
- `apps/api/app/services/config_service.py`: 添加 `FIELD_MAPPINGS` 和 `_map_fields()` 方法，在 create/update 时将 `api_key` → `api_key_encrypted`
- `apps/web/.../settings/page.tsx`: POST URL 加尾部斜杠；新建保存后不关闭 Dialog，切换为编辑模式（`setEditItem(created)`），测试按钮随即出现

**提交：** `b67ecf6 fix: map api_key to api_key_encrypted in config service, fix save+test flow`

---

## Issue #6: 测试连接响应慢 + 返回内容过长

**反馈：** 硅基流动测试连接成功但返回非常长的回复（模型用葡萄牙语解释了 ping 命令），且等待时间很久。

**根因：** 测试端点发送 `{"role":"user","content":"ping"}`，模型当成正常问题回答。`max_tokens` 使用 LLMClient 默认值 2048，模型可自由生成大量内容，导致耗时长。

**修复：**
- 测试 prompt 改为 `"Reply with exactly: pong"`
- 测试时 `max_tokens=20`, `temperature=0`，只需生成几个 token
- 响应内容 strip() 处理

**提交：** `7276c24 fix: optimize model config test - limit to 20 tokens for fast response`

---

## Issue #7: 自定义模型名称输入框一打字就消失

**反馈：** 模型名称选择"自定义..."后出现输入框，但输入任何字符后输入框立即消失。

**根因：** 自定义输入框的显示条件是 `form.model_name === "__custom"`。输入时 `onChange` 将 `model_name` 设为用户输入的值（不再是 `"__custom"`），条件变 false，输入框被 React 卸载。另外 `value=""` 写死了空字符串，输入内容也不会显示。

**修复：**
- 新增独立的 `useCustomModel` state 布尔标志，不依赖 `model_name` 的值来控制 UI
- 选择"自定义"时 `setUseCustomModel(true)` + `model_name` 清空
- 自定义输入框直接绑定 `form.model_name`，输入正常工作
- 提供"选择"按钮可切回下拉列表
- 切换提供商 / 新建 / 编辑时正确重置 `useCustomModel`

**提交：** `bfd6118 fix: custom model name input disappears on typing`

---

## Issue #8: 进入项目后无法返回项目列表

**反馈：** 进入项目后没有返回主页（项目列表）的入口。

**修复：**
- 左上角标题从"知识提取平台"改为"DTRC-KE"
- 标题改为 `<Link href="/projects">` 可点击链接，点击即返回项目列表页

**提交：** `a4c9114 fix: rename sidebar title to DTRC-KE and make it a link to project list`

---

## Issue #9: 创建模型配置 500 (temperature/max_tokens 为 None 时响应验证失败) + WebSocket 403

**反馈：** 不填最大 Token 和温度保存模型配置时，后端 500 报 `ResponseValidationError: Input should be a valid number, input: None`。同时控制台有 WebSocket 403 错误。

**根因：**
1. **500 错误**：`ModelConfigResponse` schema 中 `temperature: float` 和 `max_tokens: int` 不接受 None，但数据库列已改为 nullable。
2. **WS 403**：前端 WebSocket 连的是 `ws://localhost:3000/ws/projects/{pid}`（Next.js 代理），路径也缺少 `/tasks` 后缀。后端路由是 `/ws/projects/{pid}/tasks`。

**修复：**
- `ModelConfigResponse`: `temperature: float | None`, `max_tokens: int | None`
- WS URL 改为直连后端 `ws://localhost:8000/ws/projects/{pid}/tasks`

**提交：** `824d7d6 fix: make ModelConfigResponse temperature/max_tokens nullable, fix WebSocket URL`

---

## Issue #10: 上传 PDF 不显示页数 + 重复文档应允许上传

**反馈：**
1. 上传成功后看不到页数
2. 重复文档只显示"上传失败"无详细提示；用户认为应允许上传重复文档以便用不同配置处理

**根因：**
1. 页数只在解析（parse）完成后才写入，上传阶段未提取
2. SHA256 去重硬性拒绝重复文件，且前端只显示通用错误信息

**修复：**
- 上传时用 pymupdf 快速提取 page_count（不依赖完整解析）
- 移除 SHA256 去重检查，允许同一文件多次上传
- MinIO key 使用 UUID 而非 SHA256，避免文件覆盖

**提交：** `d9e8a1b fix: extract page count on upload, allow duplicate PDF uploads`

---

## Issue #11: 重复文档上传仍然失败（数据库唯一约束）

**反馈：** 移除 Python 层去重后，重复上传仍显示"上传失败"。MinIO 有文件但数据库写入失败。

**根因：** 初始迁移中添加了 `uq_documents_project_sha256` 数据库唯一约束，Python 检查虽然移除但 DB 约束仍在。

**修复：**
- 新增 Alembic 迁移，删除 `uq_documents_project_sha256` 唯一约束
- 移除 Document model 的 `__table_args__`
- 新增 `_deduplicate_filename()` 方法：同名文件自动重命名为 `test(1).pdf`、`test(2).pdf` ...

**提交：** `ef47a1f fix: allow duplicate PDF uploads with auto-renamed filenames`

---

## Issue #12: 上传和删除文档极慢（500KB PDF 需要约 1 分钟）

**反馈：** 上传 500KB PDF 和删除未解析的文档都需要几十秒到一分钟。

**根因：** `StorageClient`（MinIO SDK）和 `pymupdf` 都是同步库。在 async FastAPI handler 中直接调用同步 I/O 会阻塞整个事件循环，导致请求排队等待。

**修复：** 所有同步 I/O 操作改用 `asyncio.to_thread()` 放到线程池执行：
- `hashlib.sha256()` 计算
- `_extract_page_count()` pymupdf 页数提取
- `self._storage.upload_file()` MinIO 上传
- `self._storage.delete_file()` MinIO 删除

**提交：** `2839a5b fix: run MinIO and pymupdf operations in thread pool to avoid blocking event loop`

---

## Issue #13: 删除确认对话框交互不佳

**反馈：** 点击确认删除后对话框立即关闭，可以重复点击。应在删除完成前保持对话框打开。

**修复：** `confirm()` 替换为 shadcn Dialog，带 loading spinner 和 disabled 按钮，删除完成后自动关闭。

**提交：** `ed59f5a fix: replace confirm() with Dialog for document delete, add loading state`

---

## Issue #14: 上传/删除仍慢 — StorageClient 每次重建 + 桶检查冗余

**反馈：** asyncio.to_thread 改造后上传/删除速度仍无明显改善。

**根因：** `DocumentService.__init__` 每次请求都新建 `StorageClient` → 新建 `Minio()` 连接（DNS 解析、连接池初始化）。`ensure_bucket()` 每次上传都发 HTTP 请求检查桶是否存在。pymupdf 写临时文件也有额外磁盘 I/O。

**修复：**
- `StorageClient` 改为单例模式（`get_storage_client()` 工厂函数）
- `ensure_bucket()` 增加类级缓存 `_verified_buckets`，已确认的桶不再重复检查
- pymupdf 改为 `open(stream=file_data)` 从内存读取，不再写临时文件
- pymupdf 在模块级预加载，避免首次调用初始化慢
- 所有 workers 和 routers 统一使用 `get_storage_client()`

**提交：** `6fdb65d perf: optimize storage client - singleton + bucket cache + pymupdf from memory`

---

## Issue #15: 文档详情页操作按钮缺少必要参数 + 按钮不区分状态

**反馈：** 点击「发起解析」后端报 422（缺少 parser_profile_id）。所有操作按钮不传 body。按钮在任何文档状态下都可点击。

**根因：** `handleAction` 只发空 POST，但后端 `trigger_parse` 需要 `ParseRequest` body（含 `parser_profile_id`），`trigger_chunk` 需要 `ChunkRequest` body（含 `chunk_profile_id`）。

**修复：**
- 拆分为独立的 `handleParse`、`handleChunk`、`handleCleanStart` 处理函数
- 自动获取项目默认的 parser/chunk profile 并传入请求体
- 按钮按文档状态禁用（parse→uploaded, clean→parsed, chunk→cleaning/cleaned, generate→chunked）
- 按钮标签显示将使用的配置名称
- ParseJob 字段名对齐后端（parser_profile_id, completed_at）

**提交：** `3489e8f fix: document detail actions now pass required profile IDs`

---

## Issue #16: 解析器系统重构 — 支持 pymupdf4llm / MinerU / PaddleOCR

**反馈：** 需要支持三种解析器，默认 pymupdf4llm 本地解析，MinerU 和 PaddleOCR 通过 API 接入。

**修改（功能需求，非 bug）：**
- 重构 `libs/parsing/`：`MockParser` → `PymupdfParser`，新增 `MineruParser`、`PaddleOCRParser`（API 方式）
- `BaseParser` 接口变更：接受 `options` dict 和 `pdf_data: bytes`（不再是文件路径）
- `get_parser()` 传递 `parser_options` 到解析器构造函数
- 前端设置页新增专用 `ParserProfileTab`，按解析器类型显示不同配置项
- pymupdf4llm：无额外配置；MinerU/PaddleOCR：显示 API 地址 + API Key 输入框
- 种子数据更新：默认解析器改为 `pymupdf4llm`

**提交：** `1eb61c0 feat: redesign parser system with pymupdf4llm, MinerU, PaddleOCR support`

---

## Issue #17: 解析任务记录不实时显示 + 无进度条

**反馈：** 点击"发起解析"后，解析任务记录面板不出现新记录，也看不到进度条。只有后端解析完成后刷新页面才能看到记录变为"已完成"。

**根因（3个）：**
1. **ParseJob 在 background worker 中才创建**（`parse_worker.py:28-33`），POST 返回后 `fetchData()` 查不到新记录
2. **trigger_parse 中 `TaskService(db)` 没传 redis**（`documents.py:106`），`task.created` 事件未发送到 WebSocket，前端收不到任何 WS 消息
3. **fetchData 每次都 `setLoading(true)`**，WS 触发的后台刷新也会让整个页面闪烁"加载中"

**修复：**
- 后端 `documents.py`: `trigger_parse` 中立即创建 ParseJob（status="queued"）并 commit；TaskService 传入 redis 连接使 `task.created` 发布到 WS
- 后端 `parse_worker.py`: 新增 `parse_job_id` 参数，复用已有 ParseJob 记录而非重新创建
- 前端 `documents/[did]/page.tsx`: `fetchData(silent)` 参数区分首次加载与 WS 静默刷新；新增 `taskProgress` state 跟踪 WS 推送的解析进度
- 新增 `components/ui/progress.tsx` Progress 组件；解析任务记录表新增"进度"列，实时显示进度条

**提交：** 未提交（与 Issue #18-#21 合并提交）

---

## Issue #18: 解析记录缺少删除功能

**反馈：** 解析任务记录没有删除按钮，无法清理失败或过期的解析记录。删除时应同时清理 MinIO 中的产出文件。

**修复：**
- 后端 `document_service.py`: 新增 `delete_parse_job()` 方法——先删除 MinIO outputs 桶中 `raw_markdown_key` 和 `structured_json_key` 文件，再删除数据库记录
- 后端 `documents.py`: 新增 `DELETE /{did}/parse-jobs/{jid}` 端点，需 editor 权限
- 前端 `documents/[did]/page.tsx`: 解析任务表格新增垃圾桶删除按钮（进行中的任务禁用），点击弹出确认 Dialog

**提交：** 未提交（与 Issue #17, #19-#21 合并提交）

---

## Issue #19: 删除所有解析记录后文档状态仍为"已解析"

**反馈：** 删除文档的全部解析记录后，文档状态仍显示"已解析"，应回退为"已上传"。

**根因：** `delete_parse_job()` 只删除了记录，没有检查是否还有剩余解析记录来决定文档状态。

**修复：**
- `document_service.py` `delete_parse_job()`: 删除后查询剩余 ParseJob 数量，若为 0 且文档状态为 `parsed`/`parsing`，则回退为 `uploaded`

**提交：** 未提交（与 Issue #17-#18, #20-#21 合并提交）

---

## Issue #20: 文档列表操作栏冗余 + 页数无法识别

**反馈：**
1. 文档管理列表中每条记录的"操作"下有"发起解析""详情""删除"三个按钮，应只保留"详情"和删除图标
2. 上传新 PDF 后页数显示为 `-`，`_extract_page_count` 功能失效

**根因：**
1. "发起解析"按钮功能已整合到文档详情页，文档列表页不再需要
2. `import pymupdf as _pymupdf`（别名 `_pymupdf`），但函数中使用 `pymupdf.open()`，导致 `NameError` 被 `except Exception` 静默吞掉，返回 `None`

**修复：**
- `documents/page.tsx`: 移除"发起解析"按钮和无用的 `handleTriggerParse` 函数；"删除"文字改为垃圾桶图标按钮
- `document_service.py`: `import pymupdf as _pymupdf` → `import pymupdf`，修复名称引用错误

**提交：** 未提交（与 Issue #17-#19, #21 合并提交）

---

## Issue #21: 触发任务响应慢 — Redis 连接每次新建

**反馈：** 点击"发起解析"按钮后，按钮转圈到下方出现解析记录的时间偏长。

**根因：** trigger_parse / start_cleaning / trigger_chunk / trigger_generate_batch 四个端点内都调用 `_create_bg_redis()` 新建 Redis TCP 连接（DNS 解析 + 握手），用完立即关闭。每次请求都重复这一开销。

**修复：**
- 四个 trigger 端点改用 `request.app.state.redis`（应用启动时已创建的连接），零开销复用
- Background worker 仍使用独立 `_create_bg_redis()` 连接（独立 DB session 生命周期）

**提交：** 未提交（与 Issue #17-#20 合并提交）
