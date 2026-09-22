"""进程内存会话/句柄存储（单用户本地工具，无需数据库）。

- 数据句柄 handle：上传/粘贴/结果的 DataFrame（dict / df），前端只传 handle。
- 会话 session：学习/执行流的有状态上下文（澄清轮次、job 等）。
"""
from __future__ import annotations

import threading
import uuid

_DATA: dict[str, object] = {}
_SESS: dict[str, dict] = {}
_lock = threading.Lock()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# ── 数据句柄 ───────────────────────────────────────────────────────────────
def put_data(obj) -> str:
    with _lock:
        h = _new_id()
        _DATA[h] = obj
        return h


def get_data(handle: str):
    with _lock:
        return _DATA.get(handle)


# ── 流程会话 ───────────────────────────────────────────────────────────────
def new_session(initial: dict | None = None) -> str:
    with _lock:
        sid = _new_id()
        _SESS[sid] = initial or {}
        return sid


def get_session(sid: str) -> dict | None:
    with _lock:
        return _SESS.get(sid)
