#!/usr/bin/env python3
"""Prepare and smoke-test a standalone MinerU service backed by local weights.

This script intentionally stays outside the platform parser flow. It supports
the first-stage deployment check: run official ``mineru-api`` in a separate
environment, point it at the repository's downloaded VLM model, and submit a
small PDF without any official API token.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = REPO_ROOT / "models" / "MinerU2.5-Pro-2604-1.2B"
DEFAULT_SERVICE_ENV = REPO_ROOT / ".venv-mineru-service"
DEFAULT_RUNTIME_DIR = REPO_ROOT / "logs" / "mineru-local-service"
DEFAULT_BASE_URL = "http://127.0.0.1:9010"


def _model_path(value: str | None) -> Path:
    path = Path(value).expanduser() if value else DEFAULT_MODEL_PATH
    if not path.is_absolute():
        path = REPO_ROOT / path
    path = path.resolve()
    if not (path / "model.safetensors").is_file():
        raise ValueError(f"本地 MinerU 权重目录无效，未找到 model.safetensors：{path}")
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


def _write_config(model_path: Path, runtime_dir: Path) -> Path:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path = runtime_dir / "mineru.json"
    config = {
        "models-dir": {
            "pipeline": "",
            "vlm": str(model_path),
        },
        "config_version": "1.3.1",
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return config_path


def _service_environment(config_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["MINERU_MODEL_SOURCE"] = "local"
    env["MINERU_TOOLS_CONFIG_JSON"] = str(config_path)
    return env


def command_config(args: argparse.Namespace) -> int:
    model_path = _model_path(args.model_path)
    runtime_dir = _runtime_dir(args.runtime_dir)
    config_path = _write_config(model_path, runtime_dir)
    print(f"本地模型目录：{model_path}")
    print(f"MinerU 服务配置：{config_path}")
    print("配置仅引用本地权重，不包含官方 API token。")
    return 0


def command_setup(args: argparse.Namespace) -> int:
    model_path = _model_path(args.model_path)
    runtime_dir = _runtime_dir(args.runtime_dir)
    service_env = _service_env(args.service_env)
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("未找到 uv，请先安装 uv 后重新执行。")

    print(f"创建独立 MinerU 服务环境：{service_env}")
    subprocess.run([uv, "venv", "--no-project", "--python", args.python_version, str(service_env)], check=True)
    python = service_env / "bin" / "python"
    if not python.is_file():
        raise RuntimeError(f"未创建服务 Python 环境：{python}")

    print(f"安装官方 MinerU 服务包：{args.package}")
    subprocess.run([uv, "pip", "install", "--python", str(python), "--upgrade", args.package], check=True)
    config_path = _write_config(model_path, runtime_dir)
    print(f"服务环境准备完成；配置文件：{config_path}")
    print("Mac 阶段首先验证 mineru-api 的本地权重解析；vLLM 后端需按运行硬件另行核验。")
    return 0


def command_serve(args: argparse.Namespace) -> int:
    model_path = _model_path(args.model_path)
    runtime_dir = _runtime_dir(args.runtime_dir)
    service_env = _service_env(args.service_env)
    executable = service_env / "bin" / "mineru-api"
    if not executable.is_file():
        raise RuntimeError(f"未找到 {executable}。请先执行：{sys.executable} scripts/mineru_local_service.py setup")
    config_path = _write_config(model_path, runtime_dir)
    output_dir = runtime_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(executable),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--enable-vlm-preload",
        "true" if args.preload else "false",
    ]
    print(f"启动本地 MinerU 服务：http://{args.host}:{args.port}")
    print(f"本地 VLM 权重：{model_path}")
    print(f"服务配置：{config_path}")
    print(f"输出目录：{output_dir}")
    os.chdir(output_dir)
    os.execvpe(command[0], command, _service_environment(config_path))
    return 0


def _http_json(url: str, *, timeout: float) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"服务返回非对象 JSON：{url}")
    return payload


def command_health(args: argparse.Namespace) -> int:
    payload = _http_json(f"{args.base_url.rstrip('/')}/health", timeout=args.timeout_seconds)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _minimal_pdf() -> bytes:
    stream = b"BT /F1 16 Tf 72 730 Td (MinerU local service PDF smoke test) Tj ET"
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


def _encode_multipart(fields: dict[str, str], filename: str, pdf_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"----mineru-local-service-{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode("ascii"),
                f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("ascii"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )
    parts.extend(
        [
            f"--{boundary}\r\n".encode("ascii"),
            f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'.encode("ascii"),
            b"Content-Type: application/pdf\r\n\r\n",
            pdf_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("ascii"),
        ]
    )
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _find_markdown(value: object) -> str | None:
    if isinstance(value, dict):
        for key in ("md_content", "markdown", "raw_markdown", "md"):
            markdown = value.get(key)
            if isinstance(markdown, str) and markdown.strip():
                return markdown
        for nested in value.values():
            markdown = _find_markdown(nested)
            if markdown is not None:
                return markdown
    elif isinstance(value, list):
        for nested in value:
            markdown = _find_markdown(nested)
            if markdown is not None:
                return markdown
    return None


def command_smoke(args: argparse.Namespace) -> int:
    runtime_dir = _runtime_dir(args.runtime_dir)
    run_dir = runtime_dir / "smoke" / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)

    if args.pdf:
        pdf_path = Path(args.pdf).expanduser().resolve()
        pdf_bytes = pdf_path.read_bytes()
        filename = pdf_path.name
    else:
        pdf_path = run_dir / "generated-smoke.pdf"
        pdf_bytes = _minimal_pdf()
        pdf_path.write_bytes(pdf_bytes)
        filename = pdf_path.name

    health = _http_json(f"{args.base_url.rstrip('/')}/health", timeout=args.timeout_seconds)
    (run_dir / "health.json").write_text(json.dumps(health, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    fields = {
        "backend": args.backend,
        "lang_list": args.language,
        "image_analysis": str(args.image_analysis).lower(),
        "return_md": "true",
        "return_middle_json": "true",
        "return_content_list": "true",
        "return_model_output": "false",
        "return_images": "false",
        "response_format_zip": "false",
        "formula_enable": "true",
        "table_enable": "true",
    }
    if args.server_url:
        fields["server_url"] = args.server_url
    body, content_type = _encode_multipart(fields, filename, pdf_bytes)
    request = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/file_parse",
        data=body,
        headers={"Content-Type": content_type, "Accept": "application/json"},
        method="POST",
    )
    print(f"提交 PDF：{pdf_path}")
    print(f"解析后端：{args.backend}")
    if args.server_url:
        print(f"VLM 服务地址：{args.server_url}")
    try:
        with urllib.request.urlopen(request, timeout=args.parse_timeout_seconds) as response:
            raw_response = response.read()
    except urllib.error.HTTPError as exc:
        error_body = exc.read()
        (run_dir / "error-response.txt").write_bytes(error_body)
        raise RuntimeError(f"MinerU 服务返回 HTTP {exc.code}；响应已保存至 {run_dir}") from exc

    (run_dir / "response.json").write_bytes(raw_response)
    result = json.loads(raw_response.decode("utf-8"))
    markdown = _find_markdown(result)
    if markdown is None:
        raise RuntimeError(f"服务响应中未找到 Markdown 内容；响应已保存至 {run_dir / 'response.json'}")
    (run_dir / "result.md").write_text(markdown.rstrip() + "\n", encoding="utf-8")
    print(f"解析完成，Markdown：{run_dir / 'result.md'}")
    print("本次请求未使用官方 MinerU API token。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="独立 MinerU 本地解析服务的准备、启动与无 Token 冒烟测试工具。")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model-path", help="本地 MinerU VLM 模型目录；默认使用仓库 models 目录中的权重。")
    common.add_argument("--runtime-dir", help="运行配置与测试输出目录；默认 logs/mineru-local-service。")
    common.add_argument("--service-env", help="独立服务虚拟环境目录；默认 .venv-mineru-service。")

    subparsers = parser.add_subparsers(dest="command", required=True)
    config_parser = subparsers.add_parser("config", parents=[common], help="生成指向本地模型的 MinerU 配置。")
    config_parser.set_defaults(func=command_config)

    setup_parser = subparsers.add_parser("setup", parents=[common], help="创建独立服务环境并安装官方 MinerU 服务。")
    setup_parser.add_argument("--python-version", default="3.12", help="服务环境 Python 版本；默认 3.12。")
    setup_parser.add_argument("--package", default="mineru[all]>=3.0.0", help="安装的 MinerU 包规格。")
    setup_parser.set_defaults(func=command_setup)

    serve_parser = subparsers.add_parser("serve", parents=[common], help="以前台进程启动官方 mineru-api。")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", default=9010, type=int)
    serve_parser.add_argument("--preload", action="store_true", help="在服务启动时预加载 VLM。")
    serve_parser.set_defaults(func=command_serve)

    health_parser = subparsers.add_parser("health", help="查询已启动的 MinerU 服务健康状态。")
    health_parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    health_parser.add_argument("--timeout-seconds", default=10.0, type=float)
    health_parser.set_defaults(func=command_health)

    smoke_parser = subparsers.add_parser("smoke", parents=[common], help="提交单页 PDF 并检查 Markdown 结果。")
    smoke_parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    smoke_parser.add_argument("--pdf", help="可选输入 PDF；省略时自动生成一页最小 PDF。")
    smoke_parser.add_argument("--backend", default="vlm-auto-engine", help="MinerU 请求后端。")
    smoke_parser.add_argument("--server-url", help="使用 *-http-client 后端时的 OpenAI 兼容 VLM 地址。")
    smoke_parser.add_argument("--language", default="ch")
    smoke_parser.add_argument("--image-analysis", action="store_true", help="启用 VLM 图片/图表分析；默认关闭。")
    smoke_parser.add_argument("--timeout-seconds", default=10.0, type=float)
    smoke_parser.add_argument("--parse-timeout-seconds", default=1800.0, type=float)
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
