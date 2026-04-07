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
