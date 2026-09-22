"""引擎/存储导入 + 全局配置（单用户，进程内存）。

把仓库根目录加进 sys.path，复用现有 engine.py / storage.py（不改、不搬，
旧 Streamlit app.py 仍依赖它们）。
"""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]  # swiftgrid/backend/deps.py -> 仓库根
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import engine   # noqa: E402  (复用核心引擎)
import storage  # noqa: E402

# ── 全局配置（API Key 仅来自环境变量/进程内存，绝不落盘/入库）───────────────
_CONFIG = {
    "api_key": os.getenv("SWIFTGRID_API_KEY", "").strip(),
    "model": os.getenv("SWIFTGRID_MODEL", "").strip(),
    "base_url": os.getenv("SWIFTGRID_BASE_URL", "").strip(),
}
_lock = threading.Lock()


def runtime_config_enabled() -> bool:
    """Whether browsers may replace the process-wide model configuration."""
    value = os.getenv("SWIFTGRID_ALLOW_RUNTIME_CONFIG", "true").strip().lower()
    return value in {"1", "true", "yes", "on"}


def set_config(api_key: str | None = None, model: str | None = None, base_url: str | None = None):
    with _lock:
        if api_key is not None:
            _CONFIG["api_key"] = api_key.strip()
        if model is not None:
            _CONFIG["model"] = model.strip()
        if base_url is not None:
            _CONFIG["base_url"] = base_url.strip()


def get_config() -> dict:
    with _lock:
        return dict(_CONFIG)


def engine_kwargs() -> dict:
    """把企业私有部署的 base_url/model 透传给 engine 调用。"""
    c = get_config()
    kw = {}
    if c["base_url"]:
        kw["base_url"] = c["base_url"]
    if c["model"]:
        kw["model"] = c["model"]
    return kw


def require_key() -> str:
    c = get_config()
    if not c["api_key"]:
        raise ValueError("尚未配置 API Key，请先在「模型与密钥配置」里填写。")
    return c["api_key"]
