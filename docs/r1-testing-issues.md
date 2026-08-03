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

---

## Issue #22: 清洗工作台 — PDF 无法加载

**反馈：** 进入清洗工作台后，PDF 原文栏显示空白。

**根因：** 后端不存在 `GET /projects/{pid}/documents/{did}/file` 端点，PDF 文件无法通过 HTTP 提供给 iframe。此外，iframe 无法在请求中携带 `Authorization` header，传统的 JWT header 认证方案不适用于嵌入场景。

**修复：**
- 后端 `documents.py`: 新增 `GET /{did}/file` 端点，从 MinIO 下载 PDF 并返回 `Response(media_type="application/pdf")`
- 该端点同时支持 `Authorization: Bearer` header 和 `?token=` query param 两种认证方式，后者供 iframe 使用
- 前端 `clean/page.tsx`: PDF URL 改为直连后端 `${API_BASE}/projects/.../file?token=...`，从 localStorage 取 JWT 拼入 query param

**提交：** 未提交（与 Issue #23-#26 合并提交）

---

## Issue #23: "开始清洗"和"清洗工作台"按钮拆分不合理

**反馈：** 文档详情页有"开始清洗"和"清洗工作台"两个按钮，操作分散，用户需要先点一个再点另一个。

**修复：**
- 合并为单个「文档清洗」按钮
- 文档状态为 `parsed` → 自动调用 `cleaning/start` 然后跳转工作台
- 文档状态为 `cleaning`/`cleaned` → 直接跳转工作台
- 引入 `useRouter` 实现编程式导航

**提交：** 未提交（与 Issue #22, #24-#26 合并提交）

---

## Issue #24: 清洗工作台四栏布局冗余 + 多处字段不匹配

**反馈：** 四栏布局中"原始 Markdown（只读）"栏与编辑器功能重叠，实际只需 PDF + 预览 + 编辑器三栏。同时 Markdown 预览和编辑器无内容显示。

**根因（多个）：**
1. **布局冗余**：原始 MD 只读栏无实际用途，用户需要的是编辑器内容的实时渲染预览
2. **字段不匹配**：前端 Section 接口用 `title` / `section_index`，后端返回 `heading_path` / `ordinal`
3. **状态筛选不匹配**：前端 option 值 `raw`/`cleaning`/`cleaned`/`in_review`/`approved`/`rejected` 与后端枚举 `draft`/`in_cleaning`/`review_pending`/`accepted`/`rejected` 不一致
4. **Comments API 格式**：前端按分页响应 `{items:[]}` 解析，后端实际返回 `list[]`
5. **PDF URL**：使用 `/api/...`（Next.js 代理路径），应直连后端

**修复：** 全面重写 `clean/page.tsx`：
- 布局从 4 栏改为 3 栏：PDF 原文 | Markdown 实时预览 | CodeMirror 编辑器 + 评论
- Section 接口对齐：`title` → `heading_path`，`section_index` → `ordinal`
- 状态筛选选项对齐后端枚举值
- Comments API 直接解析为 `Comment[]`
- 侧边栏新增"返回文档"链接

**提交：** 未提交（与 Issue #22-#23, #25-#26 合并提交）

---

## Issue #25: 清洗工作台 Markdown 编辑器和预览无内容

**反馈：** 进入清洗工作台后，左侧 Section 列表正常，但中间预览和右侧编辑器均为空白。

**根因：** `fetchSections` 回调的依赖数组包含 `selectedSectionId`。首次加载时：
1. `fetchSections()` 获取 sections 列表并设置 `selectedSectionId`（从 null → 第一个 section ID）
2. `selectedSectionId` 变化导致 `fetchSections` 被重新创建
3. `useEffect(() => fetchSections(), [fetchSections])` 再次触发
4. `fetchSections()` 内部调用 `setLoading(true)`，导致整个工作台被卸载（显示"加载中..."）
5. Section 详情 API 的结果被丢弃，`editedMarkdown` 始终为空字符串

**修复：**
- 将 `selectedSectionId` 从 `fetchSections` 依赖中移除
- "自动选中第一个 section"逻辑拆到独立的 `useEffect` 中
- sections 列表加载只触发一次，不再干扰 section 详情获取

**提交：** 未提交（与 Issue #22-#24, #26 合并提交）

---

## Issue #26: 多条解析记录时清洗目标不明确

**反馈：** 一个文档允许多条解析记录（不同解析器），点击"文档清洗"时无法确定基于哪条记录进行清洗。

**根因：** `POST /cleaning/start` 硬编码取最新的已完成 ParseJob（`order_by(created_at.desc()).first()`），不接受用户指定。

**修复：**
- 后端 `documents.py`: `cleaning/start` 新增可选 `parse_job_id` body 参数，传了用指定的，没传 fallback 到最新
- 前端 `documents/[did]/page.tsx`:
  - 0 条已完成解析 → toast 提示
  - 1 条已完成解析 → 直接使用
  - 多条已完成解析 → 弹出选择 Dialog（显示解析器名 + 完成时间），用户选择后开始清洗

**提交：** 未提交（与 Issue #22-#25 合并提交）

---

## Issue #27: PDF 文件端点 500 — 中文文件名编码错误

**反馈：** 清洗工作台 PDF 原文栏显示 "Internal Server Error"，后端报 `UnicodeEncodeError: 'latin-1' codec can't encode characters`。

**根因：** `Content-Disposition` header 中直接嵌入中文文件名 `filename="AFC2026 最佳论文&优秀论文统计分析.pdf"`，Starlette 用 latin-1 编码 header 值，中文字符超出 latin-1 范围。

**修复：**
- 使用 RFC 5987 标准：`Content-Disposition: inline; filename*=UTF-8''<url-encoded-filename>`
- 用 `urllib.parse.quote()` 对文件名进行 URL 编码

**提交：** 未提交（与 Issue #28 合并提交）

---

## Issue #28: 清洗工作台 "加载章节列表失败" — page_size 超限

**反馈：** 进入清洗工作台弹出 "加载章节列表失败" toast。

**根因：** 前端请求 `sections?page_size=200`，但后端 `list_sections` 端点限制 `page_size: int = Query(50, ge=1, le=100)`，200 超过上限返回 422 校验错误。

**修复：**
- 前端 `clean/page.tsx`: `page_size=200` → `page_size=100`

**提交：** 未提交（与 Issue #27 合并提交）

---

## Issue #29: 删除解析记录失败 — 外键约束 + 删除文档后 MinIO outputs 残留

**反馈：**
1. 已做过清洗的解析记录，点击删除弹窗显示"删除失败"
2. 直接删除文档成功，但 MinIO outputs 桶中仍残留该文档的解析产出文件（raw.md、structured.json）

**根因：**
1. **外键约束**：`cleaning_jobs.parse_job_id` → `parse_jobs.id` 没有 `ondelete="CASCADE"`。已做清洗的 ParseJob 被 CleaningJob 引用，直接删除触发外键约束错误
2. **outputs 未清理**：`delete_document()` 只删 documents 桶的 PDF 源文件，未清理 outputs 桶中解析产出的 markdown/json 文件

**修复：**
- `document_service.py` `delete_parse_job()`: 删除 ParseJob 前，先查找并删除所有引用它的 CleaningJob（会级联删除 sections、comments、revisions、leases）
- `document_service.py` `delete_document()`: 删除文档前，遍历所有关联的 ParseJob，逐一删除 outputs 桶中的 `raw_markdown_key` 和 `structured_json_key` 文件

**提交：** 未提交

---

## Issue #31: PDF 加载慢 — 后端无缓存头 + Markdown 预览无独立滚动 + 编辑器默认暗色

**反馈：**
1. 清洗工作台 PDF 加载需要较长时间，每次切换 section 或重新进入都要重新下载
2. Markdown 预览栏内容过长时没有独立滚动条，导致整个页面被撑长需要不断下滑
3. CodeMirror 编辑器默认黑色背景（oneDark 主题），应默认白色并支持手动切换

**根因：**
1. PDF 文件端点未设置 `Cache-Control` header，浏览器每次都重新向后端请求完整 PDF
2. 预览栏的父 div 缺少 `overflow: hidden`，`ScrollArea` 无法在受限高度内生效
3. CodeMirror 硬编码使用 `oneDark` 主题，无切换机制

**修复：**
- 后端 `documents.py` `/file` 端点：添加 `Cache-Control: private, max-age=3600`，浏览器缓存 1 小时
- 前端 `clean/page.tsx`: 预览栏父 div 加 `overflow-hidden`，`ScrollArea` 加 `min-h-0`
- `codemirror-editor.tsx`: 默认无主题（白色背景），新增 `darkMode` state + 切换按钮（亮色/暗色），`oneDark` 仅在暗色模式下加载；编辑器在 `darkMode` 变化时重新创建

**提交：** 未提交

---

## Issue #30: 页面导航偶发空白卡死 — 无超时保护 + 重定向空白

**反馈：** 切换页面、返回主页、刷新时偶尔页面空白卡死，浏览器显示已加载完成但无内容。有时正常有时卡住。

**根因（3个）：**
1. **Auth 初始化无超时**：`AuthProvider` 挂载时调用 `api.get("/auth/me")` 验证 token，如果后端慢或网络波动，该请求可能长时间无响应，`loading` 永远不变 false，整个 Dashboard 卡在空白
2. **API 层无请求超时**：`api.ts` 内所有 `fetch()` 调用无超时机制，网络异常时请求永远挂起。token 刷新 `refreshToken()` 也可能挂起，层层阻塞
3. **Dashboard layout 返回 null**：`!user` 时触发 `router.push("/login")` 重定向，但组件返回 `null`（空白），如果重定向未立即完成，用户看到空白页

**修复：**
- `lib/api.ts`: 新增 `fetchWithTimeout()` 封装，所有请求 15 秒超时（使用 `AbortController`）
- `contexts/auth-context.tsx`: `/auth/me` 初始化增加 10 秒安全超时，超时后自动清除 token 并跳转登录
- `(dashboard)/layout.tsx`: `!user` 时不再返回 `null`，改为显示"正在跳转..."提示

**提交：** 未提交

---

## Issue #32: Markdown 预览数字标号错乱 — 有序列表起始编号丢失

**反馈：** 解析产出的 markdown 中包含论文编号列表（如 `46. **论文名**`、`53. **论文名**`），预览渲染后编号全部变成从 1 开始递增（1、2、3...），原始编号丢失。

**根因：** 标准 markdown 规范中，有序列表 `数字. 文本` 的数字仅影响首项，后续自动递增。`react-markdown` 默认行为遵循此规范：解析出 `<ol start="46">` 但浏览器默认 CSS 和 Tailwind `prose` 类会重置 `counter-reset`，导致始终从 1 开始渲染。

**修复：**
- `clean/page.tsx`: 给 `ReactMarkdown` 添加 `components.ol` 自定义渲染器，保留 `start` 属性并设置 `counterReset: list-item ${start - 1}`，确保浏览器从正确编号开始计数
- 后续修复：`start` 增加 `typeof === "number"` 类型检查，防止 `undefined - 1 = NaN` 传入 DOM

**提交：** 未提交

---

## Issue #33: 分块列表页 NaN 错误 + 字段名不匹配

**反馈：** 点击"查看分块"后，控制台报 `Received NaN for the children attribute`，页面序号列显示 NaN。

**根因：** 前端 Chunk 接口使用 `chunk_index`、Section 接口使用 `section_index` / `title`，但后端返回的字段名是 `ordinal` / `heading_path`。`undefined + 1 = NaN` 被传给 `<td>` 元素。另外 `content_preview` 字段不存在，后端返回的是完整 `content`。sections 请求 `page_size=200` 也超过后端限制 100。

**修复：**
- `chunks/page.tsx`: Chunk 接口 `chunk_index` → `ordinal`，`content_preview` → `content`（截取前 80 字符显示）
- Section 接口 `section_index` → `ordinal`，`title` → `heading_path`
- `token_count` 渲染用 `String()` 包裹防止数字直接传入 DOM
- sections 请求 `page_size=200` → `100`

**提交：** 未提交

---

## Issue #34: Next.js dev server 反复崩溃 — Turbopack HMR 内存泄漏

**反馈：** 开发过程中 Next.js dev server 频繁报 `RangeError: Map maximum size exceeded` 后退出运行，需要反复手动重启。

**根因：** Next.js 16 默认启用 Turbopack 编译器。Turbopack 的 HMR（热更新）模块中 `subscribeToClientHmrEvents` 函数使用 `Map` 跟踪异步操作，但从不清理已完成的条目，在长时间运行的 dev session 中 Map 超过 V8 引擎上限而崩溃。这是 Next.js 已知 bug，与项目代码无关。

**修复：**
- `package.json`: `"dev": "next dev"` → `"next dev --no-turbopack"`，回退使用 Webpack dev server，稳定性更好

**提交：** 未提交

---

## Issue #35: R1+ Slice 1 — code-reviewer 发现的 2 处预合入修复（2026-04-18）

**背景：** R1+ Slice 1（Clean 协作升级）实现完毕，commit `efa160f..b91906a`。code-reviewer subagent 裁定 APPROVE WITH NITS，两条重要建议已在 merge 前修复：

**修复 1：迁移缺少显式 UPDATE 回填语句**

- 根因：`ADD COLUMN ... DEFAULT ... NOT NULL` 虽然会让 Postgres 自动把默认值回写到旧行，但 spec §2.3 明确要求显式 UPDATE 语句。如果将来拆分成 DDL/DML 两步或部分数据库版本行为不同，缺 UPDATE 就会让旧行 `clean_status` / `assignment_status` / `review_status` 为 NULL。
- 修复：迁移 `upgrade()` 末尾加 3 条 `UPDATE ... WHERE ... IS NULL`，确保幂等兜底。

**修复 2：`update_section` 未处理 `returned → in_progress` 转换**

- 根因：spec §2.4 定义 `completed → returned → in_progress`。原实现只在 `assignment_status == "assigned"` 时自动推进到 `in_progress`，导致 section 被 admin 退回后，编辑者即使已经改了内容，侧栏徽章仍旧停留在 `returned`。
- 修复：`apps/api/app/services/section_service.py:35-36` 条件扩展为 `if section.assignment_status in ("assigned", "returned"):`。

**其他 3 条 nit 判为已知限制，非 blocker：**
- `/api/cleaned-versions/{vid}` 用 `get_current_user` 未做项目成员校验（flat route 的权衡）
- `/cleaning/final-review` 的 reject 分支对已 completed 文档的 clean_status 降级（极边缘，P4 再改）
- `complete_section` 非 existent section 返回 404（reviewer 自纠，其实正确）

**提交：** `0f6da40 fix(r1plus): address review nits — returned→in_progress on edit, explicit backfill in migration`

**冒烟结论：** 迁移应用成功，8 个新端点全部 HTTP 200，状态机端到端走通，MinIO artifact 写入正确。前端 UI 目检仍待手工验证（见根目录 `DevLog.md` "下一步测试清单"）。
