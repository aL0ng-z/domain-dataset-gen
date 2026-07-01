#!/usr/bin/env python3
"""Validate local MinerU PDF-to-Markdown inference on a small page selection.

This script is intentionally separate from the application parse pipeline. It
renders selected PDF pages, runs the locally downloaded MinerU model through
the official transformers backend, and records inspectable output artifacts.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pymupdf

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = REPO_ROOT / "models" / "MinerU2.5-Pro-2604-1.2B"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "logs" / "mineru-local-tests"
INSTALL_COMMAND = (
    "cd apps/api && uv run --python 3.11 "
    "python ../../scripts/test_mineru_local.py --pdf /path/to/sample.pdf"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="用本地 MinerU2.5-Pro 模型对 PDF 的少量页面进行 Markdown 解析冒烟测试。",
    )
    parser.add_argument("--pdf", required=True, type=Path, help="待测试的 PDF 文件路径。")
    parser.add_argument(
        "--model-path",
        type=Path,
        default=DEFAULT_MODEL_PATH,
        help=f"本地模型目录，默认：{DEFAULT_MODEL_PATH}",
    )
    parser.add_argument(
        "--pages",
        default="1",
        help='解析页码（从 1 开始），例如 "1"、"1-3,5" 或 "all"；默认仅第 1 页。',
    )
    parser.add_argument("--dpi", type=int, default=160, help="将 PDF 页面渲染为图片的 DPI；默认 160。")
    parser.add_argument(
        "--device-map",
        default="auto",
        help='传递给 transformers 的 device_map，例如 "auto"、"mps" 或 "cpu"；默认 auto。',
    )
    parser.add_argument(
        "--image-analysis",
        action="store_true",
        help="启用 MinerU 图片/图表分析能力；首次验证建议不要开启。",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="输出目录；默认在 logs/mineru-local-tests/ 下创建带时间戳的目录。",
    )
    parser.add_argument(
        "--render-only",
        action="store_true",
        help="只执行 PDF 页面渲染，不加载 MinerU 模型；用于先验证输入和页面选择。",
    )
    parser.add_argument("--debug", action="store_true", help="失败时同时将完整异常输出到终端。")
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    return path.expanduser().resolve()


def parse_page_selection(spec: str, page_count: int) -> list[int]:
    if page_count < 1:
        raise ValueError("PDF 不包含可解析页面。")
    if spec.strip().lower() == "all":
        return list(range(1, page_count + 1))

    selected: set[int] = set()
    for token in spec.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"无效页码范围：{token}")
            selected.update(range(start, end + 1))
        else:
            selected.add(int(token))

    if not selected:
        raise ValueError("未选择任何页面。")
    invalid = sorted(page for page in selected if page < 1 or page > page_count)
    if invalid:
        raise ValueError(f"页码超出范围：{invalid}；该 PDF 共 {page_count} 页。")
    return sorted(selected)


def output_directory(requested: Path | None) -> Path:
    if requested:
        return resolve_path(requested)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return DEFAULT_OUTPUT_ROOT / timestamp


def render_pages(pdf_path: Path, pages_spec: str, dpi: int, output_dir: Path) -> tuple[int, list[dict[str, Any]]]:
    if dpi < 72:
        raise ValueError("DPI 不能小于 72。")

    image_dir = output_dir / "pages"
    image_dir.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open(pdf_path)
    try:
        if not document.is_pdf:
            raise ValueError(f"文件不是有效 PDF：{pdf_path}")
        selected_pages = parse_page_selection(pages_spec, document.page_count)
        rendered: list[dict[str, Any]] = []
        for page_number in selected_pages:
            page = document.load_page(page_number - 1)
            started = time.perf_counter()
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            image_path = image_dir / f"page-{page_number:04d}.png"
            pixmap.save(image_path)
            rendered.append(
                {
                    "page_number": page_number,
                    "pdf_width": page.rect.width,
                    "pdf_height": page.rect.height,
                    "image_width": pixmap.width,
                    "image_height": pixmap.height,
                    "image_path": str(image_path),
                    "render_seconds": round(time.perf_counter() - started, 4),
                }
            )
        return document.page_count, rendered
    finally:
        document.close()


def dependency_error(import_error: ImportError) -> RuntimeError:
    return RuntimeError(
        "未安装 MinerU 本地推理依赖。当前脚本的完整推理需要官方推荐的 "
        '`mineru-vl-utils[transformers]`。请先重新执行 `./scripts/dev-start.sh` '
        f"同步项目依赖，再使用下列命令运行测试：\n{INSTALL_COMMAND}\n"
        f"原始导入错误：{import_error}"
    )


def create_mineru_client(model_path: Path, device_map: str, image_analysis: bool) -> tuple[Any, Any]:
    try:
        from mineru_vl_utils import MinerUClient
        from mineru_vl_utils.post_process import json2md
        from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
    except ImportError as exc:
        raise dependency_error(exc) from exc

    print(f"加载本地 MinerU 模型：{model_path}")
    print(f"推理后端：transformers；device_map={device_map}")
    load_started = time.perf_counter()
    model = Qwen2VLForConditionalGeneration.from_pretrained(
        str(model_path),
        dtype="auto",
        device_map=device_map,
        local_files_only=True,
    )
    processor = AutoProcessor.from_pretrained(
        str(model_path),
        use_fast=True,
        local_files_only=True,
    )
    client = MinerUClient(
        backend="transformers",
        model=model,
        processor=processor,
        image_analysis=image_analysis,
    )
    print(f"模型加载完成，耗时 {time.perf_counter() - load_started:.2f} 秒。")
    return client, json2md


def run_inference(
    rendered_pages: list[dict[str, Any]],
    output_dir: Path,
    model_path: Path,
    device_map: str,
    image_analysis: bool,
) -> tuple[list[dict[str, Any]], str, float]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise dependency_error(exc) from exc

    model_started = time.perf_counter()
    client, json2md = create_mineru_client(model_path, device_map, image_analysis)
    model_load_seconds = time.perf_counter() - model_started
    markdown_parts: list[str] = []
    parsed_pages: list[dict[str, Any]] = []

    for page in rendered_pages:
        page_number = page["page_number"]
        print(f"解析第 {page_number} 页...")
        page_started = time.perf_counter()
        with Image.open(page["image_path"]) as image:
            content_list = client.two_step_extract(image.convert("RGB"))
        markdown = json2md(content_list)
        elapsed = time.perf_counter() - page_started

        page_json_path = output_dir / f"page-{page_number:04d}.json"
        page_markdown_path = output_dir / f"page-{page_number:04d}.md"
        page_json_path.write_text(
            json.dumps(content_list, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        page_markdown_path.write_text(markdown, encoding="utf-8")
        markdown_parts.append(f"<!-- Page {page_number} -->\n\n{markdown.strip()}")
        parsed_pages.append(
            {
                **page,
                "markdown_path": str(page_markdown_path),
                "structured_json_path": str(page_json_path),
                "inference_seconds": round(elapsed, 4),
            }
        )
        print(f"第 {page_number} 页完成，耗时 {elapsed:.2f} 秒。")

    return parsed_pages, "\n\n".join(markdown_parts) + "\n", model_load_seconds


def write_report(output_dir: Path, report: dict[str, Any]) -> Path:
    report_path = output_dir / "run-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report_path


def main() -> int:
    args = parse_args()
    pdf_path = resolve_path(args.pdf)
    model_path = resolve_path(args.model_path)
    output_dir = output_directory(args.output_dir)
    report: dict[str, Any] = {
        "status": "started",
        "started_at": datetime.now(UTC).isoformat(),
        "pdf_path": str(pdf_path),
        "model_path": str(model_path),
        "backend": "transformers",
        "device_map": args.device_map,
        "dpi": args.dpi,
        "pages_requested": args.pages,
        "image_analysis": args.image_analysis,
        "render_only": args.render_only,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
    }

    if not pdf_path.is_file():
        print(f"PDF 文件不存在：{pdf_path}", file=sys.stderr)
        return 2
    if not args.render_only and not (model_path / "model.safetensors").is_file():
        print(f"本地 MinerU 模型目录无效，未找到 model.safetensors：{model_path}", file=sys.stderr)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    total_started = time.perf_counter()
    try:
        page_count, rendered_pages = render_pages(pdf_path, args.pages, args.dpi, output_dir)
        report.update({"page_count": page_count, "rendered_pages": rendered_pages})
        print(f"已渲染 {len(rendered_pages)} 页图片，输出目录：{output_dir}")

        if args.render_only:
            report.update(
                {
                    "status": "rendered_only",
                    "total_seconds": round(time.perf_counter() - total_started, 4),
                }
            )
            report_path = write_report(output_dir, report)
            print(f"轻量检查完成，报告：{report_path}")
            return 0

        parsed_pages, markdown, model_load_seconds = run_inference(
            rendered_pages,
            output_dir,
            model_path,
            args.device_map,
            args.image_analysis,
        )
        markdown_path = output_dir / "result.md"
        structured_path = output_dir / "result.json"
        markdown_path.write_text(markdown, encoding="utf-8")
        structured_path.write_text(
            json.dumps({"pages": parsed_pages}, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        report.update(
            {
                "status": "completed",
                "model_load_seconds": round(model_load_seconds, 4),
                "parsed_pages": parsed_pages,
                "markdown_path": str(markdown_path),
                "structured_json_path": str(structured_path),
                "total_seconds": round(time.perf_counter() - total_started, 4),
            }
        )
        report_path = write_report(output_dir, report)
        print(f"MinerU 本地解析完成，Markdown：{markdown_path}")
        print(f"运行报告：{report_path}")
        return 0
    except Exception as exc:
        report.update(
            {
                "status": "failed",
                "error": str(exc),
                "total_seconds": round(time.perf_counter() - total_started, 4),
            }
        )
        report_path = write_report(output_dir, report)
        print(f"测试失败：{exc}", file=sys.stderr)
        print(f"失败报告：{report_path}", file=sys.stderr)
        if args.debug:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
