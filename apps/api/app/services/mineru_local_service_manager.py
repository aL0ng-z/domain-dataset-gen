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

DEFAULT_BASE_URL = "http://127.0.0.1:9010"
REPO_ROOT = Path(__file__).resolve().parents[4]
SERVICE_SCRIPT = REPO_ROOT / "scripts" / "mineru_local_service.py"
SERVICE_EXECUTABLE = REPO_ROOT / ".venv-mineru-service" / "bin" / "mineru-api"
RUNTIME_DIR = REPO_ROOT / "logs" / "mineru-local-service"
SERVICE_LOG_PATH = RUNTIME_DIR / "managed-service.log"
SERVICE_PID_PATH = REPO_ROOT / "logs" / "MinerU-Service.pid"

_SERVICE_LOCK = threading.Lock()
_managed_process: subprocess.Popen | None = None


def ensure_mineru_local_service(options: dict | None) -> None:
    """Start the locally managed MinerU service on first use, if needed."""
    settings = options or {}
    if not _is_enabled(settings.get("auto_start", True)):
        return

    base_url = str(settings.get("base_url", DEFAULT_BASE_URL)).strip().rstrip("/")
    port = _managed_local_port(base_url)
    if port is None:
        return
    startup_timeout = _positive_float(settings.get("startup_timeout_seconds", 30), "startup_timeout_seconds")

    with _SERVICE_LOCK:
        if _is_healthy(base_url):
            return
        process = _get_or_start_process(port)
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if _is_healthy(base_url):
                return
            exit_code = process.poll()
            if exit_code is not None:
                raise RuntimeError(f"MinerU 本地服务自动启动失败（退出码 {exit_code}），请查看日志：{SERVICE_LOG_PATH}")
            time.sleep(0.2)

        _terminate_process(process)
        raise TimeoutError(f"等待 MinerU 本地服务自动启动超时，请查看日志：{SERVICE_LOG_PATH}")


def _managed_local_port(base_url: str) -> int | None:
    parsed = urllib.parse.urlparse(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/")
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        return parsed.port or 80
    except ValueError:
        return None


def _get_or_start_process(port: int) -> subprocess.Popen:
    global _managed_process

    if _managed_process is not None and _managed_process.poll() is None:
        return _managed_process
    if not SERVICE_EXECUTABLE.is_file():
        raise RuntimeError(
            "MinerU 本地服务环境尚未准备，请先在仓库根目录执行："
            "./.venv/bin/python scripts/mineru_local_service.py setup"
        )
    if not SERVICE_SCRIPT.is_file():
        raise RuntimeError(f"未找到 MinerU 本地服务启动脚本：{SERVICE_SCRIPT}")

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SERVICE_PID_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SERVICE_LOG_PATH.open("ab") as log_file:
        _managed_process = subprocess.Popen(
            [sys.executable, str(SERVICE_SCRIPT), "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=REPO_ROOT,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    SERVICE_PID_PATH.write_text(f"{_managed_process.pid}\n", encoding="ascii")
    return _managed_process


def _is_healthy(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/health", timeout=0.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "healthy"


def _is_enabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"false", "0", "no", "off"}


def _positive_float(value: object, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"MinerU 本地服务参数 {name} 必须为数字") from exc
    if parsed <= 0:
        raise ValueError(f"MinerU 本地服务参数 {name} 必须大于 0")
    return parsed


def _terminate_process(process: subprocess.Popen) -> None:
    global _managed_process

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if _managed_process is process:
        _managed_process = None
    try:
        recorded_pid = SERVICE_PID_PATH.read_text(encoding="ascii").strip()
    except OSError:
        return
    if recorded_pid == str(process.pid):
        SERVICE_PID_PATH.unlink(missing_ok=True)
