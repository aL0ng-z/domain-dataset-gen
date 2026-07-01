#!/usr/bin/env python3
"""Prepare and smoke-test PaddleOCR-VL local API services on Apple Silicon.

This script intentionally stays outside the platform parser flow. It verifies
the official two-service deployment:

    local PaddleOCR-VL weights -> MLX-VLM OpenAI-compatible server
    -> PaddleX /layout-parsing API -> Markdown

The platform can be integrated with this protocol after the standalone smoke
test passes.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = REPO_ROOT / "models" / "PaddleOCR-VL-1.5-0.9B"
DEFAULT_LAYOUT_MODEL_PATH = REPO_ROOT / "models" / "PP-DocLayoutV3"
DEFAULT_SERVICE_ENV = REPO_ROOT / ".venv-paddleocr-service"
DEFAULT_VLM_ENV = REPO_ROOT / ".venv-paddleocr-mlx-service"
DEFAULT_RUNTIME_DIR = REPO_ROOT / "logs" / "paddleocr-local-service"
DEFAULT_MLX_MODEL_DIR_NAME = "PaddleOCR-VL-1.5-MLX"
DEFAULT_API_BASE_URL = "http://127.0.0.1:9020"
DEFAULT_VLM_BASE_URL = "http://127.0.0.1:9021"
DEFAULT_PIPELINE_NAME = "PaddleOCR-VL-1.5"
LAYOUT_MODEL_REPO = "PaddlePaddle/PP-DocLayoutV3"
LAYOUT_MODEL_REQUIRED_FILES = ("inference.json", "inference.pdiparams", "inference.yml")
PADDLEOCR_MLX_RUNTIME_PACKAGES = (
    "mlx-vlm==0.3.10",
    "mlx==0.31.1",
    "mlx-metal==0.31.1",
    "mlx-lm==0.30.5",
    "transformers==5.0.0rc3",
    "torch==2.12.0",
    "torchvision==0.27.0",
    "pandas==2.3.3",
    "datasets==4.8.5",
    "pyarrow==24.0.0",
    "numpy==2.4.6",
    "fsspec==2026.2.0",
)


def _model_path(value: str | None) -> Path:
    path = Path(value).expanduser() if value else DEFAULT_MODEL_PATH
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    if not (path / "model.safetensors").is_file():
        raise ValueError(f"本地 PaddleOCR-VL 权重目录无效，未找到 model.safetensors：{path}")
    return path


def _layout_model_path(value: str | None, *, require_files: bool = True) -> Path:
    path = Path(value).expanduser() if value else DEFAULT_LAYOUT_MODEL_PATH
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    if require_files:
        missing = [file_name for file_name in LAYOUT_MODEL_REQUIRED_FILES if not (path / file_name).is_file()]
        if missing:
            raise ValueError(
                f"本地 PP-DocLayoutV3 目录不完整：{path}，缺少 {', '.join(missing)}。"
                f"请先执行：{sys.executable} scripts/paddleocr_local_service.py download-layout"
            )
    return path


def _runtime_dir(value: str | None) -> Path:
    path = Path(value).expanduser() if value else DEFAULT_RUNTIME_DIR
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _service_env(value: str | None) -> Path:
    path = Path(value).expanduser() if value else DEFAULT_SERVICE_ENV
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _vlm_env(value: str | None) -> Path:
    path = Path(value).expanduser() if value else DEFAULT_VLM_ENV
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def _mlx_model_path(value: str | None, runtime_dir: Path, *, require_weights: bool = True) -> Path:
    path = Path(value).expanduser() if value else runtime_dir / "models" / DEFAULT_MLX_MODEL_DIR_NAME
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    if require_weights and not any(path.glob("*.safetensors")):
        raise ValueError(
            f"未找到 MLX 格式 PaddleOCR-VL 权重：{path}。"
            f"请先执行：{sys.executable} scripts/paddleocr_local_service.py convert"
        )
    return path


def _service_environment(runtime_dir: Path) -> dict[str, str]:
    cache_dir = runtime_dir / "cache"
    env = dict(os.environ)
    env["PADDLE_PDX_CACHE_HOME"] = str(cache_dir / "paddlex")
    env["HF_HOME"] = str(cache_dir / "huggingface")
    env["MODELSCOPE_CACHE"] = str(cache_dir / "modelscope")
    env["TOKENIZERS_PARALLELISM"] = "false"
    for key in ("NO_PROXY", "no_proxy"):
        entries = [item for item in env.get(key, "").split(",") if item]
        for hostname in ("127.0.0.1", "localhost", "::1"):
            if hostname not in entries:
                entries.append(hostname)
        env[key] = ",".join(entries)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return env


def _require_executable(service_env: Path, executable_name: str) -> Path:
    executable = service_env / "bin" / executable_name
    if not executable.is_file():
        raise RuntimeError(
            f"未找到 {executable}。请先执行：{sys.executable} scripts/paddleocr_local_service.py setup"
        )
    return executable


def _pipeline_config_path(runtime_dir: Path) -> Path:
    return runtime_dir / "config" / f"{DEFAULT_PIPELINE_NAME}.yaml"


def _write_pipeline_config(
    mlx_model_path: Path,
    layout_model_path: Path | None,
    service_env: Path,
    runtime_dir: Path,
    vlm_base_url: str,
    *,
    use_layout_detection: bool = True,
) -> Path:
    paddlex = _require_executable(service_env, "paddlex")
    service_python = _require_executable(service_env, "python")
    config_dir = runtime_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    env = _service_environment(runtime_dir)
    config_path = _pipeline_config_path(runtime_dir)
    if not config_path.is_file():
        subprocess.run(
            [
                str(paddlex),
                "--get_pipeline_config",
                DEFAULT_PIPELINE_NAME,
                "--save_path",
                str(config_dir),
            ],
            check=True,
            env=env,
        )
    if not config_path.is_file():
        raise RuntimeError(f"PaddleX 未生成预期的 pipeline 配置：{config_path}")

    patch_program = """\
import sys
from pathlib import Path
import yaml

config_path = Path(sys.argv[1])
model_path = sys.argv[2]
layout_model_path = sys.argv[3] or None
vlm_base_url = sys.argv[4]
use_layout_detection = sys.argv[5].lower() == "true"
with config_path.open("r", encoding="utf-8") as source:
    config = yaml.safe_load(source)
config["batch_size"] = 1
config["use_queues"] = False
config["use_layout_detection"] = use_layout_detection
layout = config["SubModules"]["LayoutDetection"]
layout["batch_size"] = 1
layout["model_dir"] = layout_model_path
vlm = config["SubModules"]["VLRecognition"]
vlm["model_dir"] = None
vlm["batch_size"] = 1
vlm["genai_config"] = {
    "backend": "mlx-vlm-server",
    "server_url": vlm_base_url,
    "max_concurrency": 1,
    "client_kwargs": {
        "model_name": model_path,
        "api_key": "null",
    },
}
with config_path.open("w", encoding="utf-8") as target:
    yaml.safe_dump(config, target, allow_unicode=True, sort_keys=False)
"""
    subprocess.run(
        [
            str(service_python),
            "-c",
            patch_program,
            str(config_path),
            str(mlx_model_path),
            "" if layout_model_path is None else str(layout_model_path),
            vlm_base_url,
            str(use_layout_detection).lower(),
        ],
        check=True,
        env=env,
    )
    return config_path


def command_setup(args: argparse.Namespace) -> int:
    _model_path(args.model_path)
    runtime_dir = _runtime_dir(args.runtime_dir)
    service_env = _service_env(args.service_env)
    vlm_env = _vlm_env(args.vlm_env)
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("未找到 uv，请先安装 uv 后重新执行。")

    print(f"创建独立 PaddleOCR 服务环境：{service_env}")
    subprocess.run([uv, "venv", "--no-project", "--python", args.python_version, str(service_env)], check=True)
    python = service_env / "bin" / "python"
    if not python.is_file():
        raise RuntimeError(f"未创建服务 Python 环境：{python}")

    print("安装 PaddleOCR-VL 与 PaddleX Serving 依赖...")
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            "--upgrade",
            args.paddle_package,
            args.ocr_package,
            args.serving_package,
        ],
        check=True,
    )
    print(f"创建独立 MLX-VLM 推理环境：{vlm_env}")
    subprocess.run([uv, "venv", "--no-project", "--python", args.vlm_python_version, str(vlm_env)], check=True)
    vlm_python = vlm_env / "bin" / "python"
    if not vlm_python.is_file():
        raise RuntimeError(f"未创建 MLX-VLM Python 环境：{vlm_python}")
    print("安装 Apple Silicon MLX-VLM 推理依赖...")
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--python",
            str(vlm_python),
            "--upgrade",
            *PADDLEOCR_MLX_RUNTIME_PACKAGES,
        ],
        check=True,
    )
    mlx_model_path = _mlx_model_path(args.mlx_model_path, runtime_dir, require_weights=False)
    print(f"服务环境准备完成；下一步将原始权重转换为 MLX 格式：{mlx_model_path}")
    print(f"执行：{sys.executable} scripts/paddleocr_local_service.py convert")
    print("本阶段只准备独立冒烟服务，不改变平台 ParserProfile 或启动流程。")
    return 0


def command_convert(args: argparse.Namespace) -> int:
    model_path = _model_path(args.model_path)
    runtime_dir = _runtime_dir(args.runtime_dir)
    vlm_env = _vlm_env(args.vlm_env)
    python = _require_executable(vlm_env, "python")
    mlx_model_path = _mlx_model_path(args.mlx_model_path, runtime_dir, require_weights=False)
    if any(mlx_model_path.glob("*.safetensors")):
        print(f"已存在 MLX 格式权重，复用目录：{mlx_model_path}")
        return 0

    mlx_model_path.mkdir(parents=True, exist_ok=True)
    env = _service_environment(runtime_dir)
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    command = [
        str(python),
        "-m",
        "mlx_vlm",
        "convert",
        "--hf-path",
        str(model_path),
        "--mlx-path",
        str(mlx_model_path),
        "--trust-remote-code",
    ]
    print(f"转换原始权重目录：{model_path}")
    print(f"生成 MLX 格式目录：{mlx_model_path}")
    print("转换启用离线模式，只读取仓库已有权重。")
    subprocess.run(command, check=True, env=env)
    _mlx_model_path(str(mlx_model_path), runtime_dir)
    print("MLX 格式权重转换完成。")
    return 0


def command_download_layout(args: argparse.Namespace) -> int:
    runtime_dir = _runtime_dir(args.runtime_dir)
    service_env = _service_env(args.service_env)
    _require_executable(service_env, "python")
    layout_model_path = _layout_model_path(args.layout_model_path, require_files=False)
    if all((layout_model_path / file_name).is_file() for file_name in LAYOUT_MODEL_REQUIRED_FILES):
        print(f"已存在 PP-DocLayoutV3 本地模型，复用目录：{layout_model_path}")
        return 0

    hf = service_env / "bin" / "hf"
    if not hf.is_file():
        hf = service_env / "bin" / "huggingface-cli"
    if not hf.is_file():
        raise RuntimeError(f"未找到 Hugging Face CLI：{service_env / 'bin' / 'hf'}。请先执行 setup。")

    layout_model_path.mkdir(parents=True, exist_ok=True)
    command = [
        str(hf),
        "download",
        LAYOUT_MODEL_REPO,
        "--local-dir",
        str(layout_model_path),
        "--include",
        "inference.json",
        "--include",
        "inference.pdiparams",
        "--include",
        "inference.yml",
        "--include",
        "README.md",
        "--include",
        ".gitattributes",
    ]
    print(f"下载 PP-DocLayoutV3：{LAYOUT_MODEL_REPO}")
    print(f"保存目录：{layout_model_path}")
    subprocess.run(command, check=True, env=_service_environment(runtime_dir))
    _layout_model_path(str(layout_model_path))
    print("PP-DocLayoutV3 下载完成。")
    return 0


def command_config(args: argparse.Namespace) -> int:
    runtime_dir = _runtime_dir(args.runtime_dir)
    mlx_model_path = _mlx_model_path(args.mlx_model_path, runtime_dir)
    layout_model_path = None if args.whole_page_smoke else _layout_model_path(args.layout_model_path)
    service_env = _service_env(args.service_env)
    config_path = _write_pipeline_config(
        mlx_model_path,
        layout_model_path,
        service_env,
        runtime_dir,
        args.vlm_base_url,
        use_layout_detection=not args.whole_page_smoke,
    )
    print(f"本地 MLX VLM 权重目录：{mlx_model_path}")
    if layout_model_path is not None:
        print(f"本地 PP-DocLayoutV3 目录：{layout_model_path}")
    print(f"PaddleX 服务配置：{config_path}")
    print(f"内部 MLX-VLM 地址：{args.vlm_base_url}")
    if args.whole_page_smoke:
        print("冒烟模式：关闭布局检测，将整页作为 OCR 区块交给本地 VLM。")
    print("配置使用本地权重路径，不包含官方 API token。")
    return 0


def command_vlm_serve(args: argparse.Namespace) -> int:
    runtime_dir = _runtime_dir(args.runtime_dir)
    mlx_model_path = _mlx_model_path(args.mlx_model_path, runtime_dir)
    vlm_env = _vlm_env(args.vlm_env)
    python = _require_executable(vlm_env, "python")
    try:
        subprocess.run([str(python), "-c", "import mlx_vlm"], check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("服务环境中未安装 mlx-vlm，请先执行 setup。") from exc

    command = [
        str(python),
        "-m",
        "mlx_vlm.server",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    env = _service_environment(runtime_dir)
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["MLX_TRUST_REMOTE_CODE"] = "true"
    print(f"启动 MLX-VLM 内部推理服务：http://{args.host}:{args.port}")
    print(f"请求中将指定本地 MLX 模型目录：{mlx_model_path}")
    print("MLX-VLM 进程启用离线模型模式，并信任本地模型随附的处理器实现。")
    os.execvpe(command[0], command, env)
    return 0


def command_api_serve(args: argparse.Namespace) -> int:
    runtime_dir = _runtime_dir(args.runtime_dir)
    mlx_model_path = _mlx_model_path(args.mlx_model_path, runtime_dir)
    layout_model_path = None if args.whole_page_smoke else _layout_model_path(args.layout_model_path)
    service_env = _service_env(args.service_env)
    paddlex = _require_executable(service_env, "paddlex")
    config_path = _write_pipeline_config(
        mlx_model_path,
        layout_model_path,
        service_env,
        runtime_dir,
        args.vlm_base_url,
        use_layout_detection=not args.whole_page_smoke,
    )
    command = [
        str(paddlex),
        "--serve",
        "--pipeline",
        str(config_path),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--device",
        args.device,
    ]
    print(f"启动 PaddleX 完整解析 API：http://{args.host}:{args.port}/layout-parsing")
    print(f"Pipeline 配置：{config_path}")
    print(f"VLM 推理转发至：{args.vlm_base_url}")
    if layout_model_path is not None:
        print(f"版面检测读取本地 PP-DocLayoutV3：{layout_model_path}")
    if args.whole_page_smoke:
        print("冒烟模式关闭布局检测：整页直接交给本地 VLM，不加载 PP-DocLayoutV3。")
    elif layout_model_path is not None:
        print("完整模式启用本地 PP-DocLayoutV3，不再隐式下载版面检测模型。")
    else:
        print("首次启动可能下载 PP-DocLayoutV3 布局检测模型；PaddleOCR-VL 权重读取本地目录。")
    os.execvpe(command[0], command, _service_environment(runtime_dir))
    return 0


def _http_json(url: str, *, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"服务返回非对象 JSON：{url}")
    return payload


def command_health(args: argparse.Namespace) -> int:
    payload = _http_json(f"{args.api_base_url.rstrip('/')}/openapi.json", timeout=args.timeout_seconds)
    print(f"完整 API 就绪：{args.api_base_url.rstrip('/')}/layout-parsing")
    print(f"服务标题：{payload.get('info', {}).get('title', 'unknown')}")
    return 0


def _minimal_pdf() -> bytes:
    stream = b"BT /F1 16 Tf 72 730 Td (PaddleOCR local API smoke test) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = [0]
    for number, content in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{number} 0 obj\n".encode("ascii"))
        document.extend(content)
        document.extend(b"\nendobj\n")
    xref_offset = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    document.extend(
        (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n").encode("ascii")
    )
    return bytes(document)


def _extract_markdown(response: dict[str, Any]) -> str:
    body = response.get("result")
    pages = body.get("layoutParsingResults") if isinstance(body, dict) else None
    if not isinstance(pages, list) or not pages:
        raise ValueError("PaddleOCR 响应缺少 result.layoutParsingResults")
    markdown_parts: list[str] = []
    for index, page in enumerate(pages):
        markdown = page.get("markdown") if isinstance(page, dict) else None
        text = markdown.get("text") if isinstance(markdown, dict) else None
        if not isinstance(text, str):
            raise ValueError(f"PaddleOCR 第 {index + 1} 页响应缺少 Markdown 文本")
        markdown_parts.append(text.strip())
    return "\n\n".join(markdown_parts).strip() + "\n"


def command_smoke(args: argparse.Namespace) -> int:
    runtime_dir = _runtime_dir(args.runtime_dir)
    run_dir = runtime_dir / "smoke" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.pdf:
        pdf_path = Path(args.pdf).expanduser().resolve()
        pdf_bytes = pdf_path.read_bytes()
    else:
        pdf_path = run_dir / "generated-smoke.pdf"
        pdf_bytes = _minimal_pdf()
        pdf_path.write_bytes(pdf_bytes)

    payload: dict[str, Any] = {
        "file": base64.b64encode(pdf_bytes).decode("ascii"),
        "fileType": 0,
        "useDocOrientationClassify": False,
        "useDocUnwarping": False,
        "useLayoutDetection": not args.whole_page_smoke,
        "visualize": False,
        "maxNewTokens": args.max_new_tokens,
    }
    request = urllib.request.Request(
        f"{args.api_base_url.rstrip('/')}/layout-parsing",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    print(f"提交 PDF：{pdf_path}")
    print(f"完整 API 地址：{args.api_base_url.rstrip('/')}/layout-parsing")
    if args.whole_page_smoke:
        print("冒烟模式：请求关闭布局检测，仅验证整页本地 VLM 的 API 链路。")
    try:
        with urllib.request.urlopen(request, timeout=args.parse_timeout_seconds) as response:
            raw_response = response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read()
        (run_dir / "error-response.txt").write_bytes(error_body)
        raise RuntimeError(f"PaddleOCR 服务返回 HTTP {exc.code}；响应已保存至 {run_dir}") from exc

    (run_dir / "response.json").write_bytes(raw_response)
    result = json.loads(raw_response.decode("utf-8"))
    markdown = _extract_markdown(result)
    (run_dir / "result.md").write_text(markdown, encoding="utf-8")
    print(f"解析完成，Markdown：{run_dir / 'result.md'}")
    print("本次请求未使用官方 PaddleOCR API token。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PaddleOCR-VL 本地 API 双服务的准备与无 Token 冒烟验证工具。")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model-path", help="原始 PaddleOCR-VL 模型目录；默认使用仓库 models 目录中的权重。")
    common.add_argument("--layout-model-path", help="本地 PP-DocLayoutV3 版面检测模型目录；默认 models/PP-DocLayoutV3。")
    common.add_argument("--mlx-model-path", help="转换后的 MLX 模型目录；默认使用运行目录下的 models/PaddleOCR-VL-1.5-MLX。")
    common.add_argument("--runtime-dir", help="服务配置、缓存与测试输出目录；默认 logs/paddleocr-local-service。")
    common.add_argument("--service-env", help="PaddleX 完整 API 虚拟环境目录；默认 .venv-paddleocr-service。")
    common.add_argument("--vlm-env", help="MLX-VLM 推理虚拟环境目录；默认 .venv-paddleocr-mlx-service。")

    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", parents=[common], help="创建独立 API 与 MLX 服务环境。")
    setup_parser.add_argument("--python-version", default="3.11", help="服务环境 Python 版本；默认 3.11。")
    setup_parser.add_argument("--vlm-python-version", default="3.12", help="MLX 推理环境 Python 版本；默认 3.12。")
    setup_parser.add_argument("--paddle-package", default="paddlepaddle>=3.2.1")
    setup_parser.add_argument("--ocr-package", default="paddleocr[doc-parser]>=3.5.0")
    setup_parser.add_argument("--serving-package", default="paddlex[serving]>=3.5.0")
    setup_parser.set_defaults(func=command_setup)

    convert_parser = subparsers.add_parser("convert", parents=[common], help="将下载的 PaddleOCR-VL 权重转换为 MLX 服务权重。")
    convert_parser.set_defaults(func=command_convert)

    download_layout_parser = subparsers.add_parser(
        "download-layout",
        parents=[common],
        help="下载完整布局检测所需的 PP-DocLayoutV3 模型。",
    )
    download_layout_parser.set_defaults(func=command_download_layout)

    config_parser = subparsers.add_parser("config", parents=[common], help="从官方模板生成指向 MLX-VLM 的 PaddleX 配置。")
    config_parser.add_argument("--vlm-base-url", default=DEFAULT_VLM_BASE_URL)
    config_parser.add_argument(
        "--whole-page-smoke",
        action="store_true",
        help="关闭布局检测，以无需 PP-DocLayoutV3 的整页 OCR 模式验证本地 API 链路。",
    )
    config_parser.set_defaults(func=command_config)

    vlm_parser = subparsers.add_parser("vlm-serve", parents=[common], help="以前台进程启动 MLX-VLM 内部推理服务。")
    vlm_parser.add_argument("--host", default="127.0.0.1")
    vlm_parser.add_argument("--port", type=int, default=9021)
    vlm_parser.set_defaults(func=command_vlm_serve)

    api_parser = subparsers.add_parser("api-serve", parents=[common], help="以前台进程启动 PaddleX /layout-parsing API。")
    api_parser.add_argument("--host", default="127.0.0.1")
    api_parser.add_argument("--port", type=int, default=9020)
    api_parser.add_argument("--device", default="cpu")
    api_parser.add_argument("--vlm-base-url", default=DEFAULT_VLM_BASE_URL)
    api_parser.add_argument(
        "--whole-page-smoke",
        action="store_true",
        help="关闭布局检测，以无需 PP-DocLayoutV3 的整页 OCR 模式验证本地 API 链路。",
    )
    api_parser.set_defaults(func=command_api_serve)

    health_parser = subparsers.add_parser("health", help="检查 PaddleX 完整 API 是否可访问。")
    health_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    health_parser.add_argument("--timeout-seconds", default=10.0, type=float)
    health_parser.set_defaults(func=command_health)

    smoke_parser = subparsers.add_parser("smoke", parents=[common], help="提交单页 PDF 并检查 Markdown 结果。")
    smoke_parser.add_argument("--api-base-url", default=DEFAULT_API_BASE_URL)
    smoke_parser.add_argument("--pdf", help="可选输入 PDF；省略时自动生成一页最小 PDF。")
    smoke_parser.add_argument("--max-new-tokens", default=256, type=int)
    smoke_parser.add_argument("--parse-timeout-seconds", default=1800.0, type=float)
    smoke_parser.add_argument(
        "--whole-page-smoke",
        action="store_true",
        help="请求关闭布局检测，应与以同名参数启动的完整 API 配合使用。",
    )
    smoke_parser.set_defaults(func=command_smoke)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError, urllib.error.URLError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
