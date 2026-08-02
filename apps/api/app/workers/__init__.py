"""业务 handler 注册与 payload schema 校验（T07 §5）。

所有 handler 以稳定名 + payload version 注册，接收 ExecutionContext。
payload 只存资源 id 与非敏感选项，绝不存 API key、临时下载 URL、PDF 二进制、
Prompt 全文等敏感/大对象。

注册表为模块级单例（app.workers.execution.registry）；runner 启动时调用
``register_all_handlers()`` 完成注册。
"""

from __future__ import annotations

import uuid

from app.workers.execution import HandlerRegistry, registry


def _validate_payload(payload: dict, *required_keys: str) -> dict:
    """校验 payload 必填键；缺键抛 ValueError（永久失败 VALIDATION_FAILED）。"""
    missing = [k for k in required_keys if payload.get(k) is None]
    if missing:
        raise ValueError(f"payload 缺少必填键: {', '.join(missing)}")
    return payload


def _require_uuid(payload: dict, key: str) -> uuid.UUID:
    value = payload.get(key)
    if value is None:
        raise ValueError(f"payload 缺少必填键: {key}")
    return uuid.UUID(str(value))


def _register_parse_handlers(r: HandlerRegistry) -> None:
    from app.workers.parse_worker import run_parse_handler

    r.register("parse_document", 1)(run_parse_handler)


def _register_clean_handlers(r: HandlerRegistry) -> None:
    from app.workers.clean_worker import run_clean_handler

    r.register("clean_document", 1)(run_clean_handler)


def _register_chunk_handlers(r: HandlerRegistry) -> None:
    from app.workers.chunk_worker import run_chunk_handler

    r.register("chunk_document", 1)(run_chunk_handler)


def _register_generate_handlers(r: HandlerRegistry) -> None:
    from app.workers.generate_worker import run_generate_batch_handler, run_generate_single_handler

    r.register("generate_single", 1)(run_generate_single_handler)
    r.register("generate_batch", 1)(run_generate_batch_handler)


def _register_export_handlers(r: HandlerRegistry) -> None:
    from app.workers.export_worker import run_export_benchmark_handler, run_export_dataset_handler

    r.register("export_dataset", 1)(run_export_dataset_handler)
    r.register("export_benchmark", 1)(run_export_benchmark_handler)


def register_all_handlers() -> None:
    """注册全部业务 handler。幂等：重复调用跳过已注册项。"""
    for registrar in (
        _register_parse_handlers,
        _register_clean_handlers,
        _register_chunk_handlers,
        _register_generate_handlers,
        _register_export_handlers,
    ):
        registrar(registry)
