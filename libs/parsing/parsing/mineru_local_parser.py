from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pymupdf

from parsing.base import BaseParser, ParseResult

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MODEL_PATH = REPO_ROOT / "models" / "MinerU2.5-Pro-2604-1.2B"

_CLIENT_CACHE: dict[tuple[str, str, bool], tuple[Any, Any]] = {}
_CLIENT_CACHE_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: object, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        return int(str(value))
    except ValueError as exc:
        raise ValueError(f"MinerU 本地解析参数必须为整数：{value}") from exc


def _json_safe(value: object) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class MineruLocalParser(BaseParser):
    """Local MinerU2.5-Pro PDF parser using the official transformers client."""

    def _resolve_model_path(self) -> Path:
        configured = self.options.get("model_path")
        model_path = Path(str(configured)).expanduser() if configured else DEFAULT_MODEL_PATH
        if not model_path.is_absolute():
            model_path = REPO_ROOT / model_path
        model_path = model_path.resolve()
        if not (model_path / "model.safetensors").is_file():
            raise ValueError(f"MinerU 本地模型不存在或不完整：{model_path}")
        return model_path

    def _load_client(self, model_path: Path, device_map: str, image_analysis: bool) -> tuple[Any, Any]:
        key = (str(model_path), device_map, image_analysis)
        with _CLIENT_CACHE_LOCK:
            cached = _CLIENT_CACHE.get(key)
            if cached is not None:
                return cached

            try:
                from mineru_vl_utils import MinerUClient
                from mineru_vl_utils.post_process import json2md
                from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
            except ImportError as exc:
                raise RuntimeError(
                    "MinerU 本地解析依赖尚未安装。请重新运行一键启动脚本，"
                    "或在 apps/api 目录执行： uv sync --python 3.11 --extra dev"
                ) from exc

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
            cached = (client, json2md)
            _CLIENT_CACHE[key] = cached
            return cached

    def parse(self, pdf_data: bytes) -> ParseResult:
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "MinerU 本地解析依赖尚未安装。请重新运行一键启动脚本，"
                "或在 apps/api 目录执行： uv sync --python 3.11 --extra dev"
            ) from exc

        model_path = self._resolve_model_path()
        device_map = str(self.options.get("device_map", "auto"))
        render_dpi = _as_int(self.options.get("render_dpi"), 160)
        max_pages = _as_int(self.options.get("max_pages"), 0)
        image_analysis = _as_bool(self.options.get("image_analysis"), False)

        if render_dpi < 72:
            raise ValueError("MinerU 本地解析 render_dpi 不能小于 72。")
        if max_pages < 0:
            raise ValueError("MinerU 本地解析 max_pages 不能小于 0。")

        document = pymupdf.open(stream=pdf_data, filetype="pdf")
        try:
            page_count = document.page_count
            parsed_page_count = min(page_count, max_pages) if max_pages else page_count
            page_mapping: list[dict] = []
            page_results: list[dict] = []
            markdown_parts: list[str] = []

            # The cached model is memory-heavy; keep local parsing serial on a laptop.
            with _INFERENCE_LOCK:
                client, json2md = self._load_client(model_path, device_map, image_analysis)
                for page_index in range(parsed_page_count):
                    page = document.load_page(page_index)
                    page_started = time.perf_counter()
                    pixmap = page.get_pixmap(dpi=render_dpi, alpha=False)
                    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                    content_list = client.two_step_extract(image)
                    markdown = str(json2md(content_list)).strip()
                    markdown_start = sum(len(part) + 2 for part in markdown_parts)
                    markdown_parts.append(markdown)
                    page_mapping.append(
                        {
                            "page_number": page_index + 1,
                            "width": page.rect.width,
                            "height": page.rect.height,
                            "image_width": pixmap.width,
                            "image_height": pixmap.height,
                            "markdown_start": markdown_start,
                            "markdown_end": markdown_start + len(markdown),
                            "inference_seconds": round(time.perf_counter() - page_started, 4),
                        }
                    )
                    page_results.append(
                        {
                            "page_number": page_index + 1,
                            "content": _json_safe(content_list),
                            "markdown": markdown,
                        }
                    )
        finally:
            document.close()

        return ParseResult(
            raw_markdown="\n\n".join(markdown_parts) + "\n",
            structured_json={
                "parser": "mineru_local",
                "model": model_path.name,
                "model_path": str(model_path),
                "backend": "transformers",
                "device_map": device_map,
                "render_dpi": render_dpi,
                "image_analysis": image_analysis,
                "page_count": page_count,
                "parsed_page_count": parsed_page_count,
                "partial": parsed_page_count != page_count,
                "pages": page_results,
            },
            page_mapping=page_mapping,
        )
