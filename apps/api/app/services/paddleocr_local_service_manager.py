from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_API_BASE_URL = "http://127.0.0.1:9020/layout-parsing"
DEFAULT_VLM_BASE_URL = "http://127.0.0.1:9021"
REPO_ROOT = Path(__file__).resolve().parents[4]
SERVICE_SCRIPT = REPO_ROOT / "scripts" / "paddleocr_local_service.py"
API_EXECUTABLE = REPO_ROOT / ".venv-paddleocr-service" / "bin" / "paddlex"
VLM_PYTHON = REPO_ROOT / ".venv-paddleocr-mlx-service" / "bin" / "python"
LAYOUT_MODEL_WEIGHTS = REPO_ROOT / "models" / "PP-DocLayoutV3" / "inference.pdiparams"
MLX_MODEL_WEIGHTS = REPO_ROOT / "logs" / "paddleocr-local-service" / "models" / "PaddleOCR-VL-1.5-MLX" / "model.safetensors"
RUNTIME_DIR = REPO_ROOT / "logs" / "paddleocr-local-service"
API_LOG_PATH = RUNTIME_DIR / "managed-api.log"
VLM_LOG_PATH = RUNTIME_DIR / "managed-vlm.log"
API_PID_PATH = REPO_ROOT / "logs" / "PaddleOCR-API.pid"
VLM_PID_PATH = REPO_ROOT / "logs" / "PaddleOCR-VLM.pid"

_SERVICE_LOCK = threading.Lock()
_api_process: subprocess.Popen | None = None
_vlm_process: subprocess.Popen | None = None


def ensure_paddleocr_local_service(options: dict | None) -> None:
    """Start the locally managed PaddleOCR-VL services on first use, if needed."""
    settings = options or {}
    if not _is_enabled(settings.get("auto_start", True)):
        return

    api_base_url = str(settings.get("base_url", DEFAULT_API_BASE_URL)).strip().rstrip("/")
    vlm_base_url = str(settings.get("vlm_base_url", DEFAULT_VLM_BASE_URL)).strip().rstrip("/")
    startup_timeout = _positive_float(settings.get("startup_timeout_seconds", 90), "startup_timeout_seconds")
    api_port, api_root = _managed_local_service(api_base_url, allowed_paths={"", "/", "/layout-parsing"})
    if api_port is None or api_root is None:
        return
    vlm_port, vlm_root = _managed_local_service(vlm_base_url, allowed_paths={"", "/"})

    with _SERVICE_LOCK:
        if vlm_port is not None and vlm_root is not None and not _is_healthy(vlm_root):
            vlm_process = _get_or_start_vlm_process(vlm_port)
            _wait_until_healthy(vlm_root, vlm_process, startup_timeout, "PaddleOCR MLX-VLM", VLM_LOG_PATH)

        if not _is_healthy(api_root):
            api_process = _get_or_start_api_process(api_port, vlm_base_url, settings)
            _wait_until_healthy(api_root, api_process, startup_timeout, "PaddleOCR API", API_LOG_PATH)


def _managed_local_service(base_url: str, *, allowed_paths: set[str]) -> tuple[int | None, str | None]:
    parsed = urllib.parse.urlparse(base_url)
    path = parsed.path.rstrip("/")
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.username is not None
        or parsed.password is not None
        or path not in allowed_paths
        or parsed.query
        or parsed.fragment
    ):
        return None, None
    try:
        port = parsed.port or 80
    except ValueError:
        return None, None
    root = urllib.parse.urlunparse(parsed._replace(path="", params="", query="", fragment="")).rstrip("/")
    return port, root


def _get_or_start_vlm_process(port: int) -> subprocess.Popen:
    global _vlm_process

    if _vlm_process is not None and _vlm_process.poll() is None:
        return _vlm_process
    _ensure_runtime_ready()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    VLM_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    with VLM_LOG_PATH.open("ab") as log_file:
        _vlm_process = subprocess.Popen(
            [sys.executable, str(SERVICE_SCRIPT), "vlm-serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=REPO_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    VLM_PID_PATH.write_text(f"{_vlm_process.pid}\n", encoding="ascii")
    return _vlm_process


def _get_or_start_api_process(port: int, vlm_base_url: str, settings: dict) -> subprocess.Popen:
    global _api_process

    if _api_process is not None and _api_process.poll() is None:
        return _api_process
    _ensure_runtime_ready()
    command = [
        sys.executable,
        str(SERVICE_SCRIPT),
        "api-serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--vlm-base-url",
        vlm_base_url,
    ]
    if not _is_enabled(settings.get("use_layout_detection", True)) or _is_enabled(settings.get("whole_page_smoke", False)):
        command.append("--whole-page-smoke")

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    API_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    with API_LOG_PATH.open("ab") as log_file:
        _api_process = subprocess.Popen(
            command,
            cwd=REPO_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    API_PID_PATH.write_text(f"{_api_process.pid}\n", encoding="ascii")
    return _api_process


def _ensure_runtime_ready() -> None:
    if not SERVICE_SCRIPT.is_file():
        raise RuntimeError(f"未找到 PaddleOCR 本地服务启动脚本：{SERVICE_SCRIPT}")
    if not API_EXECUTABLE.is_file() or not VLM_PYTHON.is_file():
        raise RuntimeError(
            "PaddleOCR 本地服务环境尚未准备，请先在仓库根目录执行："
            "./.venv/bin/python scripts/paddleocr_local_service.py setup"
        )
    if not MLX_MODEL_WEIGHTS.is_file():
        raise RuntimeError(
            "PaddleOCR-VL 的 MLX 本地权重尚未转换，请先在仓库根目录执行："
            "./.venv/bin/python scripts/paddleocr_local_service.py convert"
        )
    if not LAYOUT_MODEL_WEIGHTS.is_file():
        raise RuntimeError(
            "PP-DocLayoutV3 本地版面检测模型尚未下载，请先在仓库根目录执行："
            "./.venv/bin/python scripts/paddleocr_local_service.py download-layout"
        )


def _wait_until_healthy(
    base_url: str,
    process: subprocess.Popen,
    startup_timeout: float,
    label: str,
    log_path: Path,
) -> None:
    deadline = time.monotonic() + startup_timeout
    while time.monotonic() < deadline:
        if _is_healthy(base_url):
            return
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"{label} 自动启动失败（退出码 {exit_code}），请查看日志：{log_path}")
        time.sleep(0.5)

    _terminate_process(process)
    raise TimeoutError(f"等待 {label} 自动启动超时，请查看日志：{log_path}")


def _is_healthy(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url.rstrip('/')}/openapi.json", timeout=0.8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and isinstance(payload.get("openapi"), str)


def _is_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "off", ""}


def _positive_float(value: object, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"PaddleOCR 本地服务参数 {name} 必须为数字") from exc
    if parsed <= 0:
        raise ValueError(f"PaddleOCR 本地服务参数 {name} 必须大于 0")
    return parsed


def _terminate_process(process: subprocess.Popen) -> None:
    global _api_process, _vlm_process

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if _api_process is process:
        _api_process = None
        _unlink_pid(API_PID_PATH, process.pid)
    if _vlm_process is process:
        _vlm_process = None
        _unlink_pid(VLM_PID_PATH, process.pid)


def _unlink_pid(pid_path: Path, pid: int) -> None:
    try:
        recorded_pid = pid_path.read_text(encoding="ascii").strip()
    except OSError:
        return
    if recorded_pid == str(pid):
        pid_path.unlink(missing_ok=True)
