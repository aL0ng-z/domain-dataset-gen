# 解析器出站与凭证安全（T03）运维指南

本指南面向部署方，说明如何配置服务端 `ParserEndpointRegistry`、迁移存量 profile、
以及在真实 provider 上验证出站安全边界。对应任务卡 `docs/code-review-remediation/T03-parser-egress-and-credential-security.md`。

## 1. 核心概念

- **ParserProfile 不再保存任何网络 URL 或密钥**，只保存 `endpoint_ref` 与功能参数。
- **endpoint_ref 是不透明稳定 ID**，只能解析到服务端 registry；数据库不保存真实 URL/Token。
- **全局 MinerU/PaddleOCR Token** 只在发送到其绑定的 credential origin 前由 worker 即时注入。
- **所有解析器出站调用**（初始任务、轮询、signed upload、结果 ZIP、PaddleOCR 请求、
  本地服务请求）都经过统一安全 transport（`parsing.transport.SecureTransport`）。

## 2. 配置 ParserEndpointRegistry

在 API 服务环境变量 `PARSER_ENDPOINT_REGISTRY` 提供 JSON 数组（见 `apps/api/.env.example`）。

### 2.1 public-remote 端点（远程 provider API）

```json
[
  {
    "endpoint_ref": "mineru-official",
    "parser_name": "mineru",
    "network_zone": "public-remote",
    "base_url": "https://mineru.net/api/v4/extract/task",
    "credential_ref": "env:MINERU_API_TOKEN",
    "credential_origins": ["https://mineru.net"],
    "artifact_origins": [
      { "usage": "upload", "suffix": "*.oss-cn-hangzhou.aliyuncs.com" },
      { "usage": "download", "suffix": "*.oss-cn-hangzhou.aliyuncs.com" }
    ],
    "redirect_policy": "deny"
  }
]
```

- `credential_origins`：可携带 Authorization 的**精确** scheme/host/port 集合。
- `artifact_origins`：PDF 上传与结果下载的精确 origin 或受控域后缀（边界正确匹配，
  不是字符串前缀）。**必须由管理员从 provider 官方文档确认后维护**。
- `redirect_policy`：默认 `deny`（禁止自动重定向）；若 provider 必需，设 `bounded`
  并配 `max_redirects`（每跳重新校验，跨 origin 不转发 Authorization）。

### 2.2 managed-local 端点（私有部署服务）

```json
{
  "endpoint_ref": "ml-mineru-9010",
  "parser_name": "mineru_local_service",
  "network_zone": "managed-local",
  "base_url": "http://127.0.0.1:9010",
  "allowed_paths": ["/tasks", "/tasks/{task_id}", "/tasks/{task_id}/result"],
  "pinned_ips": ["127.0.0.1"]
}
```

- managed-local **必须**由管理员注册精确地址，**不允许**携带远程 provider 全局 Token。
- `pinned_ips` 固定已验证解析结果（防 DNS rebinding）；本地服务默认 `["127.0.0.1"]`。
- 若本地服务需要内部 VLM 地址（如 PaddleOCR 的 MLX-VLM），通过 `additional_urls` 配置：
  `"additional_urls": {"vlm_http_url": "http://127.0.0.1:9021"}`（不暴露给项目用户）。

## 3. 存量 ParserProfile 迁移

```powershell
# dry-run：只输出映射计划，不写入
conda run -n DatasetGen python scripts/migrate_parser_profiles.py --dry-run

# 检查是否已全部迁移（exit 0 表示全部映射）
conda run -n DatasetGen python scripts/migrate_parser_profiles.py --check

# 单事务写入（可重复运行）
conda run -n DatasetGen python scripts/migrate_parser_profiles.py --migrate
```

- 仅将与 registry `base_url` **精确匹配**的旧 URL 映射为 `endpoint_ref` 并删除网络字段。
- 无法唯一映射的 profile 使迁移**在写入前整体失败**，输出 profile ID 与 URL hash
  （不输出完整 signed URL / secret）。此时需管理员给出可信映射。
- 迁移前已存在的 ParseJob 标记 `snapshot_schema_version=0 + legacy_unavailable`，
  T11 不会将其宣称为完整可复现快照。
- **运行时对含旧网络字段的 profile 一律拒绝**（触发解析返回 409），不会为兼容继续发送请求。

## 4. 部署验证清单

1. 设置 `PARSER_ENDPOINT_REGISTRY` 后重启 API，观察启动日志无 registry 校验错误
   （重复 ref、非法 scheme、managed-local 绑定凭证等）。
2. 创建 ParserProfile：确认设置页只提供 endpoint_ref 下拉，无自由网络字段。
3. 触发解析：确认 ParseJob 带 `parser_profile_sha256`、`endpoint_policy_ref/version/sha256`。
4. 修改 Profile 后重试：确认旧 ParseJob 快照字节级不变（数据库触发器禁止改写）。
5. 用假 endpoint_ref 或旧 base_url 触发解析：确认返回 409 且不下载 PDF。
6. 观察日志/错误消息：确认不含 Token、Authorization、PDF 内容或预签名 query。
7. `scripts/migrate_parser_profiles.py --check` 返回 0。

## 5. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| 创建 profile 报 422 unsafe_parser_option | 提交了禁用字段（base_url/upload_url/token 等） | 改用 endpoint_ref + 白名单字段 |
| 触发解析返回 409 credential_not_configured | 端点 credential_ref 指向的 Token 未配置 | 设置 MINERU_API_TOKEN / PADDLEOCR_API_TOKEN |
| 触发解析返回 409 invalid_parser_endpoint | endpoint_ref 未注册或 parser_name 不匹配 | 检查 registry 配置 |
| worker 报 "快照 hash 不匹配" | 快照被篡改或 schema 变更 | 重新创建 ParseJob（不可修改历史快照） |
| signed upload 被拒 | artifact origin 未覆盖 provider 对象存储域名 | 管理员补充 `artifact_origins`（不允许动态放宽） |
| 本地服务无法连接 | managed-local 地址未注册或 pinned_ips 错误 | 在 registry 注册精确地址与 IP |

## 6. 安全边界提醒

- **禁止**允许项目用户把全局 Token 或 PDF 发送到任意自定义 endpoint。
- **禁止**用字符串 `startswith`、仅解析一次 DNS 或"禁止 localhost"代替完整网络策略。
- **禁止**在错误消息、日志、快照、API 响应中包含 Token、Authorization、PDF 内容或预签名 query。
- DNS rebinding 防护：连接必须使用已验证并固定的解析结果（`pinned_ips`）。
- 紧急禁用端点：用独立 kill switch 阻止尚未执行的 job，但**不得修改原快照**；
  恢复后若需新策略必须创建新 ParseJob。
