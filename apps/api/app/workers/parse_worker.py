import asyncio
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.document import Document
from app.models.parse import ParseJob
from app.security.snapshot import profile_snapshot_sha256, scan_for_secrets
from app.services.mineru_local_service_manager import ensure_mineru_local_service
from app.services.paddleocr_local_service_manager import ensure_paddleocr_local_service
from app.services.parse_freeze_service import build_worker_options
from app.services.task_service import TaskService
from parsing import get_parser
from storage import get_storage_client


async def run_parse(
    task_id: uuid.UUID,
    document_id: uuid.UUID,
    parser_profile_id: uuid.UUID,
    db: AsyncSession,
    redis=None,
    parse_job_id: uuid.UUID | None = None,
):
    task_service = TaskService(db, redis)
    storage = get_storage_client(
        settings.minio_endpoint, settings.minio_access_key, settings.minio_secret_key, settings.minio_secure
    )
    parse_job: ParseJob | None = None

    await task_service.update_status(task_id, "processing", progress=10)

    try:
        doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one()
        if parse_job_id:
            parse_job = (await db.execute(select(ParseJob).where(ParseJob.id == parse_job_id))).scalar_one()
        else:
            raise RuntimeError("parse_job_id 必须提供；worker 只消费冻结快照，不再实时读取 profile")

        # ---- 执行前复核（PDF 下载/联网前失败）----
        _validate_frozen_snapshot(parse_job)

        parse_job.status = "processing"
        parse_job.started_at = datetime.now(UTC)
        await db.flush()

        # 从冻结快照读取功能参数与安全上下文；绝不读取当前 ParserProfile 覆盖语义。
        profile_snapshot = parse_job.parser_profile_snapshot or {}
        parser_name = profile_snapshot.get("parser_name", "")
        functional_options = dict(profile_snapshot.get("options") or {})
        # 安全上下文从 registry 按冻结的 endpoint_ref 重新解析（registry 只读，永不改语义）。
        security = _security_context_from_snapshot(parse_job)

        # Download PDF (sync IO → thread pool)
        await task_service.update_status(task_id, "processing", progress=20)
        pdf_data = await asyncio.to_thread(
            storage.download_file,
            settings.minio_bucket_documents,
            doc.minio_key,
        )

        # Parse (CPU/IO-bound → thread pool)
        await task_service.update_status(task_id, "processing", progress=40)
        api_tokens = {
            "mineru": settings.mineru_api_token,
            "paddleocr": settings.paddleocr_api_token,
        }
        parser_options = build_worker_options(
            parse_job,
            parser_name=parser_name,
            stored_options=functional_options,
            security=security,
            api_tokens=api_tokens,
        )
        if parser_name == "mineru_local_service":
            await asyncio.to_thread(ensure_mineru_local_service, _local_manager_options(security, functional_options))
        if parser_name == "paddleocr_local_service":
            await asyncio.to_thread(ensure_paddleocr_local_service, _local_manager_options(security, functional_options))
        parser = get_parser(parser_name, options=parser_options)
        result = await asyncio.to_thread(parser.parse, pdf_data)

        # Upload results (sync IO → thread pool)
        await task_service.update_status(task_id, "processing", progress=70)
        md_key = f"{doc.project_id}/{document_id}/parsed/{parse_job.id}/raw.md"
        await asyncio.to_thread(
            storage.upload_file,
            settings.minio_bucket_outputs,
            md_key,
            result.raw_markdown.encode("utf-8"),
            "text/markdown",
        )

        json_key = f"{doc.project_id}/{document_id}/parsed/{parse_job.id}/structured.json"
        await asyncio.to_thread(
            storage.upload_file,
            settings.minio_bucket_outputs,
            json_key,
            json.dumps(result.structured_json, ensure_ascii=False, default=str).encode("utf-8"),
            "application/json",
        )

        # Update parse job
        parse_job.status = "completed"
        parse_job.completed_at = datetime.now(UTC)
        parse_job.raw_markdown_key = md_key
        parse_job.structured_json_key = json_key
        parse_job.page_mapping = result.page_mapping

        # Update document
        doc.status = "parsed"
        if result.structured_json.get("page_count"):
            doc.page_count = result.structured_json["page_count"]

        await db.flush()
        await task_service.update_status(task_id, "completed", progress=100)

    except Exception as e:
        job = parse_job
        if job is None and parse_job_id is not None:
            job = (await db.execute(select(ParseJob).where(ParseJob.id == parse_job_id))).scalar_one_or_none()
        if job is None:
            result_job = await db.execute(
                select(ParseJob).where(ParseJob.document_id == document_id).order_by(ParseJob.created_at.desc())
            )
            job = result_job.scalars().first()
        if job:
            job.status = "failed"
            # 错误消息脱敏：不把 URL query、响应体秘密或 PDF 内容写入 error_message。
            job.error_message = _safe_error_message(e)
            job.completed_at = datetime.now(UTC)
        await task_service.update_status(task_id, "failed", error_message=_safe_error_message(e))


def _validate_frozen_snapshot(parse_job: ParseJob) -> None:
    """worker 执行前复核：快照缺失/hash 不匹配/schema 不支持/含禁用秘密字段即失败。"""
    if parse_job.snapshot_schema_version is None or parse_job.snapshot_schema_version < 1:
        raise RuntimeError("parse_job 缺少可复现快照（legacy_unavailable）")
    if not parse_job.parser_profile_snapshot or not parse_job.endpoint_policy_snapshot:
        raise RuntimeError("parse_job 快照不完整")
    if not parse_job.parser_profile_sha256 or not parse_job.endpoint_policy_sha256:
        raise RuntimeError("parse_job 快照 hash 缺失")
    recomputed = profile_snapshot_sha256(
        parse_job.parser_profile_snapshot, parse_job.snapshot_schema_version
    )
    if recomputed != parse_job.parser_profile_sha256:
        raise RuntimeError("parse_job profile 快照 hash 不匹配")
    hits = scan_for_secrets(parse_job.parser_profile_snapshot) + scan_for_secrets(parse_job.endpoint_policy_snapshot)
    if hits:
        raise RuntimeError("parse_job 快照含禁用秘密字段")
    if parse_job.endpoint_policy_snapshot.get("legacy_unavailable"):
        raise RuntimeError("parse_job 历史快照不可用")


def _security_context_from_snapshot(parse_job: ParseJob) -> dict:
    """从 registry 按冻结 endpoint_ref 重新解析安全上下文（registry 只读，语义不变）。"""
    from app.security.registry import get_registry

    policy = parse_job.endpoint_policy_snapshot or {}
    endpoint_ref = policy.get("endpoint_ref")
    if not endpoint_ref or endpoint_ref == "local":
        return {}
    definition = get_registry().get(str(endpoint_ref))
    if definition is None:
        raise RuntimeError("冻结 endpoint_ref 已不在 registry")
    security = {
        "endpoint_ref": definition.endpoint_ref,
        "base_url": definition.base_url,
        "network_zone": definition.network_zone,
        "credential_origins": [str(o) for o in definition.credential_origins],
        "credential_ref": definition.credential_ref,
    }
    if definition.network_zone == "managed-local":
        from urllib.parse import urlsplit

        parts = urlsplit(definition.base_url)
        security["managed_local"] = {
            "port": parts.port or (80 if parts.scheme == "http" else 443),
            "pinned_ips": list(definition.pinned_ips) or (["127.0.0.1"] if parts.hostname in {"localhost", "127.0.0.1"} else []),
            "vlm_http_url": definition.additional_urls.get("vlm_http_url"),
        }
    return security


def _local_manager_options(security: dict, functional_options: dict) -> dict:
    """local service manager 只接受本地精确地址（admin 配置），不接受用户自定义。"""
    options = {
        "auto_start": functional_options.get("auto_start", True),
        "startup_timeout_seconds": functional_options.get("startup_timeout_seconds", 90),
    }
    if security.get("base_url"):
        options["base_url"] = security["base_url"]
    ml = security.get("managed_local") or {}
    if ml.get("vlm_http_url"):
        options["vlm_base_url"] = ml["vlm_http_url"]
    return options


def _safe_error_message(exc: Exception) -> str:
    """把异常转成无秘密的错误消息（截断、去 query、去响应体）。"""
    from app.security.snapshot import redact_url

    message = str(exc)
    # 若异常消息包含 URL，脱敏其 query 签名参数。
    if "http" in message.lower():
        import re as _re

        urls = _re.findall(r"https?://[^\s'\"]+", message)
        for url in urls:
            message = message.replace(url, redact_url(url))
    return message[:2000]
