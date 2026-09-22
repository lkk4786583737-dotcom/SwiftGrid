"""后台 Job + SSE：让阻塞的 LLM 调用在线程里跑，进度/终态通过事件队列推给前端。

事件类型：progress / success / clarify / error / cancelled。
取消：LLM 调用无法真正中途打断，置 cancelled 标志后，当前调用返回即丢弃结果、不进下一轮。
"""
from __future__ import annotations

import asyncio
import json
import queue
import threading
from typing import Callable

TERMINAL = ("success", "clarify", "error", "cancelled")


class Job:
    def __init__(self):
        self.q: queue.Queue = queue.Queue()
        self.cancelled = False
        self.done = False

    def emit(self, etype: str, data: dict | None = None):
        self.q.put((etype, data or {}))

    def finish(self, etype: str, data: dict | None = None):
        self.emit(etype, data)
        self.done = True

    def cancel(self):
        self.cancelled = True


def run_job(job: Job, fn: Callable[[Job], None]):
    def _worker():
        try:
            fn(job)
        except Exception as e:  # noqa: BLE001
            job.finish("error", {"message": f"{type(e).__name__}: {e}"})
        finally:
            job.done = True

    threading.Thread(target=_worker, daemon=True).start()


async def sse(job: Job):
    """异步生成器：轮询线程安全队列，转成 SSE 事件。"""
    while True:
        try:
            etype, data = job.q.get_nowait()
        except queue.Empty:
            if job.done:
                break
            await asyncio.sleep(0.08)
            continue
        yield {"event": etype, "data": json.dumps(data, ensure_ascii=False)}
        if etype in TERMINAL:
            break
